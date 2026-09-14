"""轻量级股票画像聚合服务，支持按块隔离失败。

该模块提供统一的股票画像查询接口，将实时行情、历史数据、基本面信息
聚合为结构化的画像数据。每个数据块独立获取，单块失败不影响其他块，
并通过 evidence_quality 字段反映数据完整度。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from data_provider.base import DataFetcherManager, canonical_stock_code
from src.services.stock_code_utils import normalize_code
from src.services.stock_service import StockService


class InvalidStockProfileCode(ValueError):
    """股票代码无效或无法识别时抛出的异常。"""
    pass


class StockProfileService:
    """股票画像服务：聚合实时行情、历史数据、基本面信息。

    通过分块获取策略，每个数据块独立拉取，单块失败时返回 unavailable 状态，
    不影响其他数据块的正常返回。调用方可根据 evidence_quality 判断数据完整度。

    Attributes:
        stock_service: 股票数据服务实例，用于获取实时行情和历史数据。
        manager: 数据获取器管理器实例，用于获取基本面数据。
    """

    def __init__(self, stock_service: StockService | None = None, manager: DataFetcherManager | None = None):
        """初始化股票画像服务。

        Args:
            stock_service: 股票数据服务实例，默认创建新的 StockService。
            manager: 数据获取器管理器实例，默认 None（使用时按需创建）。
        """
        self.stock_service = stock_service or StockService()
        self.manager = manager

    def get_profile(self, requested_code: str, *, history_days: int = 60) -> Dict[str, Any]:
        """获取指定股票的完整画像数据。

        依次获取实时行情、历史数据、基本面信息三个数据块，
        并根据各块的状态计算整体数据质量。

        Args:
            requested_code: 用户请求的股票代码，支持多种格式（如 "600519", "SH600519" 等）。
            history_days: 历史数据获取天数，默认为 60 天。

        Returns:
            包含以下字段的字典：
            - requested_code: 原始请求代码
            - canonical_code: 规范化后的代码
            - market: 市场标识（"cn"/"hk"/"us"）
            - as_of: 数据获取时间（ISO 格式）
            - quote: 实时行情数据块
            - history: 历史数据块
            - fundamentals: 基本面数据块
            - evidence_quality: 数据质量评估（status/available_blocks/total_blocks）

        Raises:
            InvalidStockProfileCode: 当代码无法识别时抛出。
        """
        normalized = normalize_code(requested_code)
        if not normalized:
            raise InvalidStockProfileCode("无法识别股票代码")
        code = canonical_stock_code(normalized)
        market = "hk" if code.startswith("HK") or (code.isdigit() and len(code) == 5) else "cn" if code.isdigit() else "us"
        blocks = {
            "quote": self._quote(code),
            "history": self._history(code, history_days),
            "fundamentals": self._fundamentals(code),
        }
        available = sum(block["status"] != "unavailable" for block in blocks.values())
        return {
            "requested_code": requested_code.strip(),
            "canonical_code": code,
            "market": market,
            "as_of": datetime.now().astimezone().isoformat(),
            **blocks,
            "evidence_quality": {
                "status": "fresh" if available == len(blocks) else "partial" if available else "unavailable",
                "available_blocks": available,
                "total_blocks": len(blocks),
            },
        }

    def _quote(self, code: str) -> Dict[str, Any]:
        """获取实时行情数据块。

        Args:
            code: 规范化后的股票代码。

        Returns:
            实时行情数据字典，包含 status、data、limitations 字段。
            若获取失败则返回 unavailable 状态。
        """
        try:
            data = self.stock_service.get_realtime_quote(code)
        except Exception:
            data = None
        return {"status": "fresh", "data": data, "limitations": []} if data else self._unavailable("quote_unavailable", None)

    def _history(self, code: str, days: int) -> Dict[str, Any]:
        """获取历史数据块。

        Args:
            code: 规范化后的股票代码。
            days: 获取历史数据的天数。

        Returns:
            历史数据字典，包含 status、period、data、limitations 字段。
            若获取失败则返回 unavailable 状态。
        """
        try:
            data = self.stock_service.get_history_data(code, period="daily", days=days).get("data", [])
        except Exception:
            data = []
        return {"status": "fresh", "period": "daily", "data": data, "limitations": []} if data else {"status": "unavailable", "period": "daily", "data": [], "limitations": ["history_unavailable"]}

    def _fundamentals(self, code: str) -> Dict[str, Any]:
        """获取基本面数据块。

        通过 DataFetcherManager 获取基本面上下文数据，
        支持 ok/partial/unavailable 三种状态。

        Args:
            code: 规范化后的股票代码。

        Returns:
            基本面数据字典，包含 status、data、limitations 字段。
            若获取失败则返回 unavailable 状态。
        """
        try:
            manager = self.manager or DataFetcherManager()
            data = manager.get_fundamental_context(code)
        except Exception:
            data = None
        if not data:
            return self._unavailable("fundamentals_unavailable", None)
        status = "fresh" if data.get("status") == "ok" else "partial" if data.get("status") == "partial" else "unavailable"
        return {"status": status, "data": data, "limitations": [] if status == "fresh" else ["fundamentals_partial"]}

    @staticmethod
    def _unavailable(reason: str, data: Any) -> Dict[str, Any]:
        """构造不可用状态的数据块。

        Args:
            reason: 不可用的原因标识字符串。
            data: 原始数据（通常为 None）。

        Returns:
            包含 status="unavailable"、data、limitations 的字典。
        """
        return {"status": "unavailable", "data": data, "limitations": [reason]}
