"""生成后端标识符与安全的配置解析。

集中维护 `litellm` / `codex_cli` / `opencode_cli` 三类后端的标识常量与白名单，
并提供从配置对象（dict / dataclass / pydantic 均可）安全读取主备后端 ID 的辅助函数，
供上层工厂与注册中心复用。

主要导出：
- `LITELLM_BACKEND_ID` / `CODEX_CLI_BACKEND_ID` / `OPENCODE_CLI_BACKEND_ID`：后端标识常量。
- `SUPPORTED_GENERATION_BACKENDS`：支持的后端 id 冻结集合（白名单）。
- `resolve_generation_backend_id` / `resolve_generation_fallback_backend_id`：从配置解析主/备后端。
"""
from typing import Any, Mapping, Optional
from src.llm.generation_backend import GenerationError, GenerationErrorCode
LITELLM_BACKEND_ID = "litellm"
CODEX_CLI_BACKEND_ID = "codex_cli"
OPENCODE_CLI_BACKEND_ID = "opencode_cli"
SUPPORTED_GENERATION_BACKENDS = frozenset({LITELLM_BACKEND_ID, CODEX_CLI_BACKEND_ID, OPENCODE_CLI_BACKEND_ID})

def _value(config: Any, name: str, default: Any = None):
    """从 config 中以统一方式取字段，兼容 Mapping 与普通对象。"""
    if isinstance(config, Mapping): return config.get(name, default)
    return getattr(config, name, default)

def resolve_generation_backend_id(config: Any) -> str:
    """解析并校验主生成后端 id。

    Args:
        config: 配置对象（Mapping 或普通对象均可）。

    Returns:
        小写并去空白后的后端 id。

    Raises:
        GenerationError: 当解析出的后端 id 不在白名单内时抛出。
    """
    raw_value = _value(config, "generation_backend", LITELLM_BACKEND_ID)
    # Partial config objects and mocks from existing callers may not declare
    # this optional field. Treat non-string placeholders as the legacy default.
    value = raw_value.strip().lower() if isinstance(raw_value, str) else LITELLM_BACKEND_ID
    if value not in SUPPORTED_GENERATION_BACKENDS:
        raise GenerationError(GenerationErrorCode.BACKEND_NOT_CONFIGURED, "configuration", False, False, value,
                              details={"supported_backends": sorted(SUPPORTED_GENERATION_BACKENDS)})
    return value

def resolve_generation_fallback_backend_id(config: Any) -> Optional[str]:
    """解析降级用后端 id，仅允许是 litellm 且不能与主后端相同。

    Args:
        config: 配置对象。

    Returns:
        合法的降级后端 id；若未配置、与主后端相同或非法，则返回 None。

    Raises:
        GenerationError: 当配置的降级后端是已知的非 litellm 后端时抛出（防止误配置）。
    """
    value = _value(config, "generation_fallback_backend", LITELLM_BACKEND_ID)
    if not isinstance(value, str):
        return None
    if not value.strip(): return None
    value = value.strip().lower()
    # 与主后端相同时不视作降级，直接返回 None
    if value == resolve_generation_backend_id(config): return None
    # 非 litellm 的后端不允许担任 fallback，避免把 litellm 专用的 fallback 路径指向别的后端
    if value != LITELLM_BACKEND_ID:
        raise GenerationError(GenerationErrorCode.BACKEND_NOT_CONFIGURED, "configuration", False, False, value)
    return value
