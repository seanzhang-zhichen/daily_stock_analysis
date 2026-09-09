"""生成后端的工厂函数。

将后端标识解析与具体实现解耦，根据配置返回对应的 `GenerationBackend` 实例；
litellm 后端由上层 litellm 直接管理，本工厂仅在本地 CLI 后端场景下返回实例，
其余场景返回 None 以便上层按需加载默认 litellm 适配器。
"""
from src.llm.backend_registry import resolve_generation_backend_id, LITELLM_BACKEND_ID
from src.llm.local_cli_backend import LocalCliGenerationBackend

def build_generation_backend(config):
    """根据配置构造生成后端实例。

    Args:
        config: 配置对象（Mapping 或普通对象均可），用于解析后端 id。

    Returns:
        `LocalCliGenerationBackend` 实例；当前解析为 litellm 时返回 None，
        由调用方继续走默认 litellm 路径。
    """
    backend_id = resolve_generation_backend_id(config)
    if backend_id == LITELLM_BACKEND_ID:
        return None
    return LocalCliGenerationBackend(backend_id, config)
