# -*- coding: utf-8 -*-
"""近新高策略：筛选当前股价接近 120 日滚动窗口新高、且新高出现在近 15 个交易日内的股票。

策略思想：当一只股票处在上升趋势时，其收盘价会不断刷新 N 日新高，
而"接近新高"往往意味着仍处于强势阶段、尚未大幅回撤。本策略通过
``min_high_position`` 控制"接近"门槛（例如 0.85 表示当前价不能低于
滚动高点的 85%），并通过 ``recent_high_days`` 限制"新高必须近期出现"，
以避免将长期震荡股误判为强势。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

import pandas as pd

from src.services.stock_selection.models import StockSelectionCandidate, StockSelectionStock
from src.services.stock_selection.strategies import StockSelectionStrategy


class NearNewHighStrategy(StockSelectionStrategy):
    """近新高选股策略。

    在 ``lookback_days``（默认 120）日窗口内寻找最高点，并要求：
    1. 最新收盘价相对该最高点的回撤不超过 ``(1 - min_high_position)``；
    2. 该最高点距离最新交易日不超过 ``recent_high_days`` 天。
    同时给出综合得分 ``score``，用于在同一策略内做排序。
    """

    # 策略在注册表中的英文唯一标识
    name = "near_new_high"
    # 策略对外展示的中文名称，供 UI / 报告使用
    display_name = "近新高策略"
    # 策略简介，向用户说明本策略的筛选口径
    description = "筛选当前价接近 120 日新高，且新高发生在近 15 个交易日内的股票。"
    # 策略的别名集合，兼容历史调用方传入的旧名称
    aliases = ("new_high", "recent_high", "near_high", "新高", "近新高")

    def default_params(self) -> Dict[str, Any]:
        """返回近新高策略的默认参数。

        Returns:
            Dict[str, Any]: 包含 ``lookback_days`` / ``min_high_position`` /
                ``recent_high_days`` / ``sort_by`` 的字典。
        """
        return {
            "lookback_days": 120,
            "min_high_position": 0.85,
            "recent_high_days": 15,
            "sort_by": "volatility_then_return",
        }

    def validate_params(self, params: Dict[str, Any]) -> None:
        """校验近新高策略的参数取值是否合法。

        Args:
            params: 用户传入的参数字典。

        Raises:
            ValueError: 当 ``lookback_days`` 不在 [2, 500] 区间；
                ``recent_high_days`` 超出 [0, lookback_days] 区间；
                ``min_high_position`` 不在 (0, 1] 区间时抛出。
        """
        # 三个核心数值参数，缺失时回落到默认值再校验
        lookback_days = int(params.get("lookback_days", 120))
        recent_high_days = int(params.get("recent_high_days", 15))
        min_high_position = float(params.get("min_high_position", 0.85))
        # 回溯窗口过短统计意义不足，过长则时效性差
        if lookback_days < 2 or lookback_days > 500:
            raise ValueError("lookback_days must be between 2 and 500")
        # "距新高天数"必须合法，否则无法表达"近期"的语义
        if recent_high_days < 0 or recent_high_days > lookback_days:
            raise ValueError("recent_high_days must be between 0 and lookback_days")
        # 接近度必须为正且不超过 1，否则除法/比较无意义
        if min_high_position <= 0 or min_high_position > 1:
            raise ValueError("min_high_position must be in (0, 1]")

    def evaluate(
        self,
        *,
        stock: StockSelectionStock,
        history: pd.DataFrame,
        source: str,
        params: Dict[str, Any],
        target_date: Optional[date] = None,
    ) -> Optional[StockSelectionCandidate]:
        """评估单只股票是否符合"近新高"条件，并构造候选结果。

        Args:
            stock: 标的基本信息（代码、名称、市场）。
            history: 历史行情 DataFrame，需包含 ``date`` / ``high`` / ``close`` 列。
            source: 数据来源标识（如 akshare / tushare）。
            params: 策略参数字典。
            target_date: 评估的目标日期，None 表示使用最新一根 K 线。

        Returns:
            Optional[StockSelectionCandidate]: 命中策略时返回候选对象；否则返回 None。
        """
        # 解析运行时参数（带默认值兜底）
        lookback_days = int(params.get("lookback_days", 120))
        min_high_position = float(params.get("min_high_position", 0.85))
        recent_high_days = int(params.get("recent_high_days", 15))

        # 先对历史数据进行归一化（日期/数值类型、空值剔除）
        df = _normalize_history(history)
        # 若指定了目标日期，则只看该日期之前的数据，避免用到未来函数
        if target_date is not None and "date" in df.columns:
            df = df[df["date"].dt.date <= target_date]
        # 数据长度不足以形成有效回溯窗口时直接放弃
        if len(df) < lookback_days:
            return None

        # 截取最近 lookback_days 根 K 线作为分析窗口
        window = df.tail(lookback_days).copy()
        if window.empty:
            return None

        # 在窗口内找到最高点（按 high 列取最大）
        high_idx = window["high"].idxmax()
        high_row = window.loc[high_idx]
        latest = window.iloc[-1]

        latest_close = float(latest["close"])
        window_high = float(high_row["high"])
        # 防御性兜底：价格必须为正，否则无法计算相对强弱
        if latest_close <= 0 or window_high <= 0:
            return None

        # "近新高"的两个核心门槛：相对位置 + 时间距离
        high_position = latest_close / window_high
        days_since_high = int(len(window) - 1 - window.index.get_loc(high_idx))
        if high_position < min_high_position or days_since_high > recent_high_days:
            return None

        # 衍生指标：窗口期收益率、年化波动率、与高点的偏离度
        first_close = float(window.iloc[0]["close"])
        window_return_pct = (latest_close - first_close) / first_close * 100.0 if first_close > 0 else 0.0
        volatility_pct = _calculate_volatility_pct(window["close"])
        distance_to_high_pct = (latest_close / window_high - 1.0) * 100.0
        # 综合打分，用于同策略内排序
        score = _calculate_score(
            high_position=high_position,
            days_since_high=days_since_high,
            recent_high_days=recent_high_days,
            volatility_pct=volatility_pct,
            window_return_pct=window_return_pct,
        )

        return StockSelectionCandidate(
            code=stock.code,
            name=stock.name,
            market=stock.market,
            strategy=self.name,
            latest_date=_format_date(latest["date"]),
            latest_close=round(latest_close, 4),
            window_high=round(window_high, 4),
            window_high_date=_format_date(high_row["date"]),
            days_since_high=days_since_high,
            distance_to_high_pct=round(distance_to_high_pct, 4),
            window_return_pct=round(window_return_pct, 4),
            volatility_pct=round(volatility_pct, 4),
            score=round(score, 4),
            source=source,
        )


def _normalize_history(history: pd.DataFrame) -> pd.DataFrame:
    """对历史行情做基础归一化处理，使其满足近新高策略所需的列与类型。

    关键步骤：
    1. 缺失关键列时直接返回空表（调用方将其视为"无信号"）；
    2. 将 ``date`` 转成 ``datetime``，``high`` / ``close`` 转成数值；
    3. 丢弃任一关键字段为 NaN 的行；
    4. 过滤掉价格为 0 的无效数据；
    5. 按日期升序排序后重置索引。
    """
    if history is None or history.empty:
        return pd.DataFrame()

    df = history.copy()
    required = ["date", "high", "close"]
    for column in required:
        if column not in df.columns:
            return pd.DataFrame()

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["high"] = pd.to_numeric(df["high"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=required)
    df = df[df["high"] > 0]
    df = df[df["close"] > 0]
    return df.sort_values("date", ascending=True).reset_index(drop=True)


def _calculate_volatility_pct(close: pd.Series) -> float:
    """根据日收益率计算年化波动率（百分比）。

    Args:
        close: 收盘价序列。

    Returns:
        float: 年化波动率，单位为百分比（如 25.0 表示 25%）。
        数据不足时返回 0.0。
    """
    returns = close.pct_change().dropna()
    if returns.empty:
        return 0.0
    # 252 为 A 股年度交易日惯例近似值
    volatility = float(returns.std(ddof=0) * (252 ** 0.5) * 100.0)
    return 0.0 if pd.isna(volatility) else volatility


def _calculate_score(
    *,
    high_position: float,
    days_since_high: int,
    recent_high_days: int,
    volatility_pct: float,
    window_return_pct: float,
) -> float:
    """构建稳定的排序得分，分值越高越好。

    加权构成：
    * ``high_position`` × 60：相对位置越高越好（核心权重）；
    * ``recency_score`` × 20：新高越近期越好；
    * ``volatility_component`` × 12：适度波动优于死水；
    * ``return_component`` × 8：窗口期累计收益为正加分。
    """
    recency_score = 1.0 - min(max(days_since_high, 0), recent_high_days) / max(recent_high_days, 1)
    volatility_component = max(volatility_pct, 0.0) / 100.0
    return_component = window_return_pct / 100.0
    return high_position * 60.0 + recency_score * 20.0 + volatility_component * 12.0 + return_component * 8.0


def _format_date(value: Any) -> str:
    """把 pandas 时间戳或日期类值格式化为 ``YYYY-MM-DD`` 字符串。"""
    if hasattr(value, "date"):
        return value.date().isoformat()
    return str(value)[:10]
