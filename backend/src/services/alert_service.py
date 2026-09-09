# -*- coding: utf-8 -*-
"""告警规则服务的业务逻辑层（Alert API MVP）。

对外为 FastAPI 路由层（`backend/api/v1/endpoints/...`）提供：
- 规则的增删改查（CRUD）与启停切换；
- 单规则 dry-run 试算（直接评估、批量评估两路）；
- 触发历史与通知历史的查询与序列化；
- 规则运行所需的标准化（含 parameter 校验、cooldown 计算）。

底层委托：规则/触发/通知的持久化交给 `AlertRepository`；
真实行情、量价、技术指标的判定分别走 `src.services.portfolio_alerts`、
`src.services.market_light_alerts`、`src.services.alert_indicators`。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from src.agent.events import (
    EventMonitor,
    PriceAlert,
    PriceChangeAlert,
    VolumeAlert,
    _read_quote_float,
    validate_event_alert_rule,
)
from src.repositories.alert_repo import AlertRepository
from src.services.alert_indicators import (
    TECHNICAL_ALERT_TYPES,
    TechnicalIndicatorAlert,
    compute_requested_days,
    evaluate_indicator_alert,
    normalize_indicator_parameters,
    threshold_for_indicator,
)
from src.services.portfolio_alerts import (
    DRY_RUN_TARGET_TIMEOUT_SECONDS,
    DRY_RUN_TOTAL_TIMEOUT_SECONDS,
    PORTFOLIO_ALERT_TYPES,
    SYMBOL_BATCH_TARGET_SCOPES,
    PortfolioRiskAlert,
    RuntimeAlertPayload,
    StaticAlertEvaluation,
    aggregate_dry_run_results,
    ensure_active_portfolio_account,
    evaluate_portfolio_risk_alert,
    evaluate_static_alert,
    expand_symbol_targets,
    make_portfolio_risk_payload,
    make_static_payload,
    normalize_batch_target_scope_target,
    normalize_portfolio_alert_parameters,
    portfolio_effective_target,
    result_to_target_result,
)
from src.services.market_light_alerts import (
    MARKET_ALERT_TYPES,
    MARKET_LIGHT_DATA_SOURCE,
    MarketLightAlert,
    evaluate_market_light_alert,
    make_market_light_payload,
    normalize_market_alert_parameters,
)
from src.services.market_light_service import normalize_market_alert_region
from src.services.decision_signal_summary import summarize_decision_signal
from src.analysis_context_pack_overview import (
    ANALYSIS_CONTEXT_PACK_OVERVIEW_KEY,
    extract_analysis_context_pack_overview,
)
from src.market_phase_summary import MARKET_PHASE_SUMMARY_KEY, extract_market_phase_summary
from src.storage import (
    AlertCooldownRecord,
    AlertNotificationRecord,
    AlertRuleRecord,
    AlertTriggerRecord,
    DatabaseManager,
)
from src.utils.sanitize import sanitize_diagnostic_text


# 传统"运行时"告警类型：价格穿越/涨跌幅/成交量异动；这些规则仍走单标的实时评估
LEGACY_RUNTIME_ALERT_TYPES = frozenset({"price_cross", "price_change_percent", "volume_spike"})
SYMBOL_ALERT_TYPES = LEGACY_RUNTIME_ALERT_TYPES | TECHNICAL_ALERT_TYPES
SUPPORTED_ALERT_TYPES = SYMBOL_ALERT_TYPES | PORTFOLIO_ALERT_TYPES | MARKET_ALERT_TYPES
# 所有受支持的 target_scope：单标的、自选股、组合持仓、组合账户、市场灯
SUPPORTED_TARGET_SCOPES = frozenset({"single_symbol", "watchlist", "portfolio_holdings", "portfolio_account", "market"})
SUPPORTED_SEVERITIES = frozenset({"info", "warning", "critical"})
# 更新规则时允许显式置 None 的字段（保留现状语义）
NULLABLE_RULE_UPDATE_FIELDS = frozenset({"cooldown_policy", "notification_policy"})

logger = logging.getLogger(__name__)


class AlertServiceError(ValueError):
    """告警服务输入校验失败时抛出的基础异常。"""

    error_code = "validation_error"


class AlertNotFoundError(AlertServiceError):
    """请求的资源（规则/触发/通知）不存在。"""

    error_code = "not_found"


class UnsupportedAlertTypeError(AlertServiceError):
    """API 接收到尚未实现或非运行时支持的告警类型。"""

    error_code = "unsupported_alert_type"


class AlertService:
    """告警规则 CRUD 与 dry-run 评估的业务逻辑。

    通过 `db_manager` 拿到数据库会话，把所有 SQL 访问委托给 `AlertRepository`，
    本类只负责参数标准化、跨字段校验、序列化与触发状态编排。
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        """初始化：获取 DatabaseManager 单例并创建告警仓库。

        未显式传入 db_manager 时取全局单例，便于直接以默认配置运行。
        """
        self.db = db_manager or DatabaseManager.get_instance()
        self.repo = AlertRepository(self.db)

    def create_rule(self, payload: Dict[str, Any], *, user_id: Optional[int] = None) -> Dict[str, Any]:
        """创建一条告警规则，返回序列化结果。"""
        fields = self._normalize_rule_payload(payload)
        if user_id is not None:
            fields["user_id"] = user_id
        return self._serialize_rule(self.repo.create_rule(fields))

    def get_rule(self, rule_id: int, *, user_id: Optional[int] = None) -> Dict[str, Any]:
        """按 ID 取单条规则；不存在时抛 AlertNotFoundError。"""
        row = self.repo.get_rule(rule_id, user_id=user_id)
        if row is None:
            raise AlertNotFoundError(f"Alert rule not found: {rule_id}")
        return self._serialize_rule(row)

    def update_rule(self, rule_id: int, payload: Dict[str, Any], *, user_id: Optional[int] = None) -> Dict[str, Any]:
        """局部更新规则字段；payload 为空时报错，不存在的规则抛 AlertNotFoundError。

        先校验 rule 存在并对 payload 做"非空字段校验"，再把旧值与新值合并
        后整体重新走一次标准化流程，确保写入数据库的字段自洽。
        """
        row = self.repo.get_rule(rule_id, user_id=user_id)
        if row is None:
            raise AlertNotFoundError(f"Alert rule not found: {rule_id}")
        if not payload:
            raise AlertServiceError("No fields provided for update")
        self._validate_rule_update_payload(payload)

        merged = self._serialize_rule_base(row)
        merged.update(payload)
        fields = self._normalize_rule_payload(merged, source=merged.get("source") or "api")
        updated = self.repo.update_rule(rule_id, fields, user_id=user_id)
        if updated is None:
            raise AlertNotFoundError(f"Alert rule not found: {rule_id}")
        return self._serialize_rule(updated)

    def delete_rule(self, rule_id: int, *, user_id: Optional[int] = None) -> bool:
        """删除规则；底层实现决定是否级联清理触发/通知记录。"""
        return self.repo.delete_rule(rule_id, user_id=user_id)

    def enable_rule(self, rule_id: int, enabled: bool, *, user_id: Optional[int] = None) -> Dict[str, Any]:
        """切换规则的启用状态。"""
        updated = self.repo.update_rule(rule_id, {"enabled": enabled}, user_id=user_id)
        if updated is None:
            raise AlertNotFoundError(f"Alert rule not found: {rule_id}")
        return self._serialize_rule(updated)

    def list_rules(
        self,
        *,
        enabled: Optional[bool] = None,
        alert_type: Optional[str] = None,
        target_scope: Optional[str] = None,
        target: Optional[str] = None,
        source: Optional[str] = None,
        user_id: Optional[int] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """分页查询规则；返回 items + total + 分页元信息。"""
        rows, total = self.repo.list_rules(
            enabled=enabled,
            alert_type=alert_type,
            target_scope=target_scope,
            target=target,
            source=source,
            user_id=user_id,
            page=page,
            page_size=page_size,
        )
        return {
            "items": [self._serialize_rule(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def test_rule(self, rule_id: int, *, user_id: Optional[int] = None) -> Dict[str, Any]:
        """对单条规则做 dry-run 试算。

        单标的目标走快速路径；组合/市场/批量目标走并发评估并由
        ``aggregate_dry_run_results`` 汇总。任何抛出都会被转成
        ``evaluation_error`` 结构返回，避免试算调用把 5xx 透出去。
        """
        row = self.repo.get_rule(rule_id, user_id=user_id)
        if row is None:
            raise AlertNotFoundError(f"Alert rule not found: {rule_id}")

        payloads = self.build_runtime_payloads(row)
        monitor = EventMonitor()
        try:
            if len(payloads) == 1 and row.target_scope == "single_symbol":
                result = asyncio.run(self._evaluate_rule(payloads[0].rule, monitor, daily_cache=None))
                return self._dry_run_response_for_single(payloads[0], result, target_scope=row.target_scope)
            results = asyncio.run(self._evaluate_runtime_payloads(payloads, monitor))
            return aggregate_dry_run_results(rule_id, row.target_scope, results)
        except Exception as exc:
            sanitized_message = self._sanitize_text(str(exc) or "Alert evaluation failed")
            return {
                "rule_id": rule_id,
                "target_scope": row.target_scope,
                "status": "evaluation_error",
                "record_status": "failed",
                "triggered": False,
                "observed_value": None,
                "threshold": None,
                "data_source": None,
                "data_timestamp": None,
                "reason": sanitized_message,
                "message": sanitized_message,
                "evaluated_count": 0,
                "triggered_count": 0,
                "degraded_count": 0,
                "skipped_count": 0,
                "target_results": [],
            }

    async def _evaluate_rule(
        self,
        rule,
        monitor: EventMonitor,
        daily_cache: Optional[Dict[Any, Any]] = None,
    ) -> Dict[str, Any]:
        """按规则类型分发到对应的评估器；不识别则返回 evaluation_error。"""
        if isinstance(rule, PriceAlert):
            return await self._evaluate_price(rule, monitor)
        if isinstance(rule, PriceChangeAlert):
            return await self._evaluate_price_change(rule, monitor)
        if isinstance(rule, VolumeAlert):
            return await self._evaluate_volume(rule)
        if isinstance(rule, TechnicalIndicatorAlert):
            return await self._evaluate_technical_indicator(rule, daily_cache=daily_cache)
        # 组合风险与市场灯是 CPU/IO 密集型，丢到默认线程池避免阻塞事件循环
        if isinstance(rule, PortfolioRiskAlert):
            return await asyncio.to_thread(evaluate_portfolio_risk_alert, rule)
        if isinstance(rule, MarketLightAlert):
            return await asyncio.to_thread(evaluate_market_light_alert, rule, cache=daily_cache)
        if isinstance(rule, StaticAlertEvaluation):
            return evaluate_static_alert(rule)
        return self._evaluation_error(rule, f"unsupported runtime alert type: {rule.alert_type}")

    async def _evaluate_runtime_payloads(
        self,
        payloads: List[RuntimeAlertPayload],
        monitor: EventMonitor,
    ) -> List[Dict[str, Any]]:
        """并发评估一批 payload；总耗时受 DRY_RUN 总超时控制，单条受目标超时控制。"""
        # 并发上限 8，避免短时间内打爆数据源
        semaphore = asyncio.Semaphore(8)
        daily_cache: Dict[Any, Any] = {}

        async def _evaluate_one(payload: RuntimeAlertPayload) -> Dict[str, Any]:
            """并发评估单条规则：受信号量限流与单目标超时保护，异常映射为 failed。"""
            async with semaphore:
                try:
                    result = await asyncio.wait_for(
                        self._evaluate_rule(payload.rule, monitor, daily_cache=daily_cache),
                        timeout=DRY_RUN_TARGET_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    # 单目标超时单独标记为 skipped，不阻塞其余目标
                    result = {
                        "rule_id": self._runtime_rule_id(payload.rule),
                        "status": "not_triggered",
                        "record_status": "skipped",
                        "triggered": False,
                        "observed_value": None,
                        "threshold": self._threshold_for_rule(payload.rule),
                        "data_source": self._data_source_for_rule(payload.rule),
                        "data_timestamp": None,
                        "reason": "dry-run evaluation timed out",
                        "message": "dry-run evaluation timed out",
                    }
                except Exception as exc:
                    sanitized_message = self._sanitize_text(str(exc) or "Alert evaluation failed")
                    result = {
                        "rule_id": self._runtime_rule_id(payload.rule),
                        "status": "evaluation_error",
                        "record_status": "failed",
                        "triggered": False,
                        "observed_value": None,
                        "threshold": self._threshold_for_rule(payload.rule),
                        "data_source": self._data_source_for_rule(payload.rule),
                        "data_timestamp": None,
                        "reason": sanitized_message,
                        "message": sanitized_message,
                    }
                return result_to_target_result(payload, result)

        tasks = [asyncio.create_task(_evaluate_one(payload)) for payload in payloads]
        # 等全部完成或总超时，剩余未完成的任务会被取消并标记为 skipped
        done, pending = await asyncio.wait(tasks, timeout=DRY_RUN_TOTAL_TIMEOUT_SECONDS)
        for task in pending:
            task.cancel()
        output: List[Dict[str, Any]] = []
        for task in done:
            output.append(task.result())
        for task, payload in zip(tasks, payloads):
            if task in pending:
                output.append({
                    "target": payload.effective_target,
                    "display_target": payload.display_target,
                    "status": "not_triggered",
                    "record_status": "skipped",
                    "triggered": False,
                    "observed_value": None,
                    "threshold": None,
                    "message": "dry-run evaluation timed out",
                })
        return output

    @staticmethod
    def _dry_run_response_for_single(payload: RuntimeAlertPayload, result: Dict[str, Any], *, target_scope: str) -> Dict[str, Any]:
        """单目标 dry-run 的统一响应结构，便于 API 层零分支处理。"""
        target_result = result_to_target_result(payload, result)
        response = {
            "rule_id": result.get("rule_id") or 0,
            "target_scope": target_scope,
            "status": result.get("status") or "evaluation_error",
            "triggered": bool(result.get("triggered")),
            "observed_value": result.get("observed_value"),
            "message": result.get("message") or result.get("reason") or "",
            "evaluated_count": 1,
            "triggered_count": 1 if result.get("triggered") else 0,
            "degraded_count": 1 if result.get("record_status") == "degraded" else 0,
            "skipped_count": 1 if result.get("record_status") == "skipped" else 0,
            "target_results": [target_result],
        }
        return response

    async def _evaluate_price(self, rule: PriceAlert, monitor: EventMonitor) -> Dict[str, Any]:
        """评估"价格穿越"规则：拉一次实时行情并与方向/阈值比较。"""
        threshold = float(rule.price)
        try:
            quote = await monitor._get_realtime_quote(rule.stock_code)
        except Exception as exc:
            return self._evaluation_error(
                rule,
                exc,
                threshold=threshold,
                data_source="realtime_quote",
            )
        if quote is None:
            return self._not_triggered(
                rule,
                None,
                "No realtime quote available",
                record_status="skipped",
                threshold=threshold,
                data_source="realtime_quote",
            )

        try:
            current_price = float(getattr(quote, "price", 0) or 0)
        except (TypeError, ValueError) as exc:
            return self._evaluation_error(
                rule,
                exc,
                threshold=threshold,
                data_source="realtime_quote",
                data_timestamp=self._extract_quote_datetime(quote),
            )
        # 价格字段缺失或为 0 时按 skipped 处理，避免被误判为触发
        if current_price <= 0:
            return self._not_triggered(
                rule,
                None,
                "No valid realtime price available",
                record_status="skipped",
                threshold=threshold,
                data_source="realtime_quote",
                data_timestamp=self._extract_quote_datetime(quote),
            )

        triggered = (
            (rule.direction == "above" and current_price >= rule.price)
            or (rule.direction == "below" and current_price <= rule.price)
        )
        if triggered:
            return self._triggered(
                rule,
                current_price,
                f"{rule.stock_code} price {rule.direction} {rule.price}: current = {current_price}",
                threshold=threshold,
                data_source="realtime_quote",
                data_timestamp=self._extract_quote_datetime(quote),
            )
        return self._not_triggered(
            rule,
            current_price,
            f"{rule.stock_code} price {current_price} did not cross {rule.direction} {rule.price}",
            threshold=threshold,
            data_source="realtime_quote",
            data_timestamp=self._extract_quote_datetime(quote),
        )

    async def _evaluate_price_change(self, rule: PriceChangeAlert, monitor: EventMonitor) -> Dict[str, Any]:
        """评估"涨跌幅"规则：读行情的涨跌幅字段，按方向比较绝对阈值。"""
        threshold = abs(float(rule.change_pct))
        try:
            quote = await monitor._get_realtime_quote(rule.stock_code)
        except Exception as exc:
            return self._evaluation_error(
                rule,
                exc,
                threshold=threshold,
                data_source="realtime_quote",
            )
        if quote is None:
            return self._not_triggered(
                rule,
                None,
                "No realtime quote available",
                record_status="skipped",
                threshold=threshold,
                data_source="realtime_quote",
            )

        # 行情源字段名漂移，按多个候选名顺序取值
        current_change_pct = _read_quote_float(
            quote,
            "change_pct",
            "change_percent",
            "pct_chg",
            "change_rate",
        )
        if current_change_pct is None:
            return self._not_triggered(
                rule,
                None,
                "No valid realtime change percent available",
                record_status="skipped",
                threshold=threshold,
                data_source="realtime_quote",
                data_timestamp=self._extract_quote_datetime(quote),
            )

        direction = rule.direction.lower()
        triggered = (
            (direction == "up" and current_change_pct >= threshold)
            or (direction == "down" and current_change_pct <= -threshold)
        )
        if triggered:
            return self._triggered(
                rule,
                current_change_pct,
                f"{rule.stock_code} change {direction} {threshold:.2f}%: current = {current_change_pct:+.2f}%",
                threshold=threshold,
                data_source="realtime_quote",
                data_timestamp=self._extract_quote_datetime(quote),
            )
        return self._not_triggered(
            rule,
            current_change_pct,
            f"{rule.stock_code} change {current_change_pct:+.2f}% did not cross {direction} {threshold:.2f}%",
            threshold=threshold,
            data_source="realtime_quote",
            data_timestamp=self._extract_quote_datetime(quote),
        )

    async def _evaluate_volume(self, rule: VolumeAlert) -> Dict[str, Any]:
        """评估"成交量异动"规则：取最近 20 个交易日的均值与最新一日比较。

        数据拉取与 K 线技术指标共用 daily_cache；这里直接调
        DataFetcherManager 并允许单条 IO 在线程池内执行。
        """
        def _fetch_daily_data():
            """在线程池中拉取该股票最近 20 个交易日的日线数据。"""
            from data_provider import DataFetcherManager

            return DataFetcherManager().get_daily_data(rule.stock_code, days=20)

        try:
            result = await asyncio.to_thread(_fetch_daily_data)
        except Exception as exc:
            return self._evaluation_error(rule, exc, data_source="daily_data")
        if result is None:
            return self._not_triggered(
                rule,
                None,
                "No daily volume data available",
                record_status="degraded",
                data_source="daily_data",
            )
        if not isinstance(result, tuple) or len(result) != 2:
            return self._not_triggered(
                rule,
                None,
                "Malformed daily volume data response",
                record_status="degraded",
                data_source="daily_data",
            )

        df, _source = result
        if df is None or df.empty:
            return self._not_triggered(
                rule,
                None,
                "No daily volume data available",
                record_status="degraded",
                data_source="daily_data",
            )
        if "volume" not in df:
            return self._not_triggered(
                rule,
                None,
                "daily data missing volume column",
                record_status="degraded",
                data_source="daily_data",
                data_timestamp=self._extract_daily_timestamp(df),
            )

        try:
            avg_vol = float(df["volume"].mean())
            latest_vol = float(df["volume"].iloc[-1])
        except (TypeError, ValueError, IndexError) as exc:
            return self._evaluation_error(
                rule,
                exc,
                data_source="daily_data",
                data_timestamp=self._extract_daily_timestamp(df),
            )
        # 均价 0 表示成交量列全是 0/缺失，无法计算倍量，标记 degraded
        if avg_vol <= 0:
            return self._not_triggered(
                rule,
                latest_vol,
                "Average volume is not available",
                record_status="degraded",
                data_source="daily_data",
                data_timestamp=self._extract_daily_timestamp(df),
            )

        ratio = latest_vol / avg_vol
        threshold = avg_vol * rule.multiplier
        data_timestamp = self._extract_daily_timestamp(df)
        if latest_vol > avg_vol * rule.multiplier:
            return self._triggered(
                rule,
                latest_vol,
                f"{rule.stock_code} volume spike: {latest_vol:,.0f} ({ratio:.1f}x avg)",
                threshold=threshold,
                data_source="daily_data",
                data_timestamp=data_timestamp,
            )
        return self._not_triggered(
            rule,
            latest_vol,
            f"{rule.stock_code} volume ratio {ratio:.1f}x did not exceed {rule.multiplier}x",
            threshold=threshold,
            data_source="daily_data",
            data_timestamp=data_timestamp,
        )

    async def _evaluate_technical_indicator(
        self,
        rule: TechnicalIndicatorAlert,
        *,
        daily_cache: Optional[Dict[tuple[str, int], Any]] = None,
    ) -> Dict[str, Any]:
        """评估技术指标类规则：按 indicator 计算所需天数拉日线，复用 daily_cache。"""
        requested_days = compute_requested_days(rule.alert_type, rule.indicator_params)
        cache_key = (rule.stock_code, requested_days)

        def _fetch_daily_data():
            """在线程池中拉取指定天数的日线数据用于技术指标计算。"""
            from data_provider import DataFetcherManager

            return DataFetcherManager().get_daily_data(rule.stock_code, days=requested_days)

        try:
            if daily_cache is not None and cache_key in daily_cache:
                result = daily_cache[cache_key]
            else:
                result = await asyncio.to_thread(_fetch_daily_data)
                if daily_cache is not None:
                    daily_cache[cache_key] = result
        except Exception as exc:
            return self._evaluation_error(rule, exc, data_source="daily_data")

        if result is None:
            return self._not_triggered(
                rule,
                None,
                "No daily indicator data available",
                record_status="degraded",
                data_source="daily_data",
            )
        if not isinstance(result, tuple) or len(result) != 2:
            return self._not_triggered(
                rule,
                None,
                "Malformed daily indicator data response",
                record_status="degraded",
                data_source="daily_data",
            )

        df, _source = result
        if df is None or getattr(df, "empty", True):
            return self._not_triggered(
                rule,
                None,
                "No daily indicator data available",
                record_status="degraded",
                data_source="daily_data",
            )

        try:
            evaluation = evaluate_indicator_alert(rule.alert_type, rule.stock_code, rule.indicator_params, df)
        except ValueError as exc:
            # ValueError 通常是参数不合法或窗口不足；标记 degraded 便于前端观察
            return self._not_triggered(
                rule,
                None,
                str(exc),
                record_status="degraded",
                threshold=threshold_for_indicator(rule.alert_type, rule.indicator_params),
                data_source="daily_data",
                data_timestamp=self._extract_daily_timestamp(df),
            )
        except Exception as exc:
            return self._evaluation_error(
                rule,
                exc,
                data_source="daily_data",
                data_timestamp=self._extract_daily_timestamp(df),
            )

        if evaluation.status == "triggered":
            return self._triggered(
                rule,
                evaluation.observed_value,
                evaluation.message,
                threshold=evaluation.threshold,
                data_source="daily_data",
                data_timestamp=evaluation.data_timestamp,
            )
        return self._not_triggered(
            rule,
            evaluation.observed_value,
            evaluation.message,
            record_status="degraded" if evaluation.status == "degraded" else None,
            threshold=evaluation.threshold,
            data_source="daily_data",
            data_timestamp=evaluation.data_timestamp,
        )

    def _triggered(
        self,
        rule,
        observed_value: Any,
        message: str,
        *,
        threshold: Optional[float] = None,
        data_source: Optional[str] = None,
        data_timestamp: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """构造"已触发"评估结果，统一记录/告警/UI 字段。"""
        sanitized_message = self._sanitize_text(message)
        return {
            "rule_id": self._runtime_rule_id(rule),
            "status": "triggered",
            "record_status": "triggered",
            "triggered": True,
            "observed_value": observed_value,
            "threshold": threshold,
            "data_source": data_source,
            "data_timestamp": data_timestamp,
            "reason": sanitized_message,
            "message": sanitized_message,
        }

    def _not_triggered(
        self,
        rule,
        observed_value: Any,
        message: str,
        *,
        record_status: Optional[str] = None,
        threshold: Optional[float] = None,
        data_source: Optional[str] = None,
        data_timestamp: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """构造"未触发"评估结果，record_status 可选 degraded/skipped。"""
        sanitized_message = self._sanitize_text(message)
        return {
            "rule_id": self._runtime_rule_id(rule),
            "status": "not_triggered",
            "record_status": record_status,
            "triggered": False,
            "observed_value": observed_value,
            "threshold": threshold,
            "data_source": data_source,
            "data_timestamp": data_timestamp,
            "reason": sanitized_message,
            "message": sanitized_message,
        }

    def _evaluation_error(
        self,
        rule,
        exc: Any,
        *,
        threshold: Optional[float] = None,
        data_source: Optional[str] = None,
        data_timestamp: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """构造"评估异常"结果：threshold/data_source 缺省时按规则类型兜底。"""
        sanitized_message = self._sanitize_text(str(exc) or "Alert evaluation failed")
        return {
            "rule_id": self._runtime_rule_id(rule),
            "status": "evaluation_error",
            "record_status": "failed",
            "triggered": False,
            "observed_value": None,
            "threshold": threshold if threshold is not None else self._threshold_for_rule(rule),
            "data_source": data_source if data_source is not None else self._data_source_for_rule(rule),
            "data_timestamp": data_timestamp,
            "reason": sanitized_message,
            "message": sanitized_message,
        }

    @staticmethod
    def _runtime_rule_id(rule) -> int:
        """从 rule 的 metadata 取持久化规则 ID；运行时纯构造的 rule 取 0。"""
        return int(rule.metadata.get("persisted_rule_id", 0) or 0)

    @staticmethod
    def _threshold_for_rule(rule) -> Optional[float]:
        """按规则类型返回对应的阈值字段，便于在 evaluation_error 中保留展示。"""
        if isinstance(rule, PriceAlert):
            return float(rule.price)
        if isinstance(rule, PriceChangeAlert):
            return abs(float(rule.change_pct))
        if isinstance(rule, TechnicalIndicatorAlert):
            return threshold_for_indicator(rule.alert_type, rule.indicator_params)
        if isinstance(rule, PortfolioRiskAlert):
            return None
        if isinstance(rule, MarketLightAlert):
            if rule.alert_type == "market_light_score_drop":
                return float(rule.parameters.get("min_drop", 0) or 0)
            return None
        return None

    @staticmethod
    def _data_source_for_rule(rule) -> Optional[str]:
        """按规则类型返回主数据源标识符，用于诊断与回填 data_source 字段。"""
        if isinstance(rule, (PriceAlert, PriceChangeAlert)):
            return "realtime_quote"
        if isinstance(rule, VolumeAlert):
            return "daily_data"
        if isinstance(rule, TechnicalIndicatorAlert):
            return "daily_data"
        if isinstance(rule, PortfolioRiskAlert):
            return "portfolio_risk"
        if isinstance(rule, MarketLightAlert):
            return MARKET_LIGHT_DATA_SOURCE
        return None

    @classmethod
    def _extract_quote_datetime(cls, quote: Any) -> Optional[datetime]:
        """按候选字段顺序从行情对象/字典里提取数据时间戳。"""
        for field_name in (
            "data_timestamp",
            "timestamp",
            "quote_time",
            "trade_time",
            "update_time",
            "updated_at",
            "datetime",
            "date",
        ):
            raw_value = cls._read_quote_field(quote, field_name)
            parsed = cls._coerce_datetime(raw_value)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _read_quote_field(quote: Any, field_name: str) -> Any:
        """兼容 dict / 普通对象 / 提供 to_dict() 三种行情数据结构读取字段。"""
        if quote is None:
            return None
        if isinstance(quote, dict):
            return quote.get(field_name)
        raw_value = getattr(quote, field_name, None)
        if raw_value is not None:
            return raw_value
        if hasattr(quote, "to_dict"):
            try:
                return quote.to_dict().get(field_name)
            except Exception:
                return None
        return None

    @classmethod
    def _extract_daily_timestamp(cls, df: Any) -> Optional[datetime]:
        """从日线 DataFrame 提取数据时间戳：先按列名再退回索引。"""
        if df is None or getattr(df, "empty", True):
            return None

        for field_name in ("date", "trade_date", "datetime", "time"):
            if field_name in getattr(df, "columns", []):
                try:
                    parsed = cls._coerce_datetime(df[field_name].iloc[-1])
                except Exception:
                    parsed = None
                if parsed is not None:
                    return parsed

        try:
            index_value = df.index[-1]
            # 数字索引（如按 0..N 编号的伪索引）拒绝，避免错把行号当日期
            if isinstance(index_value, (int, float)):
                return None
            return cls._coerce_datetime(index_value)
        except Exception:
            return None

    @staticmethod
    def _coerce_datetime(value: Any) -> Optional[datetime]:
        """把多种时间表示规整成 naive ``datetime``，无法识别时返回 None。

        整型时间戳含义不明（秒/毫秒/紧凑交易日），仅接受 ``YYYYMMDD``
        形态的整型/字符串，避免错误猜解。
        """
        if value is None:
            return None
        if isinstance(value, datetime):
            # 时区信息被丢弃，前端统一按本地时间展示
            return value.replace(tzinfo=None) if value.tzinfo is not None else value
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time())
        if hasattr(value, "to_pydatetime"):
            try:
                parsed = value.to_pydatetime()
                return parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed
            except Exception:
                return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                if isinstance(value, float):
                    if not value.is_integer():
                        return None
                    numeric_value = int(value)
                else:
                    numeric_value = int(value)
            except (OverflowError, ValueError):
                return None
            # 数值时间戳歧义大：秒/毫秒/紧凑交易日。仅接受 8 位 YYYYMMDD 形态。
            numeric_text = str(numeric_value)
            if re.fullmatch(r"\d{8}", numeric_text):
                try:
                    return datetime.strptime(numeric_text, "%Y%m%d")
                except ValueError:
                    return None
            return None

        text = str(value).strip()
        if not text:
            return None
        if re.fullmatch(r"\d{8}", text):
            try:
                return datetime.strptime(text, "%Y%m%d")
            except ValueError:
                return None
        try:
            # 兼容 ISO8601 'Z' 后缀
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed
        except ValueError:
            return None

    def list_triggers(
        self,
        *,
        rule_id: Optional[int] = None,
        target: Optional[str] = None,
        status: Optional[str] = None,
        user_id: Optional[int] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """分页查询触发历史记录。"""
        rows, total = self.repo.list_triggers(
            rule_id=rule_id,
            target=target,
            status=status,
            user_id=user_id,
            page=page,
            page_size=page_size,
        )
        return {
            "items": [self._serialize_trigger(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def list_notifications(
        self,
        *,
        trigger_id: Optional[int] = None,
        channel: Optional[str] = None,
        success: Optional[bool] = None,
        user_id: Optional[int] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """分页查询通知发送历史记录。"""
        rows, total = self.repo.list_notifications(
            trigger_id=trigger_id,
            channel=channel,
            success=success,
            user_id=user_id,
            page=page,
            page_size=page_size,
        )
        return {
            "items": [self._serialize_notification(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def _normalize_rule_payload(self, payload: Dict[str, Any], *, source: str = "api") -> Dict[str, Any]:
        """把 API payload 标准化成可入库的字段集合。

        校验顺序：target_scope → target → alert_type（与 scope 兼容性）
        → severity → parameters（按 alert_type 分类校验）→ 单标的 legacy 规则
        二次走 ``validate_event_alert_rule``。
        """
        target_scope = str(payload.get("target_scope") or "single_symbol").strip()
        if target_scope not in SUPPORTED_TARGET_SCOPES:
            raise AlertServiceError(f"unsupported target_scope: {target_scope}")

        target = str(payload.get("target") or "").strip()
        if not target:
            raise AlertServiceError("target is required")

        alert_type = str(payload.get("alert_type") or "").strip().lower()
        if alert_type not in SUPPORTED_ALERT_TYPES:
            raise UnsupportedAlertTypeError(f"unsupported alert_type for Alert API: {alert_type or '<empty>'}")
        self._validate_scope_alert_type(target_scope, alert_type)

        severity = str(payload.get("severity") or "warning").strip().lower()
        if severity not in SUPPORTED_SEVERITIES:
            raise AlertServiceError(f"unsupported severity: {severity}")

        parameters = self._normalize_parameters(alert_type, payload.get("parameters") or {})
        target = self._normalize_target(target_scope, target)
        # 单标的 + 传统运行时类型：复用事件告警的事件校验逻辑，确保参数语义正确
        if target_scope == "single_symbol" and alert_type in LEGACY_RUNTIME_ALERT_TYPES:
            serialized_rule = {"stock_code": target, "alert_type": alert_type, **parameters}
            try:
                validate_event_alert_rule(serialized_rule)
            except ValueError as exc:
                raise AlertServiceError(str(exc)) from exc

        name = str(payload.get("name") or "").strip()
        if not name:
            # 没有命名时按"标的 + 类型 + 关键参数"自动生成可读名称
            name = self._default_rule_name(target=target, alert_type=alert_type, parameters=parameters)

        return {
            "name": name[:64],
            "target_scope": target_scope,
            "target": target,
            "alert_type": alert_type,
            "parameters": self._dump_json(parameters),
            "severity": severity,
            "enabled": bool(payload.get("enabled", True)),
            "source": str(source or "api")[:16],
            "cooldown_policy": self._dump_json_or_none(payload.get("cooldown_policy")),
            "notification_policy": self._dump_json_or_none(payload.get("notification_policy")),
        }

    def _validate_rule_update_payload(self, payload: Dict[str, Any]) -> None:
        """禁止把非可空字段写成 None，避免规则状态被意外清空。"""
        for field_name, value in payload.items():
            if value is None and field_name not in NULLABLE_RULE_UPDATE_FIELDS:
                raise AlertServiceError(f"{field_name} must not be null")

    @staticmethod
    def _validate_scope_alert_type(target_scope: str, alert_type: str) -> None:
        """检查 target_scope 与 alert_type 的兼容矩阵，避免错配。"""
        if target_scope == "market":
            if alert_type not in MARKET_ALERT_TYPES:
                raise AlertServiceError("market target_scope only supports market alert types")
            return
        if alert_type in MARKET_ALERT_TYPES:
            raise AlertServiceError("market alert types require target_scope=market")
        if target_scope == "portfolio_account":
            if alert_type not in PORTFOLIO_ALERT_TYPES:
                raise AlertServiceError("portfolio_account only supports portfolio alert types")
            return
        if alert_type in PORTFOLIO_ALERT_TYPES:
            raise AlertServiceError("portfolio alert types require target_scope=portfolio_account")
        if target_scope in {"single_symbol", "watchlist", "portfolio_holdings"} and alert_type not in SYMBOL_ALERT_TYPES:
            raise UnsupportedAlertTypeError(f"unsupported alert_type for {target_scope}: {alert_type}")

    def _normalize_target(self, target_scope: str, target: str) -> str:
        """把 target 字段按其 scope 规范化，并校验组合账户的活跃性。"""
        if target_scope == "single_symbol":
            return target.strip()
        if target_scope == "market":
            try:
                return normalize_market_alert_region(target)
            except ValueError as exc:
                raise AlertServiceError(str(exc)) from exc
        try:
            normalized = normalize_batch_target_scope_target(target_scope, target)
            # 组合类规则要求目标账户处于活跃状态
            if target_scope in {"portfolio_holdings", "portfolio_account"}:
                ensure_active_portfolio_account(normalized)
            return normalized
        except ValueError as exc:
            raise AlertServiceError(str(exc)) from exc

    def _normalize_parameters(self, alert_type: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """按 alert_type 分派参数校验逻辑：只接受严格为正的数值字段。"""
        if not isinstance(parameters, dict):
            raise AlertServiceError("parameters must be an object")

        if alert_type == "price_cross":
            direction = str(parameters.get("direction") or "above").strip().lower()
            if direction not in {"above", "below"}:
                raise AlertServiceError(f"invalid direction: {direction}")
            return {"direction": direction, "price": self._positive_float(parameters.get("price"), "price")}

        if alert_type == "price_change_percent":
            direction = str(parameters.get("direction") or "up").strip().lower()
            if direction not in {"up", "down"}:
                raise AlertServiceError(f"invalid direction: {direction}")
            return {
                "direction": direction,
                "change_pct": self._positive_float(parameters.get("change_pct"), "change_pct"),
            }

        if alert_type == "volume_spike":
            return {"multiplier": self._positive_float(parameters.get("multiplier"), "multiplier")}

        if alert_type in TECHNICAL_ALERT_TYPES:
            try:
                return normalize_indicator_parameters(alert_type, parameters)
            except ValueError as exc:
                raise AlertServiceError(str(exc)) from exc

        if alert_type in PORTFOLIO_ALERT_TYPES:
            try:
                return normalize_portfolio_alert_parameters(alert_type, parameters)
            except ValueError as exc:
                raise AlertServiceError(str(exc)) from exc

        if alert_type in MARKET_ALERT_TYPES:
            try:
                return normalize_market_alert_parameters(alert_type, parameters)
            except ValueError as exc:
                raise AlertServiceError(str(exc)) from exc

        raise UnsupportedAlertTypeError(f"unsupported alert_type for Alert API: {alert_type}")

    @staticmethod
    def _positive_float(value: Any, field_name: str) -> float:
        """读取正浮点字段：失败或非正数时抛 AlertServiceError。"""
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise AlertServiceError(f"invalid {field_name}: {value}") from exc
        if number <= 0:
            raise AlertServiceError(f"{field_name} must be > 0")
        return number

    def build_runtime_payloads(
        self,
        row: AlertRuleRecord,
        *,
        config: Optional[Any] = None,
        include_overflow_payload: bool = True,
    ) -> List[RuntimeAlertPayload]:
        """把数据库中的规则行展开成评估器可消费的 RuntimeAlertPayload 列表。

        组合/市场类型 → 单一 payload；自选股/组合持仓 → 按目标展开成 N 个 payload，
        超软上限时附加一个 degraded 的"overflow" payload 提示丢弃的目标数。
        展开异常时改为返回一条 failed 的 payload，让上层试算流程仍能继续。
        """
        data = self._serialize_rule_base(row)
        parent_key = self._semantic_key(
            data["target_scope"],
            data["target"],
            data["alert_type"],
            data["parameters"],
        )

        if data["alert_type"] in PORTFOLIO_ALERT_TYPES:
            return [make_portfolio_risk_payload(parent_key=parent_key, data=data)]

        if data["alert_type"] in MARKET_ALERT_TYPES:
            return [make_market_light_payload(parent_key=parent_key, data=data, config=config)]

        if data["target_scope"] in SYMBOL_BATCH_TARGET_SCOPES:
            if config is None:
                from src.config import get_config

                config = get_config()
            try:
                targets, overflow_count = expand_symbol_targets(
                    target_scope=data["target_scope"],
                    target=data["target"],
                    config=config,
                )
            except Exception as exc:
                # 展开失败时返回一条 failed 静态 payload，仍保持响应结构稳定
                return [
                    make_static_payload(
                        parent_key=parent_key,
                        rule_id=int(data["id"] or 0),
                        alert_type=data["alert_type"],
                        effective_target=f"{data['target_scope']}:{data['target']}",
                        display_target=f"{data['target_scope']} {data['target']}",
                        message=self._sanitize_text(str(exc) or "target expansion failed"),
                        record_status="failed",
                    )
                ]

            payloads: List[RuntimeAlertPayload] = []
            for target in targets:
                child_data = dict(data)
                child_data["target"] = target.symbol
                rule = self._to_runtime_rule(row, child_data)
                effective_target = target.symbol
                payloads.append(
                    RuntimeAlertPayload(
                        key=f"{parent_key}|{effective_target}",
                        rule=rule,
                        effective_target=effective_target,
                        display_target=target.display_target,
                    )
                )
            if overflow_count:
                # include_overflow_payload=False 时不附加，由调用方决定如何呈现
                if include_overflow_payload:
                    payloads.append(
                        make_static_payload(
                            parent_key=parent_key,
                            rule_id=int(data["id"] or 0),
                            alert_type=data["alert_type"],
                            effective_target=f"{data['target_scope']}:{data['target']}:overflow",
                            display_target="展开目标超限",
                            message=f"Skipped {overflow_count} targets over soft cap",
                            record_status="degraded",
                        )
                    )
                logger.warning(
                    "[AlertService] Alert rule %s expansion exceeded soft cap by %s targets",
                    data["id"],
                    overflow_count,
                )
            if not payloads:
                # 自选股或组合持仓为空时给一条 skipped 提示，便于 UI 显示原因
                scope_label = "watchlist" if data["target_scope"] == "watchlist" else "portfolio holdings"
                payloads.append(
                    make_static_payload(
                        parent_key=parent_key,
                        rule_id=int(data["id"] or 0),
                        alert_type=data["alert_type"],
                        effective_target=f"{data['target_scope']}:{data['target']}",
                        display_target=scope_label,
                        message=f"No {scope_label} targets to evaluate",
                        record_status="skipped",
                    )
                )
            return payloads

        # 其余情况：单 payload 评估
        rule = self._to_runtime_rule(row, data)
        effective_target = str(data["target"])
        return [
            RuntimeAlertPayload(
                key=parent_key,
                rule=rule,
                effective_target=effective_target,
                display_target=effective_target,
            )
        ]

    def _to_runtime_rule(self, row: AlertRuleRecord, data: Optional[Dict[str, Any]] = None):
        """把规则数据装配成对应类型的运行时规则对象。

        ``metadata`` 携带持久化规则 ID、用户归属、parent/effective target 等
        上下文，便于在评估器/触发记录里反向回溯到原始规则。
        """
        data = data or self._serialize_rule_base(row)
        parameters = data["parameters"]
        metadata = {
            "persisted_rule_id": data["id"],
            "user_id": getattr(row, "user_id", None),
            "target_scope": data.get("target_scope"),
            "parent_target": row.target,
            "effective_target": data.get("target"),
        }
        if data["alert_type"] == "price_cross":
            return PriceAlert(
                stock_code=data["target"],
                direction=str(parameters["direction"]),
                price=float(parameters["price"]),
                metadata=metadata,
            )
        if data["alert_type"] == "price_change_percent":
            return PriceChangeAlert(
                stock_code=data["target"],
                direction=str(parameters["direction"]),
                change_pct=float(parameters["change_pct"]),
                metadata=metadata,
            )
        if data["alert_type"] == "volume_spike":
            return VolumeAlert(
                stock_code=data["target"],
                multiplier=float(parameters["multiplier"]),
                metadata=metadata,
            )
        if data["alert_type"] in TECHNICAL_ALERT_TYPES:
            return TechnicalIndicatorAlert(
                stock_code=data["target"],
                alert_type=data["alert_type"],
                indicator_params=parameters,
                metadata=metadata,
            )
        raise UnsupportedAlertTypeError(f"unsupported alert_type for Alert API: {data['alert_type']}")

    @staticmethod
    def _semantic_key(target_scope: str, target: str, alert_type: str, parameters: Dict[str, Any]) -> str:
        """构造规则的语义唯一键：相同 target+type+参数应被识别为同一规则。"""
        canonical_params = json.dumps(parameters or {}, ensure_ascii=False, sort_keys=True)
        return f"{target_scope}:{target}:{alert_type}:{canonical_params}"

    def _serialize_rule(self, row: AlertRuleRecord) -> Dict[str, Any]:
        """把数据库行序列化为 API 字典，并附加 cooldown 摘要字段。"""
        data = self._serialize_rule_base(row)
        cooldown_summary = self._cooldown_summary_for_rule(row)
        data.update({
            "last_triggered_at": cooldown_summary.get("last_triggered_at"),
            "cooldown_until": cooldown_summary.get("cooldown_until"),
            "cooldown_active": cooldown_summary.get("cooldown_active"),
        })
        return data

    def _serialize_rule_base(self, row: AlertRuleRecord) -> Dict[str, Any]:
        """取出数据库行的所有字段为 dict，JSON 字段会被反序列化。"""
        return {
            "id": row.id,
            "user_id": getattr(row, "user_id", None),
            "name": row.name,
            "target_scope": row.target_scope,
            "target": row.target,
            "alert_type": row.alert_type,
            "parameters": self._load_json(row.parameters, default={}),
            "severity": row.severity,
            "enabled": bool(row.enabled),
            "source": row.source,
            "cooldown_policy": self._load_json(row.cooldown_policy, default=None),
            "notification_policy": self._load_json(row.notification_policy, default=None),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    def _cooldown_summary_for_rule(self, row: AlertRuleRecord) -> Dict[str, Any]:
        """从 DB 取最近一次触发的冷却摘要；读失败时返回"全部未激活"占位。"""
        try:
            cooldown_target = (
                portfolio_effective_target(str(row.target))
                if str(row.target_scope) == "portfolio_account"
                else str(row.target)
            )
            cooldown = self.repo.get_rule_cooldown_summary(
                rule_id=int(row.id),
                target=cooldown_target,
                severity=str(row.severity) if row.severity else None,
            )
        except Exception as exc:
            logger.warning(
                "[AlertService] Failed to load alert cooldown summary for rule %s: %s",
                getattr(row, "id", "?"),
                self._sanitize_text(str(exc) or "cooldown summary read failed"),
            )
            return {"last_triggered_at": None, "cooldown_until": None, "cooldown_active": False}
        return self._serialize_cooldown_summary(cooldown)

    @staticmethod
    def _serialize_cooldown_summary(row: Optional[AlertCooldownRecord]) -> Dict[str, Any]:
        """把冷却记录序列化为 dict，并显式计算当前是否还在冷却中。"""
        if row is None:
            return {"last_triggered_at": None, "cooldown_until": None, "cooldown_active": False}
        # 三者齐备且到期时间仍在未来，才视为"激活中"
        cooldown_active = bool(
            row.state == "active"
            and row.cooldown_until is not None
            and row.cooldown_until > datetime.now()
        )
        return {
            "last_triggered_at": row.last_triggered_at.isoformat() if row.last_triggered_at else None,
            "cooldown_until": row.cooldown_until.isoformat() if row.cooldown_until else None,
            "cooldown_active": cooldown_active,
        }

    def _serialize_trigger(self, row: AlertTriggerRecord) -> Dict[str, Any]:
        """把触发记录序列化为 dict，并解析其中的"分析可视化"子结构。"""
        visibility = self._parse_analysis_visibility(row.diagnostics)
        return {
            "id": row.id,
            "rule_id": row.rule_id,
            "target": row.target,
            "observed_value": row.observed_value,
            "threshold": row.threshold,
            "reason": row.reason,
            "data_source": row.data_source,
            "data_timestamp": row.data_timestamp.isoformat() if row.data_timestamp else None,
            "triggered_at": row.triggered_at.isoformat() if row.triggered_at else None,
            "status": row.status,
            "diagnostics": self._sanitize_text(row.diagnostics) if row.diagnostics else None,
            "market_phase_summary": visibility.get("market_phase_summary"),
            "analysis_context_pack_overview": visibility.get("analysis_context_pack_overview"),
            "analysis_visibility_source": visibility.get("analysis_visibility_source"),
            "decision_signal_summary": visibility.get("decision_signal_summary"),
        }

    @staticmethod
    def _parse_analysis_visibility(diagnostics: Optional[str]) -> Dict[str, Any]:
        """从 trigger 的 diagnostics JSON 中提取"分析可视化"字段。

        兼容两种形态：纯文本（旧数据）会被记为 ``legacy_text``；结构化 JSON
        则抽取 market_phase / context_pack_overview / decision_signal_summary。
        """
        result = {
            "market_phase_summary": None,
            "analysis_context_pack_overview": None,
            "analysis_visibility_source": None,
            "decision_signal_summary": None,
        }
        if not diagnostics:
            return result
        try:
            parsed = json.loads(diagnostics)
        except (TypeError, ValueError, json.JSONDecodeError):
            result["analysis_visibility_source"] = "legacy_text"
            return result
        if not isinstance(parsed, dict):
            result["analysis_visibility_source"] = "legacy_text"
            return result
        result["decision_signal_summary"] = summarize_decision_signal(parsed.get("decision_signal_summary"))
        visibility = parsed.get("analysis_visibility")
        if not isinstance(visibility, dict):
            return result
        phase = extract_market_phase_summary({MARKET_PHASE_SUMMARY_KEY: visibility.get("market_phase_summary")})
        overview = extract_analysis_context_pack_overview(
            {ANALYSIS_CONTEXT_PACK_OVERVIEW_KEY: visibility.get("analysis_context_pack_overview")}
        )
        result["market_phase_summary"] = phase
        result["analysis_context_pack_overview"] = overview
        result["analysis_visibility_source"] = visibility.get("source")
        return result

    def _serialize_notification(self, row: AlertNotificationRecord) -> Dict[str, Any]:
        """把通知发送记录序列化为 API 字典。"""
        return {
            "id": row.id,
            "trigger_id": row.trigger_id,
            "channel": row.channel,
            "attempt": row.attempt,
            "success": bool(row.success),
            "error_code": row.error_code,
            "retryable": bool(row.retryable),
            "latency_ms": row.latency_ms,
            "diagnostics": self._sanitize_text(row.diagnostics) if row.diagnostics else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    @staticmethod
    def _default_rule_name(*, target: str, alert_type: str, parameters: Dict[str, Any]) -> str:
        """根据规则类型与参数自动生成可读的默认规则名。

        注意：此函数是用户可见的规则命名，函数内 f-string 中的字段名必须
        保持稳定，否则会改变已写入数据库的默认名称。
        """
        if alert_type == "price_cross":
            return f"{target} price {parameters['direction']} {parameters['price']}"
        if alert_type == "price_change_percent":
            return f"{target} change {parameters['direction']} {parameters['change_pct']}%"
        if alert_type == "volume_spike":
            return f"{target} volume spike {parameters['multiplier']}x"
        if alert_type == "ma_price_cross":
            return f"{target} close {parameters['direction']} MA{parameters['window']}"
        if alert_type == "rsi_threshold":
            return f"{target} RSI{parameters['period']} {parameters['direction']} {parameters['threshold']}"
        if alert_type == "macd_cross":
            return f"{target} MACD {parameters['direction']}"
        if alert_type == "kdj_cross":
            return f"{target} KDJ {parameters['direction']}"
        if alert_type == "cci_threshold":
            return f"{target} CCI{parameters['period']} {parameters['direction']} {parameters['threshold']}"
        if alert_type == "portfolio_stop_loss":
            return f"{target} portfolio stop loss {parameters.get('mode', 'near')}"
        if alert_type == "portfolio_concentration":
            return f"{target} portfolio concentration"
        if alert_type == "portfolio_drawdown":
            return f"{target} portfolio drawdown"
        if alert_type == "portfolio_price_stale":
            return f"{target} portfolio stale price"
        if alert_type == "market_light_status":
            statuses = ",".join(parameters.get("statuses") or ["red", "yellow"])
            return f"{target} market light status {statuses}"
        if alert_type == "market_light_score_drop":
            return f"{target} market light score drop {parameters['min_drop']}"
        return f"{target} {alert_type}"

    @staticmethod
    def _dump_json(value: Dict[str, Any]) -> str:
        """把 dict 序列化为稳定的 JSON 字符串（key 排序、中文不转义）。"""
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    def _dump_json_or_none(self, value: Optional[Dict[str, Any]]) -> Optional[str]:
        """非 dict 一律报错；None 透传；其他用 _dump_json 序列化。"""
        if value is None:
            return None
        if not isinstance(value, dict):
            raise AlertServiceError("policy fields must be objects")
        return self._dump_json(value)

    @staticmethod
    def _load_json(raw: Optional[str], *, default: Any) -> Any:
        """反序列化 JSON 字段，解析失败时回落到 default，保证字段不抛错。"""
        if raw is None or raw == "":
            return default
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return default

    @staticmethod
    def _sanitize_text(text: Any) -> str:
        """统一的脱敏入口，便于把日志中的敏感字段清空。"""
        return sanitize_diagnostic_text(text)
