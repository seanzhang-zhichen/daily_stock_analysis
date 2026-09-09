# -*- coding: utf-8 -*-
"""运行股票选择策略的服务层。

负责加载股票池、按策略筛选/排序候选并汇总诊断信息，
供选股任务与相关 API 使用。
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

import pandas as pd

from src.services.history_loader import load_history_df
from src.services.stock_selection.models import (
    StockSelectionCandidate,
    StockSelectionDiagnostics,
    StockSelectionResult,
    StockSelectionStock,
)
from src.services.stock_selection.new_high import NearNewHighStrategy
from src.services.stock_selection.strategies import StockSelectionStrategyInfo, StockSelectionStrategyRegistry

logger = logging.getLogger(__name__)


# 历史行情加载函数签名：(code, days, target_date) -> (DataFrame | None, source_name)
HistoryLoader = Callable[[str, int, Optional[date]], tuple[Optional[pd.DataFrame], str]]


class StockSelectionService:
    """在股票池上运行已注册的选股策略。"""

    def __init__(
        self,
        *,
        db: Optional[Any] = None,
        registry: Optional[StockSelectionStrategyRegistry] = None,
        history_loader: Optional[HistoryLoader] = None,
        stock_pool_loader: Optional[Callable[[Sequence[str]], List[StockSelectionStock]]] = None,
    ) -> None:
        """初始化服务：默认注入全局数据库、注册表与历史行情加载器。"""
        if db is None:
            from src.storage import get_db

            db = get_db()
        self.db = db
        self.registry = registry or build_default_registry()
        self.history_loader = history_loader or load_history_df
        self._stock_pool_loader = stock_pool_loader or self.load_stock_pool

    def list_strategies(self) -> List[StockSelectionStrategyInfo]:
        """返回已注册选股策略的公开元数据。"""
        return self.registry.list()

    def select(
        self,
        *,
        strategy_name: str = "near_new_high",
        stock_codes: Optional[Sequence[str]] = None,
        markets: Optional[Sequence[str]] = None,
        limit: int = 50,
        target_date: Optional[date] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> StockSelectionResult:
        """运行一次指定名称的选股策略并返回候选结果。"""
        strategy = self.registry.get(strategy_name)
        effective_params = strategy.default_params()
        if params:
            effective_params.update({key: value for key, value in params.items() if value is not None})
        strategy.validate_params(effective_params)

        stocks = self._resolve_stock_pool(stock_codes=stock_codes, markets=markets)
        diagnostics = StockSelectionDiagnostics(total=len(stocks))
        items: List[StockSelectionCandidate] = []

        history_days = max(strategy.required_history_days(effective_params), 1)
        request_days = int(effective_params.get("history_days") or history_days)
        request_days = max(request_days, history_days)

        for stock in stocks:
            try:
                df, source = self.history_loader(stock.code, request_days, target_date)
                if df is None or df.empty:
                    diagnostics = replace(diagnostics, no_data=diagnostics.no_data + 1)
                    continue
                if len(df) < history_days:
                    diagnostics = replace(diagnostics, insufficient_data=diagnostics.insufficient_data + 1)
                    continue

                candidate = strategy.evaluate(
                    stock=stock,
                    history=df,
                    source=source,
                    params=effective_params,
                    target_date=target_date,
                )
                diagnostics = replace(diagnostics, processed=diagnostics.processed + 1)
                if candidate is None:
                    continue
                items.append(candidate)
                diagnostics = replace(diagnostics, matched=diagnostics.matched + 1)
            except Exception as exc:  # noqa: BLE001
                diagnostics = replace(diagnostics, errors=diagnostics.errors + 1)
                logger.warning("Stock selection failed for %s via %s: %s", stock.code, strategy.name, exc)

        items = self._sort_candidates(items, str(effective_params.get("sort_by") or "volatility_then_return"))
        # 上限压到 [1, 500]，防止调用方传入极端值导致返回过大
        max_items = max(1, min(int(limit or 50), 500))
        return StockSelectionResult(
            strategy=strategy.name,
            params=effective_params,
            items=items[:max_items],
            diagnostics=diagnostics,
            generated_at=datetime.now().isoformat(),
            target_date=target_date,
        )

    def _resolve_stock_pool(
        self,
        *,
        stock_codes: Optional[Sequence[str]],
        markets: Optional[Sequence[str]],
    ) -> List[StockSelectionStock]:
        """解析股票池：显式代码列表优先，否则加载市场级股票池。"""
        if stock_codes:
            return self._stocks_from_codes(stock_codes)
        return self._stock_pool_loader(markets or ["cn"])

    def _stocks_from_codes(self, stock_codes: Sequence[str]) -> List[StockSelectionStock]:
        """从显式代码列表构建去重的股票池（按归一化后的代码去重）。"""
        stocks: List[StockSelectionStock] = []
        seen: set[str] = set()
        for raw_code in stock_codes:
            code = _canonical_stock_code(str(raw_code or "").strip())
            if not code:
                continue
            normalized = _canonical_stock_code(_normalize_stock_code(code))
            dedupe_key = normalized or code
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            stocks.append(
                StockSelectionStock(
                    code=normalized,
                    name=self._get_stock_name(code) or self._get_stock_name(normalized),
                    market=self._infer_market(code),
                )
            )
        return stocks

    def load_stock_pool(self, markets: Sequence[str]) -> List[StockSelectionStock]:
        """从数据库股票索引加载在售股票池（按代码去重）。"""
        normalized_markets = self._normalize_markets(markets)
        rows = self._load_index_rows(normalized_markets)
        stocks: List[StockSelectionStock] = []
        seen: set[str] = set()
        for row in rows:
            code = self._selection_code_from_index_row(row)
            if not code or code in seen:
                continue
            seen.add(code)
            stocks.append(
                StockSelectionStock(
                    code=code,
                    name=str(getattr(row, "name_zh", "") or "") or None,
                    market=str(getattr(row, "market", "") or "CN"),
                )
            )
        return stocks

    def _load_index_rows(self, markets: set[str]) -> Iterable[Any]:
        """从本地股票索引读取指定市场内启用的活跃股票行。"""
        from src.storage import StockIndexEntry

        with self.db.get_session() as session:
            return list(
                session.query(StockIndexEntry)
                .filter(StockIndexEntry.active.is_(True))
                .filter(StockIndexEntry.asset_type == "stock")
                .filter(StockIndexEntry.market.in_({m.upper() for m in markets}))
                .order_by(StockIndexEntry.popularity.desc(), StockIndexEntry.display_code.asc())
                .all()
            )

    def _get_stock_name(self, stock_code: str) -> Optional[str]:
        """若能命中本地索引则返回股票中文名，否则返回 None。"""
        try:
            from src.repositories.stock_index_repo import StockIndexRepository

            return StockIndexRepository(self.db).get_name_by_code(stock_code)
        except Exception:
            return None

    @staticmethod
    def _selection_code_from_index_row(row: Any) -> str:
        """按市场取数据源与 daily 模块期望的代码形态（CN 用展示码、HK 用规范码）。"""
        display = str(getattr(row, "display_code", "") or "").strip()
        canonical = str(getattr(row, "canonical_code", "") or "").strip()
        market = str(getattr(row, "market", "") or "").strip().upper()
        if market == "CN":
            return _normalize_stock_code(display or canonical)
        if market == "HK":
            return _normalize_stock_code(canonical or display)
        return _canonical_stock_code(display or canonical)

    @staticmethod
    def _normalize_markets(markets: Sequence[str]) -> set[str]:
        """归一化市场过滤条件（小写去空），未指定时默认 A 股（cn）。"""
        normalized = {str(m or "").strip().lower() for m in markets if str(m or "").strip()}
        return normalized or {"cn"}

    @staticmethod
    def _infer_market(code: str) -> str:
        """根据代码形态粗判所属市场（HK / CN / 其它一律 US）。"""
        normalized = str(code or "").strip().upper()
        if normalized.startswith("HK") or normalized.endswith(".HK"):
            return "HK"
        if normalized.isdigit() or normalized.endswith((".SH", ".SZ", ".SS", ".BJ")):
            return "CN"
        return "US"

    @staticmethod
    def _sort_candidates(
        items: List[StockSelectionCandidate],
        sort_by: str,
    ) -> List[StockSelectionCandidate]:
        """按指定的排序规则给命中候选排序。

        支持 ``return_then_volatility``、``score``，其余（含默认
        ``volatility_then_return``）按波动率优先排序。
        """
        normalized = sort_by.strip().lower()
        if normalized == "return_then_volatility":
            return sorted(
                items,
                key=lambda item: (item.window_return_pct, item.volatility_pct, item.score),
                reverse=True,
            )
        if normalized == "score":
            return sorted(items, key=lambda item: item.score, reverse=True)
        return sorted(
            items,
            key=lambda item: (item.volatility_pct, item.window_return_pct, item.score),
            reverse=True,
        )


def build_default_registry() -> StockSelectionStrategyRegistry:
    """构建运行时的策略注册表（当前只注册逼近新高策略）。"""
    return StockSelectionStrategyRegistry([NearNewHighStrategy()])


def _canonical_stock_code(code: str) -> str:
    """返回大写形式的展示/存储用代码，不引入数据提供方依赖。"""
    return str(code or "").strip().upper()


def _normalize_stock_code(stock_code: str) -> str:
    """将常见 CN / HK 代码写法归一化为标准形式（不依赖 data_provider）。

    支持识别 ``HK00700`` / ``700.HK``、``SH600519`` / ``600519.SH``、``BJ8xxxxx`` 等写法。
    """
    code = str(stock_code or "").strip()
    upper = code.upper()

    if upper.startswith("HK") and not upper.startswith("HK."):
        candidate = upper[2:]
        if candidate.isdigit() and 1 <= len(candidate) <= 5:
            return f"HK{candidate.zfill(5)}"

    if upper.startswith(("SH", "SZ")) and not upper.startswith(("SH.", "SZ.")):
        candidate = code[2:]
        if candidate.isdigit() and len(candidate) in (5, 6):
            return candidate

    if upper.startswith("BJ") and not upper.startswith("BJ."):
        candidate = code[2:]
        if candidate.isdigit() and len(candidate) == 6:
            return candidate

    if "." in code:
        base, suffix = code.rsplit(".", 1)
        suffix_upper = suffix.upper()
        if suffix_upper == "HK" and base.isdigit() and 1 <= len(base) <= 5:
            return f"HK{base.zfill(5)}"
        if suffix_upper in {"SH", "SZ", "SS", "BJ"} and base.isdigit():
            return base

    return code
