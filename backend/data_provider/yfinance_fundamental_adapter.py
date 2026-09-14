# -*- coding: utf-8 -*-
"""
yfinance 基本面适配器（失败开放，fail-open）。

该适配器通过 yfinance 库获取美股/港股的基本面数据，
按统一格式返回增长、盈利、分红等信息。它绝不应向调用方抛出异常，
允许返回部分数据。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _number(value: Any) -> Optional[float]:
    """尽力而为地将值转换为 float，失败返回 None。"""
    try:
        value = float(value)
        return value if value == value else None
    except (TypeError, ValueError):
        return None


def _pct(value: Any) -> Optional[float]:
    """将小数转换为百分比（乘以 100 并保留 4 位小数），失败返回 None。"""
    value = _number(value)
    return round(value * 100, 4) if value is not None else None


def _symbol(code: str) -> str:
    """规范化股票代码，将 HK 前缀转换为 yfinance 格式（如 0001.HK）。"""
    code = (code or "").strip().upper()
    if code.startswith("HK"):
        return f"{(code[2:].lstrip('0') or '0').zfill(4)}.HK"
    return code


class YfinanceFundamentalAdapter:
    """yfinance 基本面适配器：获取美股/港股的基本面数据，不抛出 provider 错误。"""

    def get_fundamental_bundle(self, stock_code: str) -> Dict[str, Any]:
        """
        从 yfinance 返回标准化的基本面数据块，支持部分容错。

        参数:
            stock_code: 股票代码（支持 US 和 HK 代码）

        返回:
            包含 growth、earnings、institution、belong_boards 等字段的字典
        """
        result: Dict[str, Any] = {
            "status": "not_supported", "growth": {}, "earnings": {},
            "institution": {}, "belong_boards": [], "source_chain": [], "errors": [],
        }
        try:
            import yfinance as yf
            ticker = yf.Ticker(_symbol(stock_code))
            info = ticker.get_info() if hasattr(ticker, "get_info") else ticker.info
            info = info if isinstance(info, dict) else {}
        except Exception as exc:
            result["errors"].append(f"yfinance:{type(exc).__name__}")
            return result

        growth = {
            "revenue_yoy": _pct(info.get("revenueGrowth")),
            "net_profit_yoy": _pct(info.get("earningsGrowth")),
            "roe": _pct(info.get("returnOnEquity")),
            "gross_margin": _pct(info.get("grossMargins")),
        }
        growth = {key: value for key, value in growth.items() if value is not None}
        if growth:
            result["growth"] = growth
            result["source_chain"].append("growth:yfinance")

        financial = {
            "report_date": None,
            "revenue": _number(info.get("totalRevenue")),
            "net_profit_parent": _number(info.get("netIncomeToCommon")) or _number(info.get("netIncome")),
            "operating_cash_flow": _number(info.get("operatingCashflow")),
            "roe": growth.get("roe"),
            "currency": str(info.get("financialCurrency") or info.get("currency") or "") or None,
        }
        if any(value is not None for value in financial.values()):
            result["earnings"] = {"financial_report": financial}
            dividend = _number(info.get("trailingAnnualDividendRate"))
            price = _number(info.get("currentPrice")) or _number(info.get("regularMarketPrice"))
            if dividend is not None:
                payload = {
                    "events": [], "ttm_cash_dividend_per_share": dividend,
                    "currency": str(info.get("currency") or "") or None,
                    "as_of": datetime.now(timezone.utc).date().isoformat(),
                }
                if price:
                    payload["ttm_dividend_yield_pct"] = round(dividend / price * 100, 4)
                result["earnings"]["dividend"] = payload
            result["source_chain"].append("earnings:yfinance")

        boards = []
        for key, kind in (("sector", "行业"), ("industry", "概念")):
            value = str(info.get(key) or "").strip()
            if value and not any(item["name"] == value for item in boards):
                boards.append({"name": value, "type": kind})
        if boards:
            result["belong_boards"] = boards
            result["source_chain"].append("boards:yfinance")

        result["status"] = "partial" if any(result[key] for key in ("growth", "earnings", "belong_boards")) else "not_supported"
        return result
