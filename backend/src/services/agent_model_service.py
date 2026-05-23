# -*- coding: utf-8 -*-
"""Helpers for exposing configured Agent model deployments."""

from __future__ import annotations

from typing import Any, Dict, List

from src.config import get_effective_agent_models_to_try, get_effective_agent_primary_model


def _get_models_source(config) -> str:
    source = getattr(config, "llm_models_source", "")
    if source in {"litellm_config", "llm_channels"}:
        return source
    return "direct"


def _get_model_provider(model_name: str) -> str:
    if not model_name:
        return "unknown"
    if "/" in model_name:
        return model_name.split("/", 1)[0]
    return "openai"


def _build_deployments(config) -> List[Dict[str, Any]]:
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
    """Return configured Agent model deployments without exposing secrets."""
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
