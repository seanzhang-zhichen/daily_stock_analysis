# -*- coding: utf-8 -*-
"""
数据访问层（Repository）模块初始化。

按职责把每个 Repository 拆成独立文件后, 在此统一 re-export, 保持外部
``from src.repositories import X`` 调用方式不变。

当前导出：

- ``AnalysisRepository``  分析历史读写与统计
- ``BacktestRepository``  回测结果/汇总/候选挑选
- ``StockIndexRepository`` 本地股票搜索索引与缓存
- ``StockRepository``     股票日线数据访问
"""

from src.repositories.analysis_repo import AnalysisRepository
from src.repositories.backtest_repo import BacktestRepository
from src.repositories.stock_index_repo import StockIndexRepository
from src.repositories.stock_repo import StockRepository

__all__ = [
    "AnalysisRepository",
    "BacktestRepository",
    "StockIndexRepository",
    "StockRepository",
]
