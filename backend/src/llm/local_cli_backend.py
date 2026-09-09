"""受限的、非 Shell 形式的本地 CLI 生成后端。

通过 `subprocess.run` 直接 exec 可执行文件（不走 shell），用白名单环境变量避免密钥泄漏，
支撑 codex / opencode 两类本地 CLI 作为生成后端的场景，供 `backend_registry` / `backend_factory` 调用。

主要能力：
- `LocalCliGenerationBackend`：以子进程方式调用本地 CLI 并把 stdout 作为生成结果
- 严格的 model 名字符校验（禁止空白与 shell 元字符）
- 进程级安全子集环境变量传递（safe_names 白名单）
- 超时、输出体积上限、非零退出码、空输出等失败统一翻译为 `GenerationError`
"""
from __future__ import annotations
import os, shutil, subprocess
from typing import Any, Dict, Optional
from src.llm.backend_registry import CODEX_CLI_BACKEND_ID, OPENCODE_CLI_BACKEND_ID
from src.llm.generation_backend import GenerationBackend, GenerationError, GenerationErrorCode, GenerationResult

DEFAULT_LOCAL_CLI_TIMEOUT_SECONDS = 300
DEFAULT_LOCAL_CLI_MAX_OUTPUT_BYTES = 1024 * 1024

class LocalCliGenerationBackend:
    """本地 CLI 生成后端的实现。

    Attributes:
        backend_id: 后端标识，决定具体调用哪个 CLI（codex / opencode）。
        config: 配置对象，提供模型名、超时、输出上限等运行时参数。
    """

    def __init__(self, backend_id: str, config: Any):
        """初始化后端实例。

        Args:
            backend_id: 后端标识。
            config: 业务侧传入的配置对象（一般为 pydantic / dataclass 配置实例）。
        """
        self.backend_id = backend_id
        self.config = config

    def _command(self):
        """根据 backend_id 构造要执行的命令行参数列表。

        codex 与 opencode 走不同参数；opencode 还允许从 config 中读取 model 名。
        对 opencode 的 model 名做 shell 元字符白名单校验，防止命令注入。

        Returns:
            完整的命令行参数列表（不含 shell）。

        Raises:
            GenerationError: 当 opencode 的 model 名包含不安全字符时。
        """
        if self.backend_id == CODEX_CLI_BACKEND_ID:
            return ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never", "--ephemeral", "-"]
        command = ["opencode", "run", "--format", "json", "-"]
        model = str(getattr(self.config, "opencode_cli_model", "") or "").strip()
        if model:
            # 拒绝包含空白字符或 shell 元字符的模型名，避免被拼接到命令行时被解释
            if any(char.isspace() for char in model) or any(char in model for char in "|<>;`$"):
                raise GenerationError(GenerationErrorCode.UNSAFE_CONFIG, "configuration", False, False, self.backend_id,
                                      details={"field": "OPENCODE_CLI_MODEL"})
            command.extend(["--model", model])
        return command

    def generate(self, prompt: str, generation_config: Dict[str, Any], *, system_prompt: Optional[str] = None,
                 stream: bool = False, stream_progress_callback=None, response_validator=None, audit_context=None) -> GenerationResult:
        """调用本地 CLI 子进程完成一次生成。

        执行流程：构造命令 → 校验可执行文件存在 → 拼装 prompt 与 system_prompt →
        以白名单环境变量启动子进程 → 校验返回码与输出体积 → 返回 `GenerationResult`。

        Args:
            prompt: 用户提示词主体。
            generation_config: 调用方传入的生成参数（当前后端未直接使用，保留以满足接口）。
            system_prompt: 可选系统提示，拼在 prompt 之前。
            stream: 流式开关；本地 CLI 一次性返回，仅在 progress 回调中上报总长度。
            stream_progress_callback: 流式进度回调，仅在有输出时被调用一次。
            response_validator: 可选响应校验器，失败时由调用方决定如何处理。
            audit_context: 可选审计上下文，保留供上层日志使用。

        Returns:
            成功生成的 `GenerationResult`。

        Raises:
            GenerationError: 子命令不存在 / 超时 / 输出超限 / 非零退出 / 空输出等失败场景。
        """
        command = self._command()
        if shutil.which(command[0]) is None:
            raise GenerationError(GenerationErrorCode.COMMAND_NOT_FOUND, "execution", False, True, self.backend_id,
                                  details={"executable": command[0]})
        payload = ((system_prompt or "") + "\n\n" + prompt).strip()
        # 超时与输出上限均允许 config 覆盖，缺省值在模块顶部常量中定义
        timeout = float(getattr(self.config, "generation_backend_timeout_seconds", DEFAULT_LOCAL_CLI_TIMEOUT_SECONDS) or DEFAULT_LOCAL_CLI_TIMEOUT_SECONDS)
        max_bytes = int(getattr(self.config, "generation_backend_max_output_bytes", DEFAULT_LOCAL_CLI_MAX_OUTPUT_BYTES) or DEFAULT_LOCAL_CLI_MAX_OUTPUT_BYTES)
        try:
            # 仅保留白名单内的环境变量，避免把含密钥的 PATH 扩展或代理信息泄露给子进程
            safe_names = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "HOME", "USERPROFILE", "TEMP", "TMP", "LANG", "NO_COLOR"}
            child_env = {k: v for k, v in os.environ.items() if k.upper() in safe_names}
            completed = subprocess.run(command, input=payload, text=True, capture_output=True, timeout=timeout,
                                       env=child_env,
                                       check=False)
        except subprocess.TimeoutExpired as exc:
            # 超时视作可重试也允许降级到下一个后端
            raise GenerationError(GenerationErrorCode.TIMEOUT, "execution", True, True, self.backend_id,
                                  details={"timeout_seconds": timeout}) from exc
        output = (completed.stdout or "").strip()
        # 按字节数限制输出大小，防止恶意或异常子进程输出撑爆内存
        if len(output.encode("utf-8")) > max_bytes:
            raise GenerationError(GenerationErrorCode.OUTPUT_TOO_LARGE, "execution", False, True, self.backend_id)
        if completed.returncode != 0:
            # 非零退出码保留最近 1000 字节 stderr 便于排障，避免 details 过大
            raise GenerationError(GenerationErrorCode.NON_ZERO_EXIT, "execution", True, True, self.backend_id,
                                  details={"returncode": completed.returncode, "stderr": (completed.stderr or "")[-1000:]})
        if not output:
            raise GenerationError(GenerationErrorCode.EMPTY_OUTPUT, "execution", True, True, self.backend_id)
        if response_validator:
            response_validator(output)
        if stream_progress_callback:
            stream_progress_callback(len(output))
        return GenerationResult(output, getattr(self.config, "litellm_model", "") or self.backend_id,
                                self.backend_id, self.backend_id, diagnostics={"returncode": 0})

def resolve_local_cli_preset(backend_id: str) -> str:
    """校验 backend_id 是否为已支持的本地 CLI 后端。

    Args:
        backend_id: 待校验的后端标识。

    Returns:
        原样返回 backend_id。

    Raises:
        ValueError: backend_id 不在 codex / opencode 集合中时抛出。
    """
    if backend_id not in {CODEX_CLI_BACKEND_ID, OPENCODE_CLI_BACKEND_ID}:
        raise ValueError(f"unsupported local CLI backend: {backend_id}")
    return backend_id
