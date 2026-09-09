# -*- coding: utf-8 -*-
"""LiteLLM 错误分类与一次性参数修复。

把供应商返回的明确参数错误归类，并转换为安全的一次性参数修复动作
（省略或重设特定参数）。
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from src.llm.generation_params import (
    GenerationParamRecovery,
    apply_litellm_param_recovery,
    remember_litellm_generation_param_recovery,
)

_UNSUPPORTED_PARAM_MARKERS = (
    "unsupported",
    "not supported",
    "unrecognized",
    "unknown parameter",
    "not allowed",
    "invalid parameter",
    "does not support",
)

_TEMPERATURE_VALUE_PATTERN = r"-?\d+(?:\.\d+)?"
_ALLOWED_TEMPERATURE_PATTERNS = (
    re.compile(
        rf"\bonly\s+(?:the\s+)?(?:default\s+)?(?:temperature\s+)?(?:value\s+)?[\(`'\"]*(?P<value>{_TEMPERATURE_VALUE_PATTERN})(?!\w)"
    ),
    re.compile(
        rf"\bdefault(?:\s+temperature)?(?:\s+value)?\s*(?:is|=|:)\s*[\(`'\"]*(?P<value>{_TEMPERATURE_VALUE_PATTERN})(?!\w)"
    ),
)


def _collect_error_text(value: Any, seen: Optional[set] = None) -> List[str]:
    """把嵌套的异常载荷展平为文本片段，供分类使用。"""
    if seen is None:
        seen = set()
    if value is None:
        return []
    value_id = id(value)
    if value_id in seen:
        return []
    seen.add(value_id)

    chunks = [str(value)]
    if isinstance(value, BaseException):
        chunks.extend(_collect_error_text(getattr(value, "args", None), seen))
    if isinstance(value, dict):
        for item in value.values():
            chunks.extend(_collect_error_text(item, seen))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            chunks.extend(_collect_error_text(item, seen))
    else:
        for attr in ("message", "body", "response", "llm_provider", "param"):
            if hasattr(value, attr):
                chunks.extend(_collect_error_text(getattr(value, attr), seen))
    return chunks


def _normalized_error_text(error: BaseException) -> str:
    """返回从异常对象收集到的小写可搜索文本。"""
    return " ".join(chunk for chunk in _collect_error_text(error) if chunk).lower()


def _parse_allowed_temperature(text: str) -> Optional[float]:
    """从错误信息中提取供应商强制要求的 temperature 值。"""
    for segment in re.split(r"(?<!\d)\.(?!\d)|[!?;\n]+", text):
        if "only" not in segment:
            continue
        for pattern in _ALLOWED_TEMPERATURE_PATTERNS:
            match = pattern.search(segment[segment.find("only") :])
            if match is None:
                continue
            value = float(match.group("value"))
            if 0 <= value <= 2:
                return value
    return None


def classify_litellm_generation_param_error(
    error: BaseException,
) -> Optional[GenerationParamRecovery]:
    """把供应商明确的参数错误归类为安全的一次性修复动作。"""
    text = _normalized_error_text(error)
    if not text:
        return None

    if "temperature" in text:
        allowed_temperature = _parse_allowed_temperature(text)
        if allowed_temperature is not None:
            return GenerationParamRecovery(
                set_params={"temperature": allowed_temperature},
                reason="temperature_default_only",
            )
        if "only" in text and "default" in text:
            return GenerationParamRecovery(
                omit_params=("temperature",),
                reason="temperature_default_only",
            )
        if any(marker in text for marker in _UNSUPPORTED_PARAM_MARKERS):
            return GenerationParamRecovery(
                omit_params=("temperature",),
                reason="temperature_unsupported",
            )

    for param in ("top_p", "presence_penalty", "frequency_penalty", "seed"):
        if param in text and any(marker in text for marker in _UNSUPPORTED_PARAM_MARKERS):
            return GenerationParamRecovery(
                omit_params=(param,),
                reason=f"{param}_unsupported",
            )
    return None


def call_litellm_with_param_recovery(
    call: Callable[[Dict[str, Any]], Any],
    *,
    model: str,
    call_kwargs: Dict[str, Any],
    model_list: Optional[List[Dict[str, Any]]] = None,
    cache_recovery: bool = True,
    logger: Optional[Any] = None,
    log_label: str = "[LiteLLM]",
) -> Any:
    """调用 LiteLLM 一次，遇到明确的生成参数错误时重试一次。"""
    effective_kwargs = dict(call_kwargs)
    try:
        return call(effective_kwargs)
    except Exception as exc:
        recovery = classify_litellm_generation_param_error(exc)
        if recovery is None:
            raise
        retry_kwargs = apply_litellm_param_recovery(effective_kwargs, recovery)
        # 修复动作没有带来任何变化时，直接抛出原始异常
        if retry_kwargs == effective_kwargs:
            raise
        if logger is not None:
            logger.warning(
                "%s %s generation parameter rejected (%s), retrying once with request-scoped recovery",
                log_label,
                model,
                recovery.reason,
            )
        response = call(retry_kwargs)
        if cache_recovery:
            remember_litellm_generation_param_recovery(
                model,
                recovery,
                model_list=model_list,
                request_overrides=retry_kwargs,
            )
        return response
