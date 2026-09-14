# -*- coding: utf-8 -*-
"""
Web 管理后台鉴权模块。

单一开关 `ADMIN_AUTH_ENABLED` + 基于文件的凭据存储。
首次登录时设置初始密码；支持 Web 端改密与 CLI 重置密码。

主要能力：
- 启用/关闭鉴权的环境开关读取
- 基于 PBKDF2-HMAC-SHA256 的口令哈希与校验（常量时间比较）
- 基于 HMAC 签名的会话 Cookie 创建与校验
- 基于滑动窗口的登录失败限流（按客户端 IP）
- 凭据与会话密钥的原子写入与权限收紧（0o600）
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import logging
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

from dotenv import dotenv_values

# 获取当前模块的日志记录器，用于输出鉴权相关的日志信息（如密钥轮换、登录失败等）
logger = logging.getLogger(__name__)

# =============================================================================
# 模块级常量定义
# =============================================================================

# 会话 Cookie 的名称，用于在浏览器端标识用户登录状态
COOKIE_NAME = "dsa_session"
# PBKDF2 密钥派生算法的迭代次数，100000 次可在安全与性能间取得平衡
# 迭代次数越高，暴力破解成本越高，但登录校验耗时也会相应增加
PBKDF2_ITERATIONS = 100_000
# 登录失败限流的滑动窗口时长，单位：秒（300 秒 = 5 分钟）
# 在此时间窗口内累计登录失败次数，超过阈值则触发限流
RATE_LIMIT_WINDOW_SEC = 300
# 在滑动窗口内允许的最大登录失败次数，超过此值则该 IP 被限流
RATE_LIMIT_MAX_FAILURES = 5
# 会话 Cookie 的默认最大有效时长，单位：小时
# 超过此时间后，会话 Cookie 将失效，用户需重新登录
SESSION_MAX_AGE_HOURS_DEFAULT = 24
# 密码最小长度限制，低于此长度视为弱口令，设置/修改密码时将拒绝
MIN_PASSWORD_LEN = 6

# =============================================================================
# 模块级全局状态（惰性加载）
# =============================================================================

# 鉴权是否启用的全局缓存，None 表示尚未从环境变量加载
# 使用全局缓存避免每次鉴权请求都重复读取 .env 文件
_auth_enabled: Optional[bool] = None
# 会话签名密钥的全局缓存，用于对会话 Cookie 进行 HMAC 签名与校验
# 密钥为 32 字节随机数据，存储在数据目录的 .session_secret 文件中
_session_secret: Optional[bytes] = None
# 从凭据文件解析出的盐值，用于 PBKDF2 口令哈希校验
_password_hash_salt: Optional[bytes] = None
# 从凭据文件解析出的哈希值，用于与提交的口令进行常量时间比对
_password_hash_stored: Optional[bytes] = None
# 基于滑动窗口的登录失败限流字典
# 键：客户端 IP 地址（字符串）
# 值：元组 (失败次数, 首次失败时间戳)，用于判断是否超出限流阈值
_rate_limit: dict[str, Tuple[int, float]] = {}
# 用于保护限流字典的线程锁，惰性初始化以避免不必要的开销
# 多线程/多进程环境下，防止并发修改 _rate_limit 导致数据竞争
_rate_limit_lock = None


def _get_lock():
    """惰性初始化用于限流字典的线程锁。

    由于 _rate_limit 字典可能在多线程环境下被并发访问（如多个请求同时登录失败），
    需要通过线程锁保证数据一致性。锁对象在首次调用时创建，后续直接复用。

    Returns:
        threading.Lock: 用于保护 _rate_limit 的线程锁对象。
    """
    global _rate_limit_lock
    if _rate_limit_lock is None:
        import threading
        _rate_limit_lock = threading.Lock()
    return _rate_limit_lock


def _ensure_env_loaded() -> None:
    """在读取鉴权配置前确保已加载 .env 环境变量文件。

    该函数通过调用 src.config 模块的 setup_env() 来加载环境变量，
    确保后续读取 ADMIN_AUTH_ENABLED 等配置时能够获取到正确的值。
    """
    from src.config import setup_env
    setup_env()


def _get_data_dir() -> Path:
    """返回 DATABASE_PATH 所在目录，作为凭据与密钥文件的存放位置。

    从环境变量 DATABASE_PATH 获取数据库路径，提取其父目录作为数据目录。
    如果环境变量未设置，默认使用 "./data/stock_analysis.db"，即数据目录为 "./data"。

    Returns:
        Path: 数据目录的绝对路径。
    """
    db_path = os.getenv("DATABASE_PATH", "./data/stock_analysis.db")
    return Path(db_path).resolve().parent


def _get_credential_path() -> Path:
    """返回存储口令哈希文件的完整路径。

    该文件位于数据目录下，文件名为 ".admin_password_hash"，
    采用 salt_b64:hash_b64 的格式存储密码哈希。

    Returns:
        Path: 口令哈希文件的绝对路径。
    """
    return _get_data_dir() / ".admin_password_hash"


def _is_auth_enabled_from_env() -> bool:
    """从 .env 文件读取 ADMIN_AUTH_ENABLED 配置项，判断鉴权是否启用。

    读取逻辑：
    1. 首先确保环境变量已加载
    2. 优先使用 ENV_FILE 环境变量指定的 .env 文件路径
    3. 若未指定，回退到仓库根目录的默认 .env 文件
    4. 读取 ADMIN_AUTH_ENABLED 的值，不区分大小写
    5. 值为 "true"、"1" 或 "yes" 时返回 True，其他情况返回 False

    Returns:
        bool: 鉴权是否启用。
    """
    _ensure_env_loaded()
    env_file = os.getenv("ENV_FILE")
    # 未显式指定 ENV_FILE 时，回退到仓库根目录的默认 .env
    env_path = Path(env_file) if env_file else Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return False
    values = dotenv_values(env_path)
    val = (values.get("ADMIN_AUTH_ENABLED") or "").strip().lower()
    return val in ("true", "1", "yes")


def rotate_session_secret() -> bool:
    """轮换会话签名密钥，使所有已签发的会话立即失效。

    当管理员怀疑会话密钥泄露、需要强制所有用户重新登录时，可以调用此函数。
    轮换后，所有基于旧密钥签发的会话 Cookie 将无法通过校验，用户需要重新登录。

    密钥文件写入流程（原子写入）：
    1. 生成 32 字节高质量随机密钥（secrets.token_bytes 使用操作系统提供的安全随机源）
    2. 写入临时文件（.session_secret.tmp）
    3. 设置临时文件权限为 0o600（仅所有者可读写），防止其他用户读取
    4. 原子替换（rename）到目标文件，避免半写状态导致密钥不可用

    Args:
        无

    Returns:
        bool: 轮换成功返回 True，失败返回 False。
    """
    global _session_secret
    data_dir = _get_data_dir()
    secret_path = data_dir / ".session_secret"
    data_dir.mkdir(parents=True, exist_ok=True)
    # 生成 32 字节的高质量随机密钥，用于 HMAC-SHA256 签名
    new_secret = secrets.token_bytes(32)
    try:
        # 先写临时文件再原子替换，避免半写状态导致密钥不可用
        tmp_path = secret_path.with_suffix(".tmp")
        tmp_path.write_bytes(new_secret)
        tmp_path.chmod(0o600)
        tmp_path.replace(secret_path)
        _session_secret = new_secret
        logger.info("Session secret rotated successfully")
        return True
    except OSError as e:
        logger.error("Failed to rotate .session_secret: %s", e)
        return False


def _load_session_secret() -> Optional[bytes]:
    """加载或创建会话签名密钥。

    会话密钥用于对会话 Cookie 进行 HMAC 签名，防止客户端篡改。
    密钥存储在数据目录下的 ".session_secret" 文件中，权限为 0o600。

    加载流程：
    1. 如果内存中已有缓存，直接返回缓存的密钥
    2. 如果密钥文件存在，读取并校验长度（必须为 32 字节）
    3. 如果长度不符合预期，视为损坏，触发重新生成
    4. 如果密钥文件不存在，生成新的 32 字节随机密钥并写入文件
    5. 使用排他创建（xb 模式）避免并发初始化时互相覆盖
    6. 如果排他创建失败（FileExistsError），说明其他进程已创建，读取已有内容

    Returns:
        Optional[bytes]: 会话签名密钥（32 字节），失败时返回 None。
    """
    global _session_secret
    if _session_secret is not None:
        return _session_secret

    data_dir = _get_data_dir()
    secret_path = data_dir / ".session_secret"

    try:
        if secret_path.exists():
            _session_secret = secret_path.read_bytes()
            # 密钥长度不符合预期视为损坏，触发重新生成
            if len(_session_secret) != 32:
                logger.warning("Invalid .session_secret length, regenerating")
                _session_secret = None
                if rotate_session_secret():
                    return _session_secret
                return None
            return _session_secret

        data_dir.mkdir(parents=True, exist_ok=True)
        new_secret = secrets.token_bytes(32)
        try:
            # 使用排他创建避免并发初始化时互相覆盖
            with open(secret_path, "xb") as f:
                f.write(new_secret)
            secret_path.chmod(0o600)
        except FileExistsError:
            # 竞争条件下已被其他进程写入，读取已有内容
            _session_secret = secret_path.read_bytes()
        else:
            _session_secret = new_secret
        return _session_secret
    except OSError as e:
        logger.error("Failed to create or read .session_secret: %s", e)
        return None


def _parse_password_hash(value: str) -> Optional[Tuple[bytes, bytes]]:
    """解析 ``salt_b64:hash_b64`` 格式字符串，返回 (salt, hash) 或 None。

    该格式用于存储密码哈希，结构为：
    - salt_b64: Base64 编码的随机盐值（32 字节）
    - hash_b64: Base64 编码的 PBKDF2 哈希结果

    Args:
        value: 待解析的密码哈希字符串。

    Returns:
        Optional[Tuple[bytes, bytes]]: 解析成功返回 (salt, hash) 元组，失败返回 None。
    """
    if not value or ":" not in value:
        return None
    parts = value.strip().split(":", 1)
    if len(parts) != 2:
        return None
    try:
        salt_b64, hash_b64 = parts[0].strip(), parts[1].strip()
        salt = base64.standard_b64decode(salt_b64)
        stored_hash = base64.standard_b64decode(hash_b64)
        if salt and stored_hash:
            return (salt, stored_hash)
    except (ValueError, TypeError):
        pass
    return None


def _verify_password_hash(submitted: str, salt: bytes, stored_hash: bytes) -> bool:
    """使用 PBKDF2-HMAC-SHA256 重算并以常量时间比较校验提交的口令。

    安全特性：
    - 使用 PBKDF2-HMAC-SHA256 进行密钥派生，迭代次数为 100,000 次
    - 使用 hmac.compare_digest 进行常量时间比较，防止时序攻击
    - 盐值确保相同密码的哈希结果不同，防止彩虹表攻击

    Args:
        submitted: 用户提交的明文口令。
        salt: 存储的盐值（32 字节）。
        stored_hash: 存储的 PBKDF2 哈希结果。

    Returns:
        bool: 口令匹配返回 True，不匹配返回 False。
    """
    computed = hashlib.pbkdf2_hmac(
        "sha256",
        submitted.encode("utf-8"),
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    # hmac.compare_digest 避免基于字符串比较时长的侧信道
    return hmac.compare_digest(computed, stored_hash)


def _load_credential_from_file() -> bool:
    """从磁盘加载凭据到模块全局变量，返回是否成功加载。

    该函数读取 ".admin_password_hash" 文件，解析其中的 salt 和 hash，
    并存储到模块全局变量 _password_hash_salt 和 _password_hash_stored 中。

    Returns:
        bool: 成功加载返回 True，失败返回 False。
    """
    global _password_hash_salt, _password_hash_stored

    path = _get_credential_path()
    if not path.exists():
        _password_hash_salt = None
        _password_hash_stored = None
        return False

    try:
        raw = path.read_text().strip()
        parsed = _parse_password_hash(raw)
        if parsed is None:
            logger.warning("Invalid .admin_password_hash format, ignoring")
            return False
        _password_hash_salt, _password_hash_stored = parsed
        return True
    except OSError as e:
        logger.error("Failed to read credential file: %s", e)
        return False


def refresh_auth_state() -> None:
    """从磁盘和环境变量重新加载鉴权相关状态。

    该函数清除内存中的缓存，强制重新读取环境变量和凭据文件。
    适用于配置文件变更后需要立即生效的场景。
    """
    global _auth_enabled, _session_secret
    _auth_enabled = None
    _session_secret = None
    _load_credential_from_file()


def is_auth_enabled() -> bool:
    """返回管理后台鉴权是否启用（`ADMIN_AUTH_ENABLED=true`）。

    该函数使用惰性加载策略，首次调用时从环境变量读取配置，
    后续调用直接返回缓存的结果。

    Returns:
        bool: 鉴权启用返回 True，禁用返回 False。
    """
    global _auth_enabled
    if _auth_enabled is not None:
        return _auth_enabled
    _auth_enabled = _is_auth_enabled_from_env()
    return _auth_enabled


def has_stored_password() -> bool:
    """返回磁盘上是否存在有效的口令哈希。

    该函数会触发 _load_credential_from_file() 读取凭据文件。

    Returns:
        bool: 存在有效凭据返回 True，否则返回 False。
    """
    return _load_credential_from_file()


def verify_stored_password(password: str) -> bool:
    """即使鉴权被禁用也尝试与存储凭据校验（用于首次设置/改密前置）。

    该函数不检查鉴权是否启用，直接比较输入密码与存储哈希。
    适用于需要验证当前密码的场景（如修改密码前的旧密码验证）。

    Args:
        password: 待验证的明文密码。

    Returns:
        bool: 密码匹配返回 True，不匹配返回 False。
    """
    if not has_stored_password():
        return False
    return _verify_password_hash(password, _password_hash_salt, _password_hash_stored)


def is_password_set() -> bool:
    """返回初始口令是否已设置（凭据文件存在且有效）。

    该函数仅在鉴权启用时检查凭据文件是否存在。
    如果鉴权被禁用，直接返回 False。

    Returns:
        bool: 口令已设置返回 True，未设置返回 False。
    """
    if not is_auth_enabled():
        return False
    return has_stored_password()


def is_password_changeable() -> bool:
    """返回是否可通过 Web/CLI 修改口令（鉴权启用时恒为 True）。

    Returns:
        bool: 可以修改返回 True，否则返回 False。
    """
    return is_auth_enabled()


def _get_session_secret() -> Optional[bytes]:
    """返回会话签名密钥；鉴权未启用时返回 None。

    该函数仅在鉴权启用时加载会话密钥，用于后续创建和验证会话 Cookie。

    Returns:
        Optional[bytes]: 会话签名密钥（32 字节），鉴权未启用时返回 None。
    """
    if not is_auth_enabled():
        return None
    return _load_session_secret()


def _validate_password(pwd: str) -> Optional[str]:
    """校验密码复杂度，返回校验失败时的错误消息；通过则返回 None。

    校验规则：
    1. 密码不能为空或仅包含空白字符
    2. 密码长度至少为 MIN_PASSWORD_LEN（默认 6）

    Args:
        pwd: 待校验的明文密码。

    Returns:
        Optional[str]: 校验失败返回错误消息，通过返回 None。
    """
    if not pwd or not pwd.strip():
        return "密码不能为空"
    if len(pwd) < MIN_PASSWORD_LEN:
        return f"密码至少 {MIN_PASSWORD_LEN} 位"
    return None


def set_initial_password(password: str) -> Optional[str]:
    """设置初始口令（首次启动时）。原子写入并设权限 0o600；成功返回 None。

    该函数用于系统首次启动时设置管理员密码。执行流程：
    1. 校验密码复杂度（调用 _validate_password）
    2. 生成 32 字节随机盐值
    3. 使用 PBKDF2-HMAC-SHA256 对密码进行哈希
    4. 将盐值和哈希值以 base64 编码后写入文件（格式：salt_b64:hash_b64）
    5. 使用临时文件 + 原子替换保证写入完整性
    6. 设置文件权限为 0o600（仅所有者可读写）
    7. 重新加载凭据到内存

    Args:
        password: 要设置的明文密码。

    Returns:
        Optional[str]: 成功返回 None，失败返回错误描述字符串。
    """
    err = _validate_password(password)
    if err:
        return err

    data_dir = _get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    cred_path = _get_credential_path()

    # 生成 32 字节随机盐值，用于 PBKDF2 哈希
    salt = secrets.token_bytes(32)
    # 使用 PBKDF2-HMAC-SHA256 对密码进行哈希，增加暴力破解难度
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    # 将盐值和哈希值转为 base64 字符串，便于存储和传输
    salt_b64 = base64.standard_b64encode(salt).decode("ascii")
    hash_b64 = base64.standard_b64encode(derived).decode("ascii")
    content = f"{salt_b64}:{hash_b64}"

    try:
        # 临时文件 + rename 保证写入原子性，避免半写导致凭据损坏
        tmp_path = cred_path.with_suffix(".tmp")
        tmp_path.write_text(content)
        tmp_path.chmod(0o600)
        tmp_path.replace(cred_path)
        _load_credential_from_file()
        return None
    except OSError as e:
        logger.error("Failed to write credential file: %s", e)
        return "密码保存失败"


def verify_password(password: str) -> bool:
    """用存储凭据校验口令；鉴权未启用时直接放行。

    该函数是登录校验的主要入口：
    - 如果鉴权未启用（is_auth_enabled() 返回 False），直接返回 True，允许访问
    - 如果鉴权已启用，调用 verify_stored_password() 与存储的哈希进行比对

    Args:
        password: 用户提交的明文密码。

    Returns:
        bool: 校验通过返回 True，不通过返回 False。
    """
    if not is_auth_enabled():
        return True
    return verify_stored_password(password)


def change_password(current: str, new: str) -> Optional[str]:
    """修改口令。先校验旧口令，再写入新哈希；成功返回 None。

    该函数用于 Web 界面的"修改密码"功能。执行流程：
    1. 检查鉴权是否启用
    2. 检查是否已设置密码
    3. 校验当前密码是否正确（防止未授权修改）
    4. 校验新密码复杂度
    5. 生成新盐值，对新密码进行 PBKDF2 哈希
    6. 原子写入新凭据文件
    7. 重新加载凭据到内存，使修改立即生效

    Args:
        current: 当前明文密码（用于身份验证）。
        new: 新明文密码。

    Returns:
        Optional[str]: 成功返回 None，失败返回错误描述字符串。
    """
    if not is_auth_enabled():
        return "认证功能未启用"
    if not is_password_set():
        return "尚未设置密码"

    if not current or not current.strip():
        return "请输入当前密码"
    if not _verify_password_hash(current, _password_hash_salt, _password_hash_stored):
        return "当前密码错误"

    err = _validate_password(new)
    if err:
        return err

    cred_path = _get_credential_path()
    # 生成新的随机盐值，确保即使新密码与旧密码相同，存储的哈希也不同
    salt = secrets.token_bytes(32)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        new.encode("utf-8"),
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    salt_b64 = base64.standard_b64encode(salt).decode("ascii")
    hash_b64 = base64.standard_b64encode(derived).decode("ascii")
    content = f"{salt_b64}:{hash_b64}"

    try:
        tmp_path = cred_path.with_suffix(".tmp")
        tmp_path.write_text(content)
        tmp_path.chmod(0o600)
        tmp_path.replace(cred_path)
        # 重新载入内存，使后续 verify_password 立刻生效
        _load_credential_from_file()
        return None
    except OSError as e:
        logger.error("Failed to write credential file: %s", e)
        return "密码保存失败"


def create_session() -> str:
    """创建带 HMAC 签名的会话载荷，格式：``nonce.ts.signature``。

    会话 Cookie 的格式为 "nonce.timestamp.signature"，其中：
    - nonce: 32 字节的 URL-safe base64 随机字符串，用于防止重放攻击
    - timestamp: 会话创建时的 Unix 时间戳（秒）
    - signature: 对 "nonce.timestamp" 进行 HMAC-SHA256 签名的十六进制字符串

    该格式无需服务器端存储会话状态，实现了无状态的会话管理。

    Returns:
        str: 签名后的会话字符串，鉴权未启用时返回空字符串。
    """
    secret = _get_session_secret()
    if not secret:
        return ""
    # 生成随机 nonce，防止攻击者预测或重放会话令牌
    nonce = secrets.token_urlsafe(32)
    ts = str(int(time.time()))
    payload = f"{nonce}.{ts}"
    # 使用会话密钥对 payload 进行 HMAC-SHA256 签名
    sig = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session(value: str) -> bool:
    """校验会话 Cookie 的签名与有效期。

    校验流程：
    1. 检查会话密钥和 Cookie 值是否为空
    2. 按 "." 分割字符串，验证是否为 3 部分（nonce.ts.sig）
    3. 使用会话密钥重新计算 HMAC 签名，与 Cookie 中的签名进行常量时间比较
    4. 解析时间戳，检查是否超出最大有效时长（默认 24 小时）
    5. 所有校验通过返回 True，任一失败返回 False

    Args:
        value: 客户端提交的会话 Cookie 值。

    Returns:
        bool: 会话有效返回 True，无效（签名错误、过期、格式错误）返回 False。
    """
    secret = _get_session_secret()
    if not secret or not value:
        return False
    parts = value.split(".")
    if len(parts) != 3:
        return False
    nonce, ts_str, sig = parts[0], parts[1], parts[2]
    payload = f"{nonce}.{ts_str}"
    # 重新计算期望的 HMAC 签名
    expected = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    # 常量时间比较，防止时序攻击
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        ts = int(ts_str)
    except ValueError:
        return False
    # 从环境变量读取会话最大有效时长，若配置错误或不存在则使用默认值
    try:
        max_age_hours = int(os.getenv("ADMIN_SESSION_MAX_AGE_HOURS", str(SESSION_MAX_AGE_HOURS_DEFAULT)))
    except ValueError:
        max_age_hours = SESSION_MAX_AGE_HOURS_DEFAULT
    # 检查会话是否过期
    if time.time() - ts > max_age_hours * 3600:
        return False
    return True


def get_client_ip(request) -> str:
    """获取客户端 IP，遵循 ``TRUST_X_FORWARDED_FOR`` 配置。

    当仅配置单一受信反向代理时，代理会把真实客户端 IP 追加到 ``X-Forwarded-For`` 的最右端。
    这里取 ``[-1]`` 而非 ``[0]``，避免攻击者伪造最左值来轮换限流桶，从而绕过暴力破解保护。

    获取逻辑：
    1. 如果 TRUST_X_FORWARDED_FOR 环境变量为 "true"，从 X-Forwarded-For 头获取 IP
    2. 取 X-Forwarded-For 列表的最后一个 IP（由最近的受信代理附加）
    3. 如果不使用 X-Forwarded-For 或请求头不存在，从 request.client 获取直接连接 IP
    4. 如果无法获取任何 IP，返回默认回退地址 "127.0.0.1"

    Args:
        request: HTTP 请求对象，需包含 headers 和 client 属性。

    Returns:
        str: 客户端 IP 地址字符串。
    """
    if os.getenv("TRUST_X_FORWARDED_FOR", "false").lower() == "true":
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # 取最右端 IP（由最近的受信代理附加），防止伪造
            return forwarded.split(",")[-1].strip()
    if request.client:
        return request.client.host or "127.0.0.1"
    return "127.0.0.1"


def check_rate_limit(ip: str) -> bool:
    """检查当前 IP 是否仍在限流阈值内；超出则返回 False 表示被限流。

    该函数实现基于滑动窗口的登录失败限流：
    1. 首先清理已超出滑动窗口的过期条目，防止字典无限增长
    2. 检查该 IP 在滑动窗口内的失败次数是否超过阈值
    3. 如果超过 RATE_LIMIT_MAX_FAILURES（默认 5 次），返回 False 表示被限流

    Args:
        ip: 客户端 IP 地址字符串。

    Returns:
        bool: 未被限流返回 True，已被限流返回 False。
    """
    lock = _get_lock()
    now = time.time()
    with lock:
        # 清理已超出滑动窗口的过期条目，避免字典无限增长
        expired_keys = [k for k, (_, ts) in _rate_limit.items() if now - ts > RATE_LIMIT_WINDOW_SEC]
        for k in expired_keys:
            del _rate_limit[k]
        if ip in _rate_limit:
            count, first_ts = _rate_limit[ip]
            if count >= RATE_LIMIT_MAX_FAILURES:
                return False
        return True


def record_login_failure(ip: str) -> None:
    """记录一次登录失败，用于后续限流判定。

    该函数在登录校验失败时调用，更新该 IP 的失败计数：
    1. 如果该 IP 首次失败，记录 (1, 当前时间)
    2. 如果该 IP 已有失败记录且在滑动窗口内，失败次数加 1
    3. 如果该 IP 的失败记录已超出滑动窗口，重置计数为 1，更新时间戳

    Args:
        ip: 客户端 IP 地址字符串。
    """
    lock = _get_lock()
    now = time.time()
    with lock:
        if ip in _rate_limit:
            count, first_ts = _rate_limit[ip]
            # 超出窗口则重置起始时间戳，重新计数
            if now - first_ts > RATE_LIMIT_WINDOW_SEC:
                _rate_limit[ip] = (1, now)
            else:
                _rate_limit[ip] = (count + 1, first_ts)
        else:
            _rate_limit[ip] = (1, now)


def clear_rate_limit(ip: str) -> None:
    """登录成功后清除该 IP 的限流计数。

    当用户成功登录后，应调用此函数清除该 IP 的失败记录，
    避免合法用户在成功登录后仍被之前的失败记录影响。

    Args:
        ip: 客户端 IP 地址字符串。
    """
    lock = _get_lock()
    with lock:
        _rate_limit.pop(ip, None)


def overwrite_password(new_password: str) -> Optional[str]:
    """不校验旧口令直接覆盖存储口令；仅供 CLI 重置入口使用。

    该函数与 set_initial_password 类似，但不进行旧密码校验，
    用于管理员通过 CLI 强制重置密码的场景。

    执行流程：
    1. 检查鉴权是否启用
    2. 校验新密码复杂度
    3. 生成新盐值，对新密码进行 PBKDF2 哈希
    4. 原子写入凭据文件
    5. 重新加载凭据到内存

    Args:
        new_password: 新的明文密码。

    Returns:
        Optional[str]: 成功返回 None，失败返回错误描述字符串。
    """
    if not is_auth_enabled():
        return "认证功能未启用"
    err = _validate_password(new_password)
    if err:
        return err

    data_dir = _get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    cred_path = _get_credential_path()

    salt = secrets.token_bytes(32)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        new_password.encode("utf-8"),
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    salt_b64 = base64.standard_b64encode(salt).decode("ascii")
    hash_b64 = base64.standard_b64encode(derived).decode("ascii")
    content = f"{salt_b64}:{hash_b64}"

    try:
        tmp_path = cred_path.with_suffix(".tmp")
        tmp_path.write_text(content)
        tmp_path.chmod(0o600)
        tmp_path.replace(cred_path)
        _load_credential_from_file()
        return None
    except OSError as e:
        logger.error("Failed to write credential file: %s", e)
        return "密码保存失败"


def reset_password_cli() -> int:
    """交互式命令行重置口令，返回进程退出码。

    该函数提供命令行交互界面，供管理员重置密码。执行流程：
    1. 确保环境变量已加载
    2. 检查鉴权是否启用，未启用则报错退出
    3. 提示用户输入新密码（不回显，保护密码安全）
    4. 校验密码复杂度
    5. 提示用户再次输入确认密码
    6. 两次输入不一致则报错退出
    7. 调用 overwrite_password 写入新密码
    8. 输出成功或失败信息

    Returns:
        int: 成功返回 0，失败返回 1。
    """
    _ensure_env_loaded()
    if not _is_auth_enabled_from_env():
        print("Error: Auth is not enabled. Set ADMIN_AUTH_ENABLED=true in .env", file=sys.stderr)
        return 1

    print("Enter new admin password (will not echo):", end=" ")
    pwd = getpass.getpass("")
    err = _validate_password(pwd)
    if err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    print("Confirm new password:", end=" ")
    pwd2 = getpass.getpass("")
    if pwd != pwd2:
        print("Error: Passwords do not match", file=sys.stderr)
        return 1

    err = overwrite_password(pwd)
    if err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    print("Password has been reset successfully.")
    return 0


def _main() -> int:
    """命令行入口：仅支持 ``reset_password`` 子命令。

    该函数作为模块的命令行入口点，通过 sys.argv 解析子命令。
    目前仅支持 "reset_password" 子命令，用于管理员在终端重置密码。

    用法：python -m src.auth reset_password

    Returns:
        int: 命令执行结果，成功返回 0，失败或参数错误返回 1。
    """
    if len(sys.argv) > 1 and sys.argv[1] == "reset_password":
        return reset_password_cli()
    print("Usage: python -m src.auth reset_password", file=sys.stderr)
    return 1


if __name__ == "__main__":
    # 当模块作为脚本直接运行时，调用 _main() 处理命令行参数
    sys.exit(_main())
