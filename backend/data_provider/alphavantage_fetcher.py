# -*- coding: utf-8 -*-
"""
AlphaVantageFetcher — 美股数据源（优先级 3）

数据来源：AlphaVantage REST API
速率限制：免费档 25 次/天、5 次/分钟
覆盖市场：仅美股
"""

import logging
import os
from datetime import datetime
from typing import Optional

import pandas as pd
import requests

from .base import BaseFetcher, DataFetchError, STANDARD_COLUMNS
from .realtime_types import UnifiedRealtimeQuote, RealtimeSource
from .us_index_mapping import is_us_stock_code

logger = logging.getLogger(__name__)

_AV_BASE_URL = "https://www.alphavantage.co/query"


class AlphaVantageFetcher(BaseFetcher):
    """基于 AlphaVantage 的美股 OHLCV 与实时行情 fetcher。"""

    name = "AlphaVantageFetcher"
    priority = 3

    def __init__(self):
        """从配置/环境变量加载 API key；缺失时 fetcher 实际处于禁用状态。"""
        from src.config import get_config
        config = get_config()
        self._api_key = getattr(config, 'alphavantage_api_key', None) or os.getenv('ALPHAVANTAGE_API_KEY')
        if not self._api_key:
            logger.debug("[AlphaVantage] API key not configured, fetcher disabled")

    def _is_us_stock(self, stock_code: str) -> bool:
        """判断 stock_code 是否被 AlphaVantage 美股接口支持。"""
        return is_us_stock_code(stock_code)

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """从 AlphaVantage 拉取原始日线时间序列数据。"""
        if not self._api_key:
            raise DataFetchError("[AlphaVantage] API key not configured")
        if not self._is_us_stock(stock_code):
            raise DataFetchError(f"[AlphaVantage] {stock_code} is not a US stock")

        symbol = stock_code.strip().upper()
        params = {
            'function': 'TIME_SERIES_DAILY',
            'symbol': symbol,
            'outputsize': 'compact',
            'apikey': self._api_key,
        }

        try:
            self.random_sleep(0.5, 1.5)
            resp = requests.get(_AV_BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            raise DataFetchError(f"[AlphaVantage] HTTP request failed for {symbol}: {e}") from e

        if 'Note' in data:
            raise DataFetchError(f"[AlphaVantage] Rate limited: {data['Note']}")
        if 'Error Message' in data:
            raise DataFetchError(f"[AlphaVantage] API error for {symbol}: {data['Error Message']}")

        ts_key = 'Time Series (Daily)'
        if ts_key not in data or not data[ts_key]:
            raise DataFetchError(f"[AlphaVantage] No time series data for {symbol}")

        rows = []
        start = datetime.strptime(start_date, '%Y-%m-%d').date()
        end = datetime.strptime(end_date, '%Y-%m-%d').date()
        for date_str, values in data[ts_key].items():
            row_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            if start <= row_date <= end:
                rows.append({
                    'date': date_str,
                    '1. open': float(values.get('1. open', 0)),
                    '2. high': float(values.get('2. high', 0)),
                    '3. low': float(values.get('3. low', 0)),
                    '4. close': float(values.get('4. close', 0)),
                    '5. volume': float(values.get('5. volume', 0)),
                })

        if not rows:
            raise DataFetchError(f"[AlphaVantage] No data in date range for {symbol}")

        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df['date'])
        return df.drop(columns=['date'])

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """将 AlphaVantage 日线数据规范化为项目标准字段结构。"""
        if df.empty:
            return df

        df = df.copy()
        df['date'] = pd.to_datetime(df.index).date
        df = df.rename(columns={
            '1. open': 'open', '2. high': 'high', '3. low': 'low',
            '4. close': 'close', '5. volume': 'volume',
        })
        # AlphaVantage 返回的数据为倒序（最新在前）；计算涨跌幅前先按日期升序排列
        df = df.sort_values('date', ascending=True).reset_index(drop=True)
        df['pct_chg'] = df['close'].pct_change() * 100
        df['pct_chg'] = df['pct_chg'].fillna(0).round(2)
        df['amount'] = df['volume'] * df['close']
        df['code'] = stock_code

        keep = ['code'] + STANDARD_COLUMNS
        df = df[[col for col in keep if col in df.columns]]
        return df

    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """获取实时/全球行情，并转换为 UnifiedRealtimeQuote。"""
        if not self._api_key or not self._is_us_stock(stock_code):
            return None

        symbol = stock_code.strip().upper()
        try:
            self.random_sleep(0.5, 1.5)
            resp = requests.get(_AV_BASE_URL, params={
                'function': 'GLOBAL_QUOTE',
                'symbol': symbol,
                'apikey': self._api_key,
            }, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"[AlphaVantage] Realtime quote failed for {symbol}: {e}")
            return None

        gq = data.get('Global Quote', {})
        price_str = gq.get('05. price')
        if not price_str:
            return None

        price = float(price_str)
        prev_close = float(gq.get('08. previous close', 0))
        change_pct_str = gq.get('10. change percent', '0%').replace('%', '')
        change_pct = float(change_pct_str) if change_pct_str else None

        return UnifiedRealtimeQuote(
            code=symbol,
            source=RealtimeSource.FALLBACK,
            price=price,
            change_pct=round(change_pct, 2) if change_pct is not None else None,
            change_amount=round(float(gq.get('09. change', 0)), 4),
            volume=int(float(gq.get('06. volume', 0))),
            amount=None,
            volume_ratio=None,
            turnover_rate=None,
            amplitude=None,
            open_price=float(gq.get('02. open', 0)),
            high=float(gq.get('03. high', 0)),
            low=float(gq.get('04. low', 0)),
            pre_close=prev_close,
        )

    def get_stock_name(self, stock_code: str) -> Optional[str]:
        """通过 AlphaVantage 符号搜索解析美股代码对应的显示名称。"""
        if not self._api_key or not self._is_us_stock(stock_code):
            return None

        symbol = stock_code.strip().upper()
        try:
            resp = requests.get(_AV_BASE_URL, params={
                'function': 'SYMBOL_SEARCH',
                'keywords': symbol,
                'apikey': self._api_key,
            }, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.debug(f"[AlphaVantage] Symbol search failed for {symbol}: {e}")
            return None

        for match in data.get('bestMatches', []):
            if match.get('1. symbol') == symbol and match.get('2. name'):
                return match['2. name']
        return None
