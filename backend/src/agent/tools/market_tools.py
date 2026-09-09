# -*- coding: utf-8 -*-
"""市场类工具集：把 :class:`DataFetcherManager` 的市场级方法包装为 Agent 工具。

本模块导出的工具：

- ``get_market_indices``：拉取主要市场指数（上证、深证、沪深 300 / 恒指 / 标普等）；
- ``get_sector_rankings``：板块涨跌幅排名，用于板块轮动判断。

所有工具以 :class:`ToolDefinition` 形式注册，handler 内部走 ``DataFetcherManager``，
确保与 Web / Bot 等其他调用方共用同一份数据通路。
"""

import logging

from src.agent.tools.registry import ToolParameter, ToolDefinition

logger = logging.getLogger(__name__)


def _get_fetcher_manager():
    """惰性导入 :class:`DataFetcherManager`，避免 agent 包与 data_provider 包循环依赖。

    Returns:
        DataFetcherManager: 全局唯一的数据拉取管理器实例。
    """
    from data_provider import DataFetcherManager
    return DataFetcherManager()


# ============================================================
# get_market_indices
# ============================================================

def _handle_get_market_indices(region: str = "cn") -> dict:
    """获取主要市场指数数据。

    Args:
        region: 市场区域标识，``cn`` / ``hk`` / ``us``。

    Returns:
        dict: 形如 ``{"region": ..., "indices_count": ..., "indices": [...]}``；
        无数据时返回 ``{"error": "..."}``。
    """
    manager = _get_fetcher_manager()
    indices = manager.get_main_indices(region=region)

    # 数据源没拉到数据时返回 error 字段而不是抛出异常，
    # 让上层 LLM 可以基于错误信息调整策略。
    if not indices:
        return {"error": f"No market index data available for region '{region}'"}

    return {
        "region": region,
        "indices_count": len(indices),
        "indices": indices,
    }


get_market_indices_tool = ToolDefinition(
    name="get_market_indices",
    description="Get major market indices (e.g., Shanghai Composite, Shenzhen Component, "
                "CSI 300 for China; S&P 500, Nasdaq, Dow for US). Provides market overview.",
    parameters=[
        ToolParameter(
            name="region",
            type="string",
            description="Market region: 'cn' for China A-shares, 'hk' for Hong Kong, 'us' for US stocks (default: 'cn')",
            required=False,
            default="cn",
            enum=["cn", "hk", "us"],
        ),
    ],
    handler=_handle_get_market_indices,
    category="market",
)


# ============================================================
# get_sector_rankings
# ============================================================

def _handle_get_sector_rankings(top_n: int = 10) -> dict:
    """获取板块涨跌幅排名（涨 / 跌各 top_n）。

    Args:
        top_n: 返回的板块数量（涨 / 跌各这么多条）。

    Returns:
        dict: 三种返回形态之一：
        - ``{"top_sectors": [...], "bottom_sectors": [...]}``：tuple 返回值；
        - ``{"sectors": [...]}``：直接列表返回；
        - ``{"data": str(result)}``：未知结构兜底。
        数据缺失时返回 ``{"error": "..."}``。
    """
    manager = _get_fetcher_manager()
    result = manager.get_sector_rankings(n=top_n)

    if result is None:
        return {"error": "No sector ranking data available"}

    # get_sector_rankings returns Tuple[List[Dict], List[Dict]]
    # (top_sectors, bottom_sectors)
    if isinstance(result, tuple) and len(result) == 2:
        top_sectors, bottom_sectors = result
        return {
            "top_sectors": top_sectors,
            "bottom_sectors": bottom_sectors,
        }
    # 部分老版本 fetcher 只返回单一列表，这里统一包一层 ``sectors``
    elif isinstance(result, list):
        return {"sectors": result}
    else:
        # 未知结构兜底为字符串，保证调用方一定拿到 dict
        return {"data": str(result)}


get_sector_rankings_tool = ToolDefinition(
    name="get_sector_rankings",
    description="Get sector/industry performance rankings. Returns top N and bottom N "
                "sectors by daily change percentage. Useful for sector rotation analysis.",
    parameters=[
        ToolParameter(
            name="top_n",
            type="integer",
            description="Number of top/bottom sectors to return (default: 10)",
            required=False,
            default=10,
        ),
    ],
    handler=_handle_get_sector_rankings,
    category="market",
)


# 供 Agent 注册中心一次性导入使用
ALL_MARKET_TOOLS = [
    get_market_indices_tool,
    get_sector_rankings_tool,
]
