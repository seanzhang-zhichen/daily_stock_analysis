"""可复用的市场复盘运行时装配辅助函数。

把 analyzer/search/notification 的构造集中起来，让 API、CLI、scheduler 和 Bot
入口共享同一套大盘复盘的初始化路径。把运行时装配放在这里，可避免不同入口在
可选的搜索/LLM 行为上出现细微差异。
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

from src.config import Config

logger = logging.getLogger(__name__)


def has_configured_llm_runtime(config: Config) -> bool:
    """判断是否存在任何可用的 LLM 模型配置。

    分析器既可通过 LiteLLM 配置，也可通过渠道列表或遗留的 provider 专属 API key
    进行配置。此检查刻意保持宽泛，以便在未配置任何模型时，市场复盘仍能以纯模板
    方式运行。
    """
    if (getattr(config, "litellm_model", "") or "").strip():
        return True
    if getattr(config, "llm_model_list", None):
        return True

    for field in (
        "gemini_api_key",
        "gemini_api_keys",
        "anthropic_api_key",
        "anthropic_api_keys",
        "deepseek_api_key",
        "deepseek_api_keys",
        "openai_api_key",
        "openai_api_keys",
    ):
        value = getattr(config, field, None)
        if isinstance(value, str):
            if value.strip():
                return True
        elif value:
            return True

    return False


def build_market_review_runtime(
    config: Config,
    source_message: Optional[Any] = None,
) -> Tuple[Any, Any, Any]:
    """构建共享的 NotificationService、GeminiAnalyzer 与 SearchService 实例。

    搜索与 LLM 都是可选依赖。仅当配置报告具备搜索能力时才创建搜索服务；当不存在
    LLM 运行时，或分析器虽初始化但不可用时，跳过分析器的创建。
    """
    from src.analyzer import GeminiAnalyzer
    from src.notification import NotificationService
    from src.search_service import SearchService

    notifier = NotificationService(source_message=source_message)

    search_service = None
    has_search_capability = getattr(config, "has_search_capability_enabled", None)
    if callable(has_search_capability) and has_search_capability():
        # SearchService itself handles provider key rotation and per-provider
        # fallback; this helper only decides whether search should participate.
        search_service = SearchService(
            bocha_keys=getattr(config, "bocha_api_keys", None),
            tavily_keys=getattr(config, "tavily_api_keys", None),
            anspire_keys=getattr(config, "anspire_api_keys", None),
            brave_keys=getattr(config, "brave_api_keys", None),
            serpapi_keys=getattr(config, "serpapi_keys", None),
            minimax_keys=getattr(config, "minimax_api_keys", None),
            searxng_base_urls=getattr(config, "searxng_base_urls", None),
            searxng_public_instances_enabled=getattr(
                config,
                "searxng_public_instances_enabled",
                True,
            ),
            news_max_age_days=getattr(config, "news_max_age_days", 3),
            news_strategy_profile=getattr(config, "news_strategy_profile", "short"),
        )

    analyzer = None
    if has_configured_llm_runtime(config):
        analyzer = GeminiAnalyzer(config=config)
        if not analyzer.is_available():
            logger.warning("AI 分析器初始化后不可用，请检查 LLM 配置")
            analyzer = None
    else:
        logger.warning("未检测到 LLM 模型配置，将仅使用模板生成报告")

    return notifier, analyzer, search_service
