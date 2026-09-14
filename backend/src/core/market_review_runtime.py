"""可复用的市场复盘运行时装配辅助函数。

把 analyzer/search/notification 的构造集中起来，让 API、CLI、scheduler 和 Bot
入口共享同一套大盘复盘的初始化路径。把运行时装配放在这里，可避免不同入口在
可选的搜索/LLM 行为上出现细微差异。
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

from src.config import Config

# 模块级日志记录器，用于输出运行时装配过程中的诊断信息
logger = logging.getLogger(__name__)


def has_configured_llm_runtime(config: Config) -> bool:
    """判断是否存在任何可用的 LLM 模型配置。

    分析器既可通过 LiteLLM 配置，也可通过渠道列表或遗留的 provider 专属 API key
    进行配置。此检查刻意保持宽泛，以便在未配置任何模型时，市场复盘仍能以纯模板
    方式运行。

    Args:
        config: 应用程序配置对象，包含各种 LLM 相关的配置项

    Returns:
        如果存在任何可用的 LLM 配置则返回 True，否则返回 False
    """
    # 检查是否配置了 LiteLLM 主模型
    if (getattr(config, "litellm_model", "") or "").strip():
        return True
    # 检查是否配置了 LLM 渠道列表
    if getattr(config, "llm_model_list", None):
        return True

    # 遍历检查各 provider 的 API key 配置（支持单 key 和多 key 形式）
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

    Args:
        config: 应用程序配置对象
        source_message: 可选的源消息对象，用于通知服务上下文

    Returns:
        三元组：(notifier, analyzer, search_service)
        - notifier: NotificationService 实例，用于报告保存和通知推送
        - analyzer: GeminiAnalyzer 实例或 None（当 LLM 不可用时）
        - search_service: SearchService 实例或 None（当搜索未启用时）
    """
    from src.analyzer import GeminiAnalyzer
    from src.notification import NotificationService
    from src.search_service import SearchService

    # 创建通知服务实例，用于后续的报告保存和推送
    notifier = NotificationService(source_message=source_message)

    # 初始化搜索服务（可选依赖）
    search_service = None
    has_search_capability = getattr(config, "has_search_capability_enabled", None)
    # 只有当配置明确启用搜索能力时，才创建搜索服务实例
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
            searxng_timeout_seconds=getattr(config, "searxng_timeout_seconds", None),
            news_max_age_days=getattr(config, "news_max_age_days", 3),
            news_strategy_profile=getattr(config, "news_strategy_profile", "short"),
        )

    # 初始化 AI 分析器（可选依赖）
    analyzer = None
    if has_configured_llm_runtime(config):
        analyzer = GeminiAnalyzer(config=config)
        if not analyzer.is_available():
            logger.warning("AI 分析器初始化后不可用，请检查 LLM 配置")
            analyzer = None
    else:
        logger.warning("未检测到 LLM 模型配置，将仅使用模板生成报告")

    return notifier, analyzer, search_service
