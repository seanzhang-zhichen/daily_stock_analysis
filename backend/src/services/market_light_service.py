# -*- coding: utf-8 -*-
"""Market Light 市场信号快照服务。

负责构建结构化市场快照，并从历史分析记录中读取上一次快照，
供结构化告警（structured alerts）对比当前与历史市场状态使用。

主要能力：
- 市场区域标识的归一化与校验（cn/hk/us/jp/kr 及告警区域 cn/hk/us）
- 构建当前快照（不经过 LLM 复盘）
- 按交易日读取最近一次持久化的历史快照
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from sqlalchemy import desc

from src.core.market_review import MARKET_REVIEW_HISTORY_CODE, MARKET_REVIEW_REPORT_TYPE
from src.market_analyzer import MarketAnalyzer
from src.schemas.market_light import MarketLightSnapshot
from src.storage import AnalysisHistory, DatabaseManager


logger = logging.getLogger(__name__)

MARKET_LIGHT_REGIONS = frozenset({"cn", "hk", "us", "jp", "kr"})
MARKET_LIGHT_ALERT_REGIONS = frozenset({"cn", "hk", "us"})
MARKET_LIGHT_HISTORY_BATCH_SIZE = 100


def normalize_market_region(region: str) -> str:
    """归一化并校验市场区域标识（cn/hk/us/jp/kr）。"""
    value = str(region or "").strip().lower()
    if value not in MARKET_LIGHT_REGIONS:
        raise ValueError(f"market target must be one of cn, hk, us, jp, kr: {region}")
    return value


def normalize_market_alert_region(region: str) -> str:
    """归一化并校验告警区域标识（cn/hk/us）。"""
    value = str(region or "").strip().lower()
    if value not in MARKET_LIGHT_ALERT_REGIONS:
        raise ValueError(f"market alert target must be one of cn, hk, us: {region}")
    return value


def build_current_snapshot(region: str) -> Dict[str, Any]:
    """构建当前结构化 Market Light 快照（不经过 LLM 复盘）。

    Args:
        region: 市场区域标识（cn/hk/us/jp/kr）。

    Returns:
        MarketAnalyzer 生成的快照字典。
    """

    normalized_region = normalize_market_region(region)
    analyzer = MarketAnalyzer(region=normalized_region)
    overview = analyzer.get_market_overview()
    return analyzer.build_market_light_snapshot(overview)


def load_previous_snapshot(
    region: str,
    *,
    before_trade_date: str,
    db_manager: Optional[DatabaseManager] = None,
    limit: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """读取 ``before_trade_date`` 之前最近一次持久化的 Market Light 快照。

    扫描时跳过不含 ``market_light_snapshots[region]`` 的历史 market-review 行。

    Args:
        region: 市场区域标识。
        before_trade_date: 截止交易日，只返回该日期之前的快照。
        db_manager: 可选数据库管理器，默认取单例。
        limit: 可选扫描行数上限。

    Returns:
        解析成功的快照字典；无可用快照返回 None。

    Raises:
        ValueError: 找到对应交易日但快照数据非法时抛出。
    """

    normalized_region = normalize_market_region(region)
    cutoff = str(before_trade_date or "").strip()
    if not cutoff:
        return None

    db = db_manager or DatabaseManager.get_instance()
    best_trade_date: Optional[str] = None
    best_snapshot: Optional[Dict[str, Any]] = None
    invalid_target_error: Optional[Exception] = None

    with db.get_session() as session:
        # 只扫描市场复盘历史记录，按写入时间倒序，最新一次复盘通常最先命中
        query = (
            session.query(AnalysisHistory)
            .filter(
                AnalysisHistory.code == MARKET_REVIEW_HISTORY_CODE,
                AnalysisHistory.report_type == MARKET_REVIEW_REPORT_TYPE,
            )
            .order_by(desc(AnalysisHistory.created_at), desc(AnalysisHistory.id))
        )
        if limit is not None:
            query = query.limit(limit)
        for row in query.yield_per(MARKET_LIGHT_HISTORY_BATCH_SIZE):
            snapshot = _extract_region_snapshot(row.context_snapshot, normalized_region)
            if snapshot is None:
                continue
            trade_date = str(snapshot.get("trade_date") or "").strip()
            # 只关心严格早于 cutoff 的交易日的快照
            if not trade_date or trade_date >= cutoff:
                continue
            # 遇到更大的交易日说明出现了更新的候选，重置之前记录的旧快照
            if best_trade_date is None or trade_date > best_trade_date:
                best_trade_date = trade_date
                best_snapshot = None
                invalid_target_error = None
            elif trade_date < best_trade_date:
                continue
            try:
                # 用 Pydantic 模型校验并归一化持久化的快照结构
                candidate = MarketLightSnapshot.model_validate(snapshot).model_dump()
            except Exception as exc:
                logger.warning(
                    "invalid persisted market light snapshot: row_id=%s region=%s trade_date=%s error=%s",
                    getattr(row, "id", "?"),
                    normalized_region,
                    trade_date,
                    exc,
                )
                if best_snapshot is None:
                    invalid_target_error = exc
                continue
            if best_snapshot is None:
                best_snapshot = candidate

    if best_snapshot is not None:
        return best_snapshot
    if best_trade_date is not None and invalid_target_error is not None:
        raise ValueError(
            f"invalid persisted market light snapshot for {normalized_region} on {best_trade_date}"
        ) from invalid_target_error
    return None


def _extract_region_snapshot(raw_context_snapshot: Any, region: str) -> Optional[Dict[str, Any]]:
    """从原始上下文快照中提取指定区域的结构化快照字典。"""
    if not raw_context_snapshot:
        return None
    try:
        payload = (
            json.loads(raw_context_snapshot)
            if isinstance(raw_context_snapshot, str)
            else raw_context_snapshot
        )
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    snapshots = payload.get("market_light_snapshots")
    if not isinstance(snapshots, dict):
        return None
    snapshot = snapshots.get(region)
    return snapshot if isinstance(snapshot, dict) else None
