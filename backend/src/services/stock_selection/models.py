# -*- coding: utf-8 -*-
"""选股策略共用的数据模型（dataclass）。

这些 dataclass 既是策略层返回结果的载体，也是 API 层序列化的最小单元。
统一不可变（``frozen=True``），避免策略内部状态被下游悄悄改写。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class StockSelectionStock:
    """选股宇宙中的单只股票实体。"""

    code: str
    name: Optional[str] = None
    market: str = "CN"


@dataclass(frozen=True)
class StockSelectionCandidate:
    """命中某条策略的一只候选股票及其评估指标。"""

    code: str
    name: Optional[str]
    market: str
    strategy: str
    latest_date: str
    latest_close: float
    window_high: float
    window_high_date: str
    days_since_high: int
    distance_to_high_pct: float
    window_return_pct: float
    volatility_pct: float
    score: float
    source: str

    def to_dict(self) -> Dict[str, Any]:
        """返回适合 API/JSON 序列化的字典。"""
        return asdict(self)


@dataclass(frozen=True)
class StockSelectionDiagnostics:
    """一次选股运行的统计计数器，便于诊断覆盖率与失败原因分布。"""

    total: int = 0
    processed: int = 0
    matched: int = 0
    no_data: int = 0
    insufficient_data: int = 0
    errors: int = 0
    skipped_unsupported_market: int = 0

    def to_dict(self) -> Dict[str, int]:
        """返回 API 友好字典。"""
        return asdict(self)


@dataclass(frozen=True)
class StockSelectionResult:
    """选股服务向调用方返回的整体结果包：策略、参数、命中项、统计与时间戳。"""

    strategy: str
    params: Dict[str, Any]
    items: List[StockSelectionCandidate] = field(default_factory=list)
    diagnostics: StockSelectionDiagnostics = field(default_factory=StockSelectionDiagnostics)
    generated_at: Optional[str] = None
    target_date: Optional[date] = None

    def to_dict(self) -> Dict[str, Any]:
        """返回 API 友好字典，自动把 ``target_date`` 转为 ISO 字符串。"""
        return {
            "strategy": self.strategy,
            "params": dict(self.params),
            "items": [item.to_dict() for item in self.items],
            "diagnostics": self.diagnostics.to_dict(),
            "generated_at": self.generated_at,
            "target_date": self.target_date.isoformat() if self.target_date else None,
        }
