# -*- coding: utf-8 -*-
"""用于暴露已配置的 Agent 模型部署信息的辅助函数。

settings/API 层借助本模块展示支持 Agent 的 LiteLLM 部署，同时避免泄露凭据。
这里只报告 provider/source 元数据，密钥仍保留在 ``Config`` 中。
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.config import get_effective_agent_models_to_try, get_effective_agent_primary_model


def _get_models_source(config) -> str:
    """返回模型列表元数据所使用的有效来源标签。"""
    source = getattr(config, "llm_models_source", "")
    if source in {"litellm_config", "llm_channels"}:
        return source
    return "direct"


def _get_model_provider(model_name: str) -> str:
    """从 LiteLLM 风格的 ``provider/model`` 名称推断 provider。"""
    if not model_name:
        return "unknown"
    if "/" in model_name:
        return model_name.split("/", 1)[0]
    return "openai"


def _build_deployments(config) -> List[Dict[str, Any]]:
    """根据已配置的 LiteLLM 模型条目构建原始部署记录。"""
    source = _get_models_source(config)
    primary_model = get_effective_agent_primary_model(config)
    fallback_models = set(get_effective_agent_models_to_try(config)[1:])
    deployments: List[Dict[str, Any]] = []

    for index, entry in enumerate(getattr(config, "llm_model_list", []) or []):
        params = entry.get("litellm_params", {}) or {}
        model_name = str(params.get("model") or "").strip()
        if not model_name:
            continue

        api_base = params.get("api_base")
        deployment_name = entry.get("model_name")
        deployments.append(
            {
                "deployment_id": f"{source}:{index}",
                "model": model_name,
                "provider": _get_model_provider(model_name),
                "source": source,
                "api_base": str(api_base).strip() if api_base else None,
                "deployment_name": str(deployment_name).strip() if deployment_name else None,
                "is_primary": model_name == primary_model,
                "is_fallback": model_name in fallback_models,
            }
        )

    return deployments


def list_agent_model_deployments(config) -> List[Dict[str, Any]]:
    """返回已配置的 Agent 模型部署信息，但不暴露任何密钥。

    主模型与回退模型排在前面，这样 UI 可以优先高亮 Agent 真正会尝试的模型，
    再展示次要的部署元数据。
    """
    deployments = _build_deployments(config)
    return sorted(
        deployments,
        key=lambda item: (
            not item["is_primary"],
            not item["is_fallback"],
            item["source"],
            item["model"],
            item["api_base"] or "",
            item["deployment_name"] or "",
            item["deployment_id"],
        ),
    )
