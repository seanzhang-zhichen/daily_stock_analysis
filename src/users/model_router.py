# -*- coding: utf-8 -*-
"""用户级 LLM 模型路由 (Phase 2)。

统一封装 C 端用户的模型选择约束:
- 未登录 / 未开启用户体系时使用平台默认模型。
- 登录用户按套餐 ``allowed_models`` 限制可用模型。
- ``can_byok`` 为真时优先使用用户 BYOK 凭据。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from src.config import get_effective_agent_models_to_try, get_effective_agent_primary_model
from src.storage import AppUser, AppUserByokCredential
from src.users.byok import decrypt_secret
from src.users.config import load_user_mode_settings
from src.users.plans import ResolvedPlan, resolve_user_plan


@dataclass(frozen=True)
class ModelRoute:
    """一次用户模型路由决策结果。"""

    source: str
    primary_model: str
    models_to_try: List[str]
    provider: Optional[str] = None
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    byok_credential_id: Optional[int] = None
    plan: Optional[ResolvedPlan] = None

    @property
    def uses_byok(self) -> bool:
        return self.source == "byok"


def _provider_from_model(model: str) -> str:
    if "/" in model:
        return model.split("/", 1)[0].strip().lower()
    return (model or "openai").strip().lower()


def _filter_allowed_models(models: List[str], allowed_models: List[str]) -> List[str]:
    allowed = {item.strip() for item in allowed_models if item.strip()}
    if not allowed:
        return models
    return [model for model in models if model in allowed]


def _active_byok_credentials(db: Session, user_id: int) -> List[AppUserByokCredential]:
    return (
        db.query(AppUserByokCredential)
        .filter(
            AppUserByokCredential.user_id == user_id,
            AppUserByokCredential.status == "active",
        )
        .order_by(AppUserByokCredential.updated_at.desc())
        .all()
    )


def _select_byok_credential(
    db: Session,
    *,
    user_id: int,
    allowed_models: List[str],
) -> Optional[ModelRoute]:
    allowed = {item.strip() for item in allowed_models if item.strip()}
    for row in _active_byok_credentials(db, user_id):
        model = (row.model or "").strip()
        if allowed and model and model not in allowed:
            continue
        if allowed and not model:
            continue
        try:
            api_key = decrypt_secret(row.encrypted_key)
        except Exception:
            row.status = "invalid"
            db.add(row)
            db.flush()
            continue
        if not api_key:
            continue
        selected_model = model or row.provider
        return ModelRoute(
            source="byok",
            primary_model=selected_model,
            models_to_try=[selected_model],
            provider=row.provider,
            api_key=api_key,
            api_base=row.base_url,
            byok_credential_id=int(row.id),
        )
    return None


def resolve_model_route(
    db: Session,
    *,
    user: Optional[AppUser],
    config,
    prefer_byok: bool = True,
    platform_primary_model: Optional[str] = None,
    platform_models: Optional[List[str]] = None,
) -> ModelRoute:
    """解析当前请求应使用的平台模型或用户 BYOK 模型。"""

    resolved_platform_models = list(platform_models) if platform_models is not None else get_effective_agent_models_to_try(config)
    platform_primary = (
        (platform_primary_model or "").strip()
        or get_effective_agent_primary_model(config)
    )

    if user is None:
        return ModelRoute(
            source="platform",
            primary_model=platform_primary,
            models_to_try=resolved_platform_models,
            provider=_provider_from_model(platform_primary),
        )

    plan = resolve_user_plan(db, user, settings=load_user_mode_settings())
    if prefer_byok and plan.can_byok:
        byok_route = _select_byok_credential(
            db,
            user_id=user.id,
            allowed_models=plan.allowed_models,
        )
        if byok_route is not None:
            return ModelRoute(**{**byok_route.__dict__, "plan": plan})

    filtered_models = _filter_allowed_models(resolved_platform_models, plan.allowed_models)
    primary = filtered_models[0] if filtered_models else platform_primary
    return ModelRoute(
        source="platform",
        primary_model=primary,
        models_to_try=filtered_models,
        provider=_provider_from_model(primary),
        plan=plan,
    )


def as_litellm_kwargs(route: ModelRoute) -> Dict[str, str]:
    """把路由结果转成可合并进 ``litellm.completion`` 的参数。"""

    kwargs: Dict[str, str] = {"model": route.primary_model}
    if route.api_key:
        kwargs["api_key"] = route.api_key
    if route.api_base:
        kwargs["api_base"] = route.api_base
    return kwargs
