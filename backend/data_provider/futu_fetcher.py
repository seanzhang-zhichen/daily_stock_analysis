# -*- coding: utf-8 -*-
"""
===================================
FutuFetcher - 富途 OpenD 港股实时行情适配器
===================================

数据来源：富途 OpenD 行情接口（通过 futu 库）
特点：仅支持港股实时行情，不支持历史日线数据
适用场景：港股实时行情获取

功能限制：
- 仅提供实时行情（get_realtime_quote），不支持日线数据获取
- 仅支持港股代码（HK 开头）
- 需要富途 OpenD 服务运行在本机或指定服务器上

使用方式：
    fetcher = FutuFetcher(host="127.0.0.1", port=11111)
    quote = fetcher.get_realtime_quote("HK00700")
"""

from __future__ import annotations

from typing import Any

from .base import BaseFetcher
from .realtime_types import RealtimeSource, UnifiedRealtimeQuote, safe_float, safe_int


class FutuFetcher(BaseFetcher):
    """
    基于富途 OpenD 的港股实时行情获取器。

    通过 futu 库连接富途 OpenD 服务获取港股实时行情快照。
    仅实现实时行情接口，历史日线数据获取会抛出 NotImplementedError。

    使用方式：
        fetcher = FutuFetcher(host="127.0.0.1", port=11111)
        quote = fetcher.get_realtime_quote("HK00700")
    """

    name = "FutuFetcher"

    def __init__(self, host: str, port: int, priority: int = 3):
        """
        初始化 FutuFetcher。

        Args:
            host: 富途 OpenD 服务主机地址，通常为 "127.0.0.1"。
            port: 富途 OpenD 服务端口，默认为 11111。
            priority: 数据源优先级，数值越小优先级越高，默认 3。
        """
        self.host = str(host).strip()
        self.port = int(port)
        self.priority = int(priority)

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str):
        """
        获取原始日线数据（未实现）。

        FutuFetcher 仅支持实时行情，不支持历史日线数据获取。
        调用此方法会抛出 NotImplementedError。

        Args:
            stock_code: 股票代码。
            start_date: 开始日期。
            end_date: 结束日期。

        Raises:
            NotImplementedError: 始终抛出，因为 FutuFetcher 不支持日线数据。
        """
        raise NotImplementedError("FutuFetcher only provides realtime HK quotes")

    def _normalize_data(self, df, stock_code: str):
        """
        标准化数据（未实现）。

        FutuFetcher 仅支持实时行情，此方法未实现。

        Args:
            df: 原始数据 DataFrame。
            stock_code: 股票代码。

        Returns:
            直接返回原始 DataFrame（未做处理）。
        """
        return df

    @staticmethod
    def _symbol(stock_code: str) -> str:
        """
        将内部股票代码转换为富途接口所需的格式。

        富途接口要求港股代码格式为 "HK.XXXXX"（5位数字）。
        例如："HK00700" -> "HK.00700"

        Args:
            stock_code: 内部股票代码，如 "HK00700"。

        Returns:
            富途格式的股票代码，如 "HK.00700"。

        Raises:
            ValueError: 如果代码不是以 HK 开头的数字代码。
        """
        code = str(stock_code or "").strip().upper()
        digits = code[2:] if code.startswith("HK") else code
        if not digits.isdigit():
            raise ValueError("Futu quote only supports HK numeric symbols")
        return f"HK.{digits.zfill(5)}"

    def _snapshot(self, stock_code: str):
        """
        通过富途 OpenD 获取指定股票的行情快照。

        该方法连接富途 OpenD 服务，获取指定港股的最新行情快照。
        使用 try-finally 确保连接在使用后正确关闭。

        Args:
            stock_code: 股票代码，如 "HK00700"。

        Returns:
            行情快照的 Series 对象；获取失败返回 None。
        """
        from futu import OpenQuoteContext, RET_OK

        context = OpenQuoteContext(host=self.host, port=self.port)
        try:
            status, data = context.get_market_snapshot([self._symbol(stock_code)])
            if status != RET_OK or data is None or data.empty:
                return None
            return data.iloc[0]
        finally:
            context.close()

    def get_realtime_quote(self, stock_code: str, **_kwargs: Any):
        """
        获取港股实时行情，并转换为 UnifiedRealtimeQuote。

        通过富途 OpenD 获取指定港股的实时行情数据，
        并转换为项目统一的标准行情格式。

        Args:
            stock_code: 股票代码，如 "HK00700"。
            **_kwargs: 额外的关键字参数（未使用，用于兼容接口）。

        Returns:
            UnifiedRealtimeQuote 对象；获取失败返回 None。
        """
        row = self._snapshot(stock_code)
        if row is None:
            return None
        return UnifiedRealtimeQuote(
            code=stock_code,
            name=str(row.get("name") or ""),
            source=RealtimeSource.FUTU,
            price=safe_float(row.get("last_price")),
            change_pct=safe_float(row.get("change_rate")),
            volume=safe_int(row.get("volume")),
            amount=safe_float(row.get("turnover")),
            open_price=safe_float(row.get("open_price")),
            high=safe_float(row.get("high_price")),
            low=safe_float(row.get("low_price")),
            pre_close=safe_float(row.get("prev_close_price")),
            pe_ratio=safe_float(row.get("pe_ratio")),
            pb_ratio=safe_float(row.get("pb_ratio")),
            total_mv=safe_float(row.get("market_val")),
        )

    def get_fundamental_bundle(self, stock_code: str):
        """
        获取港股基本面数据包。

        通过富途 OpenD 获取指定港股的基本面数据，包括估值指标等。
        由于富途接口限制，目前仅返回部分估值数据（PE、PB、市值等）。

        Args:
            stock_code: 股票代码，如 "HK00700"。

        Returns:
            包含基本面数据的字典，格式如下：
            {
                "status": "partial" | "not_supported",
                "growth": {},
                "earnings": {},
                "institution": {"snapshot_valuation": {...}},
                "belong_boards": [],
                "source_chain": ["institution:futu.snapshot"],
                "errors": []
            }
        """
        row = self._snapshot(stock_code)
        if row is None:
            return {"status": "not_supported", "growth": {}, "earnings": {}, "institution": {}, "belong_boards": [], "source_chain": [], "errors": ["futu snapshot unavailable"]}
        valuation = {key: value for key, value in {
            "pe_ratio": safe_float(row.get("pe_ratio")),
            "pb_ratio": safe_float(row.get("pb_ratio")),
            "total_mv": safe_float(row.get("market_val")),
        }.items() if value is not None}
        return {
            "status": "partial" if valuation else "not_supported",
            "growth": {}, "earnings": {},
            "institution": {"snapshot_valuation": valuation} if valuation else {},
            "belong_boards": [],
            "source_chain": ["institution:futu.snapshot"] if valuation else [],
            "errors": [],
        }
