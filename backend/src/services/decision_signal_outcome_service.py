# -*- coding: utf-8 -*-
"""基于日线后验的 A 股决策信号结果评估服务（posterior evaluation）。

在决策信号（decision signal）生成后，对其在不同持有周期（1d/3d/5d/10d）下的实际
收益、最大回撤等表现做"命中/未命中"判定，并把结果入库用于后续统计与回放。

- 仅支持 A 股（``market == "cn"``），其它市场显式拒绝
- 通过 :data:`ENGINE_VERSION` 标识评估算法版本，便于后续算法升级时多版本共存
- 行情数据全部依赖日线（daily bar），不做分钟级推断
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from src.repositories.decision_signal_outcome_repo import DecisionSignalOutcomeRepository
from src.repositories.stock_repo import StockRepository
from src.services.decision_signal_service import DecisionSignalNotFoundError, DecisionSignalService
from src.storage import DatabaseManager
from src.storage import DecisionSignalOutcomeRecord, DecisionSignalRecord
from sqlalchemy import select


# 算法版本号；升级评估逻辑时同步修改并保留旧版本结果以便对比
ENGINE_VERSION = "decision-signal-cn-v1"
# 持有周期（key：外部标识，value：实际交易日天数）
HORIZONS = {"1d": 1, "3d": 3, "5d": 5, "10d": 10}
# 多头方向动作：买入/加仓/持有，期望"上涨"
LONG_ACTIONS = {"buy", "add", "hold"}
# 防御方向动作：减仓/卖出/回避，期望"不涨"
DEFENSIVE_ACTIONS = {"reduce", "sell", "avoid"}


class DecisionSignalOutcomeService:
    """协调决策信号后验评估：拉信号 → 拉行情 → 计算命中 → 写库。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        """注入数据库管理器；默认走全局 :class:`DatabaseManager` 单例。"""
        self.db = db_manager or DatabaseManager.get_instance()
        self.repo = DecisionSignalOutcomeRepository(self.db)
        self.signal_service = DecisionSignalService(self.db)
        self.stock_repo = StockRepository(self.db)

    def run(self, *, signal_id: int, user_id: Optional[int], horizons: Optional[list[str]] = None, force: bool = False) -> dict[str, Any]:
        """对单个信号执行一次后验评估，支持多个 horizon 批量评估。

        Args:
            signal_id: 决策信号 ID。
            user_id: 用于权限校验的用户 ID（``None`` 表示系统调用）。
            horizons: 待评估的持有周期列表；缺省取信号自身的 ``horizon`` 或 ``3d``。
            force: 已完成评估是否重跑；为 ``True`` 时强制覆盖。

        Returns:
            含每条 horizon 的 ``items`` 与 ``created`` / ``updated`` / ``skipped`` 计数的结果。

        Raises:
            ValueError: 信号非 A 股或 horizons 包含未知键。
        """
        signal = self.signal_service.get_signal(signal_id, user_id=user_id)
        if signal["market"] != "cn":
            raise ValueError("Only A-share decision signals support posterior evaluation")
        requested = horizons or [signal.get("horizon") or "3d"]
        invalid = set(requested) - HORIZONS.keys()
        if invalid:
            raise ValueError(f"Unsupported outcome horizons: {', '.join(sorted(invalid))}")

        items, created, updated, skipped = [], 0, 0, 0
        for horizon in requested:
            existing = self.repo.get(signal_id, horizon, ENGINE_VERSION)
            # 已存在且已完成：跳过重跑（force 为 True 时强制覆盖）
            if existing is not None and not force and existing.eval_status == "completed":
                skipped += 1
                items.append(self._serialize(existing))
                continue
            row, is_created = self.repo.upsert(self._evaluate(signal, horizon))
            created += int(is_created)
            updated += int(not is_created)
            items.append(self._serialize(row))
        return {"items": items, "evaluated": created + updated, "created": created, "updated": updated, "skipped": skipped, "engine_version": ENGINE_VERSION}

    def list(self, *, signal_id: int, user_id: Optional[int], horizon: Optional[str], page: int, page_size: int) -> dict[str, Any]:
        """分页查询某信号的历史后验评估结果，并做权限校验。"""
        self.signal_service.get_signal(signal_id, user_id=user_id)
        if horizon and horizon not in HORIZONS:
            raise ValueError(f"Unsupported outcome horizon: {horizon}")
        rows, total = self.repo.list(signal_id=signal_id, horizon=horizon, page=page, page_size=page_size)
        return {"items": [self._serialize(row) for row in rows], "total": total, "page": page, "page_size": page_size}

    def stats(self, *, user_id: Optional[int], horizon: Optional[str] = None) -> dict[str, Any]:
        """Aggregate only outcomes belonging to the requesting user."""
        if horizon and horizon not in HORIZONS:
            raise ValueError(f"Unsupported outcome horizon: {horizon}")
        conditions = [DecisionSignalRecord.user_id == user_id,
                      DecisionSignalOutcomeRecord.engine_version == ENGINE_VERSION]
        if horizon:
            conditions.append(DecisionSignalOutcomeRecord.horizon == horizon)
        with self.db.get_session() as session:
            rows = session.execute(select(DecisionSignalOutcomeRecord).join(
                DecisionSignalRecord, DecisionSignalRecord.id == DecisionSignalOutcomeRecord.signal_id
            ).where(*conditions)).scalars().all()
        total = len(rows)
        completed = [row for row in rows if row.eval_status == "completed"]
        hits = sum(row.outcome == "hit" for row in completed)
        returns = [row.stock_return_pct for row in completed if row.stock_return_pct is not None]
        by_action: dict[str, dict[str, int]] = {}
        for row in rows:
            bucket = by_action.setdefault(row.action or "unknown", {"total": 0, "completed": 0, "hit": 0, "miss": 0, "unable": 0})
            bucket["total"] += 1
            if row.eval_status == "completed":
                bucket["completed"] += 1
                bucket[row.outcome or "miss"] = bucket.get(row.outcome or "miss", 0) + 1
            else:
                bucket["unable"] += 1
        return {"engine_version": ENGINE_VERSION, "horizon": horizon, "total": total,
                "completed": len(completed), "unable": total - len(completed), "hit": hits,
                "miss": sum(row.outcome == "miss" for row in completed),
                "hit_rate_pct": round(hits / len(completed) * 100, 2) if completed else None,
                "avg_stock_return_pct": round(sum(returns) / len(returns), 4) if returns else None,
                "by_action": by_action}

    def _evaluate(self, signal: dict[str, Any], horizon: str) -> dict[str, Any]:
        """对单 horizon 计算命中结果；行情缺失或异常时降级为 ``unable`` 状态。"""
        action = signal["action"]
        base = {"signal_id": signal["id"], "horizon": horizon, "engine_version": ENGINE_VERSION, "action": action, "market": "cn", "eval_window_days": HORIZONS[horizon], "holding_state": "unknown"}
        # 非方向性动作（如 watch）无法判定命中，直接降级
        if action not in LONG_ACTIONS | DEFENSIVE_ACTIONS:
            return {**base, "eval_status": "unable", "unable_reason": "non_directional_action"}

        created_at = signal.get("created_at")
        try:
            analysis_date = datetime.fromisoformat(created_at).date() if created_at else None
        except ValueError:
            analysis_date = None
        if analysis_date is None:
            return {**base, "eval_status": "unable", "unable_reason": "invalid_signal_time"}
        # 锚点日：信号当日（或其后第一个交易日）的收盘价
        start = self.stock_repo.get_start_daily(code=signal["stock_code"], analysis_date=analysis_date)
        if start is None or not self._valid_price(start.close):
            return {**base, "eval_status": "unable", "unable_reason": "missing_anchor_price", "anchor_date": analysis_date}
        # 前向 N 个交易日的行情窗口
        bars = self.stock_repo.get_forward_bars(code=signal["stock_code"], analysis_date=start.date, eval_window_days=HORIZONS[horizon])
        if len(bars) < HORIZONS[horizon]:
            return {**base, "eval_status": "unable", "unable_reason": "insufficient_forward_bars", "anchor_date": start.date, "start_price": start.close}
        end_close = bars[-1].close
        if not self._valid_price(end_close):
            return {**base, "eval_status": "unable", "unable_reason": "invalid_end_close", "anchor_date": start.date, "start_price": start.close}
        start_price = float(start.close)
        return_pct = round((float(end_close) / start_price - 1) * 100, 4)
        # 多头期望"涨"（>0），防御期望"不涨"（<=0）；防御动作包含 0 视为命中
        direction = "up" if action in LONG_ACTIONS else "not_up"
        correct = return_pct > 0 if direction == "up" else return_pct <= 0
        return {**base, "eval_status": "completed", "outcome": "hit" if correct else "miss", "direction_expected": direction, "direction_correct": correct, "anchor_date": start.date, "start_price": start_price, "end_close": float(end_close), "max_high": max(float(bar.high or bar.close) for bar in bars), "min_low": min(float(bar.low or bar.close) for bar in bars), "stock_return_pct": return_pct}

    @staticmethod
    def _valid_price(value: Any) -> bool:
        """判断数值是否为合法价格：必须为正数（bool 视为非法以排除 True==1）。"""
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0

    @staticmethod
    def _serialize(row) -> dict[str, Any]:
        """把 ORM 行序列化为 API 字典，日期/时间字段转为 ISO 字符串。"""
        return {key: (value.isoformat() if hasattr(value, "isoformat") else value) for key, value in {
            "id": row.id, "signal_id": row.signal_id, "horizon": row.horizon, "engine_version": row.engine_version, "eval_status": row.eval_status, "outcome": row.outcome, "direction_expected": row.direction_expected, "direction_correct": row.direction_correct, "unable_reason": row.unable_reason, "anchor_date": row.anchor_date, "eval_window_days": row.eval_window_days, "start_price": row.start_price, "end_close": row.end_close, "max_high": row.max_high, "min_low": row.min_low, "stock_return_pct": row.stock_return_pct, "action": row.action, "market": row.market, "holding_state": row.holding_state, "created_at": row.created_at, "updated_at": row.updated_at}.items()}


__all__ = ["DecisionSignalOutcomeService", "ENGINE_VERSION", "HORIZONS"]
