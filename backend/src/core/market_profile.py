# -*- coding: utf-8 -*-
"""为每日市场复盘提供市场区域元数据。

每个 profile 描述一个区域的代表性指数、新闻搜索关键词、prompt 提示语，
以及可用的市场统计板块。``MarketAnalyzer`` 借助这些 profile 切换 CN/HK/US
复盘行为，避免把区域相关常量散落在分析代码各处。
"""

from dataclasses import dataclass
from typing import List


@dataclass
class MarketProfile:
    """市场复盘所需的区域专属输入与功能开关。"""

    region: str  # "cn" | "us"
    # 用于判断整体走势的指数代码，cn 用上证 000001，us 用标普 SPX
    mood_index_code: str
    # 新闻搜索关键词
    news_queries: List[str]
    # 指数点评 Prompt 提示语
    prompt_index_hint: str
    # 市场概况是否包含涨跌家数、涨停跌停（A 股有，美股无）
    has_market_stats: bool
    # 市场概况是否包含板块涨跌（A 股有，美股暂无）
    has_sector_rankings: bool


CN_PROFILE = MarketProfile(
    region="cn",
    mood_index_code="000001",
    news_queries=[
        "A股 大盘 复盘",
        "股市 行情 分析",
        "A股 市场 热点 板块",
    ],
    prompt_index_hint="分析上证、深证、创业板等各指数走势特点",
    has_market_stats=True,
    has_sector_rankings=True,
)

US_PROFILE = MarketProfile(
    region="us",
    mood_index_code="SPX",
    news_queries=[
        "美股 大盘",
        "US stock market",
        "S&P 500 NASDAQ",
    ],
    prompt_index_hint="分析标普500、纳斯达克、道指等各指数走势特点",
    has_market_stats=False,
    has_sector_rankings=False,
)

HK_PROFILE = MarketProfile(
    region="hk",
    mood_index_code="HSI",
    news_queries=[
        "港股 大盘 复盘",
        "Hong Kong stock market",
        "恒生指数 行情",
    ],
    prompt_index_hint="分析恒生指数、恒生科技指数、国企指数等各指数走势特点",
    has_market_stats=False,
    has_sector_rankings=False,
)

JP_PROFILE = MarketProfile(
    region="jp",
    mood_index_code="N225",
    news_queries=["日本株 日経225", "Japan stock market Nikkei TOPIX", "日経225 東証指数"],
    prompt_index_hint="分析日经225、东证指数等日本主要指数走势特征",
    has_market_stats=False,
    has_sector_rankings=False,
)

KR_PROFILE = MarketProfile(
    region="kr",
    mood_index_code="KS11",
    news_queries=["韩国股市 KOSPI", "Korea stock market KOSPI KOSDAQ", "KOSPI KOSDAQ 行情"],
    prompt_index_hint="分析 KOSPI、KOSDAQ 等韩国主要指数走势特征",
    has_market_stats=False,
    has_sector_rankings=False,
)


def get_profile(region: str) -> MarketProfile:
    """返回已配置的 MarketProfile，未知区域默认回退到 CN。"""
    if region == "us":
        return US_PROFILE
    if region == "hk":
        return HK_PROFILE
    if region == "jp":
        return JP_PROFILE
    if region == "kr":
        return KR_PROFILE
    return CN_PROFILE
