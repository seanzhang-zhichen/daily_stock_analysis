# -*- coding: utf-8 -*-
"""用户级 LLM 模型路由 (Phase 2)。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy.orm import Session

from src.config import (
    get_configured_llm_models,
    get_effective_agent_models_to_try,
    get_effective_agent_primary_model,
)
from src.storage import AppUser
from src.users.plans import ResolvedPlan, resolve_user_plan


@dataclass(frozen=True)
class ModelRoute:
    """一次用户模型路由决策结果。"""

    source: str
    primary_model: str
    models_to_try: List[str]
    provider: Optional[str] = None
    plan: Optional[ResolvedPlan] = None


def _provider_from_model(model: str) -> str:
    """从 provider/model 格式中提取 provider，缺省按 openai 处理。"""
    if "/" in model:
        return model.split("/", 1)[0].strip().lower()
    return (model or "openai").strip().lower()


def _filter_allowed_models(models: List[str], allowed_models: List[str]) -> List[str]:
    """按套餐允许列表过滤模型；空列表表示不限制。"""
    allowed = {item.strip() for item in allowed_models if item.strip()}
    if not allowed:
        return models
    return [model for model in models if model in allowed]


def _ordered_unique(models: List[str]) -> List[str]:
    """按原顺序去重模型候选，过滤空字符串。"""
    seen = set()
    ordered: List[str] = []
    for model in models:
        normalized = (model or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _select_preferred_model(user: AppUser, models: List[str]) -> Optional[str]:
    """选择用户偏好模型；偏好不在可用列表中时忽略。"""
    preferred = (getattr(user, "preferred_model", None) or "").strip()
    if preferred and preferred in models:
        return preferred
    return None


def _platform_model_candidates(
    config,
    *,
    platform_primary_model: Optional[str] = None,
    platform_models: Optional[List[str]] = None,
) -> tuple[List[str], str, List[str]]:
    """合并平台主模型、尝试列表和全局模型列表。"""
    resolved_platform_models = (
        list(platform_models)
        if platform_models is not None
        else get_effective_agent_models_to_try(config)
    )
    platform_primary = (
        (platform_primary_model or "").strip()
        or get_effective_agent_primary_model(config)
    )
    if not resolved_platform_models and platform_primary:
        resolved_platform_models = [platform_primary]
    candidate_platform_models = _ordered_unique(
        resolved_platform_models
        + get_configured_llm_models(getattr(config, "llm_model_list", []) or [])
    )
    return resolved_platform_models, platform_primary, candidate_platform_models


def get_available_models_for_user(
    db: Session,
    *,
    user: AppUser,
    config,
    platform_primary_model: Optional[str] = None,
    platform_models: Optional[List[str]] = None,
) -> List[str]:
    """返回当前用户套餐允许选择的平台配置模型列表。"""

    _, _, candidate_platform_models = _platform_model_candidates(
        config,
        platform_primary_model=platform_primary_model,
        platform_models=platform_models,
    )
    plan = resolve_user_plan(db, user)
    return _filter_allowed_models(candidate_platform_models, plan.allowed_models)


def resolve_model_route(
    db: Session,
    *,
    user: Optional[AppUser],
    config,
    platform_primary_model: Optional[str] = None,
    platform_models: Optional[List[str]] = None,
) -> ModelRoute:
    """解析当前请求应使用的平台模型。"""

    resolved_platform_models, platform_primary, candidate_platform_models = _platform_model_candidates(
        config,
        platform_primary_model=platform_primary_model,
        platform_models=platform_models,
    )

    if user is None:
        models_to_try = candidate_platform_models or resolved_platform_models
        primary = platform_primary or (models_to_try[0] if models_to_try else "")
        return ModelRoute(
            source="platform",
            primary_model=primary,
            models_to_try=models_to_try,
            provider=_provider_from_model(primary),
        )

    plan = resolve_user_plan(db, user)
    filtered_models = _filter_allowed_models(candidate_platform_models, plan.allowed_models)
    preferred = _select_preferred_model(user, filtered_models)
    primary = preferred or (filtered_models[0] if filtered_models else platform_primary)
    models_to_try = [primary] + [model for model in filtered_models if model != primary] if primary else []
    if plan.allowed_models and not filtered_models:
        models_to_try = []
    return ModelRoute(
        source="platform",
        primary_model=primary,
        models_to_try=models_to_try,
        provider=_provider_from_model(primary),
        plan=plan,
    )
