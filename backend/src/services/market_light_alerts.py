# -*- coding: utf-8 -*-
"""Market Light 大盘告警规则的运行时辅助。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from src.core.trading_calendar import get_open_markets_today
from src.schemas.market_light import MarketLightSnapshot
from src.services.market_light_service import (
    build_current_snapshot,
    load_previous_snapshot,
    normalize_market_alert_region,
)
from src.services.portfolio_alerts import RuntimeAlertPayload


MARKET_ALERT_TYPES = frozenset({"market_light_status", "market_light_score_drop"})
MARKET_STATUS_VALUES = frozenset({"red", "yellow"})
MARKET_REGION_LABELS = {
    "cn": "A股大盘",
    "hk": "港股大盘",
    "us": "美股大盘",
    "jp": "日股大盘",
    "kr": "韩股大盘",
}
MARKET_LIGHT_DATA_SOURCE = "market_light"


@dataclass
class MarketLightAlert:
    """大盘级 Market Light 告警的运行时规则对象。

    Attributes:
        target_scope: 告警作用域（如 market / portfolio）。
        target: 市场区域码（cn/hk/us/...），构造时会被归一化。
        alert_type: 告警类型，取值见 MARKET_ALERT_TYPES。
        parameters: 已规范化的规则参数。
        metadata: 透传给告警记录的附加信息（规则 ID、是否交易日等）。
        description: 告警展示文案。
        stock_code: 与 target 同值，用于复用统一的告警数据结构。
    """

    target_scope: str
    target: str
    alert_type: str
    parameters: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)
    description: str = ""
    stock_code: str = ""

    def __post_init__(self) -> None:
        """初始化后归一化区域码，并让 stock_code 与 target 保持一致。"""
        # 统一归一化区域码，并让 stock_code 与 target 保持一致以便复用通用告警结构
        self.target = normalize_market_alert_region(self.target)
        self.stock_code = self.target


def normalize_market_alert_parameters(alert_type: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    """校验并规范化大盘告警参数，只保留该类型真正需要的字段。

    Args:
        alert_type: 告警类型。
        parameters: 用户提交的原始参数。

    Returns:
        ``market_light_status`` 返回 ``{"statuses": [...]}``；
        ``market_light_score_drop`` 返回 ``{"min_drop": float}``。

    Raises:
        ValueError: 类型不支持、参数不是对象、状态值非法或 min_drop 非正数。
    """
    if alert_type not in MARKET_ALERT_TYPES:
        raise ValueError(f"unsupported market alert_type: {alert_type}")
    if not isinstance(parameters, dict):
        raise ValueError("parameters must be an object")

    if alert_type == "market_light_status":
        raw_statuses = parameters.get("statuses")
        # 未指定状态时默认同时关注红灯与黄灯
        if raw_statuses is None:
            raw_statuses = ["red", "yellow"]
        if isinstance(raw_statuses, str):
            raw_statuses = [raw_statuses]
        if not isinstance(raw_statuses, list) or not raw_statuses:
            raise ValueError("market_light_status statuses must be a non-empty list")
        statuses = []
        for raw_status in raw_statuses:
            status = str(raw_status or "").strip().lower()
            if status not in MARKET_STATUS_VALUES:
                raise ValueError("market_light_status statuses only supports red or yellow")
            if status not in statuses:
                statuses.append(status)
        return {"statuses": statuses}

    min_drop = _positive_float(parameters.get("min_drop"), "min_drop")
    return {"min_drop": min_drop}


def make_market_light_payload(
    *,
    parent_key: str,
    data: Dict[str, Any],
    config: Optional[Any] = None,
) -> RuntimeAlertPayload:
    """把大盘红绿灯告警参数组装成可评估的运行时规则载荷。"""
    region = normalize_market_alert_region(data["target"])
    if config is None:
        from src.config import get_config

        config = get_config()
    trading_day_check_enabled = bool(getattr(config, "trading_day_check_enabled", True))
    market_is_open = True
    if trading_day_check_enabled:
        market_is_open = region in get_open_markets_today()

    display_target = MARKET_REGION_LABELS.get(region, region)
    rule = MarketLightAlert(
        target_scope=data["target_scope"],
        target=region,
        alert_type=data["alert_type"],
        parameters=dict(data.get("parameters") or {}),
        metadata={
            "persisted_rule_id": data["id"],
            "user_id": data.get("user_id"),
            "effective_target": region,
            "display_target": display_target,
            "trading_day_check_enabled": trading_day_check_enabled,
            "market_is_open": market_is_open,
        },
        description=data.get("name") or data["alert_type"],
    )
    return RuntimeAlertPayload(
        key=f"{parent_key}|{region}",
        rule=rule,
        effective_target=region,
        display_target=display_target,
    )


def evaluate_market_light_alert(
    rule: MarketLightAlert,
    *,
    current_snapshot: Optional[Dict[str, Any]] = None,
    cache: Optional[Dict[Any, Any]] = None,
) -> Dict[str, Any]:
    """对单条大盘告警规则求值，返回统一结构的告警结果字典。

    Args:
        rule: 待求值的运行时规则。
        current_snapshot: 已有的当前快照；为空时通过 cache 或实时构建。
        cache: 按区域复用快照的缓存，避免同批告警重复拉取。

    Returns:
        含 ``status`` / ``triggered`` / ``observed_value`` / ``threshold`` /
        ``record_status`` / ``reason`` / ``diagnostics`` 等字段的结果字典。
        数据不可用时不会抛异常，而是返回 ``triggered=False`` 并标注
        ``record_status`` 为 ``skipped`` 或 ``degraded``。
    """
    # 非交易日直接跳过：大盘数据不更新，告警只会产生噪音
    if rule.metadata.get("trading_day_check_enabled") and not rule.metadata.get("market_is_open", True):
        return _market_result(
            rule,
            triggered=False,
            observed_value=None,
            threshold=_threshold(rule),
            message=f"{rule.target} market is not a trading day",
            record_status="skipped",
            diagnostics={"region": rule.target, "trading_day_check": "closed"},
        )

    try:
        snapshot = current_snapshot or _cached_current_snapshot(rule.target, cache)
        current = MarketLightSnapshot.model_validate(snapshot)
        data_timestamp = parse_trade_date_to_datetime(current.trade_date)
    except Exception as exc:
        return _market_result(
            rule,
            triggered=False,
            observed_value=None,
            threshold=_threshold(rule),
            message=f"market light snapshot unavailable: {exc}",
            record_status="degraded",
            diagnostics={"region": rule.target, "error": str(exc)[:200]},
        )

    if current.data_quality == "unavailable":
        return _market_result(
            rule,
            triggered=False,
            observed_value=None,
            threshold=_threshold(rule),
            message="market light data is unavailable",
            record_status="skipped",
            data_timestamp=data_timestamp,
            diagnostics=_base_diagnostics(current),
        )

    if rule.alert_type == "market_light_status":
        return _evaluate_status(rule, current, data_timestamp)
    if rule.alert_type == "market_light_score_drop":
        return _evaluate_score_drop(rule, current, data_timestamp)

    return _market_result(
        rule,
        triggered=False,
        observed_value=None,
        threshold=None,
        message=f"unsupported market alert_type: {rule.alert_type}",
        record_status="failed",
        diagnostics={"region": rule.target, "error": "unsupported_market_alert_type"},
    )


def parse_trade_date_to_datetime(trade_date: str) -> datetime:
    """把快照的交易日字符串解析为 ``datetime``。"""
    return datetime.fromisoformat(str(trade_date))


def _cached_current_snapshot(region: str, cache: Optional[Dict[Any, Any]]) -> Dict[str, Any]:
    """按区域取当前快照；传入 cache 时按 ("market_light", region) 复用。"""
    if cache is None:
        return build_current_snapshot(region)
    cache_key = ("market_light", region)
    if cache_key not in cache:
        cache[cache_key] = build_current_snapshot(region)
    return cache[cache_key]


def _evaluate_status(
    rule: MarketLightAlert,
    current: MarketLightSnapshot,
    data_timestamp: datetime,
) -> Dict[str, Any]:
    """对 ``market_light_status`` 类规则求值：当前 status 命中白名单即触发。"""
    statuses = set(rule.parameters.get("statuses") or ["red", "yellow"])
    triggered = current.status in statuses
    diagnostics = _base_diagnostics(current)
    if current.data_quality == "partial":
        diagnostics["missing_dimensions"] = _missing_dimensions(current)
    return _market_result(
        rule,
        triggered=triggered,
        observed_value=float(current.score),
        threshold=None,
        message=(
            f"Market Light status {current.status} matched {sorted(statuses)}"
            if triggered
            else f"Market Light status {current.status} did not match {sorted(statuses)}"
        ),
        data_timestamp=data_timestamp,
        diagnostics=diagnostics,
    )


def _evaluate_score_drop(
    rule: MarketLightAlert,
    current: MarketLightSnapshot,
    data_timestamp: datetime,
) -> Dict[str, Any]:
    """评估"分数跳水"告警：对比上一交易日的红绿灯快照分差是否超过阈值。"""
    min_drop = float(rule.parameters["min_drop"])
    try:
        raw_previous = load_previous_snapshot(rule.target, before_trade_date=current.trade_date)
        previous = MarketLightSnapshot.model_validate(raw_previous) if raw_previous else None
    except Exception as exc:
        return _market_result(
            rule,
            triggered=False,
            observed_value=float(current.score),
            threshold=min_drop,
            message=f"previous market light snapshot unavailable: {exc}",
            record_status="degraded",
            data_timestamp=data_timestamp,
            diagnostics={**_base_diagnostics(current), "error": str(exc)[:200]},
        )

    if previous is None:
        return _market_result(
            rule,
            triggered=False,
            observed_value=float(current.score),
            threshold=min_drop,
            message="previous market light snapshot not found",
            record_status="skipped",
            data_timestamp=data_timestamp,
            diagnostics=_base_diagnostics(current),
        )

    # 上一份快照必须严格早于当前交易日，否则"下跌"无从谈起（数据重复或未更新）
    if previous.trade_date >= current.trade_date:
        return _market_result(
            rule,
            triggered=False,
            observed_value=float(current.score),
            threshold=min_drop,
            message="previous market light snapshot is not before current trade_date",
            record_status="skipped",
            data_timestamp=data_timestamp,
            diagnostics={
                **_base_diagnostics(current),
                "prev_trade_date": previous.trade_date,
                "prev_score": previous.score,
            },
        )

    if previous.data_quality == "unavailable":
        return _market_result(
            rule,
            triggered=False,
            observed_value=float(current.score),
            threshold=min_drop,
            message="previous market light data is unavailable",
            record_status="skipped",
            data_timestamp=data_timestamp,
            diagnostics={
                **_base_diagnostics(current),
                "prev_trade_date": previous.trade_date,
                "prev_score": previous.score,
                "prev_data_quality": previous.data_quality,
            },
        )

    drop = float(previous.score - current.score)
    triggered = drop >= min_drop
    partial_comparison = current.data_quality == "partial" or previous.data_quality == "partial"
    diagnostics = {
        **_base_diagnostics(current),
        "prev_trade_date": previous.trade_date,
        "prev_score": previous.score,
        "drop": drop,
    }
    if partial_comparison:
        diagnostics["partial_comparison"] = True
        diagnostics["missing_dimensions"] = sorted(
            set(_missing_dimensions(current)) | set(_missing_dimensions(previous))
        )
    return _market_result(
        rule,
        triggered=triggered,
        observed_value=float(current.score),
        threshold=min_drop,
        message=(
            f"Market Light score dropped {drop:.1f} points from {previous.score} to {current.score}"
            if triggered
            else f"Market Light score drop {drop:.1f} points is below {min_drop:g}"
        ),
        data_timestamp=data_timestamp,
        diagnostics=diagnostics,
    )


def _market_result(
    rule: MarketLightAlert,
    *,
    triggered: bool,
    observed_value: Optional[float],
    threshold: Optional[float],
    message: str,
    record_status: Optional[str] = None,
    data_timestamp: Optional[datetime] = None,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """组装大盘告警的标准化评估结果（统一 rule_id/status/阈值与诊断字段）。"""
    effective_status = "triggered" if triggered else "not_triggered"
    if triggered and record_status is None:
        record_status = "triggered"
    return {
        "rule_id": int(rule.metadata.get("persisted_rule_id", 0) or 0),
        "status": effective_status,
        "record_status": record_status,
        "triggered": triggered,
        "observed_value": observed_value,
        "threshold": threshold,
        "data_source": MARKET_LIGHT_DATA_SOURCE,
        "data_timestamp": data_timestamp,
        "reason": message,
        "message": message,
        "diagnostics": json.dumps(diagnostics or {}, ensure_ascii=False, sort_keys=True),
    }


def _threshold(rule: MarketLightAlert) -> Optional[float]:
    """取出该规则的数值阈值；状态类告警无阈值返回 None。"""
    if rule.alert_type == "market_light_score_drop":
        return float(rule.parameters.get("min_drop", 0) or 0)
    return None


def _base_diagnostics(snapshot: MarketLightSnapshot) -> Dict[str, Any]:
    """提取快照的基础诊断信息：区域、交易日、数据质量。"""
    return {
        "region": snapshot.region,
        "trade_date": snapshot.trade_date,
        "data_quality": snapshot.data_quality,
    }


def _missing_dimensions(snapshot: MarketLightSnapshot) -> list[str]:
    """返回快照里所有 ``available=False`` 的维度名（按字典序排序）。"""
    dimensions = snapshot.dimensions.model_dump()
    return sorted(name for name, item in dimensions.items() if not item.get("available"))


def _positive_float(value: Any, field_name: str) -> float:
    """把入参转成正数浮点数，转换失败或非正数时抛 ValueError。"""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field_name}: {value}") from exc
    if number <= 0:
        raise ValueError(f"{field_name} must be > 0")
    return number
