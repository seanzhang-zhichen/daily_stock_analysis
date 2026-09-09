# -*- coding: utf-8 -*-
"""
多 Provider LLM 工具调用适配器。

通过 LiteLLM 把各家 Provider 的 function-calling / tool-use
归一化为 AgentExecutor 消费的统一接口。
"""

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import litellm
from litellm import Router

from src.config import (
    extra_litellm_params,
    get_api_keys_for_model,
    get_config,
    get_configured_llm_models,
    get_effective_agent_models_to_try,
    get_effective_agent_primary_model,
)
from src.llm.errors import call_litellm_with_param_recovery
from src.llm.generation_params import apply_litellm_generation_params
from src.storage import AppUser, get_db
from src.users.model_router import ModelRoute, resolve_model_route

logger = logging.getLogger(__name__)


def _resolve_litellm_exception(name: str) -> type[BaseException]:
    """返回可捕获的 LiteLLM 异常类，即便在桩测试环境下也能生效。"""
    exc = getattr(litellm, name, None)
    if isinstance(exc, type) and issubclass(exc, BaseException):
        return exc

    class _FallbackLiteLLMError(Exception):
        """LiteLLM 桩缺少具名错误类时使用的兜底异常。"""

        pass

    _FallbackLiteLLMError.__name__ = f"Fallback{name}"
    return _FallbackLiteLLMError


# ============================================================
# 统一响应类型
# ============================================================

@dataclass
class ToolCall:
    """LLM 请求的单次工具调用。"""
    id: str
    name: str
    arguments: Dict[str, Any]
    thought_signature: Optional[str] = None


@dataclass
class LLMResponse:
    """任意 LLM 提供商的归一化响应。"""
    content: Optional[str] = None          # 文本响应（最终答案）
    tool_calls: List[ToolCall] = field(default_factory=list)  # 需要执行的工具调用
    reasoning_content: Optional[str] = None  # DeepSeek 思考模式返回的思维链（CoT）；多轮对话中必须回传给 assistant 消息；其他提供商为 None
    usage: Dict[str, Any] = field(default_factory=dict)       # token 用量信息
    provider: str = ""                     # 处理本次调用的提供商
    model: str = ""                        # 实际使用的完整模型名（例如 gemini/gemini-2.0-flash），用于报告元信息
    raw: Any = None                        # 原始提供商响应，用于调试


# 自动返回 reasoning_content 的模型；不要发送 extra_body（可能触发 400）。
_AUTO_THINKING_MODELS: List[str] = ["deepseek-reasoner", "deepseek-r1", "qwq"]

# 需要通过 extra_body 显式开启的模型；载荷与模型名解耦。
_OPT_IN_THINKING_MODELS: Dict[str, dict] = {
    "deepseek-chat": {"thinking": {"type": "enabled"}},
}

# 不在 LiteLLM 内置价格表中的自定义模型定价
# MiniMax 官方定价：https://platform.minimax.io/docs/guides/pricing-paygo
# - MiniMax-M2.7 / M2.5：$0.3/M 输入 token，$1.2/M 输出 token
_CUSTOM_MODEL_PRICING: Dict[str, dict] = {
    "MiniMax-M2.7": {
        "supports_function_calling": True,
        "supports_vision": False,
        "supports_audio_input": False,
        "supports_audio_output": False,
        "context_window": 100000,
        "max_tokens": 10000,
        "input_cost_per_token": 0.0000003,   # $0.3 / 1M token
        "output_cost_per_token": 0.0000012,   # $1.2 / 1M token
    },
    "MiniMax-M2.5": {
        "supports_function_calling": True,
        "supports_vision": False,
        "supports_audio_input": False,
        "supports_audio_output": False,
        "context_window": 100000,
        "max_tokens": 10000,
        "input_cost_per_token": 0.0000003,   # $0.3 / 1M token
        "output_cost_per_token": 0.0000012,   # $1.2 / 1M token
    },
}


def _model_matches(model: str, entries: List[str]) -> bool:
    """检查模型名是否匹配任一入口（精确匹配或带版本后缀的前缀匹配）。"""
    if not model:
        return False
    m = model.lower().strip()
    for e in entries:
        if m == e or m.startswith(e + "-"):
            return True
    return False


def _get_opt_in_payload(model: str, opt_in: Dict[str, dict]) -> Optional[dict]:
    """返回需显式开启思考模型的 extra_body 载荷，未命中则返回 None。"""
    if not model:
        return None
    m = model.lower().strip()
    for key, payload in opt_in.items():
        if m == key or m.startswith(key + "-"):
            return payload
    return None


def get_thinking_extra_body(model: str) -> Optional[dict]:
    """返回思考模式所需的 extra_body，否则返回 None。

    - 自动思考模型（_AUTO_THINKING_MODELS：deepseek-reasoner、deepseek-r1、qwq）：
      这些模型在 API 响应中自动返回 reasoning_content；再发送 extra_body 会导致 400，
      因为 API 默认已开启思考。返回 None 以避免重复激活。
    - 需显式开启的模型（_OPT_IN_THINKING_MODELS：deepseek-chat）：返回激活载荷以显式开启思考模式。
    - 其他模型：返回 None（无思考模式）。
    """
    if _model_matches(model, _AUTO_THINKING_MODELS):
        return None
    return _get_opt_in_payload(model, _OPT_IN_THINKING_MODELS)


# ============================================================
# LLM 工具适配器
# ============================================================

class LLMToolAdapter:
    """通过 LiteLLM 进行工具调用的统一适配器。

    借助单一的 litellm.completion() 接口支持所有提供商（Gemini、Anthropic、
    OpenAI、DeepSeek 等），并可选使用 Router 实现多 key 负载均衡。
    """

    def __init__(self, config=None, user_id: Optional[int] = None):
        """初始化 LiteLLM 路由，应用平台默认值与可选的用户模型偏好。"""
        config = config or get_config()
        self._config = config
        self._user_id = user_id
        self._router = None          # litellm Router（主模型多 key）
        self._direct_router_model_list: List[Dict[str, Any]] = []
        self._litellm_available = False
        self._register_custom_model_pricing()
        self._init_litellm()

    def _resolve_user_model_route(self, models_to_try: List[str]) -> Optional[ModelRoute]:
        """从数据库解析按用户粒度的模型限制/偏好。"""
        user_id = getattr(self, "_user_id", None)
        if not user_id:
            return None
        try:
            with get_db().session_scope() as session:
                user = (
                    session.query(AppUser)
                    .filter(AppUser.id == int(user_id), AppUser.status == "active")
                    .first()
                )
                if user is None:
                    return None
                return resolve_model_route(
                    session,
                    user=user,
                    config=self._config,
                    platform_primary_model=models_to_try[0] if models_to_try else get_effective_agent_primary_model(self._config),
                    platform_models=models_to_try,
                )
        except Exception as exc:
            logger.warning("Agent user model route resolution failed for user_id=%s: %s", user_id, exc)
            return None

    @staticmethod
    def _register_custom_model_pricing() -> None:
        """为不在 LiteLLM 内置价格表中的模型注册自定义定价。

        这能避免 MiniMax-M2.7 及类似模型出现成本计算错误。
        """
        for model_name, pricing in _CUSTOM_MODEL_PRICING.items():
            try:
                litellm.register_model(
                    {
                        model_name: pricing
                    }
                )
                logger.debug(f"Registered custom pricing for {model_name}")
            except Exception as e:
                logger.debug(f"Model {model_name} may already be registered or pricing error: {e}")

    def _has_channel_config(self) -> bool:
        """检查多通道配置（channels / YAML）是否生效。"""
        return bool(self._config.llm_model_list)

    def _init_litellm(self) -> None:
        """从 channels / YAML 或显式直连 key 初始化 litellm Router。"""
        config = self._config
        self._direct_router_model_list = []
        litellm_model = get_effective_agent_primary_model(config)
        if not litellm_model:
            logger.warning("Agent LLM: no effective primary model configured")
            return

        self._litellm_available = True

        # --- Channel / YAML path ---
        if self._has_channel_config():
            model_list = config.llm_model_list
            self._router = Router(
                model_list=model_list,
                routing_strategy="simple-shuffle",
                num_retries=2,
            )
            unique_models = list(dict.fromkeys(
                e['litellm_params']['model'] for e in model_list
            ))
            logger.info(
                f"Agent LLM: Router initialized from channels/YAML — "
                f"{len(model_list)} deployment(s), models: {unique_models}"
            )
            return

        # --- 直连 key 路径 ---
        keys = get_api_keys_for_model(litellm_model, config)
        if not keys:
            logger.info(
                f"Agent LLM: litellm initialized (model={litellm_model}, "
                f"API key from environment)"
            )
            return

        if len(keys) > 1:
            ep = extra_litellm_params(litellm_model, config)
            direct_model_list = [
                {
                    "model_name": litellm_model,
                    "litellm_params": {
                        "model": litellm_model,
                        "api_key": k,
                        **ep,
                    },
                }
                for k in keys
            ]
            self._direct_router_model_list = direct_model_list
            self._router = Router(
                model_list=direct_model_list,
                routing_strategy="simple-shuffle",
                num_retries=2,
            )
            logger.info(
                f"Agent LLM: Router initialized with {len(keys)} direct keys "
                f"for {litellm_model}"
            )
        else:
            logger.info(f"Agent LLM: litellm initialized (model={litellm_model})")

    @property
    def is_available(self) -> bool:
        """litellm 已配置且至少存在一个 API key 时为 True。"""
        return self._router is not None or self._litellm_available

    @property
    def primary_provider(self) -> str:
        """从 litellm_model 前缀提取的提供商名称。"""
        model = get_effective_agent_primary_model(self._config)
        if "/" in model:
            return model.split("/")[0]
        return model or "none"

    # ============================================================
    # 统一调用入口
    # ============================================================

    def call_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[dict],
        provider: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> LLMResponse:
        """将消息与工具声明发送给 LLM，返回归一化响应。

        Args:
            messages: 提供商无关格式的对话历史：
                      [{"role": "system"/"user"/"assistant"/"tool", "content": ...}, ...]
            tools: OpenAI 格式的工具声明；litellm 会转换为各提供商的格式。
            provider: 已忽略（仅为向后兼容保留）。

        Returns:
            LLMResponse，含 content（最终答案）或 tool_calls 之一。
        """
        return self.call_completion(messages, tools=tools, provider=provider, timeout=timeout)

    def call_text(
        self,
        messages: List[Dict[str, Any]],
        *,
        provider: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> LLMResponse:
        """通过共享路由栈发送纯文本补全。"""
        return self.call_completion(
            messages,
            tools=None,
            provider=provider,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    def call_completion(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[dict]] = None,
        provider: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> LLMResponse:
        """工具调用与纯文本调用共用的补全路径。"""
        config = self._config
        models_to_try = get_effective_agent_models_to_try(config)
        model_route = self._resolve_user_model_route(models_to_try)
        if model_route is not None:
            models_to_try = model_route.models_to_try
        if not models_to_try:
            error_msg = (
                "No LLM configured or allowed for the current user plan. "
                "Please set LITELLM_MODEL/LLM_CHANNELS or adjust plan allowed_models."
            )
            logger.error(error_msg)
            return LLMResponse(content=error_msg, provider="error")
        started_at = time.time()
        providers = [self._get_model_provider(model) for model in models_to_try]

        last_error = None
        hit_rate_limit = False
        for idx, model in enumerate(models_to_try):
            remaining_timeout = timeout
            if timeout is not None and timeout > 0:
                remaining_timeout = max(0.0, float(timeout) - (time.time() - started_at))
                if remaining_timeout <= 0:
                    last_error = TimeoutError(
                        f"LLM completion timed out before trying fallback model {model}"
                    )
                    break
            try:
                return self._call_litellm_model(
                    messages,
                    tools or [],
                    model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=remaining_timeout,
                    model_route=model_route,
                )
            except Exception as e:
                if isinstance(e, _resolve_litellm_exception("RateLimitError")):
                    logger.warning("Agent LLM rate-limited on %s: %s", model, e)
                    last_error = e
                    hit_rate_limit = True

                    # 避免跨提供商盲目退避；跨提供商回退通常意味着不同账户/限流桶。
                    should_backoff = (
                        idx + 1 < len(models_to_try)
                        and providers[idx] == providers[idx + 1]
                    )
                    if should_backoff:
                        backoff_sleep = min(2.0, (time.time() - started_at) * 0.1 + 0.5)
                        if timeout is not None and timeout > 0:
                            remaining_timeout = max(0.0, float(timeout) - (time.time() - started_at))
                            if remaining_timeout > 0:
                                time.sleep(min(backoff_sleep, remaining_timeout))
                        else:
                            time.sleep(backoff_sleep)
                    continue
                if isinstance(e, _resolve_litellm_exception("ContextWindowExceededError")):
                    logger.warning("Agent LLM context window exceeded on %s: %s", model, e)
                    last_error = e
                    continue
                logger.warning("Agent LLM call failed with %s: %s", model, e)
                last_error = e
                continue

        suffix = " (rate-limit encountered during fallback)" if hit_rate_limit else ""
        error_msg = f"All LLM models failed{suffix}. Last error: {last_error}"
        logger.error(error_msg)
        return LLMResponse(content=error_msg, provider="error")

    @staticmethod
    def _get_model_provider(model: str) -> str:
        """返回 LiteLLM 提供商命名空间，用于模型回退分组。"""
        if "/" in model:
            return model.split("/", 1)[0]
        return "openai"

    def _call_litellm_model(
        self,
        messages: List[Dict[str, Any]],
        tools: List[dict],
        model: str,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        model_route: Optional[ModelRoute] = None,
    ) -> LLMResponse:
        """使用 OpenAI 格式的消息与工具调用指定的 litellm 模型。"""
        openai_messages = self._convert_messages(messages)

        # 用短模型名（去掉提供商前缀）查找思考模型
        model_short = model.split("/")[-1] if "/" in model else model
        extra = get_thinking_extra_body(model_short)

        call_kwargs: Dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
        }
        if max_tokens is not None:
            call_kwargs["max_tokens"] = max_tokens
        if timeout is not None:
            call_kwargs["timeout"] = timeout

        if extra:
            call_kwargs["extra_body"] = extra

        if tools:
            call_kwargs["tools"] = tools

        # 主模型（多 key）使用 Router，其他模型使用直接 litellm 调用
        use_channel_router = self._has_channel_config()
        _router_model_names = set(get_configured_llm_models(self._config.llm_model_list))
        agent_primary_model = get_effective_agent_primary_model(self._config)
        uses_router = (
            bool(use_channel_router and self._router and model in _router_model_names)
            or bool(
                self._router
                and model == agent_primary_model
                and not use_channel_router
            )
        )
        recovery_model_list = self._config.llm_model_list
        if self._router and model == agent_primary_model and not use_channel_router:
            recovery_model_list = self._direct_router_model_list or self._config.llm_model_list
        if not uses_router:
            keys = get_api_keys_for_model(model, self._config)
            if keys:
                call_kwargs["api_key"] = keys[0]
            call_kwargs.update(extra_litellm_params(model, self._config))
        call_kwargs = apply_litellm_generation_params(
            call_kwargs,
            model,
            self._get_temperature() if temperature is None else temperature,
            model_list=recovery_model_list,
        )
        if use_channel_router and self._router and model in _router_model_names:
            # Channel / YAML 路径：Router 管理其 model_list 中的所有模型
            response = call_litellm_with_param_recovery(
                lambda kwargs: self._router.completion(**kwargs),
                model=model,
                call_kwargs=call_kwargs,
                model_list=recovery_model_list,
                logger=logger,
            )
        elif (
            self._router
            and model == agent_primary_model
            and not use_channel_router
        ):
            # 直连 key 路径：Router 承载主模型的多 key
            response = call_litellm_with_param_recovery(
                lambda kwargs: self._router.completion(**kwargs),
                model=model,
                call_kwargs=call_kwargs,
                model_list=recovery_model_list,
                logger=logger,
            )
        else:
            # 直接调用路径（也处理 groq/、bedrock/ 这类不在 Router
            # model_list 中的直连环境提供商，即便处于 channel 模式下也一样）
            response = call_litellm_with_param_recovery(
                lambda kwargs: litellm.completion(**kwargs),
                model=model,
                call_kwargs=call_kwargs,
                model_list=recovery_model_list,
                logger=logger,
            )

        return self._parse_litellm_response(response, model)

    def _get_temperature(self) -> float:
        """返回逐模型归一化之前的原始配置温度。"""
        return float(self._config.llm_temperature)

    def _convert_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将内部消息格式转换为 litellm 可用的 OpenAI 兼容格式。"""
        openai_messages: List[Dict[str, Any]] = []
        for msg in messages:
            if msg["role"] == "tool":
                openai_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id", ""),
                    "content": msg["content"] if isinstance(msg["content"], str) else json.dumps(msg["content"]),
                })
            elif msg["role"] == "assistant" and msg.get("tool_calls"):
                openai_tc = []
                for tc in msg["tool_calls"]:
                    tc_dict: Dict[str, Any] = {
                        "id": tc.get("id", str(uuid.uuid4())[:8]),
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"]),
                        },
                    }
                    sig = tc.get("thought_signature")
                    if sig is not None:
                        tc_dict["provider_specific_fields"] = {"thought_signature": sig}
                    openai_tc.append(tc_dict)
                openai_msg: Dict[str, Any] = {
                    "role": "assistant",
                    "content": msg.get("content"),
                    "tool_calls": openai_tc,
                }
                if msg.get("reasoning_content") is not None:
                    openai_msg["reasoning_content"] = msg["reasoning_content"]
                openai_messages.append(openai_msg)
            else:
                openai_messages.append({
                    "role": msg["role"],
                    "content": msg["content"],
                })
        return openai_messages

    def _parse_litellm_response(self, response: Any, model: str) -> LLMResponse:
        """将 litellm 的 OpenAI 兼容响应解析为 LLMResponse。"""
        choice = response.choices[0]
        tool_calls: List[ToolCall] = []

        # 处理 MiniMax 特有的 content_blocks 格式
        # MiniMax-M2.7 可能把 content_blocks 放在 choice 层级或 message 内部
        # 两处位置都要检查以保证一致性
        # 拼接所有文本块，避免截断多块响应
        text_content = choice.message.content
        if text_content is None:
            content_blocks = None
            if hasattr(choice, "content_blocks"):
                content_blocks = choice.content_blocks
            elif hasattr(choice.message, "content_blocks"):
                content_blocks = choice.message.content_blocks

            if content_blocks:
                # MiniMax 响应格式：content_blocks[].text
                # 拼接所有文本块以保留完整响应
                text_parts = []
                for block in content_blocks:
                    if getattr(block, "type", None) == "text":
                        text = getattr(block, "text", "") or ""
                        if text:
                            text_parts.append(text)
                    elif hasattr(block, "content") and block.content:
                        text_parts.append(block.content)
                text_content = "".join(text_parts).strip()

        # DeepSeek/Qwen 思考模式；不在标准 OpenAI 类型中，需通过 getattr 访问
        reasoning_content = getattr(choice.message, "reasoning_content", None)

        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                args: Dict[str, Any] = {}
                if tc.function.arguments:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {"raw": tc.function.arguments}

                # 提取 thought_signature：存放在 provider_specific_fields（Gemini 3 经 LiteLLM 代理）
                psf = getattr(tc, "provider_specific_fields", None)
                if psf is not None:
                    sig = psf.get("thought_signature") if isinstance(psf, dict) else getattr(psf, "thought_signature", None)
                else:
                    func_psf = getattr(tc.function, "provider_specific_fields", None)
                    if func_psf is not None:
                        sig = func_psf.get("thought_signature") if isinstance(func_psf, dict) else getattr(func_psf, "thought_signature", None)
                    else:
                        sig = getattr(tc, "thought_signature", None)

                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                    thought_signature=sig,
                ))

        usage: Dict[str, Any] = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        provider_name = model.split("/")[0] if "/" in model else model
        return LLMResponse(
            content=text_content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
            usage=usage,
            provider=provider_name,
            model=model,
            raw=response,
        )
