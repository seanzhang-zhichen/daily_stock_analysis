"""腾讯直连日线 K 线抓取器，用于 A 股兜底路由。

通过腾讯股票接口（qt.gtimg.cn / ifzq.gtimg.cn）直接获取 A 股日线与
快照数据，作为其他数据源失败时的兜底路径。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests

from .base import BaseFetcher, DataFetchError, STANDARD_COLUMNS, normalize_stock_code, is_bse_code

logger = logging.getLogger(__name__)


class TencentFetcher(BaseFetcher):
    """腾讯直连数据源，用于 A 股日线与行情的兜底获取。"""

    name = "TencentFetcher"
    priority = 5
    allow_empty_daily_data = True
    _KLINE_ENDPOINT = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    _QUOTE_ENDPOINT = "https://qt.gtimg.cn/q"
    _HTTP_TIMEOUT_SECONDS = 8

    def __init__(self) -> None:
        """初始化：优先从环境变量读取优先级，异常时回落默认值 5。"""
        try:
            self.priority = int(os.getenv("TENCENT_PRIORITY", "5"))
        except (TypeError, ValueError):
            self.priority = 5

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """从腾讯 K 线接口拉取 A 股日线数据，失败返回空 DataFrame。"""
        symbol = _to_tencent_symbol(stock_code)
        if not symbol:
            raise DataFetchError(f"TencentFetcher unsupported stock code: {stock_code}")
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")
            # 适当放大请求区间，确保完整覆盖目标日期范围
            lookback = max(30, min(800, int(((end - start).days + 1) * 1.8) + 20))
        except ValueError:
            lookback = 90
        response = requests.get(
            self._KLINE_ENDPOINT,
            params={"param": f"{symbol},day,{start_date},{end_date},{lookback},qfq"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=self._HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        rows = _extract_kline_rows(payload, symbol)
        if not rows:
            return _empty_daily_frame()
        return pd.DataFrame(rows)

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """将腾讯返回的行规范化为标准日线字段。"""
        normalized = df.copy()
        for column in ("open", "high", "low", "close", "volume", "amount"):
            if column in normalized.columns:
                normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
        normalized["pct_chg"] = normalized.get("close", pd.Series(dtype=float)).pct_change().fillna(0) * 100
        return normalized[["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]]

    def get_stock_name(self, stock_code: str) -> str | None:
        """通过腾讯行情接口解析股票中文名称。"""
        symbol = _to_tencent_symbol(stock_code)
        if not symbol:
            return None
        try:
            response = requests.get(f"{self._QUOTE_ENDPOINT}={symbol}", timeout=self._HTTP_TIMEOUT_SECONDS)
            response.raise_for_status()
            response.encoding = "gbk"
            content = response.text
            start, end = content.find('"'), content.rfind('"')
            fields = content[start + 1:end].split("~") if start >= 0 and end > start else []
            return fields[1].strip() or None if len(fields) > 2 and fields[2].strip() == symbol[2:] else None
        except Exception:
            return None


def _to_tencent_symbol(stock_code: str) -> str:
    """将内部股票代码转换为腾讯接口所需的前缀符号（sh/sz/bj）。"""
    raw = str(stock_code or "").strip().upper()
    code = normalize_stock_code(stock_code)
    if not code or not code.isdigit() or len(code) != 6:
        return ""
    if raw.startswith(("SH", "SS")) or raw.endswith((".SH", ".SS")):
        return f"sh{code}"
    if raw.startswith("SZ") or raw.endswith(".SZ"):
        return f"sz{code}"
    if raw.startswith("BJ") or raw.endswith(".BJ") or is_bse_code(code):
        return f"bj{code}"
    return f"sh{code}" if code.startswith(("6", "5", "9")) else f"sz{code}"


def _empty_daily_frame() -> pd.DataFrame:
    """返回列结构符合标准的空日线 DataFrame。"""
    return pd.DataFrame(columns=STANDARD_COLUMNS)


def _extract_kline_rows(payload: dict[str, Any], symbol: str) -> list[dict[str, Any]]:
    """从腾讯 K 线 JSON 负载中提取并转换日线行。"""
    item = (payload.get("data") or {}).get(symbol) if isinstance(payload, dict) else None
    rows = item.get("qfqday") or item.get("day") or [] if isinstance(item, dict) else []
    result = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        try:
            # 腾讯成交量单位为手，乘 100 换算为股
            volume = float(row[5]) * 100
        except (TypeError, ValueError):
            volume = row[5]
        result.append({"date": str(row[0]), "open": row[1], "close": row[2], "high": row[3], "low": row[4], "volume": volume, "amount": row[6] if len(row) > 6 else None})
    return result
