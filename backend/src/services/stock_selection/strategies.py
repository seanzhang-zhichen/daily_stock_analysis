# -*- coding: utf-8 -*-
"""选股策略的抽象基类与注册中心。

定义选股策略的统一接口、参数校验与注册表，配合具体策略子类实现
（详见同包下其它策略文件）。核心思想：

- 策略实现只需继承 :class:`StockSelectionStrategy` 并实现 ``evaluate``，
  由 :class:`StockSelectionStrategyRegistry` 负责调度
- 注册表支持主名（``name``）与别名（``aliases``）双轨解析，并对重名/别名冲突抛错
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from src.services.stock_selection.models import StockSelectionCandidate, StockSelectionStock


class StockSelectionStrategy(ABC):
    """可插拔选股策略的抽象基类，所有具体策略均需实现 :meth:`evaluate`。"""

    name: str
    display_name: str
    description: str
    aliases: tuple[str, ...] = ()

    @abstractmethod
    def default_params(self) -> Dict[str, Any]:
        """返回策略默认参数表，供前端展示与运行器初始化。"""

    def required_history_days(self, params: Dict[str, Any]) -> int:
        """评估单只股票所需的最小 K 线行数；默认按 ``lookback_days`` 计算。"""
        return int(params.get("lookback_days", 1) or 1)

    def validate_params(self, params: Dict[str, Any]) -> None:
        """校验策略特定参数；基类默认不做校验，子类可按需覆盖。"""
        return None

    @abstractmethod
    def evaluate(
        self,
        *,
        stock: StockSelectionStock,
        history: pd.DataFrame,
        source: str,
        params: Dict[str, Any],
        target_date: Optional[date] = None,
    ) -> Optional[StockSelectionCandidate]:
        """对单只股票评估是否符合策略，命中时返回候选，未命中时返回 ``None``。

        Args:
            stock: 待评估的股票实体。
            history: 该股票的 K 线/指标历史。
            source: 数据来源标识（如 ``eastmoney`` / ``akshare``）。
            params: 策略参数（已通过 :meth:`validate_params` 校验）。
            target_date: 可选的评估截止日期；缺省时由调用方/数据源决定。
        """


@dataclass(frozen=True)
class StockSelectionStrategyInfo:
    """对外暴露的策略元信息，用于前端策略列表与运行器分发。"""

    name: str
    display_name: str
    description: str
    aliases: List[str]
    default_params: Dict[str, Any]


class StockSelectionStrategyRegistry:
    """轻量级策略注册表，负责主名/别名解析与重复检测。"""

    def __init__(self, strategies: Optional[Iterable[StockSelectionStrategy]] = None) -> None:
        """初始化注册表，可选传入初始策略集合并立即完成注册。"""
        self._strategies: Dict[str, StockSelectionStrategy] = {}
        self._aliases: Dict[str, str] = {}
        for strategy in strategies or []:
            self.register(strategy)

    def register(self, strategy: StockSelectionStrategy) -> None:
        """按主名与别名注册一条策略；主名/别名重复时抛 ``ValueError``。"""
        key = self._normalize_key(strategy.name)
        if key in self._strategies:
            raise ValueError(f"Duplicate stock selection strategy name: {strategy.name!r}")
        self._strategies[key] = strategy
        self._aliases[key] = key
        for alias in strategy.aliases:
            alias_key = self._normalize_key(alias)
            if not alias_key:
                continue
            existing = self._aliases.get(alias_key)
            if existing and existing != key:
                # 同一别名被两个不同主名占用时直接抛错，避免运行时静默路由错误
                raise ValueError(f"Duplicate stock selection strategy alias: {alias!r}")
            self._aliases[alias_key] = key

    def get(self, name: str) -> StockSelectionStrategy:
        """按主名或别名查找；找不到抛出含可用列表的错误，便于上层友好提示。"""
        key = self._normalize_key(name)
        strategy_key = self._aliases.get(key)
        if not strategy_key:
            available = ", ".join(sorted(self._strategies))
            raise ValueError(f"Unsupported stock selection strategy: {name!r}. Available: {available}")
        return self._strategies[strategy_key]

    def list(self) -> List[StockSelectionStrategyInfo]:
        """列出全部已注册策略的对外元信息，供前端展示。"""
        return [
            StockSelectionStrategyInfo(
                name=strategy.name,
                display_name=strategy.display_name,
                description=strategy.description,
                aliases=list(strategy.aliases),
                default_params=strategy.default_params(),
            )
            for strategy in self._strategies.values()
        ]

    @staticmethod
    def _normalize_key(value: str) -> str:
        """归一化主名/别名：去空白、转小写、把 ``-`` 视作 ``_``。"""
        return str(value or "").strip().lower().replace("-", "_")
