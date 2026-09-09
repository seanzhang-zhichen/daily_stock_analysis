# -*- coding: utf-8 -*-
"""LiteLLM 生成参数兼容性辅助工具。

负责处理不同模型/供应商对生成参数（尤其是 temperature）的差异约束，
并提供参数修复（param recovery）的学习与缓存能力。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple


# Kimi K2.6 is consumed through Moonshot's OpenAI-compatible API in this
# repository. Official references:
# - https://platform.kimi.ai/docs/guide/kimi-k2-6-quickstart
# - https://platform.moonshot.ai/docs/guide/compatibility#parameters-differences-in-request-body
# - https://huggingface.co/moonshotai/Kimi-K2.6
# - https://docs.litellm.ai/docs/providers/openai_compatible
_FIXED_TEMPERATURE_LITELLM_MODELS: Dict[str, Dict[str, float]] = {
    "kimi-k2.6": {
        "thinking": 1.0,
        "non_thinking": 0.6,
    },
}


@dataclass(frozen=True)
class TemperatureDirective:
    """单次 LiteLLM 模型调用的请求级 temperature 策略。"""

    temperature: Optional[float] = None
    omit_temperature: bool = False
    reason: str = ""


@dataclass(frozen=True)
class GenerationParamRecovery:
    """针对 LiteLLM 模型调用学习到的请求参数修复。"""

    omit_params: Tuple[str, ...] = ()
    set_params: Mapping[str, Any] = field(default_factory=dict)
    reason: str = ""


_GENERATION_PARAM_RECOVERY_CACHE: Dict[str, GenerationParamRecovery] = {}

_LITELLM_ENDPOINT_PARAM_KEYS = (
    "api_base",
    "base_url",
    "api_version",
    "api_type",
    "azure_endpoint",
    "azure_deployment",
    "deployment_id",
    "custom_llm_provider",
    "organization",
    "region_name",
    "aws_region_name",
    "vertex_project",
    "vertex_location",
    "extra_headers",
    "headers",
    "default_headers",
)
_LITELLM_ROUTING_PARAM_KEYS = ("model", *_LITELLM_ENDPOINT_PARAM_KEYS)
_SECRET_CACHE_FIELD_NAMES = {
    "api_key",
    "authorization",
    "proxy_authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api-key",
    "openai-api-key",
}


def _resolve_litellm_model_list_entry(
    model: str,
    model_list: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """返回与配置别名匹配的 Router model_list 条目。"""
    entries = _resolve_litellm_model_list_entries(model, model_list)
    return entries[0] if entries else None


def _resolve_litellm_model_list_entries(
    model: str,
    model_list: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """返回与配置别名匹配的 Router model_list 条目列表。"""
    normalized_model = (model or "").strip()
    if not normalized_model or not model_list:
        return []

    entries: List[Dict[str, Any]] = []
    for entry in model_list:
        model_name = str(entry.get("model_name") or "").strip()
        if not model_name:
            params = entry.get("litellm_params", {}) or {}
            model_name = str(params.get("model") or "").strip()
        if model_name == normalized_model:
            entries.append(entry)
    return entries


def resolve_litellm_wire_model(
    model: str,
    model_list: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """把路由别名解析为其底层 LiteLLM 线上模型名。"""
    normalized_model = (model or "").strip()
    if not normalized_model or not model_list:
        return normalized_model

    model_entry = _resolve_litellm_model_list_entry(normalized_model, model_list)
    if not model_entry:
        return normalized_model

    params = model_entry.get("litellm_params", {}) or {}
    wire_model = str(params.get("model") or "").strip()
    if wire_model:
        return wire_model
    return normalized_model


def _extract_thinking_config(payload: Optional[Dict[str, Any]]) -> Any:
    """从 LiteLLM 风格的请求参数中提取思考模式（thinking）开关。"""
    if not isinstance(payload, dict):
        return None
    extra_body = payload.get("extra_body")
    if isinstance(extra_body, dict) and "thinking" in extra_body:
        return extra_body.get("thinking")
    if "thinking" in payload:
        return payload.get("thinking")
    return None


def _parse_thinking_enabled(value: Any) -> Optional[bool]:
    """把思考模式配置解析为 True/False/未知。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"enabled", "enable", "true", "1", "on", "thinking"}:
            return True
        if normalized in {"disabled", "disable", "false", "0", "off", "none", "non-thinking", "non_thinking"}:
            return False
        return None
    if isinstance(value, dict):
        if "enabled" in value:
            return _parse_thinking_enabled(value.get("enabled"))
        if "type" in value:
            return _parse_thinking_enabled(value.get("type"))
    return None


def resolve_litellm_thinking_enabled(
    model: str,
    model_list: Optional[List[Dict[str, Any]]] = None,
    request_overrides: Optional[Dict[str, Any]] = None,
) -> Optional[bool]:
    """解析发出的 LiteLLM 请求是否显式启用了思考模式。"""
    thinking_config = None
    model_entry = _resolve_litellm_model_list_entry(model, model_list)
    if model_entry:
        thinking_config = _extract_thinking_config(model_entry)
        entry_params = model_entry.get("litellm_params", {}) or {}
        entry_thinking_config = _extract_thinking_config(entry_params)
        if entry_thinking_config is not None:
            thinking_config = entry_thinking_config

    override_thinking_config = _extract_thinking_config(request_overrides)
    if override_thinking_config is not None:
        thinking_config = override_thinking_config
    return _parse_thinking_enabled(thinking_config)


def _model_parts(model: str) -> List[str]:
    """把供应商/模型别名拆分为小写的族标识 token。"""
    return [part for part in re.split(r"[/:\s]+", (model or "").lower()) if part]


def _matches_model_family(model: str, family: str) -> bool:
    """返回某个模型 token 是否等于或以给定族名为前缀。"""
    return any(part == family or part.startswith(f"{family}-") for part in _model_parts(model))


def _should_omit_litellm_temperature(model: str) -> bool:
    """返回某模型族是否应依赖供应商默认的 temperature。"""
    return any(
        part.startswith(("gpt-5", "gpt5"))
        or part in {"o1", "o3", "o4"}
        or part.startswith(("o1-", "o3-", "o4-"))
        for part in _model_parts(model)
    )


def get_fixed_litellm_temperature(
    model: str,
    model_list: Optional[List[Dict[str, Any]]] = None,
    request_overrides: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    """为已知的严格模型返回供应商强制要求的 temperature。"""
    normalized_model = resolve_litellm_wire_model(model, model_list).lower()
    if not normalized_model:
        return None
    thinking_enabled = resolve_litellm_thinking_enabled(
        model,
        model_list=model_list,
        request_overrides=request_overrides,
    )
    for model_name, temperatures in _FIXED_TEMPERATURE_LITELLM_MODELS.items():
        if _matches_model_family(normalized_model, model_name):
            if thinking_enabled is False and temperatures.get("non_thinking") is not None:
                return temperatures["non_thinking"]
            if temperatures.get("thinking") is not None:
                return temperatures["thinking"]
            if temperatures.get("non_thinking") is not None:
                return temperatures["non_thinking"]
    return None


def resolve_litellm_temperature_directive(
    model: str,
    *,
    model_list: Optional[List[Dict[str, Any]]] = None,
    request_overrides: Optional[Dict[str, Any]] = None,
) -> TemperatureDirective:
    """解析某 LiteLLM 模型的请求级 temperature 指令。"""
    fixed_temperature = get_fixed_litellm_temperature(
        model,
        model_list=model_list,
        request_overrides=request_overrides,
    )
    if fixed_temperature is not None:
        return TemperatureDirective(
            temperature=fixed_temperature,
            reason="fixed_model_temperature",
        )

    wire_model = resolve_litellm_wire_model(model, model_list)
    if _should_omit_litellm_temperature(wire_model):
        return TemperatureDirective(
            omit_temperature=True,
            reason="provider_default_temperature",
        )
    return TemperatureDirective()


def normalize_litellm_temperature(
    model: str,
    temperature: Optional[float],
    *,
    default: float = 0.7,
    model_list: Optional[List[Dict[str, Any]]] = None,
    request_overrides: Optional[Dict[str, Any]] = None,
) -> float:
    """为需要的调用方返回旧式的 float 型 temperature 归一化。"""
    fixed_temperature = get_fixed_litellm_temperature(
        model,
        model_list=model_list,
        request_overrides=request_overrides,
    )
    if fixed_temperature is not None:
        return fixed_temperature
    if temperature is None:
        return default
    return float(temperature)


def _redact_recovery_cache_value(param_name: str, value: Any) -> Any:
    """脱敏敏感字段，同时保留非敏感字段的稳定缓存指纹。"""
    if param_name.strip().lower() in _SECRET_CACHE_FIELD_NAMES:
        return "<set>" if value else "<empty>"
    if isinstance(value, Mapping):
        return {
            str(key): _redact_recovery_cache_value(str(key), nested_value)
            for key, nested_value in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_redact_recovery_cache_value(param_name, item) for item in value]
    return value


def _stable_recovery_cache_json(value: Mapping[str, Any]) -> str:
    """确定性地序列化路由参数，作为参数修复缓存的键。"""
    redacted = {
        key: _redact_recovery_cache_value(key, val)
        for key, val in sorted(value.items())
    }
    return json.dumps(redacted, sort_keys=True, separators=(",", ":"), default=str)


def _filter_litellm_routing_params(params: Mapping[str, Any]) -> Dict[str, Any]:
    """只保留与参数修复作用域相关的 LiteLLM 路由与端点参数。"""
    return {
        key: params[key]
        for key in _LITELLM_ROUTING_PARAM_KEYS
        if key in params and params[key] not in (None, "")
    }


def _request_endpoint_cache_scope(request_overrides: Optional[Dict[str, Any]]) -> Optional[str]:
    """从请求级路由覆盖构建端点缓存作用域。"""
    if not isinstance(request_overrides, Mapping):
        return None
    routing_params = _filter_litellm_routing_params(request_overrides)
    if not any(key in routing_params for key in _LITELLM_ENDPOINT_PARAM_KEYS):
        return None
    return _stable_recovery_cache_json(routing_params)


def _model_list_endpoint_cache_scope(
    model: str,
    model_list: Optional[List[Dict[str, Any]]],
) -> Optional[str]:
    """从匹配的 LiteLLM 路由条目构建端点缓存作用域。"""
    entries = _resolve_litellm_model_list_entries(model, model_list)
    if not entries:
        return "default"

    fingerprints = set()
    for entry in entries:
        params = entry.get("litellm_params", {}) or {}
        if not isinstance(params, Mapping):
            params = {}
        routing_params = _filter_litellm_routing_params(params)
        if not routing_params:
            routing_params = {"model": str(entry.get("model_name") or model).strip()}
        fingerprints.add(_stable_recovery_cache_json(routing_params))

    if len(fingerprints) != 1:
        return None
    return next(iter(fingerprints))


def _recovery_cache_key(
    model: str,
    *,
    model_list: Optional[List[Dict[str, Any]]] = None,
    request_overrides: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """为学习到的生成参数修复构造缓存键。"""
    wire_model = resolve_litellm_wire_model(model, model_list).strip().lower()
    thinking_enabled = resolve_litellm_thinking_enabled(
        model,
        model_list=model_list,
        request_overrides=request_overrides,
    )
    endpoint_scope = _request_endpoint_cache_scope(request_overrides)
    if endpoint_scope is None:
        endpoint_scope = _model_list_endpoint_cache_scope(model, model_list)
    if endpoint_scope is None:
        return None
    return (
        f"{wire_model or (model or '').strip().lower()}"
        f"|thinking={thinking_enabled}"
        f"|endpoint={endpoint_scope}"
    )


def apply_litellm_param_recovery(
    call_kwargs: Dict[str, Any],
    recovery: GenerationParamRecovery,
) -> Dict[str, Any]:
    """返回应用了学习到的参数修复后的 kwargs。"""
    updated = dict(call_kwargs)
    for param in recovery.omit_params:
        updated.pop(param, None)
    for param, value in recovery.set_params.items():
        updated[param] = value
    return updated


def get_cached_litellm_generation_param_recovery(
    model: str,
    *,
    model_list: Optional[List[Dict[str, Any]]] = None,
    request_overrides: Optional[Dict[str, Any]] = None,
) -> Optional[GenerationParamRecovery]:
    """返回为这种模型调用形态学习到的进程内参数修复。"""
    key = _recovery_cache_key(
        model,
        model_list=model_list,
        request_overrides=request_overrides,
    )
    if key is None:
        return None
    return _GENERATION_PARAM_RECOVERY_CACHE.get(key)


def remember_litellm_generation_param_recovery(
    model: str,
    recovery: GenerationParamRecovery,
    *,
    model_list: Optional[List[Dict[str, Any]]] = None,
    request_overrides: Optional[Dict[str, Any]] = None,
) -> None:
    """记住一次成功的参数修复，供本进程后续请求复用。"""
    key = _recovery_cache_key(
        model,
        model_list=model_list,
        request_overrides=request_overrides,
    )
    if key is None:
        return
    _GENERATION_PARAM_RECOVERY_CACHE[key] = recovery


def clear_litellm_generation_param_recovery_cache() -> None:
    """清空进程内学习到的参数修复（主要供测试使用）。"""
    _GENERATION_PARAM_RECOVERY_CACHE.clear()


def apply_litellm_generation_params(
    call_kwargs: Dict[str, Any],
    model: str,
    temperature: Optional[float],
    *,
    default_temperature: float = 0.7,
    model_list: Optional[List[Dict[str, Any]]] = None,
    request_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """返回应用了与模型兼容的生成参数后的 kwargs。"""
    updated = dict(call_kwargs)
    effective_overrides = request_overrides if request_overrides is not None else updated
    directive = resolve_litellm_temperature_directive(
        model,
        model_list=model_list,
        request_overrides=effective_overrides,
    )
    if directive.omit_temperature:
        updated.pop("temperature", None)
    elif directive.temperature is not None:
        updated["temperature"] = directive.temperature
    else:
        updated["temperature"] = default_temperature if temperature is None else float(temperature)
    cached_recovery = get_cached_litellm_generation_param_recovery(
        model,
        model_list=model_list,
        request_overrides=updated,
    )
    if cached_recovery:
        updated = apply_litellm_param_recovery(updated, cached_recovery)
    return updated
