# -*- coding: utf-8 -*-
"""Phase 4 BYOK (Bring Your Own Key) 服务。

把用户自带的 API Key 加密落库, 业务侧只能拿到解密后的明文使用; 持久化数据
始终是密文, 日志中不会暴露明文。

加密策略:

- 优先用 ``cryptography.fernet`` (若已安装) 加密, 密钥来自环境变量
  ``DATA_ENCRYPTION_KEY`` (32 字节 base64)。
- 若 ``cryptography`` 不可用或环境变量未配置, 退回到带 HMAC 的对称 XOR
  (基于 ``USER_BYOK_FALLBACK_KEY`` / 默认 admin secret), 仍然防止明文落库,
  但加密强度弱。生产环境**强烈建议**安装 ``cryptography`` 并设置
  ``DATA_ENCRYPTION_KEY``。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from src.storage import AppUser, AppUserByokCredential
from src.users.errors import UserError, UserErrorCode


logger = logging.getLogger(__name__)


SUPPORTED_PROVIDERS = frozenset(
    {"openai", "anspire", "aihubmix", "gemini", "anthropic", "deepseek", "custom"}
)


# ---------------------------------------------------------------------------
# 加密原语
# ---------------------------------------------------------------------------


def _load_data_encryption_key() -> Optional[bytes]:
    raw = (os.getenv("DATA_ENCRYPTION_KEY") or "").strip()
    if not raw:
        return None
    try:
        decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
    except (ValueError, TypeError):
        return None
    if len(decoded) != 32:
        return None
    return decoded


def _try_fernet() -> Optional[object]:
    """Lazy import so the dependency is optional."""
    try:
        from cryptography.fernet import Fernet  # type: ignore
    except Exception:
        return None
    key = _load_data_encryption_key()
    if key is None:
        return None
    return Fernet(base64.urlsafe_b64encode(key))


def _fallback_key() -> bytes:
    """退回方案: 用 ``USER_BYOK_FALLBACK_KEY`` 或 admin secret 派生密钥。

    仅作为最后兜底, 防止明文写入数据库; 不替代真正的加密保障。
    """
    candidate = (os.getenv("USER_BYOK_FALLBACK_KEY") or "").strip()
    if not candidate:
        # 使用项目已有的 admin secret (若配置), 否则使用进程级随机 (无法跨进程解密)
        candidate = (os.getenv("ADMIN_AUTH_SECRET") or "").strip()
    if not candidate:
        candidate = "dsa_local_dev_byok"  # 永远不应在生产命中
        logger.warning(
            "BYOK fallback key 未配置 (USER_BYOK_FALLBACK_KEY / ADMIN_AUTH_SECRET 均为空), "
            "使用本地默认值; 生产环境务必显式配置。"
        )
    return hashlib.sha256(candidate.encode("utf-8")).digest()


def encrypt_secret(plaintext: str) -> str:
    """返回可落库的字符串密文。"""
    if not plaintext:
        return ""
    fernet = _try_fernet()
    if fernet is not None:
        token = fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
        return f"v1:fernet:{token}"

    key = _fallback_key()
    nonce = secrets.token_bytes(16)
    data = plaintext.encode("utf-8")
    stream = hashlib.shake_128(key + nonce).digest(len(data))
    cipher = bytes(b ^ s for b, s in zip(data, stream))
    mac = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    blob = base64.urlsafe_b64encode(nonce + cipher + mac).decode("ascii")
    return f"v1:xorhmac:{blob}"


def decrypt_secret(stored: str) -> str:
    """与 :func:`encrypt_secret` 对称。失败时抛出 :class:`UserError`。"""
    if not stored:
        return ""
    try:
        version, scheme, payload = stored.split(":", 2)
    except ValueError as exc:
        raise UserError(UserErrorCode.INVALID_TOKEN, "BYOK 密钥格式错误") from exc
    if version != "v1":
        raise UserError(UserErrorCode.INVALID_TOKEN, "BYOK 密钥版本不支持")

    if scheme == "fernet":
        fernet = _try_fernet()
        if fernet is None:
            raise UserError(UserErrorCode.INVALID_TOKEN, "BYOK 密钥需要 fernet 解密, 但当前未配置 DATA_ENCRYPTION_KEY")
        try:
            return fernet.decrypt(payload.encode("ascii")).decode("utf-8")
        except Exception as exc:
            raise UserError(UserErrorCode.INVALID_TOKEN, "BYOK 密钥解密失败") from exc

    if scheme == "xorhmac":
        try:
            blob = base64.urlsafe_b64decode(payload.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise UserError(UserErrorCode.INVALID_TOKEN, "BYOK 密钥解码失败") from exc
        if len(blob) < 16 + 32:
            raise UserError(UserErrorCode.INVALID_TOKEN, "BYOK 密钥长度异常")
        nonce, rest = blob[:16], blob[16:]
        cipher, mac = rest[:-32], rest[-32:]
        key = _fallback_key()
        expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, mac):
            raise UserError(UserErrorCode.INVALID_TOKEN, "BYOK 密钥校验失败")
        stream = hashlib.shake_128(key + nonce).digest(len(cipher))
        return bytes(b ^ s for b, s in zip(cipher, stream)).decode("utf-8")

    raise UserError(UserErrorCode.INVALID_TOKEN, f"BYOK 密钥方案不支持: {scheme}")


# ---------------------------------------------------------------------------
# DTO + Repository helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ByokCredentialView:
    """暴露给前端的脱敏视图, 始终不返回明文 key。"""

    id: int
    provider: str
    base_url: Optional[str]
    model: Optional[str]
    status: str
    key_preview: str  # eg. "sk-***abcd"
    updated_at: Optional[datetime]


def _preview_key(plaintext: str) -> str:
    if not plaintext:
        return ""
    tail = plaintext[-4:] if len(plaintext) >= 4 else plaintext
    return f"***{tail}"


def _view(row: AppUserByokCredential, plaintext: str) -> ByokCredentialView:
    return ByokCredentialView(
        id=int(row.id),
        provider=row.provider,
        base_url=row.base_url,
        model=row.model,
        status=row.status,
        key_preview=_preview_key(plaintext),
        updated_at=row.updated_at,
    )


def _normalize_provider(provider: str) -> str:
    p = (provider or "").strip().lower()
    if p not in SUPPORTED_PROVIDERS:
        raise UserError(UserErrorCode.INVALID_TOKEN, f"不支持的 provider: {provider}")
    return p


# ---------------------------------------------------------------------------
# 业务用例
# ---------------------------------------------------------------------------


def upsert_credential(
    db: Session,
    *,
    user: AppUser,
    provider: str,
    api_key: str,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> ByokCredentialView:
    """覆盖式写入一个 provider 的 BYOK key。"""
    provider_norm = _normalize_provider(provider)
    key_norm = (api_key or "").strip()
    if not key_norm:
        raise UserError(UserErrorCode.INVALID_TOKEN, "API Key 不能为空")

    row = (
        db.query(AppUserByokCredential)
        .filter(
            AppUserByokCredential.user_id == user.id,
            AppUserByokCredential.provider == provider_norm,
        )
        .first()
    )
    encrypted = encrypt_secret(key_norm)

    if row is None:
        row = AppUserByokCredential(
            user_id=user.id,
            provider=provider_norm,
            encrypted_key=encrypted,
            base_url=(base_url or "").strip() or None,
            model=(model or "").strip() or None,
            status="active",
        )
        db.add(row)
    else:
        row.encrypted_key = encrypted
        row.base_url = (base_url or "").strip() or None
        row.model = (model or "").strip() or None
        row.status = "active"
        db.add(row)
    db.flush()
    return _view(row, key_norm)


def get_decrypted_key(
    db: Session,
    *,
    user_id: int,
    provider: str,
) -> Optional[str]:
    """业务侧调用 LLM 前用本函数拿明文 key。找不到或被禁用返回 None。"""
    provider_norm = _normalize_provider(provider)
    row = (
        db.query(AppUserByokCredential)
        .filter(
            AppUserByokCredential.user_id == user_id,
            AppUserByokCredential.provider == provider_norm,
            AppUserByokCredential.status == "active",
        )
        .first()
    )
    if row is None:
        return None
    try:
        return decrypt_secret(row.encrypted_key)
    except UserError:
        # 解密失败说明 key 已不可用 (可能 DATA_ENCRYPTION_KEY 轮换), 标记为 invalid
        row.status = "invalid"
        db.add(row)
        db.flush()
        return None


def list_credentials(db: Session, *, user_id: int) -> List[ByokCredentialView]:
    rows = (
        db.query(AppUserByokCredential)
        .filter(AppUserByokCredential.user_id == user_id)
        .order_by(AppUserByokCredential.provider.asc())
        .all()
    )
    out: List[ByokCredentialView] = []
    for row in rows:
        try:
            plaintext = decrypt_secret(row.encrypted_key)
        except UserError:
            plaintext = ""
            row.status = "invalid"
            db.add(row)
        out.append(_view(row, plaintext))
    db.flush()
    return out


def delete_credential(db: Session, *, user_id: int, provider: str) -> bool:
    provider_norm = _normalize_provider(provider)
    row = (
        db.query(AppUserByokCredential)
        .filter(
            AppUserByokCredential.user_id == user_id,
            AppUserByokCredential.provider == provider_norm,
        )
        .first()
    )
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True
