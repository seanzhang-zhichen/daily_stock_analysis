"""Tencent direct daily K-line fetcher for A-share fallback routing."""

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
    name = "TencentFetcher"
    priority = 5
    allow_empty_daily_data = True
    _KLINE_ENDPOINT = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    _QUOTE_ENDPOINT = "https://qt.gtimg.cn/q"
    _HTTP_TIMEOUT_SECONDS = 8

    def __init__(self) -> None:
        try:
            self.priority = int(os.getenv("TENCENT_PRIORITY", "5"))
        except (TypeError, ValueError):
            self.priority = 5

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        symbol = _to_tencent_symbol(stock_code)
        if not symbol:
            raise DataFetchError(f"TencentFetcher unsupported stock code: {stock_code}")
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")
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
        normalized = df.copy()
        for column in ("open", "high", "low", "close", "volume", "amount"):
            if column in normalized.columns:
                normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
        normalized["pct_chg"] = normalized.get("close", pd.Series(dtype=float)).pct_change().fillna(0) * 100
        return normalized[["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]]

    def get_stock_name(self, stock_code: str) -> str | None:
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
    return pd.DataFrame(columns=STANDARD_COLUMNS)


def _extract_kline_rows(payload: dict[str, Any], symbol: str) -> list[dict[str, Any]]:
    item = (payload.get("data") or {}).get(symbol) if isinstance(payload, dict) else None
    rows = item.get("qfqday") or item.get("day") or [] if isinstance(item, dict) else []
    result = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        try:
            volume = float(row[5]) * 100
        except (TypeError, ValueError):
            volume = row[5]
        result.append({"date": str(row[0]), "open": row[1], "close": row[2], "high": row[3], "low": row[4], "volume": volume, "amount": row[6] if len(row) > 6 else None})
    return result
