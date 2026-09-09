# -*- coding: utf-8 -*-
"""股票选股服务包。

对外暴露选股候选、诊断与结果等数据模型，以及核心选股服务。
"""

from src.services.stock_selection.models import (
    StockSelectionCandidate,
    StockSelectionDiagnostics,
    StockSelectionResult,
    StockSelectionStock,
)
from src.services.stock_selection.service import StockSelectionService

__all__ = [
    "StockSelectionCandidate",
    "StockSelectionDiagnostics",
    "StockSelectionResult",
    "StockSelectionService",
    "StockSelectionStock",
]
