"""数据能力快照服务（Read-only provider and dataset capability snapshots without secrets）。

以只读方式提供各数据提供商（provider）和数据集（dataset）的能力快照，
不包含任何密钥或敏感配置信息，供前端展示数据源的可用状态与覆盖范围。

主要功能：
- 聚合各数据提供商的配置状态和运行时可用性
- 展示各数据集在不同市场的覆盖情况
- 提供数据能力总览，帮助用户了解数据源的可用性
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from src.config import get_config


# 内置的数据提供商定义：(名称, 获取器类名, 是否内置, 支持的市场, 支持的数据集)
_DEFINITIONS = (
    # 格式：(provider_name, fetcher_class_name, is_builtin, supported_markets, supported_datasets)
    # 内置提供商无需额外配置即可使用
    ("akshare", "AkshareFetcher", True, ("cn", "hk"), ("quote.realtime", "kline.daily", "financial.snapshot")),
    ("efinance", "EfinanceFetcher", True, ("cn",), ("quote.realtime", "kline.daily")),
    ("yfinance", "YfinanceFetcher", True, ("hk", "us"), ("quote.realtime", "kline.daily", "financial.snapshot")),
    # 非内置提供商需要配置 API 密钥
    ("longbridge", "LongbridgeFetcher", False, ("hk", "us"), ("quote.realtime", "kline.daily")),
    ("futu", "FutuFetcher", False, ("hk",), ("quote.realtime", "financial.snapshot")),
    ("finnhub", "FinnhubFetcher", False, ("us",), ("kline.daily",)),
    ("alphavantage", "AlphaVantageFetcher", False, ("us",), ("kline.daily",)),
)


class DataCapabilityService:
    """数据能力服务：聚合各数据提供商的配置状态与能力信息。"""

    def __init__(self, config: Any = None, fetcher_manager: Any = None):
        """初始化数据能力服务。

        Args:
            config: 配置对象，默认为全局配置。
            fetcher_manager: 数据获取器管理器，用于查询运行时获取器状态。
        """
        self.config = config or get_config()
        self.fetcher_manager = fetcher_manager

    def get_overview(self, *, include_provider_details: bool = True) -> Dict[str, Any]:
        """获取数据提供商和数据集的能力总览。

        遍历内置提供商定义，结合运行时获取器状态，返回每个提供商的配置状态、
        可用性、支持的市场和数据集信息。

        处理流程：
        1. 获取运行时获取器快照
        2. 遍历所有预定义提供商，判断配置状态和运行时可用性
        3. 聚合各数据集在不同市场的覆盖情况
        4. 返回结构化的能力总览

        Args:
            include_provider_details: 是否包含详细的提供商信息。

        Returns:
            包含 providers、datasets、as_of 等字段的字典：
            - as_of: 快照时间戳
            - providers: 提供商列表，每个包含名称、配置状态、运行时状态等
            - datasets: 数据集列表，每个包含数据集名、市场、可用性、提供商列表
            - warnings: 警告信息列表
        """
        fetchers = self._fetchers()
        # 构建获取器名称到获取器对象的映射，用于快速查找
        by_name = {getattr(item, "name", ""): item for item in fetchers}
        providers = []
        # 使用字典聚合数据集信息，键为 (dataset, market) 元组，值为提供商列表
        datasets: Dict[tuple[str, str], list[str]] = {}
        for name, fetcher_name, builtin, markets, supported in _DEFINITIONS:
            # 判断提供商是否已配置：内置提供商始终视为已配置
            configured = builtin or self._configured(name)
            fetcher = by_name.get(fetcher_name)
            # 判断运行时状态：获取器存在则可用，已配置但无获取器则不可用，未配置则未配置
            status = "ok" if fetcher is not None else "unavailable" if configured else "unconfigured"
            if include_provider_details:
                providers.append({
                    "name": name, "configured": configured, "status": status,
                    "priority": getattr(fetcher, "priority", None),
                    "markets": list(markets), "datasets": list(supported),
                })
            # 遍历该提供商支持的所有数据集和市场组合
            for dataset in supported:
                for market in markets:
                    if configured:
                        # 使用 setdefault 确保每个 (dataset, market) 组合都有列表
                        datasets.setdefault((dataset, market), []).append(name)
        # 构建数据集可用性列表，每个数据集包含其支持的市场和可用提供商
        items = [{
            "dataset": dataset, "market": market,
            "status": "available" if names else "unavailable",
            "providers": names,
            "reason": None if names else "no_provider_configured",
        } for (dataset, market), names in sorted(datasets.items())]
        return {"as_of": datetime.now().astimezone().isoformat(), "providers": providers, "datasets": items, "warnings": []}

    def _fetchers(self) -> list[Any]:
        """获取当前运行时所有数据获取器的快照列表。

        通过反射调用获取器管理器的内部方法，获取运行时获取器状态。
        异常时返回空列表，避免影响主流程。
        """
        try:
            manager = self.fetcher_manager
            if manager is None:
                from data_provider.base import DataFetcherManager
                manager = DataFetcherManager()
            snapshot = getattr(manager, "_get_fetchers_snapshot", None)
            return list(snapshot() if callable(snapshot) else getattr(manager, "_fetchers", []))
        except Exception:
            return []

    def _configured(self, name: str) -> bool:
        """判断指定名称的数据提供商是否已配置（存在对应的 API 密钥或配置项）。

        针对不同提供商检查不同的配置项：
        - futu: 检查 futu_opend_host
        - longbridge: 检查 longbridge_app_key
        - finnhub: 检查 finnhub_api_key
        - alphavantage: 检查 alphavantage_api_key
        - 其他: 默认返回 False

        Args:
            name: 提供商名称。

        Returns:
            若已配置返回 True，否则返回 False。
        """
        if name == "futu":
            return bool(getattr(self.config, "futu_opend_host", None))
        if name == "longbridge":
            return bool(getattr(self.config, "longbridge_app_key", None))
        if name == "finnhub":
            return bool(getattr(self.config, "finnhub_api_key", None))
        if name == "alphavantage":
            return bool(getattr(self.config, "alphavantage_api_key", None))
        return False
