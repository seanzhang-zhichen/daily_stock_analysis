# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 配置管理模块
===================================

职责：
1. 使用单例模式管理全局配置
2. 从 .env 文件加载敏感配置
3. 提供类型安全的配置访问接口
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple
from urllib.parse import unquote, urlparse
from dotenv import load_dotenv, dotenv_values
from dataclasses import dataclass, field

from src.report_language import (
    is_supported_report_language_value,
    normalize_report_language,
)
from src.notification_routing import parse_notification_route_channels
from src.notification_noise import (
    NOTIFICATION_SEVERITIES,
    is_supported_notification_severity,
    parse_notification_quiet_hours,
    validate_notification_timezone,
)
from src.llm import generation_params as llm_generation_params

logger = logging.getLogger(__name__)


@dataclass
class ConfigIssue:
    """一条带严重级别的结构化配置校验问题。

    Attributes:
        severity: 问题级别，取值为 "error"、"warning" 或 "info"。
        message:  问题的可读描述文案。
        field:    与该问题最相关的环境变量 / 配置字段名（不适用时为空字符串）。
    """

    severity: Literal["error", "warning", "info"]
    message: str
    field: str = ""

    def __str__(self) -> str:  # noqa: D105
        """返回该问题的可读描述文案。"""
        return self.message


# API Key 由本模块显式管理的 provider 集合；其余 provider 交给 litellm 从环境变量直连
_MANAGED_LITELLM_KEY_PROVIDERS = {"gemini", "vertex_ai", "anthropic", "openai", "deepseek"}
# LLM_CHANNELS 中允许声明的协议（对应 LiteLLM 的 provider 标识）
SUPPORTED_LLM_CHANNEL_PROTOCOLS = ("openai", "anthropic", "gemini", "vertex_ai", "deepseek", "ollama")
# 环境变量中判定为“假”的取值（比较前统一小写化）
_FALSEY_ENV_VALUES = {"0", "false", "no", "off"}


def _has_ntfy_topic_endpoint(value: Optional[str]) -> bool:
    """判断 ntfy URL 是否指向了具体 topic endpoint（path 中存在非空片段）。"""
    raw_url = (value or "").strip()
    if not raw_url:
        return False
    parsed = urlparse(raw_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return False
    # ntfy 的 topic 直接编码在 URL path 中，缺少 topic 时推送无落点，必须判为非法
    return any(unquote(segment).strip() for segment in parsed.path.split("/") if segment)


def _has_gotify_base_url(value: Optional[str]) -> bool:
    """判断 Gotify URL 是否为 server base URL（发送端会自行拼接 /message）。"""
    raw_url = (value or "").strip().rstrip("/")
    if not raw_url:
        return False
    parsed = urlparse(raw_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return False
    if parsed.query or parsed.fragment:
        return False
    path_segments = [segment for segment in parsed.path.split("/") if segment]
    # 末尾已是 /message 说明用户填的是发送端点而不是 base URL，会导致路径重复拼接
    return not (path_segments and path_segments[-1].lower() == "message")


# Agent 单轮任务默认最大步数，防止工具调用陷入死循环
AGENT_MAX_STEPS_DEFAULT = 10
# 基本面聚合阶段的默认总预算（秒），超时即降级返回
FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT = 8.0
# 新闻策略档位对应的最大回溯窗口（天）
NEWS_STRATEGY_WINDOWS: Dict[str, int] = {
    "ultra_short": 1,
    "short": 3,
    "medium": 7,
    "long": 30,
}


@dataclass(frozen=True)
class AgentContextCompressionPreset:
    """Default values for a visible chat-history compression profile."""

    trigger_tokens: int
    protected_turns: int
    summary_tokens: int


AGENT_CONTEXT_COMPRESSION_DEFAULT_PROFILE = "balanced"
AGENT_CONTEXT_COMPRESSION_PROFILES: Dict[str, AgentContextCompressionPreset] = {
    "cost": AgentContextCompressionPreset(6000, 2, 900),
    "balanced": AgentContextCompressionPreset(12000, 4, 1500),
    "long_context_raw_first": AgentContextCompressionPreset(24000, 6, 2600),
}


def parse_env_bool(value: Optional[str], default: bool = False) -> bool:
    """把环境变量风格的字符串解析为布尔值。

    除 `0/false/no/off`（大小写不敏感）外均视为真；空值与 None 返回 default。

    Args:
        value: 原始环境变量值。
        default: 值为 None 或空字符串时的回退值。

    Returns:
        解析后的布尔值。
    """
    if value is None:
        return default
    normalized = value.strip().lower()
    if not normalized:
        return default
    return normalized not in _FALSEY_ENV_VALUES


def parse_env_int(
    value: Optional[str],
    default: int,
    *,
    field_name: str,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    """解析整型环境变量，非法值记警告并回退，越界则夹取到边界。

    Args:
        value: 原始环境变量值。
        default: 缺省或解析失败时使用的值。
        field_name: 环境变量名，仅用于日志定位。
        minimum: 下界，超出时夹取到下界。
        maximum: 上界，超出时夹取到上界。

    Returns:
        落在 [minimum, maximum] 区间内的整型配置值。
    """
    raw_value = value
    if raw_value is None or not str(raw_value).strip():
        parsed = int(default)
    else:
        try:
            parsed = int(str(raw_value).strip())
        except (TypeError, ValueError):
            logger.warning(
                "%s=%r is not a valid integer; falling back to %s",
                field_name,
                raw_value,
                default,
            )
            parsed = int(default)

    if minimum is not None and parsed < minimum:
        logger.warning(
            "%s=%r is below minimum %s; clamping to %s",
            field_name,
            parsed,
            minimum,
            minimum,
        )
        parsed = minimum
    if maximum is not None and parsed > maximum:
        logger.warning(
            "%s=%r is above maximum %s; clamping to %s",
            field_name,
            parsed,
            maximum,
            maximum,
        )
        parsed = maximum
    return parsed


def parse_env_float(
    value: Optional[str],
    default: float,
    *,
    field_name: str,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    """解析浮点型环境变量，非法值记警告并回退，越界则夹取到边界。

    Args:
        value: 原始环境变量值。
        default: 缺省或解析失败时使用的值。
        field_name: 环境变量名，仅用于日志定位。
        minimum: 下界，超出时夹取到下界。
        maximum: 上界，超出时夹取到上界。

    Returns:
        落在 [minimum, maximum] 区间内的浮点配置值。
    """
    raw_value = value
    if raw_value is None or not str(raw_value).strip():
        parsed = float(default)
    else:
        try:
            parsed = float(str(raw_value).strip())
        except (TypeError, ValueError):
            logger.warning(
                "%s=%r is not a valid number; falling back to %s",
                field_name,
                raw_value,
                default,
            )
            parsed = float(default)

    if minimum is not None and parsed < minimum:
        logger.warning(
            "%s=%r is below minimum %s; clamping to %s",
            field_name,
            parsed,
            minimum,
            minimum,
        )
        parsed = minimum
    if maximum is not None and parsed > maximum:
        logger.warning(
            "%s=%r is above maximum %s; clamping to %s",
            field_name,
            parsed,
            maximum,
            maximum,
        )
        parsed = maximum
    return parsed


def normalize_news_strategy_profile(value: Optional[str]) -> str:
    """把新闻策略档位归一化到已知取值，未知档位回退为 short。"""
    candidate = (value or "short").strip().lower()
    return candidate if candidate in NEWS_STRATEGY_WINDOWS else "short"


def resolve_news_window_days(news_max_age_days: int, news_strategy_profile: Optional[str]) -> int:
    """计算新闻实际回溯天数：全局时效与策略档位窗口取更严格的那个。

    Args:
        news_max_age_days: NEWS_MAX_AGE_DAYS 配置的全局最大时效（天）。
        news_strategy_profile: 新闻策略档位名。

    Returns:
        实际生效的新闻窗口天数，最小为 1。
    """
    profile = normalize_news_strategy_profile(news_strategy_profile)
    profile_days = NEWS_STRATEGY_WINDOWS.get(profile, NEWS_STRATEGY_WINDOWS["short"])
    return max(1, min(max(1, int(news_max_age_days)), profile_days))


def normalize_agent_context_compression_profile(value: Optional[str]) -> str:
    """Return a supported chat compression profile, defaulting to balanced."""
    candidate = (value or AGENT_CONTEXT_COMPRESSION_DEFAULT_PROFILE).strip().lower()
    if candidate in AGENT_CONTEXT_COMPRESSION_PROFILES:
        return candidate
    logger.warning(
        "Invalid AGENT_CONTEXT_COMPRESSION_PROFILE=%r; falling back to %s",
        value,
        AGENT_CONTEXT_COMPRESSION_DEFAULT_PROFILE,
    )
    return AGENT_CONTEXT_COMPRESSION_DEFAULT_PROFILE


def get_agent_context_compression_preset(value: Optional[str]) -> AgentContextCompressionPreset:
    return AGENT_CONTEXT_COMPRESSION_PROFILES[
        normalize_agent_context_compression_profile(value)
    ]


def canonicalize_llm_channel_protocol(value: Optional[str]) -> str:
    """把协议别名归一化成 LiteLLM 的 provider 标识（如 claude → anthropic）。"""
    candidate = (value or "").strip().lower().replace("-", "_")
    aliases = {
        "openai_compatible": "openai",
        "openai_compat": "openai",
        "claude": "anthropic",
        "google": "gemini",
        "vertex": "vertex_ai",
        "vertexai": "vertex_ai",
    }
    return aliases.get(candidate, candidate)


def resolve_llm_channel_protocol(
    protocol: Optional[str],
    *,
    base_url: Optional[str] = None,
    models: Optional[List[str]] = None,
    channel_name: Optional[str] = None,
) -> str:
    """推断渠道实际使用的协议（provider）。

    优先级：显式 protocol > 模型名前缀 > 渠道名 > base_url 推断；
    全部无法判定且无 base_url 时返回空字符串，由调用方按缺省处理。

    Args:
        protocol: 显式声明的协议。
        base_url: 渠道 base URL，用于本地服务的兜底推断。
        models: 渠道声明的模型列表，可携带 provider 前缀。
        channel_name: 渠道名，例如 "deepseek"。

    Returns:
        归一化后的协议名，无法判定时为空字符串。
    """
    explicit = canonicalize_llm_channel_protocol(protocol)
    if explicit in SUPPORTED_LLM_CHANNEL_PROTOCOLS:
        return explicit

    for model in models or []:
        if "/" not in model:
            continue
        prefix = canonicalize_llm_channel_protocol(model.split("/", 1)[0])
        if prefix in SUPPORTED_LLM_CHANNEL_PROTOCOLS:
            return prefix

    # 再按渠道名推断（例如 "deepseek" -> deepseek，"gemini" -> gemini）
    if channel_name:
        name_protocol = canonicalize_llm_channel_protocol(channel_name)
        if name_protocol in SUPPORTED_LLM_CHANNEL_PROTOCOLS:
            return name_protocol

    if base_url:
        parsed = urlparse(base_url)
        if parsed.hostname in {"127.0.0.1", "localhost", "0.0.0.0"}:
            # 本地服务（vLLM、LM Studio、LocalAI 等）默认按 openai 协议处理。
            # Ollama 用户需显式设置 PROTOCOL=ollama，或把渠道命名为 "ollama"。
            return "openai"
        return "openai"

    return ""


def channel_allows_empty_api_key(protocol: Optional[str], base_url: Optional[str]) -> bool:
    """判断渠道是否允许缺省 API Key（Ollama 与本地自建服务通常免鉴权）。"""
    resolved_protocol = resolve_llm_channel_protocol(protocol, base_url=base_url)
    if resolved_protocol == "ollama":
        return True
    parsed = urlparse(base_url or "")
    return parsed.hostname in {"127.0.0.1", "localhost", "0.0.0.0"}


def normalize_llm_channel_model(model: str, protocol: Optional[str], base_url: Optional[str] = None) -> str:
    """为缺少 provider 前缀的模型名补齐前缀，保证 LiteLLM 能正确路由。

    已带前缀且前缀是已知 provider（如 SiliconFlow 上的 HuggingFace 风格 ID）时保持原样，
    避免破坏用户显式声明的路由。
    """
    normalized_model = model.strip()
    if not normalized_model:
        return normalized_model

    resolved_protocol = resolve_llm_channel_protocol(protocol, base_url=base_url, models=[normalized_model])

    if "/" in normalized_model:
        # 模型名已带斜杠，例如 'deepseek-ai/DeepSeek-V3'。
        # 先判断前缀是否为已知 LiteLLM provider，是则原样保留；
        # 否则（如 SiliconFlow 上的 HuggingFace 风格 ID）补上推断出的协议，
        # 让 LiteLLM 走正确的 handler。
        raw_prefix, remainder = normalized_model.split("/", 1)
        prefix = raw_prefix.lower()
        canonical_prefix = canonicalize_llm_channel_protocol(prefix)
        known_providers = _MANAGED_LITELLM_KEY_PROVIDERS | set(SUPPORTED_LLM_CHANNEL_PROTOCOLS) | {
            "minimax",
            "cohere", "huggingface", "bedrock", "sagemaker", "azure",
            "replicate", "together_ai", "palm", "text-completion-openai",
            "command-r", "groq", "cerebras", "fireworks_ai", "friendliai",
        }
        if prefix in known_providers:
            return normalized_model
        if canonical_prefix in known_providers:
            return f"{canonical_prefix}/{remainder}"
        # 前缀不是真实 provider，补一个协议前缀以保证 LiteLLM 正确路由
        if resolved_protocol:
            return f"{resolved_protocol}/{normalized_model}"
        return normalized_model

    if not resolved_protocol:
        return normalized_model
    return f"{resolved_protocol}/{normalized_model}"


def get_configured_llm_models(model_list: List[Dict[str, Any]]) -> List[str]:
    """按 Router model_list 的声明顺序返回模型名，并保持去重。

    优先取顶层 ``model_name``（即用户在 LITELLM_MODEL 中填写的路由别名），
    而不是 ``litellm_params.model``（实际发给 provider 的模型标识）。
    由渠道构造的条目两者相同，但 YAML 配置可能定义与底层 provider/model 路径
    不同的友好别名。

    Args:
        model_list: LiteLLM Router 的 model_list。

    Returns:
        去重后的模型名列表，保持首次出现顺序。
    """
    models: List[str] = []
    seen: set = set()
    for entry in model_list or []:
        # 优先取顶层 model_name（Router 路由键）；缺失时退回 litellm_params.model
        name = str(entry.get("model_name") or "").strip()
        if not name:
            params = entry.get("litellm_params", {}) or {}
            name = str(params.get("model") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        models.append(name)
    return models


# 选股引擎与较新的 LLM 渠道消费方共用下面这些兼容性辅助函数。
# 保留在此处，便于旧部署加载选股引擎时无需再引入另一套配置实现。
def normalize_llm_channel_api_surface(value: Optional[str]) -> str:
    """把 API 形态别名归一化为 chat_completions / responses，缺省取 chat_completions。"""
    candidate = (value or "").strip().lower().replace("-", "_")
    aliases = {
        "chat": "chat_completions",
        "chat_completion": "chat_completions",
        "completions": "chat_completions",
        "response": "responses",
        "responses_api": "responses",
    }
    normalized = aliases.get(candidate, candidate)
    return normalized if normalized in {"chat_completions", "responses"} else "chat_completions"


def is_supported_llm_channel_api_surface_value(value: Optional[str]) -> bool:
    """判断 API 形态取值是否合法；空值表示未配置，同样视为合法。"""
    candidate = (value or "").strip().lower().replace("-", "_")
    return not candidate or candidate in {
        "chat", "chat_completion", "completions", "chat_completions",
        "response", "responses_api", "responses",
    }


def _screening_model_provider(model: str) -> str:
    """取模型名中的 provider 前缀，无前缀时返回空字符串。"""
    normalized = (model or "").strip()
    if "/" not in normalized:
        return ""
    return normalized.split("/", 1)[0].lower()


def find_incompatible_llm_channel_models(
    models: List[str],
    protocol: Optional[str],
    api_surface: Optional[str],
    base_url: Optional[str] = None,
) -> List[str]:
    """找出与 Responses API 形态不兼容的模型。

    仅当渠道声明为 responses 形态时才校验：非 openai 协议全部不兼容，
    openai 协议下前缀不是 openai 的模型同样无法走 Responses 路由。

    Args:
        models: 渠道声明的模型列表。
        protocol: 渠道协议。
        api_surface: 渠道声明的 API 形态。
        base_url: 渠道 base URL，用于协议兜底推断。

    Returns:
        不兼容的模型名列表；形态非 responses 时直接返回空列表。
    """
    if normalize_llm_channel_api_surface(api_surface) != "responses":
        return []
    resolved = resolve_llm_channel_protocol(protocol, base_url=base_url, models=models)
    if resolved != "openai":
        return [model for model in models if str(model or "").strip()]
    return [
        model for model in models
        if _screening_model_provider(normalize_llm_channel_model(model, resolved, base_url)) not in {"", "openai"}
    ]


def find_llm_channel_surface_conflicts(channels: List[Dict[str, Any]]) -> Dict[str, Tuple[str, ...]]:
    """检测同一模型是否被多个启用的渠道声明成了不同的 API 形态。

    Args:
        channels: 解析后的渠道字典列表。

    Returns:
        模型名 → 冲突形态元组的映射；无冲突时为空字典。
    """
    route_surfaces: Dict[str, set[str]] = {}
    for channel in channels:
        if not isinstance(channel, dict) or not channel.get("enabled", True):
            continue
        protocol = str(channel.get("protocol") or "")
        base_url = str(channel.get("base_url") or "")
        surface = normalize_llm_channel_api_surface(channel.get("api_surface"))
        for raw_model in channel.get("models") or []:
            model = normalize_llm_channel_model(str(raw_model), protocol, base_url)
            if model:
                route_surfaces.setdefault(model, set()).add(surface)
    return {
        model: tuple(sorted(surfaces))
        for model, surfaces in route_surfaces.items()
        if len(surfaces) > 1
    }


def apply_litellm_api_surface(model: str, api_surface: Optional[str]) -> str:
    """按声明的 API 形态改写模型路由；responses 形态需转成 openai/responses/<model>。

    Args:
        model: 原始模型路由（带 provider 前缀）。
        api_surface: 目标 API 形态。

    Returns:
        改写后的模型路由；非 responses 形态原样返回。

    Raises:
        ValueError: 声明 responses 形态但模型不是 openai/<model> 路由。
    """
    normalized_model = (model or "").strip()
    if not normalized_model or normalize_llm_channel_api_surface(api_surface) != "responses":
        return normalized_model
    provider = _screening_model_provider(normalized_model)
    if provider != "openai":
        raise ValueError("Responses API surface requires an openai/<model> route")
    remainder = normalized_model.split("/", 1)[1]
    return normalized_model if remainder.startswith("responses/") else f"openai/responses/{remainder}"


def resolve_litellm_wire_model(
    model: str,
    model_list: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """把 Router 别名解析为其底层实际请求的 LiteLLM wire model。"""
    return llm_generation_params.resolve_litellm_wire_model(model, model_list)


def resolve_litellm_thinking_enabled(
    model: str,
    model_list: Optional[List[Dict[str, Any]]] = None,
    request_overrides: Optional[Dict[str, Any]] = None,
) -> Optional[bool]:
    """解析本次 LiteLLM 请求是否显式开启思考（thinking）模式。"""
    return llm_generation_params.resolve_litellm_thinking_enabled(
        model,
        model_list=model_list,
        request_overrides=request_overrides,
    )


def get_fixed_litellm_temperature(
    model: str,
    model_list: Optional[List[Dict[str, Any]]] = None,
    request_overrides: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    """返回 provider 强制要求的固定温度值（针对已知的严格模型）。"""
    return llm_generation_params.get_fixed_litellm_temperature(
        model,
        model_list=model_list,
        request_overrides=request_overrides,
    )


def normalize_litellm_temperature(
    model: str,
    temperature: Optional[float],
    *,
    default: float = 0.7,
    model_list: Optional[List[Dict[str, Any]]] = None,
    request_overrides: Optional[Dict[str, Any]] = None,
) -> float:
    """发送 LiteLLM 请求前对温度参数做归一化（含 provider 强制值处理）。"""
    return llm_generation_params.normalize_litellm_temperature(
        model,
        temperature,
        default=default,
        model_list=model_list,
        request_overrides=request_overrides,
    )


def resolve_unified_llm_temperature(model: str) -> float:
    """解析统一温度（LLM_TEMPERATURE），失败时按 provider 专属变量逐级回退。

    回退顺序：LLM_TEMPERATURE → 当前模型 provider 对应的专属变量 →
    GEMINI/ANTHROPIC/OPENAI_TEMPERATURE 依次尝试 → 0.7。
    这样老版本只配 provider 专属变量的部署无需改动即可继续生效。
    """
    llm_temperature_raw = os.getenv("LLM_TEMPERATURE")
    if llm_temperature_raw and llm_temperature_raw.strip():
        try:
            return float(llm_temperature_raw)
        except (ValueError, TypeError):
            pass

    provider_temperature_env = {
        "gemini": "GEMINI_TEMPERATURE",
        "vertex_ai": "GEMINI_TEMPERATURE",
        "anthropic": "ANTHROPIC_TEMPERATURE",
        "openai": "OPENAI_TEMPERATURE",
        "deepseek": "OPENAI_TEMPERATURE",
    }
    preferred_env = provider_temperature_env.get(_get_litellm_provider(model))
    if preferred_env:
        preferred_value = os.getenv(preferred_env)
        if preferred_value and preferred_value.strip():
            try:
                return float(preferred_value)
            except (ValueError, TypeError):
                pass

    for env_name in ("GEMINI_TEMPERATURE", "ANTHROPIC_TEMPERATURE", "OPENAI_TEMPERATURE"):
        env_value = os.getenv(env_name)
        if env_value and env_value.strip():
            try:
                return float(env_value)
            except (ValueError, TypeError):
                continue

    return 0.7


def _get_litellm_provider(model: str) -> str:
    """从模型字符串中提取 LiteLLM provider 前缀；无前缀时按 openai 处理。"""
    if not model:
        return ""
    if "/" in model:
        return model.split("/", 1)[0]
    return "openai"


def _uses_direct_env_provider(model: str) -> bool:
    """判断运行时是否通过 litellm 的环境变量直连方式解析该模型。

    非本模块托管 Key 的 provider（如 cohere/*）不会进入 Router model_list，
    因此校验与调用都要走直连分支。
    """
    provider = _get_litellm_provider(model)
    return bool(provider) and provider not in _MANAGED_LITELLM_KEY_PROVIDERS


def normalize_agent_litellm_model(
    model: str,
    configured_models: Optional[set[str]] = None,
) -> str:
    """归一化 AGENT_LITELLM_MODEL，同时保留已配置的 Router 别名不被加前缀。"""
    normalized_model = (model or "").strip()
    if not normalized_model:
        return ""
    if "/" not in normalized_model:
        if configured_models and normalized_model in configured_models:
            return normalized_model
        return f"openai/{normalized_model}"
    return normalized_model


def get_effective_agent_primary_model(config: "Config") -> str:
    """返回 Agent 实际生效的主模型；未单独配置时继承全局 LITELLM_MODEL。"""
    configured_router_models = set(
        get_configured_llm_models(getattr(config, "llm_model_list", []) or [])
    )
    configured_agent_model = normalize_agent_litellm_model(
        getattr(config, "agent_litellm_model", ""),
        configured_models=configured_router_models,
    )
    if configured_agent_model:
        return configured_agent_model
    return (getattr(config, "litellm_model", "") or "").strip()


def get_effective_agent_models_to_try(config: "Config") -> List[str]:
    """返回 Agent 的模型尝试顺序：主模型 + 全局备选模型（已去重）。"""
    configured_router_models = set(
        get_configured_llm_models(getattr(config, "llm_model_list", []) or [])
    )
    raw_models = [get_effective_agent_primary_model(config)] + (
        getattr(config, "litellm_fallback_models", []) or []
    )
    seen = set()
    ordered_models: List[str] = []
    for model in raw_models:
        normalized_model = (model or "").strip()
        if not normalized_model:
            continue
        dedupe_key = normalize_agent_litellm_model(
            normalized_model,
            configured_models=configured_router_models,
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        ordered_models.append(normalized_model)
    return ordered_models


def setup_env(override: bool = False):
    """
    从 .env 文件初始化环境变量。

    Args:
        override: 为 True 时用 .env 中的值覆盖已存在的环境变量；
                  配置热更新后重新加载时应设为 True。默认 False，
                  保持首次加载时“系统环境变量优先”的历史行为。
    """
    Config._capture_bootstrap_runtime_env_overrides()
    # src/config.py -> src/ -> 项目根目录
    env_file = os.getenv("ENV_FILE")
    if env_file:
        env_path = Path(env_file)
    else:
        env_path = Path(__file__).resolve().parents[2] / '.env'
    load_dotenv(dotenv_path=env_path, override=override)


@dataclass
class Config:
    """
    系统配置类 - 单例模式
    
    设计说明：
    - 使用 dataclass 简化配置属性定义
    - 所有配置项从环境变量读取，支持默认值
    - 类方法 get_instance() 实现单例访问
    """
    
    # === 自选股配置 ===
    stock_list: List[str] = field(default_factory=list)

    # === 飞书云文档配置 ===
    feishu_app_id: Optional[str] = None
    feishu_app_secret: Optional[str] = None
    feishu_folder_token: Optional[str] = None  # 目标文件夹 Token
    feishu_chat_id: Optional[str] = None
    feishu_domain: str = "feishu"
    feishu_send_as_file: bool = False

    # === 数据源 API Token ===
    tushare_token: Optional[str] = None
    tickflow_api_key: Optional[str] = None
    finnhub_api_key: Optional[str] = None
    alphavantage_api_key: Optional[str] = None
    longbridge_app_key: Optional[str] = None
    longbridge_app_secret: Optional[str] = None
    longbridge_access_token: Optional[str] = None

    # === AI 分析配置 ===
    # LiteLLM 统一模型配置（provider/model 格式，例如 gemini/gemini-3.1-pro-preview）
    generation_backend: str = "litellm"
    generation_fallback_backend: str = "litellm"
    generation_backend_timeout_seconds: int = 300
    generation_backend_max_output_bytes: int = 1048576
    opencode_cli_model: str = ""
    litellm_model: str = ""  # Primary model; must include provider prefix when set explicitly
    litellm_fallback_models: List[str] = field(default_factory=list)  # Cross-model fallback list

    # 所有 LLM 调用的统一温度（LLM_TEMPERATURE）
    llm_temperature: float = 0.7

    # --- Multi-channel LLM config (new) ---
    # LITELLM_CONFIG：标准 litellm_config.yaml 文件路径（能力最强）
    litellm_config_path: Optional[str] = None
    # 内部元数据：记录 llm_model_list 实际由哪一层配置生成
    llm_models_source: str = ""
    # LLM_CHANNELS：渠道字典列表，每项含 name/base_url/api_keys/models
    llm_channels: List[Dict[str, Any]] = field(default_factory=list)
    # 预构建的 LiteLLM Router model_list（由渠道或 YAML 填充）
    llm_model_list: List[Dict[str, Any]] = field(default_factory=list)

    # 由 provider 专属环境变量解析出的 API Key 列表
    gemini_api_keys: List[str] = field(default_factory=list)
    anthropic_api_keys: List[str] = field(default_factory=list)
    openai_api_keys: List[str] = field(default_factory=list)
    deepseek_api_keys: List[str] = field(default_factory=list)

    # provider 专属字段，供 LiteLLM 直连调用与诊断使用
    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-3.1-pro-preview"  # 主模型
    gemini_model_fallback: str = "gemini-3-flash-preview"  # 备选模型
    gemini_temperature: float = 0.7  # 温度参数（0.0-2.0，控制输出随机性，默认0.7）

    # Gemini API 请求配置（防止 429 限流）
    gemini_request_delay: float = 2.0  # 请求间隔（秒）
    gemini_max_retries: int = 5  # 最大重试次数
    gemini_retry_delay: float = 5.0  # 重试基础延时（秒）

    # Anthropic Claude API（备选，当 Gemini 不可用时使用）
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-sonnet-4-6"  # Claude model name
    anthropic_temperature: float = 0.7  # Anthropic temperature (0.0-1.0, default 0.7)
    anthropic_max_tokens: int = 8192  # Max tokens for Anthropic responses

    # OpenAI 兼容 API（备选，当 Gemini/Anthropic 不可用时使用）
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None  # 如: https://api.openai.com/v1
    openai_model: str = "gpt-5.5"  # OpenAI 兼容模型名称
    openai_vision_model: Optional[str] = None  # Deprecated: use VISION_MODEL instead
    openai_temperature: float = 0.7  # OpenAI 温度参数（0.0-2.0，默认0.7）

    # === Vision 配置 ===
    # VISION_MODEL：用于图片理解调用的 litellm 模型字符串
    # 回退链：VISION_MODEL → OPENAI_VISION_MODEL → gemini/gemini-2.0-flash
    vision_model: str = ""
    # VISION_PROVIDER_PRIORITY：Vision 回退时的 provider 顺序（逗号分隔）
    vision_provider_priority: str = "gemini,anthropic,openai"

    # === 搜索引擎配置（支持多 Key 负载均衡）===
    anspire_api_keys: List[str] = field(default_factory=list)  # Anspire Search API Keys
    bocha_api_keys: List[str] = field(default_factory=list)  # Bocha API Keys
    minimax_api_keys: List[str] = field(default_factory=list)  # MiniMax API Keys
    tavily_api_keys: List[str] = field(default_factory=list)  # Tavily API Keys
    brave_api_keys: List[str] = field(default_factory=list)  # Brave Search API Keys
    serpapi_keys: List[str] = field(default_factory=list)  # SerpAPI Keys
    searxng_base_urls: List[str] = field(default_factory=list)  # SearXNG instance URLs (self-hosted, no quota)
    searxng_public_instances_enabled: bool = True  # Auto-discover public SearXNG instances when base URLs are absent

    # === Social Sentiment (US stocks only, api.adanos.org) ===
    social_sentiment_api_key: Optional[str] = None
    social_sentiment_api_url: str = "https://api.adanos.org"

    # === 新闻与分析筛选配置 ===
    news_max_age_days: int = 3   # 新闻最大时效（天）
    news_strategy_profile: str = "short"  # 新闻窗口策略档位：ultra_short/short/medium/long
    news_intel_retention_days: int = 30
    news_intel_fetch_timeout_sec: float = 8.0
    news_intel_max_items_per_source: int = 50
    news_intel_auto_fetch_enabled: bool = False
    newsnow_base_url: str = "https://newsnow.busiyi.world"
    bias_threshold: float = 5.0  # 乖离率阈值（%），超过此值提示不追高

    # === Agent 模式配置 ===
    agent_litellm_model: str = ""  # 可选的 Agent 专用主模型；留空时继承 LITELLM_MODEL
    agent_mode: bool = False
    _agent_mode_explicit: bool = False  # True when AGENT_MODE was explicitly set in env
    agent_max_steps: int = AGENT_MAX_STEPS_DEFAULT
    agent_skills: List[str] = field(default_factory=list)
    agent_skill_dir: Optional[str] = None
    agent_nl_routing: bool = False  # Enable natural language routing in bot dispatcher
    agent_arch: str = "single"     # Agent architecture: 'single' (legacy) or 'multi' (orchestrator)
    agent_orchestrator_mode: str = "standard"  # Orchestrator mode: quick/standard/full/specialist
    agent_orchestrator_timeout_s: int = 600  # Cooperative timeout budget for the whole multi-agent pipeline
    agent_skill_max_concurrency: int = 3  # Maximum parallel specialist skill workers (1-4)
    agent_data_tool_timeout_s: float = 0.0
    agent_search_tool_timeout_s: float = 0.0
    agent_analysis_tool_timeout_s: float = 0.0
    agent_action_tool_timeout_s: float = 0.0
    agent_context_compression_enabled: bool = False
    agent_context_compression_profile: str = AGENT_CONTEXT_COMPRESSION_DEFAULT_PROFILE
    agent_context_compression_trigger_tokens: int = 12000
    agent_context_protected_turns: int = 4
    agent_context_compression_summary_tokens: int = 1500
    agent_risk_override: bool = True  # Allow risk agent to veto buy signals
    agent_deep_research_budget: int = 30000  # Max token budget for deep research
    agent_deep_research_timeout: int = 600  # Max seconds for /research command before returning timeout
    agent_deep_research_max_sub_questions: int = 8
    agent_deep_research_sub_question_steps: int = 6
    agent_memory_enabled: bool = False  # Enable memory & calibration system
    agent_skill_autoweight: bool = True  # Auto-weight skills by backtest performance
    agent_skill_routing: str = "auto"  # Skill routing: 'auto' (regime-based) or 'manual'
    agent_event_monitor_enabled: bool = False  # Enable periodic event-driven alert checks in schedule mode
    agent_event_monitor_interval_minutes: int = 5  # Polling interval for event monitor background checks
    agent_event_alert_rules_json: str = ""  # 序列化的 EventMonitor 规则 JSON 数组

    # === 通知配置（可同时配置多个，全部推送）===
    
    # 企业微信 Webhook
    wechat_webhook_url: Optional[str] = None
    
    # 飞书 Webhook
    feishu_webhook_url: Optional[str] = None
    feishu_webhook_secret: Optional[str] = None  # 自定义机器人签名密钥（可选）
    feishu_webhook_keyword: Optional[str] = None  # 自定义机器人关键词（可选）
    
    # Telegram 配置（需要同时配置 Bot Token 和 Chat ID）
    telegram_bot_token: Optional[str] = None  # Bot Token（@BotFather 获取）
    telegram_chat_id: Optional[str] = None  # Chat ID
    telegram_message_thread_id: Optional[str] = None  # Topic ID (Message Thread ID) for groups
    
    # 邮件配置（只需邮箱和授权码，SMTP 自动识别）
    email_sender: Optional[str] = None  # 发件人邮箱
    email_sender_name: str = "daily_stock_analysis股票分析助手"  # 发件人显示名称
    email_password: Optional[str] = None  # 邮箱密码/授权码
    email_receivers: List[str] = field(default_factory=list)  # 收件人列表（留空则发给自己）

    # 股票→邮件分组路由（Issue #268）：STOCK_GROUP_N + EMAIL_GROUP_N
    # 配置后，每组的报告只发送给该组配置的收件人。
    stock_email_groups: List[Tuple[List[str], List[str]]] = field(default_factory=list)

    # Pushover 配置（手机/桌面推送通知）
    pushover_user_key: Optional[str] = None  # 用户 Key（https://pushover.net 获取）
    pushover_api_token: Optional[str] = None  # 应用 API Token

    # ntfy 配置（完整 topic endpoint，例如 https://ntfy.sh/my-topic）
    ntfy_url: Optional[str] = None
    ntfy_token: Optional[str] = None

    # Gotify 配置（server base URL；sender 会拼接 /message）
    gotify_url: Optional[str] = None
    gotify_token: Optional[str] = None
    
    # 自定义 Webhook（支持多个，逗号分隔）
    # 适用于：钉钉、Discord、Slack、自建服务等任意支持 POST JSON 的 Webhook
    custom_webhook_urls: List[str] = field(default_factory=list)
    custom_webhook_bearer_token: Optional[str] = None  # Bearer Token（用于需要认证的 Webhook）
    custom_webhook_body_template: Optional[str] = None  # 自定义 Webhook JSON body 模板
    webhook_verify_ssl: bool = True  # Webhook HTTPS 证书校验，false 可支持自签名（有 MITM 风险）

    # Discord 通知配置
    discord_bot_token: Optional[str] = None  # Discord Bot Token
    discord_main_channel_id: Optional[str] = None  # Discord 主频道 ID
    discord_webhook_url: Optional[str] = None  # Discord Webhook URL
    discord_interactions_public_key: Optional[str] = None  # Discord Interaction 入站验签公钥

    # Slack 通知配置
    slack_webhook_url: Optional[str] = None  # Slack Incoming Webhook URL
    slack_bot_token: Optional[str] = None  # Slack Bot Token (xoxb-...)
    slack_channel_id: Optional[str] = None  # Slack 频道 ID (Bot 模式必填)

    # AstrBot 通知配置
    astrbot_token: Optional[str] = None
    astrbot_url: Optional[str] = None

    # 通知路由策略（Issue #1200 P3）：留空表示该类型使用全部已配置渠道
    notification_report_channels: List[str] = field(default_factory=list)
    notification_alert_channels: List[str] = field(default_factory=list)
    notification_system_error_channels: List[str] = field(default_factory=list)

    # 通知降噪机制（Issue #1200 P4）：默认全部关闭，仅对静态通知渠道生效
    notification_dedup_ttl_seconds: int = 0
    notification_cooldown_seconds: int = 0
    notification_quiet_hours: str = ""
    notification_timezone: str = ""
    notification_min_severity: str = ""
    notification_daily_digest_enabled: bool = False

    # 单股推送模式：每分析完一只股票立即推送，而不是汇总后推送
    single_stock_notify: bool = False

    # 报告类型：simple(精简) 或 full(完整)
    report_type: str = "simple"
    report_language: str = "zh"

    # 仅分析结果摘要：true 时只推送汇总，不含个股详情（Issue #262）
    report_summary_only: bool = False
    report_show_llm_model: bool = True

    # 报告引擎 P0：Jinja2 渲染器与内容完整性校验
    report_templates_dir: str = "backend/templates"  # Template directory (relative to project root)
    report_renderer_enabled: bool = False  # Enable Jinja2 rendering (default off for zero regression)
    report_integrity_enabled: bool = True  # Content integrity validation after LLM output
    report_integrity_retry: int = 1  # Retry count when mandatory fields missing (0 = placeholder only)
    report_history_compare_n: int = 0  # History comparison count (0 = disabled)

    # PushPlus 推送配置
    pushplus_token: Optional[str] = None  # PushPlus Token
    pushplus_topic: Optional[str] = None  # PushPlus 群组编码（一对多推送）

    # Server酱3 推送配置
    serverchan3_sendkey: Optional[str] = None  # Server酱3 SendKey

    # 分析间隔时间（秒）- 用于避免API限流
    analysis_delay: float = 0.0  # 个股分析与大盘分析之间的延迟

    # 把个股报告与大盘报告合并为一条通知推送（Issue #190）
    merge_email_notification: bool = False

    # 消息长度限制（字节）- 超长自动分批发送
    feishu_max_bytes: int = 20000  # 飞书限制约 20KB，默认 20000 字节
    wechat_max_bytes: int = 4000   # 企业微信限制 4096 字节，默认 4000 字节
    discord_max_words: int = 2000  # Discord 限制 2000 字，默认 2000 字
    wechat_msg_type: str = "markdown"  # 企业微信消息类型，默认 markdown 类型

    # Markdown 转图片（Issue #289）：对不支持 Markdown 的渠道以图片发送
    markdown_to_image_channels: List[str] = field(default_factory=list)  # 逗号分隔：telegram,wechat,custom,email
    markdown_to_image_max_chars: int = 15000  # 超过此长度不转换，避免超大图片
    md2img_engine: str = "wkhtmltoimage"  # wkhtmltoimage | markdown-to-file (Issue #455, better emoji support)

    # 实时行情预取（Issue #455）：设为 false 可禁用，避免 efinance/akshare_em 全市场拉取
    prefetch_realtime_quotes: bool = True

    # === 数据库配置 ===
    database_url: str = ""  # 完整 SQLAlchemy URL（优先级高于 database_path）；例如 mysql+pymysql://user:pass@host/db
    database_path: str = "./data/stock_analysis.db"
    sqlite_wal_enabled: bool = True
    sqlite_busy_timeout_ms: int = 5000
    sqlite_write_retry_max: int = 3
    sqlite_write_retry_base_delay: float = 0.1

    # 是否保存分析上下文快照（用于历史回溯）
    save_context_snapshot: bool = True

    # === 回测配置 ===
    backtest_enabled: bool = True
    backtest_eval_window_days: int = 10
    backtest_min_age_days: int = 14
    backtest_engine_version: str = "v1"
    backtest_neutral_band_pct: float = 2.0
    
    # === 日志配置 ===
    log_dir: str = "./logs"  # 日志文件目录
    log_level: str = "INFO"  # 日志级别
    
    # === 系统配置 ===
    max_workers: int = 3  # 低并发防封禁
    debug: bool = False
    http_proxy: Optional[str] = None  # HTTP 代理 (例如: http://127.0.0.1:10809)
    https_proxy: Optional[str] = None # HTTPS 代理
    
    # === 定时任务配置 ===
    schedule_enabled: bool = False            # 是否启用定时任务
    schedule_time: str = "18:00"              # 每日推送时间（HH:MM 格式）
    schedule_run_immediately: bool = True     # 启动时是否立即执行一次
    runtime_scheduler_timeout_seconds: int = 2700  # API/Web scheduler watchdog budget
    run_immediately: bool = True              # 启动时是否立即执行一次（非定时模式）
    market_review_enabled: bool = True        # 是否启用大盘复盘
    daily_market_context_enabled: bool = True  # 是否将当日大盘摘要注入个股分析
    # 大盘复盘市场区域：cn(A股)、hk(港股)、us(美股)、both(三市场)，us 适合仅关注美股的用户
    market_review_region: str = "cn"
    market_review_color_scheme: str = "green_up"
    # 交易日检查：默认启用，非交易日跳过执行；设为 false 或 --force-run 可强制执行（Issue #373）
    trading_day_check_enabled: bool = True

    # === 实时行情增强数据配置 ===
    # 实时行情开关（关闭后使用历史收盘价进行分析）
    enable_realtime_quote: bool = True
    # 盘中实时技术面：启用时用实时价计算 MA/多头排列（Issue #234）；关闭则用昨日收盘
    daily_quote_sync_enabled: bool = False
    daily_quote_sync_markets: List[str] = field(default_factory=lambda: ["cn"])
    daily_quote_sync_max_workers: int = 3
    daily_quote_sync_lookback_days: int = 30
    daily_quote_sync_limit: int = 0
    enable_realtime_technical_indicators: bool = True
    # 筹码分布开关（该接口不稳定，云端部署建议关闭）
    enable_chip_distribution: bool = True
    # 东财接口补丁开关
    enable_eastmoney_patch: bool = False
    # 实时行情数据源优先级（逗号分隔）
    # 推荐顺序：tencent > akshare_sina > efinance > akshare_em > tushare
    # - tencent: 腾讯财经，有量比/换手率/市盈率等，单股查询稳定（推荐）
    # - akshare_sina: 新浪财经，基本行情稳定，但无量比
    # - efinance/akshare_em: 东财全量接口，数据最全但容易被封
    # - tushare: Tushare Pro，需要2000积分，数据全面（付费用户可优先使用）
    realtime_source_priority: str = "tencent,akshare_sina,efinance,akshare_em"
    # 实时行情缓存时间（秒）
    realtime_cache_ttl: int = 600
    # 熔断器冷却时间（秒）
    circuit_breaker_cooldown: int = 300

    # === 基本面聚合开关与降级保护 ===
    # 全局总开关；关闭时返回 not_supported 并保持主流程无变化
    enable_fundamental_pipeline: bool = True
    # 基本面阶段总预算（秒）
    fundamental_stage_timeout_seconds: float = FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT
    # 单能力源调用超时（秒）
    fundamental_fetch_timeout_seconds: float = 3.0
    # 单能力失败重试次数（已包含首次）
    fundamental_retry_max: int = 1
    # 基本面上下文短 TTL（秒）
    fundamental_cache_ttl_seconds: int = 120
    # 基本面缓存最大条目数（避免长时间运行内存增长）
    fundamental_cache_max_entries: int = 256

    # === Portfolio PR2：导入/风险/汇率设置 ===
    portfolio_risk_concentration_alert_pct: float = 35.0
    portfolio_risk_drawdown_alert_pct: float = 15.0
    portfolio_risk_stop_loss_alert_pct: float = 10.0
    portfolio_risk_stop_loss_near_ratio: float = 0.8
    portfolio_risk_lookback_days: int = 180
    portfolio_fx_update_enabled: bool = True

    # Discord 机器人状态
    discord_bot_status: str = "A股智能分析 | /help"

    # === 流控配置（防封禁关键参数）===
    # Akshare 请求间隔范围（秒）
    akshare_sleep_min: float = 2.0
    akshare_sleep_max: float = 5.0
    
    # Tushare 每分钟最大请求数（免费配额）
    tushare_rate_limit_per_minute: int = 80
    
    # 重试配置
    max_retries: int = 3
    retry_base_delay: float = 1.0
    retry_max_delay: float = 30.0
    
    # === WebUI 配置 ===
    webui_enabled: bool = False
    webui_host: str = "127.0.0.1"
    webui_port: int = 8000
    
    # === 机器人配置 ===
    bot_enabled: bool = True              # 是否启用机器人功能
    bot_command_prefix: str = "/"         # 命令前缀
    bot_rate_limit_requests: int = 10     # 频率限制：窗口内最大请求数
    bot_rate_limit_window: int = 60       # 频率限制：窗口时间（秒）
    bot_admin_users: List[str] = field(default_factory=list)  # 管理员用户 ID 列表
    
    # 飞书机器人（事件订阅）- 已有 feishu_app_id, feishu_app_secret
    feishu_verification_token: Optional[str] = None  # 事件订阅验证 Token
    feishu_encrypt_key: Optional[str] = None         # 消息加密密钥（可选）
    feishu_stream_enabled: bool = False              # 是否启用 Stream 长连接模式（无需公网IP）
    
    # 钉钉机器人
    dingtalk_app_key: Optional[str] = None      # 应用 AppKey
    dingtalk_app_secret: Optional[str] = None   # 应用 AppSecret
    dingtalk_stream_enabled: bool = False       # 是否启用 Stream 模式（无需公网IP）
    dingtalk_webhook_url: Optional[str] = None
    dingtalk_secret: Optional[str] = None
    
    # 企业微信机器人（回调模式）
    wecom_corpid: Optional[str] = None              # 企业 ID
    wecom_token: Optional[str] = None               # 回调 Token
    wecom_encoding_aes_key: Optional[str] = None    # 消息加解密密钥
    wecom_agent_id: Optional[str] = None            # 应用 AgentId
    
    # Telegram 机器人 - 已有 telegram_bot_token, telegram_chat_id
    telegram_webhook_secret: Optional[str] = None   # Webhook 密钥

    # === 配置校验模式 ===
    # CONFIG_VALIDATE_MODE=warn（默认）：记录所有问题但始终继续启动
    # CONFIG_VALIDATE_MODE=strict：发现任意 "error" 级别问题时 exit(1)
    config_validate_mode: str = "warn"

    # --- Post-init validation ---------------------------------------------------
    _VALID_AGENT_ARCH = {"single", "multi"}
    _VALID_ORCHESTRATOR_MODES = {"quick", "standard", "full", "specialist"}
    _VALID_SKILL_ROUTING = {"auto", "manual"}
    _WEBUI_RUNTIME_ENV_FILE_PRIORITY_KEYS = frozenset(
        {
            "STOCK_LIST",
            "SCHEDULE_ENABLED",
            "SCHEDULE_TIME",
            "SCHEDULE_RUN_IMMEDIATELY",
        }
    )
    _BOOTSTRAP_RUNTIME_ENV_OVERRIDES_CAPTURED = False
    _BOOTSTRAP_RUNTIME_ENV_OVERRIDES = frozenset()
    _BOOTSTRAP_RUNTIME_ENV_PRESENT_KEYS = frozenset()

    def __post_init__(self) -> None:
        """在 dataclass 构造完成后，对枚举型配置值做归一化校验与回退。"""
        _log = logging.getLogger(__name__)
        if self.agent_arch not in self._VALID_AGENT_ARCH:
            _log.warning(
                "Invalid AGENT_ARCH=%r, falling back to 'single'. Valid: %s",
                self.agent_arch, self._VALID_AGENT_ARCH,
            )
            object.__setattr__(self, "agent_arch", "single")
        if self.agent_orchestrator_mode not in self._VALID_ORCHESTRATOR_MODES:
            _log.warning(
                "Invalid AGENT_ORCHESTRATOR_MODE=%r, falling back to 'standard'. Valid: %s",
                self.agent_orchestrator_mode, self._VALID_ORCHESTRATOR_MODES,
            )
            object.__setattr__(self, "agent_orchestrator_mode", "standard")
        if self.agent_skill_routing not in self._VALID_SKILL_ROUTING:
            _log.warning(
                "Invalid AGENT_SKILL_ROUTING=%r, falling back to 'auto'. Valid: %s",
                self.agent_skill_routing, self._VALID_SKILL_ROUTING,
            )
            object.__setattr__(self, "agent_skill_routing", "auto")

    # 单例实例存储
    _instance: Optional['Config'] = None
    
    @classmethod
    def get_instance(cls) -> 'Config':
        """
        获取配置单例实例
        
        单例模式确保：
        1. 全局只有一个配置实例
        2. 配置只从环境变量加载一次
        3. 所有模块共享相同配置
        """
        if cls._instance is None:
            cls._instance = cls._load_from_env()
        return cls._instance
    
    @classmethod
    def _load_from_env(cls) -> 'Config':
        """
        从 .env 文件加载配置
        
        加载优先级：
        1. 大多数配置保持系统环境变量优先
        2. WebUI 可写的运行期关键键优先复用持久化 `.env`，但保留启动时显式进程环境变量的 override
        3. 代码中的默认值
        """
        cls._capture_bootstrap_runtime_env_overrides()
        preexisting_report_language = os.environ.get("REPORT_LANGUAGE")

        # 确保环境变量已加载
        setup_env()

        # === 智能代理配置 (关键修复) ===
        # 如果配置了代理，自动设置 NO_PROXY 以排除国内数据源，避免行情获取失败
        http_proxy = os.getenv('HTTP_PROXY') or os.getenv('http_proxy')
        if http_proxy:
            # 国内金融数据源域名列表
            domestic_domains = [
                'eastmoney.com',   # 东方财富 (Efinance/Akshare)
                'sina.com.cn',     # 新浪财经 (Akshare)
                '163.com',         # 网易财经 (Akshare)
                'tushare.pro',     # Tushare
                'baostock.com',    # Baostock
                'sse.com.cn',      # 上交所
                'szse.cn',         # 深交所
                'csindex.com.cn',  # 中证指数
                'cninfo.com.cn',   # 巨潮资讯
                'localhost',
                '127.0.0.1'
            ]

            # 获取现有的 no_proxy
            current_no_proxy = os.getenv('NO_PROXY') or os.getenv('no_proxy') or ''
            existing_domains = current_no_proxy.split(',') if current_no_proxy else []

            # 合并去重
            final_domains = list(set(existing_domains + domestic_domains))
            final_no_proxy = ','.join(filter(None, final_domains))

            # 设置环境变量 (requests/urllib3/aiohttp 都会遵守此设置)
            os.environ['NO_PROXY'] = final_no_proxy
            os.environ['no_proxy'] = final_no_proxy

            # 确保 HTTP_PROXY 也被正确设置（以防仅在 .env 中定义但未导出）
            os.environ['HTTP_PROXY'] = http_proxy
            os.environ['http_proxy'] = http_proxy

            # HTTPS_PROXY 同理
            https_proxy = os.getenv('HTTPS_PROXY') or os.getenv('https_proxy')
            if https_proxy:
                os.environ['HTTPS_PROXY'] = https_proxy
                os.environ['https_proxy'] = https_proxy

        
        # 解析自选股列表（逗号分隔，统一为大写 Issue #355）
        stock_list_str = cls._resolve_env_value(
            'STOCK_LIST',
            default='',
            prefer_env_file=True,
        )
        stock_list = [
            (c or "").strip().upper()
            for c in stock_list_str.split(',')
            if (c or "").strip()
        ]
        
        # 如果没有配置，使用默认的示例股票
        if not stock_list:
            stock_list = ['600519', '000001', '300750']
        
        # === LiteLLM multi-key parsing ===
        # GEMINI_API_KEYS（逗号分隔多个）优先于 GEMINI_API_KEY（单个）
        _gemini_keys_raw = os.getenv('GEMINI_API_KEYS', '')
        gemini_api_keys = [k.strip() for k in _gemini_keys_raw.split(',') if k.strip()]
        _single_gemini = os.getenv('GEMINI_API_KEY', '').strip()
        if not gemini_api_keys and _single_gemini:
            gemini_api_keys = [_single_gemini]

        # ANTHROPIC_API_KEYS 优先于 ANTHROPIC_API_KEY
        _anthropic_keys_raw = os.getenv('ANTHROPIC_API_KEYS', '')
        anthropic_api_keys = [k.strip() for k in _anthropic_keys_raw.split(',') if k.strip()]
        _single_anthropic = os.getenv('ANTHROPIC_API_KEY', '').strip()
        if not anthropic_api_keys and _single_anthropic:
            anthropic_api_keys = [_single_anthropic]

        # 取值优先级：OPENAI_API_KEYS > AIHUBMIX_KEY > OPENAI_API_KEY
        _aihubmix = os.getenv('AIHUBMIX_KEY', '').strip()
        _openai_keys_raw = os.getenv('OPENAI_API_KEYS', '')
        openai_api_keys = [k.strip() for k in _openai_keys_raw.split(',') if k.strip()]
        if not openai_api_keys:
            _single_openai = os.getenv('OPENAI_API_KEY', '').strip()
            _fallback_key = _aihubmix or _single_openai
            if _fallback_key:
                openai_api_keys = [_fallback_key]
        openai_base_url = os.getenv('OPENAI_BASE_URL') or (
            'https://aihubmix.com/v1' if _aihubmix else None
        )

        # DEEPSEEK_API_KEYS 优先于 DEEPSEEK_API_KEY（独立于 OpenAI 兼容层）
        _deepseek_keys_raw = os.getenv('DEEPSEEK_API_KEYS', '')
        deepseek_api_keys = [k.strip() for k in _deepseek_keys_raw.split(',') if k.strip()]
        if not deepseek_api_keys:
            _single_deepseek = os.getenv('DEEPSEEK_API_KEY', '').strip()
            if _single_deepseek:
                deepseek_api_keys = [_single_deepseek]

        anspire_keys_str = os.getenv('ANSPIRE_API_KEYS', '')
        anspire_api_keys = [k.strip() for k in anspire_keys_str.split(',') if k.strip()]
        litellm_model = os.getenv('LITELLM_MODEL', '').strip()
        _openai_model_env = os.getenv('OPENAI_MODEL', '').strip()
        _openai_model_name = _openai_model_env or 'gpt-5.5'

        # LITELLM_FALLBACK_MODELS：逗号分隔的备选模型列表
        _fallback_str = os.getenv('LITELLM_FALLBACK_MODELS', '')
        litellm_fallback_models = [m.strip() for m in _fallback_str.split(',') if m.strip()]

        # === LLM Channels + YAML config ===
        litellm_config_path = os.getenv('LITELLM_CONFIG', '').strip() or None
        llm_models_source = ""
        llm_channels: List[Dict[str, Any]] = []
        llm_model_list: List[Dict[str, Any]] = []

        # 优先级 1：LITELLM_CONFIG（标准 LiteLLM YAML 配置文件）
        if litellm_config_path:
            llm_model_list = cls._parse_litellm_yaml(litellm_config_path)
            if llm_model_list:
                llm_models_source = "litellm_config"

        # 优先级 2：LLM_CHANNELS（基于环境变量的渠道配置）
        if not llm_model_list:
            _channels_str = os.getenv('LLM_CHANNELS', '').strip()
            if _channels_str:
                llm_channels = cls._parse_llm_channels(_channels_str)
                llm_model_list = cls._channels_to_model_list(llm_channels)
                if llm_model_list:
                    llm_models_source = "llm_channels"

        agent_litellm_model = normalize_agent_litellm_model(
            os.getenv('AGENT_LITELLM_MODEL', ''),
            configured_models=set(get_configured_llm_models(llm_model_list)),
        )

        # 解析搜索引擎 API Keys（支持多个 key，逗号分隔）
        bocha_keys_str = os.getenv('BOCHA_API_KEYS', '')
        bocha_api_keys = [k.strip() for k in bocha_keys_str.split(',') if k.strip()]

        minimax_keys_str = os.getenv('MINIMAX_API_KEYS', '')
        minimax_api_keys = [k.strip() for k in minimax_keys_str.split(',') if k.strip()]
        
        tavily_keys_str = os.getenv('TAVILY_API_KEYS', '')
        tavily_api_keys = [k.strip() for k in tavily_keys_str.split(',') if k.strip()]
        
        serpapi_keys_str = os.getenv('SERPAPI_API_KEYS', '')
        serpapi_keys = [k.strip() for k in serpapi_keys_str.split(',') if k.strip()]

        brave_keys_str = os.getenv('BRAVE_API_KEYS', '')
        brave_api_keys = [k.strip() for k in brave_keys_str.split(',') if k.strip()]

        _raw_urls = [u.strip() for u in os.getenv('SEARXNG_BASE_URLS', '').split(',') if u.strip()]
        searxng_base_urls = []
        invalid_searxng_urls = []
        for u in _raw_urls:
            p = urlparse(u)
            if p.scheme in ('http', 'https') and p.netloc:
                searxng_base_urls.append(u)
            else:
                invalid_searxng_urls.append(u)
        if invalid_searxng_urls:
            logger.warning(
                "SEARXNG_BASE_URLS 中存在无效 URL，已忽略: %s",
                ", ".join(invalid_searxng_urls[:3]),
            )
        searxng_public_instances_enabled = parse_env_bool(
            os.getenv('SEARXNG_PUBLIC_INSTANCES_ENABLED'),
            default=True,
        )

        # 企微消息类型与最大字节数逻辑
        wechat_msg_type = os.getenv('WECHAT_MSG_TYPE', 'markdown')
        wechat_msg_type_lower = wechat_msg_type.lower()
        wechat_max_bytes_env = os.getenv('WECHAT_MAX_BYTES')
        if wechat_max_bytes_env not in (None, ''):
            wechat_max_bytes = parse_env_int(
                wechat_max_bytes_env,
                2048 if wechat_msg_type_lower == 'text' else 4000,
                field_name='WECHAT_MAX_BYTES',
                minimum=1,
            )
        else:
            # 未显式配置时，根据消息类型选择默认字节数
            wechat_max_bytes = 2048 if wechat_msg_type_lower == 'text' else 4000

        schedule_run_immediately_env = cls._resolve_env_value(
            'SCHEDULE_RUN_IMMEDIATELY',
            prefer_env_file=True,
        )
        schedule_run_immediately = (
            schedule_run_immediately_env.lower() == 'true'
            if schedule_run_immediately_env is not None
            else True
        )
        schedule_time_value = cls._resolve_env_value(
            'SCHEDULE_TIME',
            default='18:00',
            prefer_env_file=True,
        )

        report_language_raw = cls._resolve_report_language_env_value(
            preexisting_report_language
        )
        report_show_llm_model_raw = os.getenv('REPORT_SHOW_LLM_MODEL')
        report_show_llm_model = parse_env_bool(report_show_llm_model_raw, default=True)
        if report_show_llm_model_raw is not None and not report_show_llm_model_raw.strip():
            report_show_llm_model = False

        agent_context_compression_profile = normalize_agent_context_compression_profile(
            os.getenv('AGENT_CONTEXT_COMPRESSION_PROFILE')
        )
        agent_context_compression_preset = get_agent_context_compression_preset(
            agent_context_compression_profile
        )

        return cls(
            stock_list=stock_list,
            feishu_app_id=os.getenv('FEISHU_APP_ID'),
            feishu_app_secret=os.getenv('FEISHU_APP_SECRET'),
            feishu_folder_token=os.getenv('FEISHU_FOLDER_TOKEN'),
            feishu_chat_id=os.getenv('FEISHU_CHAT_ID'),
            feishu_domain=(os.getenv('FEISHU_DOMAIN', 'feishu') or 'feishu').strip().lower(),
            feishu_send_as_file=os.getenv('FEISHU_SEND_AS_FILE', '').lower() in ('true', '1', 'yes'),
            tushare_token=os.getenv('TUSHARE_TOKEN'),
            tickflow_api_key=os.getenv('TICKFLOW_API_KEY'),
            finnhub_api_key=os.getenv('FINNHUB_API_KEY') or None,
            alphavantage_api_key=os.getenv('ALPHAVANTAGE_API_KEY') or None,
            longbridge_app_key=os.getenv('LONGBRIDGE_APP_KEY') or None,
            longbridge_app_secret=os.getenv('LONGBRIDGE_APP_SECRET') or None,
            longbridge_access_token=os.getenv('LONGBRIDGE_ACCESS_TOKEN') or None,
            generation_backend=(os.getenv('GENERATION_BACKEND') or 'litellm').strip().lower(),
            generation_fallback_backend=(os.getenv('GENERATION_FALLBACK_BACKEND') or 'litellm').strip().lower(),
            generation_backend_timeout_seconds=parse_env_int(
                os.getenv('GENERATION_BACKEND_TIMEOUT_SECONDS'), 300,
                field_name='GENERATION_BACKEND_TIMEOUT_SECONDS', minimum=1, maximum=3600,
            ),
            generation_backend_max_output_bytes=parse_env_int(
                os.getenv('GENERATION_BACKEND_MAX_OUTPUT_BYTES'), 1048576,
                field_name='GENERATION_BACKEND_MAX_OUTPUT_BYTES', minimum=1024, maximum=33554432,
            ),
            opencode_cli_model=(os.getenv('OPENCODE_CLI_MODEL') or '').strip(),
            litellm_model=litellm_model,
            litellm_fallback_models=litellm_fallback_models,
            llm_temperature=resolve_unified_llm_temperature(litellm_model),
            litellm_config_path=litellm_config_path,
            llm_models_source=llm_models_source,
            llm_channels=llm_channels,
            llm_model_list=llm_model_list,
            gemini_api_keys=gemini_api_keys,
            anthropic_api_keys=anthropic_api_keys,
            openai_api_keys=openai_api_keys,
            deepseek_api_keys=deepseek_api_keys,
            gemini_api_key=os.getenv('GEMINI_API_KEY'),
            gemini_model=os.getenv('GEMINI_MODEL', 'gemini-3.1-pro-preview'),
            gemini_model_fallback=os.getenv('GEMINI_MODEL_FALLBACK', 'gemini-3-flash-preview'),
            gemini_temperature=parse_env_float(os.getenv('GEMINI_TEMPERATURE'), 0.7, field_name='GEMINI_TEMPERATURE'),
            gemini_request_delay=parse_env_float(os.getenv('GEMINI_REQUEST_DELAY'), 2.0, field_name='GEMINI_REQUEST_DELAY', minimum=0.0),
            gemini_max_retries=parse_env_int(os.getenv('GEMINI_MAX_RETRIES'), 5, field_name='GEMINI_MAX_RETRIES', minimum=0),
            gemini_retry_delay=parse_env_float(os.getenv('GEMINI_RETRY_DELAY'), 5.0, field_name='GEMINI_RETRY_DELAY', minimum=0.0),
            anthropic_api_key=os.getenv('ANTHROPIC_API_KEY'),
            anthropic_model=os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4-6'),
            anthropic_temperature=parse_env_float(os.getenv('ANTHROPIC_TEMPERATURE'), 0.7, field_name='ANTHROPIC_TEMPERATURE'),
            anthropic_max_tokens=parse_env_int(os.getenv('ANTHROPIC_MAX_TOKENS'), 8192, field_name='ANTHROPIC_MAX_TOKENS', minimum=1),
            # AIHubmix 是首选的 OpenAI 兼容 provider（一把 Key 通吃所有模型，无需梯子）。
            # 在 OpenAI 兼容层内部：AIHUBMIX_KEY 优先于 OPENAI_API_KEY。
            # 整体 provider 回退顺序：Gemini > Anthropic > OpenAI 兼容层（含 AIHubmix）。
            # 使用 AIHUBMIX_KEY 且未显式设置 OPENAI_BASE_URL 时，
            # base_url 自动设为 aihubmix.com/v1。
            # 模型名与上游保持一致（如 gemini-3.1-pro-preview、gpt-5.5、deepseek-v4-flash）。
            openai_api_key=openai_api_keys[0] if openai_api_keys else None,
            openai_base_url=openai_base_url,
            openai_model=_openai_model_name,
            openai_vision_model=os.getenv('OPENAI_VISION_MODEL') or None,
            openai_temperature=parse_env_float(os.getenv('OPENAI_TEMPERATURE'), 0.7, field_name='OPENAI_TEMPERATURE'),
            # Vision 模型取值优先级：VISION_MODEL > OPENAI_VISION_MODEL（别名）> 默认值
            vision_model=(
                os.getenv('VISION_MODEL')
                or os.getenv('OPENAI_VISION_MODEL')
                or ""
            ),
            vision_provider_priority=os.getenv('VISION_PROVIDER_PRIORITY', 'gemini,anthropic,openai'),
            anspire_api_keys=anspire_api_keys,
            bocha_api_keys=bocha_api_keys,
            minimax_api_keys=minimax_api_keys,
            tavily_api_keys=tavily_api_keys,
            brave_api_keys=brave_api_keys,
            serpapi_keys=serpapi_keys,
            searxng_base_urls=searxng_base_urls,
            searxng_public_instances_enabled=searxng_public_instances_enabled,
            social_sentiment_api_key=os.getenv('SOCIAL_SENTIMENT_API_KEY') or None,
            social_sentiment_api_url=os.getenv('SOCIAL_SENTIMENT_API_URL', 'https://api.adanos.org').rstrip('/'),
            news_max_age_days=parse_env_int(os.getenv('NEWS_MAX_AGE_DAYS'), 3, field_name='NEWS_MAX_AGE_DAYS', minimum=1),
            news_strategy_profile=cls._parse_news_strategy_profile(
                os.getenv('NEWS_STRATEGY_PROFILE', 'short')
            ),
            news_intel_retention_days=parse_env_int(os.getenv('NEWS_INTEL_RETENTION_DAYS'), 30, field_name='NEWS_INTEL_RETENTION_DAYS', minimum=1),
            news_intel_fetch_timeout_sec=parse_env_float(os.getenv('NEWS_INTEL_FETCH_TIMEOUT_SEC'), 8.0, field_name='NEWS_INTEL_FETCH_TIMEOUT_SEC', minimum=1.0),
            news_intel_max_items_per_source=parse_env_int(os.getenv('NEWS_INTEL_MAX_ITEMS_PER_SOURCE'), 50, field_name='NEWS_INTEL_MAX_ITEMS_PER_SOURCE', minimum=1),
            news_intel_auto_fetch_enabled=os.getenv('NEWS_INTEL_AUTO_FETCH_ENABLED', 'false').lower() == 'true',
            newsnow_base_url=(os.getenv('NEWSNOW_BASE_URL') or 'https://newsnow.busiyi.world').strip().rstrip('/'),
            bias_threshold=parse_env_float(os.getenv('BIAS_THRESHOLD'), 5.0, field_name='BIAS_THRESHOLD', minimum=1.0),
            agent_litellm_model=agent_litellm_model,
            agent_mode=os.getenv('AGENT_MODE', 'false').lower() == 'true',
            _agent_mode_explicit=os.getenv('AGENT_MODE') is not None,
            agent_max_steps=parse_env_int(
                os.getenv('AGENT_MAX_STEPS'),
                AGENT_MAX_STEPS_DEFAULT,
                field_name='AGENT_MAX_STEPS',
                minimum=1,
            ),
            agent_skills=[s.strip() for s in os.getenv('AGENT_SKILLS', '').split(',') if s.strip()],
            agent_skill_dir=os.getenv('AGENT_SKILL_DIR'),
            agent_nl_routing=os.getenv('AGENT_NL_ROUTING', 'false').lower() == 'true',
            agent_arch=os.getenv('AGENT_ARCH', 'single').lower(),
            agent_orchestrator_mode=os.getenv('AGENT_ORCHESTRATOR_MODE', 'standard').lower(),
            agent_orchestrator_timeout_s=parse_env_int(
                os.getenv('AGENT_ORCHESTRATOR_TIMEOUT_S'),
                600,
                field_name='AGENT_ORCHESTRATOR_TIMEOUT_S',
                minimum=0,
            ),
            agent_skill_max_concurrency=parse_env_int(
                os.getenv('AGENT_SKILL_MAX_CONCURRENCY'),
                3,
                field_name='AGENT_SKILL_MAX_CONCURRENCY',
                minimum=1,
                maximum=4,
            ),
            agent_data_tool_timeout_s=parse_env_float(
                os.getenv('AGENT_DATA_TOOL_TIMEOUT_S'), 0.0,
                field_name='AGENT_DATA_TOOL_TIMEOUT_S', minimum=0.0,
            ),
            agent_search_tool_timeout_s=parse_env_float(
                os.getenv('AGENT_SEARCH_TOOL_TIMEOUT_S'), 0.0,
                field_name='AGENT_SEARCH_TOOL_TIMEOUT_S', minimum=0.0,
            ),
            agent_analysis_tool_timeout_s=parse_env_float(
                os.getenv('AGENT_ANALYSIS_TOOL_TIMEOUT_S'), 0.0,
                field_name='AGENT_ANALYSIS_TOOL_TIMEOUT_S', minimum=0.0,
            ),
            agent_action_tool_timeout_s=parse_env_float(
                os.getenv('AGENT_ACTION_TOOL_TIMEOUT_S'), 0.0,
                field_name='AGENT_ACTION_TOOL_TIMEOUT_S', minimum=0.0,
            ),
            agent_context_compression_enabled=parse_env_bool(
                os.getenv('AGENT_CONTEXT_COMPRESSION_ENABLED'), default=False,
            ),
            agent_context_compression_profile=agent_context_compression_profile,
            agent_context_compression_trigger_tokens=parse_env_int(
                os.getenv('AGENT_CONTEXT_COMPRESSION_TRIGGER_TOKENS'),
                agent_context_compression_preset.trigger_tokens,
                field_name='AGENT_CONTEXT_COMPRESSION_TRIGGER_TOKENS', minimum=1000,
            ),
            agent_context_protected_turns=parse_env_int(
                os.getenv('AGENT_CONTEXT_PROTECTED_TURNS'),
                agent_context_compression_preset.protected_turns,
                field_name='AGENT_CONTEXT_PROTECTED_TURNS', minimum=0, maximum=50,
            ),
            agent_context_compression_summary_tokens=parse_env_int(
                os.getenv('AGENT_CONTEXT_COMPRESSION_SUMMARY_TOKENS'),
                agent_context_compression_preset.summary_tokens,
                field_name='AGENT_CONTEXT_COMPRESSION_SUMMARY_TOKENS', minimum=200, maximum=8000,
            ),
            agent_risk_override=os.getenv('AGENT_RISK_OVERRIDE', 'true').lower() == 'true',
            agent_deep_research_budget=parse_env_int(
                os.getenv('AGENT_DEEP_RESEARCH_BUDGET'),
                30000,
                field_name='AGENT_DEEP_RESEARCH_BUDGET',
                minimum=5000,
            ),
            agent_deep_research_timeout=parse_env_int(
                os.getenv('AGENT_DEEP_RESEARCH_TIMEOUT'),
                600,
                field_name='AGENT_DEEP_RESEARCH_TIMEOUT',
                minimum=30,
            ),
            agent_deep_research_max_sub_questions=parse_env_int(
                os.getenv('AGENT_DEEP_RESEARCH_MAX_SUB_QUESTIONS'),
                8,
                field_name='AGENT_DEEP_RESEARCH_MAX_SUB_QUESTIONS',
                minimum=1,
                maximum=20,
            ),
            agent_deep_research_sub_question_steps=parse_env_int(
                os.getenv('AGENT_DEEP_RESEARCH_SUB_QUESTION_STEPS'),
                6,
                field_name='AGENT_DEEP_RESEARCH_SUB_QUESTION_STEPS',
                minimum=1,
                maximum=20,
            ),
            agent_memory_enabled=os.getenv('AGENT_MEMORY_ENABLED', 'false').lower() == 'true',
            agent_skill_autoweight=os.getenv('AGENT_SKILL_AUTOWEIGHT', 'true').lower() == 'true',
            agent_skill_routing=os.getenv('AGENT_SKILL_ROUTING', 'auto').lower(),
            agent_event_monitor_enabled=os.getenv('AGENT_EVENT_MONITOR_ENABLED', 'false').lower() == 'true',
            agent_event_monitor_interval_minutes=parse_env_int(
                os.getenv('AGENT_EVENT_MONITOR_INTERVAL_MINUTES'),
                5,
                field_name='AGENT_EVENT_MONITOR_INTERVAL_MINUTES',
                minimum=1,
            ),
            agent_event_alert_rules_json=os.getenv('AGENT_EVENT_ALERT_RULES_JSON', ''),
            wechat_webhook_url=os.getenv('WECHAT_WEBHOOK_URL'),
            feishu_webhook_url=os.getenv('FEISHU_WEBHOOK_URL'),
            feishu_webhook_secret=os.getenv('FEISHU_WEBHOOK_SECRET'),
            feishu_webhook_keyword=os.getenv('FEISHU_WEBHOOK_KEYWORD'),
            telegram_bot_token=os.getenv('TELEGRAM_BOT_TOKEN'),
            telegram_chat_id=os.getenv('TELEGRAM_CHAT_ID'),
            telegram_message_thread_id=os.getenv('TELEGRAM_MESSAGE_THREAD_ID'),
            email_sender=os.getenv('EMAIL_SENDER'),
            email_sender_name=os.getenv('EMAIL_SENDER_NAME', 'daily_stock_analysis股票分析助手'),
            email_password=os.getenv('EMAIL_PASSWORD'),
            email_receivers=[r.strip() for r in os.getenv('EMAIL_RECEIVERS', '').split(',') if r.strip()],
            stock_email_groups=cls._parse_stock_email_groups(),
            pushover_user_key=os.getenv('PUSHOVER_USER_KEY'),
            pushover_api_token=os.getenv('PUSHOVER_API_TOKEN'),
            ntfy_url=os.getenv('NTFY_URL'),
            ntfy_token=os.getenv('NTFY_TOKEN'),
            gotify_url=os.getenv('GOTIFY_URL'),
            gotify_token=os.getenv('GOTIFY_TOKEN'),
            pushplus_token=os.getenv('PUSHPLUS_TOKEN'),
            pushplus_topic=os.getenv('PUSHPLUS_TOPIC'),
            serverchan3_sendkey=os.getenv('SERVERCHAN3_SENDKEY'),
            custom_webhook_urls=[u.strip() for u in os.getenv('CUSTOM_WEBHOOK_URLS', '').split(',') if u.strip()],
            custom_webhook_bearer_token=os.getenv('CUSTOM_WEBHOOK_BEARER_TOKEN'),
            custom_webhook_body_template=os.getenv('CUSTOM_WEBHOOK_BODY_TEMPLATE'),
            webhook_verify_ssl=os.getenv('WEBHOOK_VERIFY_SSL', 'true').lower() == 'true',
            discord_bot_token=os.getenv('DISCORD_BOT_TOKEN'),
            discord_main_channel_id=os.getenv('DISCORD_MAIN_CHANNEL_ID'),
            discord_webhook_url=os.getenv('DISCORD_WEBHOOK_URL'),
            discord_interactions_public_key=os.getenv('DISCORD_INTERACTIONS_PUBLIC_KEY'),
            slack_webhook_url=os.getenv('SLACK_WEBHOOK_URL'),
            slack_bot_token=os.getenv('SLACK_BOT_TOKEN'),
            slack_channel_id=os.getenv('SLACK_CHANNEL_ID'),
            astrbot_url=os.getenv('ASTRBOT_URL'),
            astrbot_token=os.getenv('ASTRBOT_TOKEN'),
            notification_report_channels=parse_notification_route_channels(
                os.getenv('NOTIFICATION_REPORT_CHANNELS')
            ),
            notification_alert_channels=parse_notification_route_channels(
                os.getenv('NOTIFICATION_ALERT_CHANNELS')
            ),
            notification_system_error_channels=parse_notification_route_channels(
                os.getenv('NOTIFICATION_SYSTEM_ERROR_CHANNELS')
            ),
            notification_dedup_ttl_seconds=parse_env_int(
                os.getenv('NOTIFICATION_DEDUP_TTL_SECONDS'),
                0,
                field_name='NOTIFICATION_DEDUP_TTL_SECONDS',
                minimum=0,
            ),
            notification_cooldown_seconds=parse_env_int(
                os.getenv('NOTIFICATION_COOLDOWN_SECONDS'),
                0,
                field_name='NOTIFICATION_COOLDOWN_SECONDS',
                minimum=0,
            ),
            notification_quiet_hours=(os.getenv('NOTIFICATION_QUIET_HOURS') or '').strip(),
            notification_timezone=(os.getenv('NOTIFICATION_TIMEZONE') or '').strip(),
            notification_min_severity=(os.getenv('NOTIFICATION_MIN_SEVERITY') or '').strip().lower(),
            notification_daily_digest_enabled=parse_env_bool(
                os.getenv('NOTIFICATION_DAILY_DIGEST_ENABLED'),
                default=False,
            ),
            single_stock_notify=os.getenv('SINGLE_STOCK_NOTIFY', 'false').lower() == 'true',
            report_type=cls._parse_report_type(os.getenv('REPORT_TYPE', 'simple')),
            report_language=cls._parse_report_language(report_language_raw),
            report_summary_only=os.getenv('REPORT_SUMMARY_ONLY', 'false').lower() == 'true',
            report_show_llm_model=report_show_llm_model,
            report_templates_dir=os.getenv('REPORT_TEMPLATES_DIR', 'backend/templates'),
            report_renderer_enabled=os.getenv('REPORT_RENDERER_ENABLED', 'false').lower() == 'true',
            report_integrity_enabled=os.getenv('REPORT_INTEGRITY_ENABLED', 'true').lower() == 'true',
            report_integrity_retry=parse_env_int(os.getenv('REPORT_INTEGRITY_RETRY'), 1, field_name='REPORT_INTEGRITY_RETRY', minimum=0),
            report_history_compare_n=parse_env_int(os.getenv('REPORT_HISTORY_COMPARE_N'), 0, field_name='REPORT_HISTORY_COMPARE_N', minimum=0),
            analysis_delay=parse_env_float(os.getenv('ANALYSIS_DELAY'), 0.0, field_name='ANALYSIS_DELAY', minimum=0.0),
            merge_email_notification=os.getenv('MERGE_EMAIL_NOTIFICATION', 'false').lower() == 'true',
            feishu_max_bytes=parse_env_int(os.getenv('FEISHU_MAX_BYTES'), 20000, field_name='FEISHU_MAX_BYTES', minimum=1),
            wechat_max_bytes=wechat_max_bytes,
            wechat_msg_type=wechat_msg_type_lower,
            discord_max_words=parse_env_int(os.getenv('DISCORD_MAX_WORDS'), 2000, field_name='DISCORD_MAX_WORDS', minimum=1),
            markdown_to_image_channels=[
                c.strip().lower()
                for c in os.getenv('MARKDOWN_TO_IMAGE_CHANNELS', '').split(',')
                if c.strip()
            ],
            markdown_to_image_max_chars=parse_env_int(
                os.getenv('MARKDOWN_TO_IMAGE_MAX_CHARS'),
                15000,
                field_name='MARKDOWN_TO_IMAGE_MAX_CHARS',
                minimum=1,
            ),
            md2img_engine=cls._parse_md2img_engine(os.getenv('MD2IMG_ENGINE', 'wkhtmltoimage')),
            prefetch_realtime_quotes=os.getenv('PREFETCH_REALTIME_QUOTES', 'true').lower() == 'true',
            database_url=os.getenv('DATABASE_URL', '').strip(),
            database_path=os.getenv('DATABASE_PATH', './data/stock_analysis.db'),
            sqlite_wal_enabled=os.getenv('SQLITE_WAL_ENABLED', 'true').lower() == 'true',
            sqlite_busy_timeout_ms=parse_env_int(
                os.getenv('SQLITE_BUSY_TIMEOUT_MS'),
                5000,
                field_name='SQLITE_BUSY_TIMEOUT_MS',
                minimum=0,
            ),
            sqlite_write_retry_max=parse_env_int(
                os.getenv('SQLITE_WRITE_RETRY_MAX'),
                3,
                field_name='SQLITE_WRITE_RETRY_MAX',
                minimum=0,
            ),
            sqlite_write_retry_base_delay=parse_env_float(
                os.getenv('SQLITE_WRITE_RETRY_BASE_DELAY'),
                0.1,
                field_name='SQLITE_WRITE_RETRY_BASE_DELAY',
                minimum=0.0,
            ),
            save_context_snapshot=os.getenv('SAVE_CONTEXT_SNAPSHOT', 'true').lower() == 'true',
            backtest_enabled=os.getenv('BACKTEST_ENABLED', 'true').lower() == 'true',
            backtest_eval_window_days=parse_env_int(os.getenv('BACKTEST_EVAL_WINDOW_DAYS'), 10, field_name='BACKTEST_EVAL_WINDOW_DAYS', minimum=1),
            backtest_min_age_days=parse_env_int(os.getenv('BACKTEST_MIN_AGE_DAYS'), 14, field_name='BACKTEST_MIN_AGE_DAYS', minimum=1),
            backtest_engine_version=os.getenv('BACKTEST_ENGINE_VERSION', 'v1'),
            backtest_neutral_band_pct=parse_env_float(
                os.getenv('BACKTEST_NEUTRAL_BAND_PCT'),
                2.0,
                field_name='BACKTEST_NEUTRAL_BAND_PCT',
                minimum=0.0,
            ),
            log_dir=os.getenv('LOG_DIR', './logs'),
            log_level=os.getenv('LOG_LEVEL', 'INFO'),
            max_workers=parse_env_int(os.getenv('MAX_WORKERS'), 3, field_name='MAX_WORKERS', minimum=1),
            debug=os.getenv('DEBUG', 'false').lower() == 'true',
            config_validate_mode=os.getenv('CONFIG_VALIDATE_MODE', 'warn').lower(),
            http_proxy=os.getenv('HTTP_PROXY'),
            https_proxy=os.getenv('HTTPS_PROXY'),
            schedule_enabled=cls._resolve_env_value(
                'SCHEDULE_ENABLED',
                default='false',
                prefer_env_file=True,
            ).lower() == 'true',
            schedule_time=(schedule_time_value or '18:00').strip() or '18:00',
            schedule_run_immediately=schedule_run_immediately,
            runtime_scheduler_timeout_seconds=parse_env_int(
                os.getenv('RUNTIME_SCHEDULER_TIMEOUT_SECONDS'),
                2700,
                field_name='RUNTIME_SCHEDULER_TIMEOUT_SECONDS',
                minimum=60,
            ),
            run_immediately=schedule_run_immediately,
            market_review_enabled=os.getenv('MARKET_REVIEW_ENABLED', 'true').lower() == 'true',
            daily_market_context_enabled=os.getenv('DAILY_MARKET_CONTEXT_ENABLED', 'true').lower() == 'true',
            market_review_region=cls._parse_market_review_region(
                os.getenv('MARKET_REVIEW_REGION', 'cn')
            ),
            market_review_color_scheme=cls._parse_market_review_color_scheme(
                os.getenv('MARKET_REVIEW_COLOR_SCHEME', 'green_up')
            ),
            trading_day_check_enabled=os.getenv('TRADING_DAY_CHECK_ENABLED', 'true').lower() != 'false',
            daily_quote_sync_enabled=parse_env_bool(os.getenv('DAILY_QUOTE_SYNC_ENABLED'), False),
            daily_quote_sync_markets=[
                m.strip().lower()
                for m in os.getenv('DAILY_QUOTE_SYNC_MARKETS', 'cn').split(',')
                if m.strip()
            ],
            daily_quote_sync_max_workers=parse_env_int(
                os.getenv('DAILY_QUOTE_SYNC_MAX_WORKERS'),
                3,
                field_name='DAILY_QUOTE_SYNC_MAX_WORKERS',
                minimum=1,
            ),
            daily_quote_sync_lookback_days=parse_env_int(
                os.getenv('DAILY_QUOTE_SYNC_LOOKBACK_DAYS'),
                30,
                field_name='DAILY_QUOTE_SYNC_LOOKBACK_DAYS',
                minimum=1,
            ),
            daily_quote_sync_limit=parse_env_int(
                os.getenv('DAILY_QUOTE_SYNC_LIMIT'),
                0,
                field_name='DAILY_QUOTE_SYNC_LIMIT',
                minimum=0,
            ),
            webui_enabled=os.getenv('WEBUI_ENABLED', 'false').lower() == 'true',
            webui_host=os.getenv('WEBUI_HOST', '127.0.0.1'),
            webui_port=parse_env_int(os.getenv('WEBUI_PORT'), 8000, field_name='WEBUI_PORT', minimum=1, maximum=65535),
            # 机器人配置
            bot_enabled=os.getenv('BOT_ENABLED', 'true').lower() == 'true',
            bot_command_prefix=os.getenv('BOT_COMMAND_PREFIX', '/'),
            bot_rate_limit_requests=parse_env_int(os.getenv('BOT_RATE_LIMIT_REQUESTS'), 10, field_name='BOT_RATE_LIMIT_REQUESTS', minimum=1),
            bot_rate_limit_window=parse_env_int(os.getenv('BOT_RATE_LIMIT_WINDOW'), 60, field_name='BOT_RATE_LIMIT_WINDOW', minimum=1),
            bot_admin_users=[u.strip() for u in os.getenv('BOT_ADMIN_USERS', '').split(',') if u.strip()],
            # 飞书机器人
            feishu_verification_token=os.getenv('FEISHU_VERIFICATION_TOKEN'),
            feishu_encrypt_key=os.getenv('FEISHU_ENCRYPT_KEY'),
            feishu_stream_enabled=os.getenv('FEISHU_STREAM_ENABLED', 'false').lower() == 'true',
            # 钉钉机器人
            dingtalk_app_key=os.getenv('DINGTALK_APP_KEY'),
            dingtalk_app_secret=os.getenv('DINGTALK_APP_SECRET'),
            dingtalk_stream_enabled=os.getenv('DINGTALK_STREAM_ENABLED', 'false').lower() == 'true',
            dingtalk_webhook_url=os.getenv('DINGTALK_WEBHOOK_URL'),
            dingtalk_secret=os.getenv('DINGTALK_SECRET'),
            # 企业微信机器人
            wecom_corpid=os.getenv('WECOM_CORPID'),
            wecom_token=os.getenv('WECOM_TOKEN'),
            wecom_encoding_aes_key=os.getenv('WECOM_ENCODING_AES_KEY'),
            wecom_agent_id=os.getenv('WECOM_AGENT_ID'),
            # Telegram
            telegram_webhook_secret=os.getenv('TELEGRAM_WEBHOOK_SECRET'),
            # Discord 机器人扩展配置
            discord_bot_status=os.getenv('DISCORD_BOT_STATUS', 'A股智能分析 | /help'),
            # 实时行情增强数据配置
            enable_realtime_quote=os.getenv('ENABLE_REALTIME_QUOTE', 'true').lower() == 'true',
            enable_realtime_technical_indicators=os.getenv(
                'ENABLE_REALTIME_TECHNICAL_INDICATORS', 'true'
            ).lower() == 'true',
            enable_chip_distribution=os.getenv('ENABLE_CHIP_DISTRIBUTION', 'true').lower() == 'true',
            # 东财接口补丁开关
            enable_eastmoney_patch=os.getenv('ENABLE_EASTMONEY_PATCH', 'false').lower() == 'true',
            # 实时行情数据源优先级：
            # - tencent: 腾讯财经，有量比/换手率/PE/PB等，单股查询稳定（推荐）
            # - akshare_sina: 新浪财经，基本行情稳定，但无量比
            # - efinance/akshare_em: 东财全量接口，数据最全但容易被封
            # - tushare: Tushare Pro，需要2000积分，数据全面
            realtime_source_priority=cls._resolve_realtime_source_priority(),
            realtime_cache_ttl=parse_env_int(os.getenv('REALTIME_CACHE_TTL'), 600, field_name='REALTIME_CACHE_TTL', minimum=0),
            circuit_breaker_cooldown=parse_env_int(os.getenv('CIRCUIT_BREAKER_COOLDOWN'), 300, field_name='CIRCUIT_BREAKER_COOLDOWN', minimum=0),
            enable_fundamental_pipeline=os.getenv('ENABLE_FUNDAMENTAL_PIPELINE', 'true').lower() == 'true',
            fundamental_stage_timeout_seconds=parse_env_float(
                os.getenv('FUNDAMENTAL_STAGE_TIMEOUT_SECONDS'),
                FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT,
                field_name='FUNDAMENTAL_STAGE_TIMEOUT_SECONDS',
                minimum=0.0,
            ),
            fundamental_fetch_timeout_seconds=parse_env_float(
                os.getenv('FUNDAMENTAL_FETCH_TIMEOUT_SECONDS'),
                3.0,
                field_name='FUNDAMENTAL_FETCH_TIMEOUT_SECONDS',
                minimum=0.0,
            ),
            fundamental_retry_max=parse_env_int(os.getenv('FUNDAMENTAL_RETRY_MAX'), 1, field_name='FUNDAMENTAL_RETRY_MAX', minimum=0),
            fundamental_cache_ttl_seconds=parse_env_int(
                os.getenv('FUNDAMENTAL_CACHE_TTL_SECONDS'),
                120,
                field_name='FUNDAMENTAL_CACHE_TTL_SECONDS',
                minimum=0,
            ),
            fundamental_cache_max_entries=parse_env_int(
                os.getenv('FUNDAMENTAL_CACHE_MAX_ENTRIES'),
                256,
                field_name='FUNDAMENTAL_CACHE_MAX_ENTRIES',
                minimum=1,
            ),
            portfolio_risk_concentration_alert_pct=parse_env_float(
                os.getenv('PORTFOLIO_RISK_CONCENTRATION_ALERT_PCT'),
                35.0,
                field_name='PORTFOLIO_RISK_CONCENTRATION_ALERT_PCT',
                minimum=0.0,
            ),
            portfolio_risk_drawdown_alert_pct=parse_env_float(
                os.getenv('PORTFOLIO_RISK_DRAWDOWN_ALERT_PCT'),
                15.0,
                field_name='PORTFOLIO_RISK_DRAWDOWN_ALERT_PCT',
                minimum=0.0,
            ),
            portfolio_risk_stop_loss_alert_pct=parse_env_float(
                os.getenv('PORTFOLIO_RISK_STOP_LOSS_ALERT_PCT'),
                10.0,
                field_name='PORTFOLIO_RISK_STOP_LOSS_ALERT_PCT',
                minimum=0.0,
            ),
            portfolio_risk_stop_loss_near_ratio=parse_env_float(
                os.getenv('PORTFOLIO_RISK_STOP_LOSS_NEAR_RATIO'),
                0.8,
                field_name='PORTFOLIO_RISK_STOP_LOSS_NEAR_RATIO',
                minimum=0.0,
            ),
            portfolio_risk_lookback_days=parse_env_int(
                os.getenv('PORTFOLIO_RISK_LOOKBACK_DAYS'),
                180,
                field_name='PORTFOLIO_RISK_LOOKBACK_DAYS',
                minimum=1,
            ),
            portfolio_fx_update_enabled=os.getenv('PORTFOLIO_FX_UPDATE_ENABLED', 'true').lower() == 'true'
        )
    
    @classmethod
    def _parse_litellm_yaml(cls, config_path: str) -> List[Dict[str, Any]]:
        """把标准 LiteLLM YAML 配置解析为 Router 的 model_list。

        支持 ``os.environ/VAR_NAME`` 语法引用密钥。任何异常都只记日志并返回空列表，
        保证配置错误不会让进程启动失败。

        Args:
            config_path: YAML 路径；相对路径按项目根目录解析。

        Returns:
            Router model_list；解析失败时为空列表。
        """
        import logging
        _logger = logging.getLogger(__name__)
        try:
            import yaml
        except ImportError:
            _logger.warning("PyYAML not installed; LITELLM_CONFIG ignored. Run: uv sync --locked")
            return []

        path = Path(config_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[2] / path
        if not path.exists():
            _logger.warning(f"LITELLM_CONFIG file not found: {path}")
            return []

        try:
            with open(path, encoding='utf-8') as f:
                yaml_config = yaml.safe_load(f) or {}
        except Exception as e:
            _logger.warning(f"Failed to parse LITELLM_CONFIG: {e}")
            return []

        model_list = yaml_config.get('model_list', [])
        if not isinstance(model_list, list):
            _logger.warning("LITELLM_CONFIG: model_list must be a list")
            return []

        # 解析字符串参数中的 os.environ/ 引用，替换为真实环境变量值
        for entry in model_list:
            params = entry.get('litellm_params', {})
            for key in list(params.keys()):
                val = params.get(key)
                if isinstance(val, str) and val.startswith('os.environ/'):
                    env_name = val.split('/', 1)[1]
                    params[key] = os.getenv(env_name, '')

        _logger.info(f"LITELLM_CONFIG: loaded {len(model_list)} model deployment(s) from {path}")
        return model_list

    @classmethod
    def _parse_llm_channels(cls, channels_str: str) -> List[Dict[str, Any]]:
        """解析 LLM_CHANNELS 以及每个渠道的专属环境变量。

        约定格式（渠道名大写后拼在 LLM_ 前缀之后）：
            LLM_CHANNELS=aihubmix,deepseek,gemini
            LLM_AIHUBMIX_PROTOCOL=openai
            LLM_AIHUBMIX_BASE_URL=https://aihubmix.com/v1
            LLM_AIHUBMIX_API_KEY=sk-xxx           (或 LLM_AIHUBMIX_API_KEYS=k1,k2)
            LLM_AIHUBMIX_MODELS=gpt-5.5,claude-sonnet-4-6
            LLM_AIHUBMIX_ENABLED=true

        Args:
            channels_str: LLM_CHANNELS 的原始逗号分隔字符串。

        Returns:
            解析成功的渠道字典列表；缺 Key / 缺模型 / 被禁用的渠道会被跳过。
        """
        import logging
        _logger = logging.getLogger(__name__)

        channels: List[Dict[str, Any]] = []
        for raw_name in channels_str.split(','):
            ch_name = raw_name.strip()
            if not ch_name:
                continue
            ch_lower = ch_name.lower()
            ch_upper = ch_name.upper()

            base_url = os.getenv(f'LLM_{ch_upper}_BASE_URL', '').strip() or None
            protocol_raw = os.getenv(f'LLM_{ch_upper}_PROTOCOL', '').strip()
            enabled_raw = os.getenv(f'LLM_{ch_upper}_ENABLED')
            enabled = parse_env_bool(enabled_raw, default=True)

            # API Key 取值：LLM_{NAME}_API_KEYS（多个）优先于 LLM_{NAME}_API_KEY（单个）
            api_keys_raw = os.getenv(f'LLM_{ch_upper}_API_KEYS', '')
            api_keys = [k.strip() for k in api_keys_raw.split(',') if k.strip()]
            if not api_keys:
                single_key = os.getenv(f'LLM_{ch_upper}_API_KEY', '').strip()
                if single_key:
                    api_keys = [single_key]
            # 模型列表
            models_raw = os.getenv(f'LLM_{ch_upper}_MODELS', '')
            raw_models = [m.strip() for m in models_raw.split(',') if m.strip()]
            protocol = resolve_llm_channel_protocol(protocol_raw, base_url=base_url, models=raw_models, channel_name=ch_name)
            models = [normalize_llm_channel_model(m, protocol, base_url) for m in raw_models]

            # 附加请求头（JSON 字符串，可选）
            extra_headers_raw = os.getenv(f'LLM_{ch_upper}_EXTRA_HEADERS', '').strip()
            extra_headers = None
            if extra_headers_raw:
                try:
                    extra_headers = json.loads(extra_headers_raw)
                except json.JSONDecodeError:
                    _logger.warning(f"LLM_{ch_upper}_EXTRA_HEADERS: invalid JSON, ignored")

            if not enabled:
                _logger.info(f"LLM channel '{ch_name}': disabled, skipped")
                continue

            if protocol_raw and canonicalize_llm_channel_protocol(protocol_raw) not in SUPPORTED_LLM_CHANNEL_PROTOCOLS:
                _logger.warning(
                    "LLM_%s_PROTOCOL=%s is unsupported; auto-detected protocol=%s",
                    ch_upper,
                    protocol_raw,
                    protocol or "unknown",
                )

            if not api_keys and channel_allows_empty_api_key(protocol, base_url):
                api_keys = [""]

            if not api_keys:
                _logger.warning(f"LLM channel '{ch_name}': no API key configured, skipped")
                continue
            if not models:
                _logger.warning(f"LLM channel '{ch_name}': no models configured, skipped")
                continue

            channels.append({
                'name': ch_name.lower(),
                'protocol': protocol,
                'enabled': enabled,
                'base_url': base_url,
                'api_keys': api_keys,
                'models': models,
                'extra_headers': extra_headers,
            })
            _logger.info(f"LLM channel '{ch_name}': {len(models)} model(s), {len(api_keys)} key(s)")

        return channels

    @classmethod
    def _channels_to_model_list(cls, channels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """把解析后的渠道列表展开成 LiteLLM Router 的 model_list 格式。

        模型 × API Key 做笛卡尔展开，使 Router 能在多 Key 之间做负载均衡。
        """
        model_list: List[Dict[str, Any]] = []
        for ch in channels:
            for model_name in ch['models']:
                for api_key in ch['api_keys']:
                    litellm_params: Dict[str, Any] = {
                        'model': model_name,
                    }
                    if api_key:
                        litellm_params['api_key'] = api_key
                    if ch['base_url']:
                        litellm_params['api_base'] = ch['base_url']
                    # 自动注入 aihubmix 的推广渠道请求头
                    headers = dict(ch.get('extra_headers') or {})
                    if ch['base_url'] and 'aihubmix.com' in ch['base_url']:
                        headers.setdefault('APP-Code', 'GPIJ3886')
                    if headers:
                        litellm_params['extra_headers'] = headers

                    model_list.append({
                        'model_name': model_name,
                        'litellm_params': litellm_params,
                    })
        return model_list

    @classmethod
    def _parse_stock_email_groups(cls) -> List[Tuple[List[str], List[str]]]:
        """
        从环境变量解析 STOCK_GROUP_N 与 EMAIL_GROUP_N。
        返回按分组序号升序排列的 [(stocks, emails), ...]。
        股票代码统一经过 normalize_stock_code 标准化，
        以保证运行期路由与校验阶段使用的是同一套等价规则。
        """
        from data_provider.base import normalize_stock_code

        groups: dict = {}
        stock_re = re.compile(r'^STOCK_GROUP_(\d+)$', re.IGNORECASE)
        email_re = re.compile(r'^EMAIL_GROUP_(\d+)$', re.IGNORECASE)
        for key in os.environ:
            m = stock_re.match(key)
            if m:
                idx = int(m.group(1))
                val = os.environ[key].strip()
                groups.setdefault(idx, {})['stocks'] = [
                    normalize_stock_code(c.strip())
                    for c in val.split(',') if c.strip()
                ]
            m = email_re.match(key)
            if m:
                idx = int(m.group(1))
                val = os.environ[key].strip()
                groups.setdefault(idx, {})['emails'] = [e.strip() for e in val.split(',') if e.strip()]
        result = []
        for idx in sorted(groups.keys()):
            g = groups[idx]
            if 'stocks' in g and 'emails' in g and g['stocks'] and g['emails']:
                result.append((g['stocks'], g['emails']))
        return result

    @classmethod
    def _parse_report_type(cls, value: str) -> str:
        """解析 REPORT_TYPE，非法值记警告后回退为 simple（支持 brief）。"""
        v = (value or 'simple').strip().lower()
        if v in ('simple', 'full', 'brief'):
            return v
        import logging
        logging.getLogger(__name__).warning(
            f"REPORT_TYPE '{value}' invalid, fallback to 'simple' (valid: simple/full/brief)"
        )
        return 'simple'

    @classmethod
    def _get_env_file_value(cls, key: str) -> Optional[str]:
        """直接从当前生效的 `.env` 文件中读取指定配置项（不经 os.environ）。"""
        env_file = os.getenv("ENV_FILE")
        env_path = Path(env_file) if env_file else (Path(__file__).resolve().parents[2] / ".env")
        if not env_path.exists():
            return None

        try:
            env_values = dotenv_values(env_path)
        except Exception as exc:  # pragma: no cover - defensive branch
            logging.getLogger(__name__).warning(
                "Failed to read %s while resolving %s: %s",
                env_path,
                key,
                exc,
            )
            return None

        value = env_values.get(key)
        if value is None:
            return None
        return str(value)

    @classmethod
    def _resolve_env_value(
        cls,
        key: str,
        *,
        default: Optional[str] = None,
        prefer_env_file: bool = False,
    ) -> Optional[str]:
        """解析单个环境变量值，可指定是否优先采用持久化的 `.env` 副本。

        运行期可被 WebUI 改写的键（见 _WEBUI_RUNTIME_ENV_FILE_PRIORITY_KEYS）默认以
        `.env` 为准，除非该键在进程启动时被显式覆盖，从而兼顾“界面改配置即时生效”
        与“容器环境变量不被静默覆盖”。

        Args:
            key: 环境变量名。
            default: 环境变量与 `.env` 都不存在时的回退值。
            prefer_env_file: 为 True 时对该键强制采用 `.env` 优先策略。

        Returns:
            解析到的字符串值，均不存在时返回 default。
        """
        env_value = os.getenv(key)
        file_value = cls._get_env_file_value(key)

        should_prefer_file = prefer_env_file or key in cls._WEBUI_RUNTIME_ENV_FILE_PRIORITY_KEYS
        if should_prefer_file and file_value is not None:
            if env_value is not None and cls._has_bootstrap_runtime_env_override(key):
                return env_value
            return file_value
        if env_value is not None:
            return env_value
        if file_value is not None:
            return file_value
        return default

    @classmethod
    def _capture_bootstrap_runtime_env_overrides(cls) -> None:
        """在 dotenv 改写 os.environ 之前，记录进程级显式传入的运行期环境变量。

        由 ``setup_env()`` 在 ``load_dotenv()`` **之前**调用，此时 ``os.environ``
        中只含真正的进程级取值（Docker ``environment:``、Dockerfile ``ENV``、
        shell export 等）。

        一个键被判定为“显式覆盖”的条件是：它存在于 ``os.environ`` 且
        * 持久化的 ``.env`` 文件中不存在，**或**
        * 两处取值 **不同**。

        两者完全相同时不做标记，因为区分没有意义 —— 这样 WebUI 后续改写
        ``.env`` 能在配置热加载时生效，无需重启容器。
        """
        if cls._BOOTSTRAP_RUNTIME_ENV_OVERRIDES_CAPTURED:
            return

        explicit_overrides = set()
        present_keys = set()
        for key in cls._WEBUI_RUNTIME_ENV_FILE_PRIORITY_KEYS:
            env_value = os.environ.get(key)
            if env_value is None:
                continue

            present_keys.add(key)
            file_value = cls._get_env_file_value(key)
            if file_value is None or env_value != file_value:
                explicit_overrides.add(key)

        cls._BOOTSTRAP_RUNTIME_ENV_OVERRIDES = frozenset(explicit_overrides)
        cls._BOOTSTRAP_RUNTIME_ENV_PRESENT_KEYS = frozenset(present_keys)
        cls._BOOTSTRAP_RUNTIME_ENV_OVERRIDES_CAPTURED = True

    @classmethod
    def _has_bootstrap_runtime_env_override(cls, key: str) -> bool:
        """判断某个键是否在 `.env` 之外被进程级显式覆盖过。"""
        cls._capture_bootstrap_runtime_env_overrides()
        return key in cls._BOOTSTRAP_RUNTIME_ENV_OVERRIDES

    @classmethod
    def _had_bootstrap_runtime_env_key(cls, key: str) -> bool:
        """判断受监控的键在启动时的进程环境变量中是否存在（无论是否被覆盖）。"""
        cls._capture_bootstrap_runtime_env_overrides()
        return key in cls._BOOTSTRAP_RUNTIME_ENV_PRESENT_KEYS

    @classmethod
    def _resolve_report_language_env_value(
        cls,
        preexisting_env_value: Optional[str],
    ) -> str:
        """解析 REPORT_LANGUAGE，同时保证真正的进程级覆盖不被 `.env` 静默改写。

        Args:
            preexisting_env_value: dotenv 加载前记录的 REPORT_LANGUAGE 值，
                为 None 表示进程启动时未设置该变量。
        """
        file_value = cls._get_env_file_value("REPORT_LANGUAGE")
        env_value = os.getenv("REPORT_LANGUAGE")

        if preexisting_env_value is not None:
            env_text = preexisting_env_value.strip()
            file_text = (file_value or "").strip()
            if file_text and env_text and env_text.lower() != file_text.lower():
                env_file = os.getenv("ENV_FILE") or str(Path(__file__).resolve().parents[2] / ".env")
                logging.getLogger(__name__).warning(
                    "REPORT_LANGUAGE environment value '%s' overrides %s ('%s')",
                    preexisting_env_value,
                    env_file,
                    file_value,
                )
            return preexisting_env_value

        if file_value is not None:
            return file_value

        return env_value or "zh"

    @classmethod
    def _parse_report_language(cls, value: Optional[str]) -> str:
        """解析 REPORT_LANGUAGE，非法值记警告后回退为 zh。"""
        normalized = normalize_report_language(value, default="zh")
        raw = (value or "").strip()
        if raw and not is_supported_report_language_value(raw):
            logging.getLogger(__name__).warning(
                "REPORT_LANGUAGE '%s' invalid, fallback to 'zh' (valid: zh/en)",
                value,
            )
        return normalized

    @classmethod
    def _parse_news_strategy_profile(cls, value: Optional[str]) -> str:
        """解析 NEWS_STRATEGY_PROFILE，非法值记警告后回退为 short。"""
        normalized = normalize_news_strategy_profile(value)
        raw = (value or "short").strip().lower()
        if raw != normalized:
            logging.getLogger(__name__).warning(
                "NEWS_STRATEGY_PROFILE '%s' invalid, fallback to 'short' "
                "(valid: ultra_short/short/medium/long)",
                value,
            )
        return normalized

    def get_effective_news_window_days(self) -> int:
        """返回策略档位与全局时效合并后的实际新闻窗口天数。"""
        return resolve_news_window_days(
            news_max_age_days=self.news_max_age_days,
            news_strategy_profile=self.news_strategy_profile,
        )

    @classmethod
    def _parse_market_review_region(cls, value: str) -> str:
        """解析大盘复盘市场区域，非法值记录警告后回退为 cn"""
        import logging
        v = (value or 'cn').strip().lower()
        if v in ('cn', 'us', 'hk', 'both'):
            return v
        logging.getLogger(__name__).warning(
            f"MARKET_REVIEW_REGION 配置值 '{value}' 无效，已回退为默认值 'cn'（合法值：cn / hk / us / both）"
        )
        return 'cn'

    @classmethod
    def _parse_market_review_color_scheme(cls, value: str) -> str:
        """解析大盘复盘涨跌幅配色方案，非法值记警告后回退为 green_up。"""
        import logging
        v = (value or 'green_up').strip().lower().replace('-', '_')
        if v in ('green_up', 'red_up'):
            return v
        logging.getLogger(__name__).warning(
            "MARKET_REVIEW_COLOR_SCHEME 配置值 '%s' 无效，已回退为默认值 'green_up'（合法值：green_up / red_up）",
            value,
        )
        return 'green_up'

    @classmethod
    def _parse_md2img_engine(cls, value: str) -> str:
        """解析 MD2IMG_ENGINE，非法值记警告后回退为 wkhtmltoimage（Issue #455）。"""
        v = (value or 'wkhtmltoimage').strip().lower()
        if v in ('wkhtmltoimage', 'markdown-to-file'):
            return v
        if v:
            import logging
            logging.getLogger(__name__).warning(
                f"MD2IMG_ENGINE '{value}' invalid, fallback to 'wkhtmltoimage' "
                "(valid: wkhtmltoimage | markdown-to-file)"
            )
        return 'wkhtmltoimage'

    @classmethod
    def _resolve_realtime_source_priority(cls) -> str:
        """
        解析实时行情数据源优先级，并在配置 Tushare 时自动注入 tushare。

        当配置了 TUSHARE_TOKEN 但未显式设置 REALTIME_SOURCE_PRIORITY 时，
        自动把 'tushare' 前置到默认优先级之前，让付费数据源也用于实时行情。
        """
        explicit = os.getenv('REALTIME_SOURCE_PRIORITY')
        default_priority = 'tencent,akshare_sina,efinance,akshare_em'

        if explicit:
            # 用户显式设置了优先级，直接采用
            return explicit

        tushare_token = os.getenv('TUSHARE_TOKEN', '').strip()
        if tushare_token:
            # 已配置 Token 但没有显式优先级覆盖
            # 把 tushare 前置，让付费数据源优先被尝试
            import logging
            logger = logging.getLogger(__name__)
            resolved = f'tushare,{default_priority}'
            logger.info(
                f"TUSHARE_TOKEN detected, auto-injecting tushare into realtime priority: {resolved}"
            )
            return resolved

        return default_priority

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（主要用于测试）"""
        cls._instance = None
        cls._BOOTSTRAP_RUNTIME_ENV_OVERRIDES_CAPTURED = False
        cls._BOOTSTRAP_RUNTIME_ENV_OVERRIDES = frozenset()
        cls._BOOTSTRAP_RUNTIME_ENV_PRESENT_KEYS = frozenset()

    def has_searxng_enabled(self) -> bool:
        """判断 SearXNG 兜底是否可用（自建实例或公共实例任一开启）。"""
        return bool(self.searxng_base_urls) or bool(self.searxng_public_instances_enabled)

    def has_search_capability_enabled(self) -> bool:
        """判断是否配置了任意搜索 provider，或启用了 SearXNG 兜底。"""
        return bool(
            self.anspire_api_keys
            or self.bocha_api_keys
            or self.minimax_api_keys
            or self.tavily_api_keys
            or self.brave_api_keys
            or self.serpapi_keys
            or self.has_searxng_enabled()
        )

    def is_agent_available(self) -> bool:
        """判断 Agent 能力是否可用。

        判定表：

        +-----------------------+----------------------------------+---------+
        | AGENT_MODE env        | effective Agent primary model set| Result  |
        +-----------------------+----------------------------------+---------+
        | ``true``              | any                              | True    |
        | ``false`` (explicit)  | any                              | False   |
        | not set (default)     | yes                              | True    |
        | not set (default)     | no                               | False   |
        +-----------------------+----------------------------------+---------+

        这样保持了向后兼容：从未设置 ``AGENT_MODE`` 的用户，一旦配置了
        Agent 可用模型就自动获得 Agent 能力；而 ``AGENT_MODE=false``
        始终作为显式的总开关。
        """
        # 显式设置 AGENT_MODE 时优先级最高
        if self._agent_mode_explicit:
            return self.agent_mode
        # 自动探测：AGENT_LITELLM_MODEL 为空时 Agent 继承全局模型
        return bool(get_effective_agent_primary_model(self))

    def refresh_stock_list(self) -> None:
        """
        热读取 STOCK_LIST 环境变量并更新配置中的自选股列表
        
        支持两种配置方式：
        1. .env 文件（本地开发、定时任务模式） - 修改后下次执行自动生效
        2. 系统环境变量（GitHub Actions、Docker） - 启动时固定，运行中不变
        """
        # 优先从 .env 文件读取最新配置，这样即使在容器环境中修改了 .env 文件，
        # 也能获取到最新的股票列表配置
        env_file = os.getenv("ENV_FILE")
        env_path = Path(env_file) if env_file else (Path(__file__).resolve().parents[2] / '.env')
        stock_list_str = ''
        if env_path.exists():
            # 直接从 .env 文件读取最新的配置
            env_values = dotenv_values(env_path)
            stock_list_str = (env_values.get('STOCK_LIST') or '').strip()

        # 如果 .env 文件不存在或未配置，才尝试从系统环境变量读取
        if not stock_list_str:
            stock_list_str = os.getenv('STOCK_LIST', '')

        stock_list = [
            (c or "").strip().upper()
            for c in stock_list_str.split(',')
            if (c or "").strip()
        ]

        if not stock_list:
            stock_list = ['000001']

        self.stock_list = stock_list
    
    def validate_structured(self) -> List[ConfigIssue]:
        """返回带严重级别的结构化配置校验问题列表。

        覆盖 PR #494 引入的三层 LLM 配置：
        - LITELLM_CONFIG (YAML)
        - LLM_CHANNELS (env)
        - 旧版各 provider 独立 Key

        Returns:
            ConfigIssue 列表，每项携带级别（"error" | "warning" | "info"）、
            可读描述文案，以及最相关的环境变量 / 字段名。
        """
        issues: List[ConfigIssue] = []

        # --- Stock list ---
        if not self.stock_list:
            issues.append(ConfigIssue(
                severity="error",
                message="未配置自选股列表 (STOCK_LIST)",
                field="STOCK_LIST",
            ))
        elif self.stock_email_groups:
            from data_provider.base import normalize_stock_code
            configured_stock_set = {
                normalize_stock_code(code)
                for code in self.stock_list
                if (code or "").strip()
            }
            missing_group_stocks_dict: Dict[str, None] = {}
            for stocks, _emails in self.stock_email_groups:
                for stock in stocks:
                    raw = (stock or "").strip()
                    if not raw:
                        continue
                    normalized_stock = normalize_stock_code(stock)
                    if normalized_stock in configured_stock_set:
                        continue
                    if normalized_stock in missing_group_stocks_dict:
                        continue
                    missing_group_stocks_dict[normalized_stock] = None
            missing_group_stocks = list(missing_group_stocks_dict.keys())
            if missing_group_stocks:
                issues.append(ConfigIssue(
                    severity="warning",
                    message=(
                        "检测到 STOCK_GROUP_N 中存在未包含在 STOCK_LIST 内的股票："
                        f"{', '.join(missing_group_stocks[:6])}。"
                        "STOCK_GROUP_N 仅用于邮件路由，不会扩大分析范围；"
                        "请先将这些股票加入 STOCK_LIST。"
                    ),
                    field="STOCK_GROUP_N",
                ))

        # --- Data sources (informational only) ---
        if not self.tushare_token:
            issues.append(ConfigIssue(
                severity="info",
                message="未配置 Tushare Token，将使用其他数据源",
                field="TUSHARE_TOKEN",
            ))

        # --- LLM availability ---
        # llm_model_list 由 YAML / 渠道配置填充。
        # 其它 LiteLLM 原生 provider（例如 cohere/*）走 litellm 环境变量直连，
        # 因此不会出现在 llm_model_list 中，需要单独判定。
        has_direct_env_model = bool(self.litellm_model) and _uses_direct_env_provider(self.litellm_model)
        if not self.llm_model_list and not has_direct_env_model:
            issues.append(ConfigIssue(
                severity="error",
                message=(
                    "未配置任何可用的 AI 模型接入（高级模型路由配置 / 渠道 / API Key），"
                    "AI 分析功能将不可用"
                ),
                field="LITELLM_CONFIG",
            ))
        elif not self.litellm_model:
            issues.append(ConfigIssue(
                severity="info",
                message=(
                    "尚未明确指定主模型。"
                    "建议尽早配置主模型（格式如 gemini/gemini-3.1-pro-preview）"
                ),
                field="LITELLM_MODEL",
            ))

        available_router_models = get_configured_llm_models(self.llm_model_list)
        available_router_model_set = set(available_router_models)

        def _has_runtime_source_for_model(model: str) -> bool:
            """判断模型是否存在可用的运行期来源（provider Key 或 Router 条目）。"""
            if not model or _uses_direct_env_provider(model):
                return True
            provider = _get_litellm_provider(model)
            if provider in {"gemini", "vertex_ai"}:
                return any(k and len(k) >= 8 for k in (self.gemini_api_keys or []))
            if provider == "anthropic":
                return any(k and len(k) >= 8 for k in (self.anthropic_api_keys or []))
            if provider == "deepseek":
                return any(k and len(k) >= 8 for k in (self.deepseek_api_keys or []))
            if provider == "openai":
                return any(k and len(k) >= 8 for k in (self.openai_api_keys or []))
            return False

        configured_agent_primary_model = bool((self.agent_litellm_model or "").strip())
        effective_agent_primary_model = get_effective_agent_primary_model(self)

        if available_router_model_set:
            if (
                self.litellm_model
                and not _uses_direct_env_provider(self.litellm_model)
                and self.litellm_model not in available_router_model_set
            ):
                issues.append(ConfigIssue(
                    severity="error",
                    message=(
                        "已配置的主模型未出现在当前渠道或高级模型路由配置中。"
                        f" 当前可用模型：{', '.join(available_router_models[:6])}"
                    ),
                    field="LITELLM_MODEL",
                ))

            if (
                configured_agent_primary_model
                and effective_agent_primary_model
                and not _uses_direct_env_provider(effective_agent_primary_model)
                and effective_agent_primary_model not in available_router_model_set
            ):
                issues.append(ConfigIssue(
                    severity="error",
                    message=(
                        "已配置的 Agent 主模型未出现在当前渠道或高级模型路由配置中。"
                        f" 当前可用模型：{', '.join(available_router_models[:6])}"
                    ),
                    field="AGENT_LITELLM_MODEL",
                ))

            invalid_fallbacks = [
                model for model in (self.litellm_fallback_models or [])
                if model and model not in available_router_model_set
                and not _uses_direct_env_provider(model)
            ]
            if invalid_fallbacks:
                issues.append(ConfigIssue(
                    severity="warning",
                    message=(
                        "备选模型中包含未在当前渠道或高级模型路由配置中声明的模型："
                        f"{', '.join(invalid_fallbacks[:3])}"
                    ),
                    field="LITELLM_FALLBACK_MODELS",
                ))

            if (
                self.vision_model
                and not _uses_direct_env_provider(self.vision_model)
                and self.vision_model not in available_router_model_set
            ):
                issues.append(ConfigIssue(
                    severity="warning",
                    message=(
                        "VISION_MODEL 未出现在当前渠道声明中。"
                        f" 当前可用模型：{', '.join(available_router_models[:6])}"
                    ),
                    field="VISION_MODEL",
                ))
        elif (
            configured_agent_primary_model
            and effective_agent_primary_model
            and not _has_runtime_source_for_model(effective_agent_primary_model)
        ):
            issues.append(ConfigIssue(
                severity="error",
                message=(
                    "已配置 Agent 主模型，但未找到可用的运行时来源"
                    "（启用渠道或匹配的 API Key）。"
                ),
                field="AGENT_LITELLM_MODEL",
            ))

        # --- Search engine (informational only) ---
        if not self.has_search_capability_enabled():
            issues.append(ConfigIssue(
                severity="info",
                message="未配置搜索引擎能力 (Bocha/MiniMax/Tavily/Brave/SerpAPI/SearXNG)，新闻搜索功能将不可用",
                field="BOCHA_API_KEYS",
            ))

        # --- Notification channels ---
        has_notification = bool(
            self.wechat_webhook_url
            or self.feishu_webhook_url
            or (self.telegram_bot_token and self.telegram_chat_id)
            or (self.email_sender and self.email_password)
            or (self.pushover_user_key and self.pushover_api_token)
            or _has_ntfy_topic_endpoint(self.ntfy_url)
            or (
                self.gotify_url
                and (self.gotify_token or "").strip()
                and _has_gotify_base_url(self.gotify_url)
            )
            or self.pushplus_token
            or self.serverchan3_sendkey
            or self.custom_webhook_urls
            or self.astrbot_url
            or (self.discord_bot_token and self.discord_main_channel_id)
            or self.discord_webhook_url
            or self.slack_webhook_url
            or (self.slack_bot_token and self.slack_channel_id)
        )

        if not has_notification:
            issues.append(ConfigIssue(
                severity="warning",
                message="未配置通知渠道，将不发送推送通知",
                field="WECHAT_WEBHOOK_URL",
            ))

        if self.ntfy_url and not _has_ntfy_topic_endpoint(self.ntfy_url):
            issues.append(ConfigIssue(
                severity="error",
                message="NTFY_URL 必须包含 topic path，例如 https://ntfy.sh/my-topic",
                field="NTFY_URL",
            ))

        if self.gotify_url and not _has_gotify_base_url(self.gotify_url):
            issues.append(ConfigIssue(
                severity="error",
                message="GOTIFY_URL 必须是 Gotify server base URL，不包含 /message，例如 https://gotify.example",
                field="GOTIFY_URL",
            ))

        if (
            self.gotify_url
            and _has_gotify_base_url(self.gotify_url)
            and not (self.gotify_token or "").strip()
        ):
            issues.append(ConfigIssue(
                severity="warning",
                message="已配置 GOTIFY_URL，但缺少 GOTIFY_TOKEN，Gotify 渠道不会启用",
                field="GOTIFY_TOKEN",
            ))

        if self.notification_quiet_hours:
            try:
                parse_notification_quiet_hours(self.notification_quiet_hours)
            except ValueError as exc:
                issues.append(ConfigIssue(
                    severity="error",
                    message=f"通知静默时段配置无效：{exc}",
                    field="NOTIFICATION_QUIET_HOURS",
                ))

        if self.notification_timezone:
            try:
                validate_notification_timezone(self.notification_timezone)
            except ValueError as exc:
                issues.append(ConfigIssue(
                    severity="error",
                    message=f"通知时区配置无效：{exc}",
                    field="NOTIFICATION_TIMEZONE",
                ))

        if self.notification_min_severity and not is_supported_notification_severity(self.notification_min_severity):
            issues.append(ConfigIssue(
                severity="error",
                message=(
                    "通知最低级别配置无效，允许值："
                    f"{', '.join(NOTIFICATION_SEVERITIES)}"
                ),
                field="NOTIFICATION_MIN_SEVERITY",
            ))

        if self.notification_daily_digest_enabled:
            issues.append(ConfigIssue(
                severity="warning",
                message=(
                    "NOTIFICATION_DAILY_DIGEST_ENABLED 当前为预留配置；"
                    "P4 不会发送每日摘要或持久化摘要内容。"
                ),
                field="NOTIFICATION_DAILY_DIGEST_ENABLED",
            ))

        has_feishu_app_id = bool((self.feishu_app_id or "").strip())
        has_feishu_app_secret = bool((self.feishu_app_secret or "").strip())
        has_feishu_app_credentials = has_feishu_app_id or has_feishu_app_secret
        has_feishu_doc_token = bool((self.feishu_folder_token or "").strip())
        has_feishu_full_cloud_doc_credentials = (
            has_feishu_app_id
            and has_feishu_app_secret
            and has_feishu_doc_token
        )
        has_feishu_file_bot_credentials = (
            has_feishu_app_id
            and has_feishu_app_secret
            and bool((self.feishu_chat_id or "").strip())
        )
        if (
            has_feishu_app_credentials
            and not has_feishu_full_cloud_doc_credentials
            and not has_feishu_file_bot_credentials
            and not self.feishu_webhook_url
            and not (self.feishu_stream_enabled and has_feishu_app_id and has_feishu_app_secret)
        ):
            issues.append(ConfigIssue(
                severity="warning",
                message=(
                    "仅配置 FEISHU_APP_ID / FEISHU_APP_SECRET 不会开启飞书群 Webhook 推送；"
                    "如需群消息通知，请配置 FEISHU_WEBHOOK_URL。若要使用应用机器人，请同时开启 "
                    "FEISHU_STREAM_ENABLED 并完成应用发布与权限配置。"
                ),
                field="FEISHU_WEBHOOK_URL",
            ))

        # --- Deprecated field migration hints ---
        if os.getenv("OPENAI_VISION_MODEL"):
            issues.append(ConfigIssue(
                severity="info",
                message=(
                    "OPENAI_VISION_MODEL 已废弃，请改用 VISION_MODEL。"
                    "当前值已自动迁移，建议更新配置文件以消除此提示。"
                ),
                field="OPENAI_VISION_MODEL",
            ))

        # --- Vision Key 可用性 ---
        # 仅在用户显式配置 VISION_MODEL（或别名 OPENAI_VISION_MODEL）时才告警。
        # vision_model 为空说明并未有意启用 Vision，跳过检查。
        if self.vision_model:
            # provider 前缀 → Config 中维护的对应 Key 列表。
            # vertex_ai 与 gemini 共用同一份 Key；其它 LiteLLM 原生 provider 不在该映射中
            # （其 Key 来自环境变量，此处无法检查）。
            _VISION_KEY_MAP = {
                "gemini": self.gemini_api_keys,
                "vertex_ai": self.gemini_api_keys,
                "anthropic": self.anthropic_api_keys,
                "openai": self.openai_api_keys,
                "deepseek": self.deepseek_api_keys,
            }
            # 推导主模型的 provider 前缀，即使该 provider 未出现在
            # VISION_PROVIDER_PRIORITY 中，也要一并检查其 Key。
            _primary_prefix = (
                self.vision_model.split("/")[0]
                if "/" in self.vision_model
                else "openai"
            )
            _priority_providers = [
                p.strip().lower()
                for p in self.vision_provider_priority.split(",")
                if p.strip()
            ]
            # 取并集：回退 provider + 主模型自身的 provider
            _all_providers = {_primary_prefix} | set(_priority_providers)

            # 与 get_api_keys_for_model 保持一致：Key 非空且长度 >= 8 才算有效
            _has_any_key = any(
                any(k and len(k) >= 8 for k in (_VISION_KEY_MAP.get(p) or []))
                for p in _all_providers
                if p in _VISION_KEY_MAP
            )
            if not _has_any_key:
                for entry in self.llm_model_list or []:
                    if not isinstance(entry, dict):
                        continue
                    params = entry.get("litellm_params") or {}
                    if not isinstance(params, dict):
                        continue
                    candidates = {
                        str(entry.get("model_name") or "").strip(),
                        str(params.get("model") or "").strip(),
                    }
                    if self.vision_model not in candidates:
                        continue
                    api_key = str(params.get("api_key") or "").strip()
                    api_base = str(params.get("api_base") or params.get("base_url") or "").strip()
                    if len(api_key) >= 8 or channel_allows_empty_api_key(_primary_prefix, api_base):
                        _has_any_key = True
                        break
            if not _has_any_key:
                _checked = sorted(_all_providers & _VISION_KEY_MAP.keys())
                issues.append(ConfigIssue(
                    severity="warning",
                    message=(
                        "VISION_MODEL 已配置，但未找到可用的 Vision API Key "
                        f"（已检查：{', '.join(_checked)}）。"
                        "图片股票代码提取功能将不可用，请配置对应的 API Key。"
                    ),
                    field="VISION_MODEL",
                ))

        return issues

    def validate(self) -> List[str]:
        """以纯字符串形式返回校验信息（向后兼容接口）。

        内部委托 validate_structured()，只需要可读文案的调用方无需改动。

        Returns:
            每个 ConfigIssue 的 message 组成的字符串列表。
        """
        return [issue.message for issue in self.validate_structured()]
    
    def get_db_url(self) -> str:
        """
        获取 SQLAlchemy 数据库连接 URL

        优先使用 DATABASE_URL（支持 MySQL/PostgreSQL 等），
        未设置时退回 DATABASE_PATH 指定的 SQLite 文件。
        """
        if self.database_url:
            return self.database_url
        db_path = Path(self.database_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{db_path.absolute()}"


# === 便捷的配置访问函数 ===
def get_config() -> Config:
    """获取全局配置实例的快捷方式"""
    return Config.get_instance()


# ============================================================
# 共用的 LLM 辅助函数（analyzer 与 agent/llm_adapter 都会使用）
# ============================================================

def get_api_keys_for_model(model: str, config: Config) -> List[str]:
    """返回指定 litellm 模型对应的托管 API Key 列表（仅旧版直连路径使用）。

    llm_model_list 非空（渠道 / YAML）时由 Router 负责选 Key，无需调用本函数；
    保留它是为了兼容未构建 Router、需要直接 litellm.completion() 的场景。
    """
    provider = _get_litellm_provider(model)
    if provider in {"gemini", "vertex_ai"}:
        return [k for k in config.gemini_api_keys if k and len(k) >= 8]
    if provider == "anthropic":
        return [k for k in config.anthropic_api_keys if k and len(k) >= 8]
    if provider == "deepseek":
        return [k for k in config.deepseek_api_keys if k and len(k) >= 8]
    if provider == "openai":
        return [k for k in config.openai_api_keys if k and len(k) >= 8]
    # 其它 LiteLLM 原生 provider 的 API Key 由环境变量解析，这里不返回
    return []


def extra_litellm_params(model: str, config: Config) -> Dict[str, Any]:
    """构造模型的 litellm 附加参数（仅旧版直连路径使用）。

    llm_model_list 非空时，Router 已按 deployment 携带 api_base 与请求头，
    因此不会调用本函数。
    """
    params: Dict[str, Any] = {}
    # deepseek/ provider：litellm 会自动解析 api_base，无需手动覆盖
    if model.startswith("deepseek/"):
        return params
    if model.startswith("openai/") or "/" not in model:
        if config.openai_base_url:
            params["api_base"] = config.openai_base_url
        if config.openai_base_url and "aihubmix.com" in config.openai_base_url:
            params["extra_headers"] = {"APP-Code": "GPIJ3886"}
    return params


if __name__ == "__main__":
    # 测试配置加载
    config = get_config()
    print("=== 配置加载测试 ===")
    print(f"自选股列表: {config.stock_list}")
    print(f"数据库路径: {config.database_path}")
    print(f"最大并发数: {config.max_workers}")
    print(f"调试模式: {config.debug}")
    
    # 验证配置
    warnings = config.validate()
    if warnings:
        print("\n配置验证结果:")
        for w in warnings:
            print(f"  - {w}")
