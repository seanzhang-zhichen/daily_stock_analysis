# -*- coding: utf-8 -*-
"""为告警中心 P6 提供的组合与自选股告警辅助逻辑。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional

from src.services.portfolio_risk_service import PortfolioRiskService
from src.services.portfolio_service import PortfolioService


logger = logging.getLogger(__name__)

# 目标作用域白名单(按"批处理目标"展开股票列表, 例如自选股 / 组合持仓)
SYMBOL_BATCH_TARGET_SCOPES = frozenset({"watchlist", "portfolio_holdings"})
# 组合级告警允许的作用域(账户维度, 不需要展开到股票)
PORTFOLIO_TARGET_SCOPES = frozenset({"portfolio_holdings", "portfolio_account"})
# P6 组合告警合法类型
PORTFOLIO_ALERT_TYPES = frozenset({
    "portfolio_stop_loss",
    "portfolio_concentration",
    "portfolio_drawdown",
    "portfolio_price_stale",
})

# 批量展开后保留的标的软上限, 超出会记录 overflow_count, 避免接口回包过大
EXPANDED_TARGET_SOFT_CAP = 100
# dry-run 模式下保留多少条 target_results 详情返回给前端, 控制响应体积
TARGET_RESULTS_LIMIT = 20
DRY_RUN_TARGET_TIMEOUT_SECONDS = 10  # 单个 target 评估的超时(秒)
DRY_RUN_TOTAL_TIMEOUT_SECONDS = 30  # 整条 dry-run 评估的超时(秒)


@dataclass(frozen=True)
class ExpandedSymbolTarget:
    """由父级批规则展开出的具体股票目标。"""

    symbol: str
    display_target: str


@dataclass(frozen=True)
class RuntimeAlertPayload:
    """运行时告警载荷, 含 cooldown / 历史用到的唯一身份。"""

    key: str
    rule: Any
    effective_target: str
    display_target: str


@dataclass
class PortfolioRiskAlert:
    """账户级组合风险告警的运行时实例。"""

    target_scope: str
    target: str
    alert_type: str
    parameters: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)
    description: str = ""
    stock_code: str = ""

    def __post_init__(self) -> None:
        """把 effective_target 解析成统一的 ``account:<id>`` 形式并存入 stock_code。"""
        effective_target = self.metadata.get("effective_target") or portfolio_effective_target(self.target)
        self.stock_code = str(effective_target)


@dataclass
class StaticAlertEvaluation:
    """无法展开或被跳过的占位告警评估结果。"""

    stock_code: str
    alert_type: str
    message: str
    record_status: str = "skipped"
    metadata: Dict[str, Any] = field(default_factory=dict)
    description: str = ""


def normalize_portfolio_alert_parameters(alert_type: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    """归一化 P6 组合告警的参数, 非法参数直接抛错。"""
    # 不支持的告警类型直接拒绝, 防止 DB 写脏
    if alert_type not in PORTFOLIO_ALERT_TYPES:
        raise ValueError(f"unsupported portfolio alert_type: {alert_type}")
    if not isinstance(parameters, dict):
        raise ValueError("parameters must be an object")

    if alert_type == "portfolio_stop_loss":
        mode = str(parameters.get("mode") or "near").strip().lower()
        if mode not in {"near", "breach"}:
            raise ValueError("portfolio_stop_loss mode must be near or breach")
        return {"mode": mode}

    return {}


def portfolio_effective_target(target: str) -> str:
    """把"全部 / 账户 ID"统一映射成 ``account:<id|all>`` 形式作为 effective_target。"""
    target_text = str(target or "all").strip() or "all"
    return "account:all" if target_text == "all" else f"account:{target_text}"


def normalize_batch_target_scope_target(target_scope: str, target: str) -> str:
    """校验并归一化批处理规则的目标字符串(空字符串 / default / account id / all)。"""
    target_text = str(target or "").strip()
    if target_scope == "watchlist":
        if target_text not in {"", "default"}:
            # 自选股只支持 default, 防止误把任意字符串当目标
            raise ValueError("watchlist target must be default")
        return "default"
    if target_scope in PORTFOLIO_TARGET_SCOPES:
        if target_text == "all":
            return "all"
        return str(_positive_int_target(target_text))
    return target_text


def ensure_active_portfolio_account(target: str, *, portfolio_service: Optional[PortfolioService] = None) -> None:
    """校验显式组合账户目标存在且为活动状态。"""
    if str(target or "").strip() == "all":
        return
    account_id = _positive_int_target(target)
    service = portfolio_service or PortfolioService()
    accounts = service.list_accounts(include_inactive=False)
    active_ids = {int(item.get("id")) for item in accounts if item.get("id") is not None}
    if account_id not in active_ids:
        raise ValueError(f"portfolio account is not active or does not exist: {account_id}")


def expand_symbol_targets(
    *,
    target_scope: str,
    target: str,
    config: Any,
    portfolio_service: Optional[PortfolioService] = None,
) -> tuple[List[ExpandedSymbolTarget], int]:
    """把 watchlist / portfolio_holdings 展开成具体的去重股票列表。

    返回 ``(targets, overflow_count)``; ``targets`` 已应用 ``EXPANDED_TARGET_SOFT_CAP`` 截断。
    """
    if target_scope == "watchlist":
        symbols = _watchlist_symbols(config)
        display_prefix = "自选股"
    elif target_scope == "portfolio_holdings":
        symbols = _portfolio_holding_symbols(target=target, portfolio_service=portfolio_service)
        display_prefix = "持仓"
    else:
        # 其它作用域不在批量展开能力范围内
        return [], 0

    unique = _dedupe_symbols(symbols)
    overflow_count = max(0, len(unique) - EXPANDED_TARGET_SOFT_CAP)
    capped = unique[:EXPANDED_TARGET_SOFT_CAP]
    return [
        ExpandedSymbolTarget(symbol=symbol, display_target=f"{display_prefix} - {symbol}")
        for symbol in capped
    ], overflow_count


def make_static_payload(
    *,
    parent_key: str,
    rule_id: int,
    alert_type: str,
    effective_target: str,
    display_target: str,
    message: str,
    record_status: str = "skipped",
) -> RuntimeAlertPayload:
    """构造一个"跳过 / 降级"用的静态告警 payload, 不实际查行情。"""
    rule = StaticAlertEvaluation(
        stock_code=effective_target,
        alert_type=alert_type,
        message=message,
        record_status=record_status,
        metadata={
            "persisted_rule_id": rule_id,
            "effective_target": effective_target,
            "display_target": display_target,
        },
        description=message,
    )
    return RuntimeAlertPayload(
        key=f"{parent_key}|{effective_target}",
        rule=rule,
        effective_target=effective_target,
        display_target=display_target,
    )


def make_portfolio_risk_payload(
    *,
    parent_key: str,
    data: Dict[str, Any],
) -> RuntimeAlertPayload:
    """构造一个账户级组合风险告警的运行时载荷。"""
    effective_target = portfolio_effective_target(data["target"])
    display_target = "全部账户" if data["target"] == "all" else f"账户 {data['target']}"
    rule = PortfolioRiskAlert(
        target_scope=data["target_scope"],
        target=data["target"],
        alert_type=data["alert_type"],
        parameters=dict(data.get("parameters") or {}),
        metadata={
            "persisted_rule_id": data["id"],
            "user_id": data.get("user_id"),
            "effective_target": effective_target,
            "display_target": display_target,
        },
        description=data.get("name") or data["alert_type"],
    )
    return RuntimeAlertPayload(
        key=f"{parent_key}|{effective_target}",
        rule=rule,
        effective_target=effective_target,
        display_target=display_target,
    )


def evaluate_static_alert(rule: StaticAlertEvaluation) -> Dict[str, Any]:
    """把跳过 / 降级告警归一化为标准的 evaluation result 结构。"""
    return {
        "rule_id": int(rule.metadata.get("persisted_rule_id", 0) or 0),
        "status": "not_triggered",
        "record_status": rule.record_status,
        "triggered": False,
        "observed_value": None,
        "threshold": None,
        "data_source": None,
        "data_timestamp": None,
        "reason": rule.message,
        "message": rule.message,
    }


def evaluate_portfolio_risk_alert(
    rule: PortfolioRiskAlert,
    *,
    portfolio_service: Optional[PortfolioService] = None,
    risk_service: Optional[PortfolioRiskService] = None,
) -> Dict[str, Any]:
    """评估单条账户级组合风险告警(stop-loss / 集中度 / 回撤 / 价过期)。"""
    # target 为 all 时不限定账户, 否则按正整数 ID 取账户
    account_id = None if rule.target == "all" else _positive_int_target(rule.target)
    service = portfolio_service or PortfolioService()
    risk = risk_service or PortfolioRiskService(portfolio_service=service)

    if rule.alert_type == "portfolio_price_stale":
        # 价格过期独立走快照, 不依赖组合风险报告
        snapshot = service.get_portfolio_snapshot(account_id=account_id, cost_method="fifo")
        return _evaluate_price_stale(rule, snapshot)

    report = risk.get_risk_report(account_id=account_id, cost_method="fifo")
    if rule.alert_type == "portfolio_stop_loss":
        return _evaluate_stop_loss(rule, report)
    if rule.alert_type == "portfolio_concentration":
        return _evaluate_concentration(rule, report)
    if rule.alert_type == "portfolio_drawdown":
        return _evaluate_drawdown(rule, report)

    # 类型不在白名单时打 failed 标记, 而不是抛错, 避免 dry-run 全失败
    return _portfolio_result(
        rule,
        triggered=False,
        observed_value=None,
        threshold=None,
        message=f"unsupported portfolio alert_type: {rule.alert_type}",
        record_status="failed",
        diagnostics={"error": "unsupported_portfolio_alert_type"},
    )


def result_to_target_result(payload: RuntimeAlertPayload, result: Dict[str, Any]) -> Dict[str, Any]:
    """把内部 evaluation result 转成 target_results 数组元素。"""
    record_status = result.get("record_status")
    return {
        "target": payload.effective_target,
        "display_target": payload.display_target,
        "status": result.get("status") or "evaluation_error",
        "record_status": record_status,
        "triggered": bool(result.get("triggered")),
        "observed_value": result.get("observed_value"),
        "threshold": result.get("threshold"),
        "message": result.get("message") or result.get("reason") or "",
    }


def aggregate_dry_run_results(rule_id: int, target_scope: str, results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总若干 target_results, 给出整体触发状态和关键计数。"""
    # 排序键: 触发 > degraded/failed > 其余; 同级按 target 字母序, 让输出稳定
    target_results = sorted(
        results,
        key=lambda item: (
            0 if item.get("triggered") else 1,
            0 if item.get("record_status") in {"degraded", "failed"} else 1,
            str(item.get("target") or ""),
        ),
    )
    visible_results = target_results[:TARGET_RESULTS_LIMIT]
    triggered_count = sum(1 for item in target_results if item.get("triggered"))
    degraded_count = sum(1 for item in target_results if item.get("record_status") == "degraded")
    skipped_count = sum(1 for item in target_results if item.get("record_status") == "skipped")
    failed_count = sum(1 for item in target_results if item.get("record_status") == "failed")
    successful_count = sum(
        1
        for item in target_results
        if item.get("record_status") not in {"failed"} and item.get("status") != "evaluation_error"
    )

    if triggered_count:
        status = "triggered"
        triggered = True
    elif successful_count or skipped_count or degraded_count:
        status = "not_triggered"
        triggered = False
    else:
        # 全部 failed 或 evaluation_error: 视为整体评估错误
        status = "evaluation_error"
        triggered = False

    if not target_results:
        status = "evaluation_error"
        triggered = False
        message = "No targets were evaluated"
    else:
        message = (
            f"Evaluated {len(target_results)} targets: "
            f"{triggered_count} triggered, {degraded_count} degraded, "
            f"{skipped_count} skipped, {failed_count} failed"
        )

    # 任意一条给出了观测值就回传给前端, 让"是否真有偏差"对用户可见
    first_observed = next((item.get("observed_value") for item in target_results if item.get("observed_value") is not None), None)
    return {
        "rule_id": rule_id,
        "target_scope": target_scope,
        "status": status,
        "triggered": triggered,
        "observed_value": first_observed,
        "message": message,
        "evaluated_count": len(target_results),
        "triggered_count": triggered_count,
        "degraded_count": degraded_count,
        "skipped_count": skipped_count,
        "target_results": visible_results,
    }


def _watchlist_symbols(config: Any) -> List[str]:
    """从全局 config 中读取自选股列表(必要时先触发一次刷新)。"""
    refresh = getattr(config, "refresh_stock_list", None)
    if callable(refresh):
        try:
            refresh()
        except Exception as exc:
            logger.warning("[portfolio_alerts] Failed to refresh watchlist symbols: %s", exc)
    return list(getattr(config, "stock_list", []) or [])


def _portfolio_holding_symbols(
    *,
    target: str,
    portfolio_service: Optional[PortfolioService],
) -> List[str]:
    """从组合快照里收集账户下所有有持仓股票代码(quantity>0)。"""
    service = portfolio_service or PortfolioService()
    account_id = None if target == "all" else _positive_int_target(target)
    snapshot = service.get_portfolio_snapshot(account_id=account_id, cost_method="fifo")
    symbols: List[str] = []
    for account in snapshot.get("accounts", []) or []:
        for position in account.get("positions", []) or []:
            try:
                quantity = float(position.get("quantity") or 0.0)
            except (TypeError, ValueError):
                quantity = 0.0
            # 0 持仓不视为有效标的, 避免空头寸触发告警
            if quantity <= 0:
                continue
            symbol = _normalize_symbol(position.get("symbol"))
            if symbol:
                symbols.append(symbol)
    return symbols


def _dedupe_symbols(symbols: Iterable[Any]) -> List[str]:
    """按输入顺序去重, 保留首次出现的 symbol。"""
    output: List[str] = []
    seen = set()
    for raw in symbols:
        symbol = _normalize_symbol(raw)
        if not symbol or symbol in seen:
            continue
        output.append(symbol)
        seen.add(symbol)
    return output


def _normalize_symbol(value: Any) -> str:
    """复用 PortfolioService 的内部股票代码归一化(委托给其私有方法)。"""
    return PortfolioService._normalize_symbol(str(value or ""))


def _positive_int_target(value: Any) -> int:
    """校验 ``target`` 是正整数; ``all`` 由调用方自己识别, 此处不接受。"""
    try:
        account_id = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("portfolio target must be all or a positive account id") from exc
    if account_id <= 0:
        raise ValueError("portfolio target must be all or a positive account id")
    return account_id


def _evaluate_stop_loss(rule: PortfolioRiskAlert, report: Dict[str, Any]) -> Dict[str, Any]:
    """在组合风险报告基础上评估"接近/触及"两种口径的账户级止损告警。"""
    mode = str(rule.parameters.get("mode") or "near")
    stop_loss = report.get("stop_loss") or {}
    items = list(stop_loss.get("items") or [])
    if mode == "breach":
        # breach: 只关心已真正触发止损阈值的标的
        affected = [item for item in items if bool(item.get("is_triggered"))]
        triggered = bool(affected)
    else:
        # near 模式: 接近阈值也算告警(配合 ``near_alert`` 标志)
        affected = items
        triggered = bool(stop_loss.get("near_alert")) and bool(affected)

    threshold_key = "stop_loss_alert_pct" if mode == "breach" else "stop_loss_near_ratio"
    threshold = _threshold(report, threshold_key)
    if mode == "near":
        # near 模式阈值 = 总额阈值 * 接近比例, 让 UI 看到的是"接近"阈值
        stop_loss_pct = _threshold(report, "stop_loss_alert_pct") or 0.0
        near_ratio = _threshold(report, "stop_loss_near_ratio") or 0.0
        threshold = stop_loss_pct * near_ratio

    observed = max((float(item.get("loss_pct") or 0.0) for item in affected), default=0.0)
    diagnostics = _base_diagnostics(report, top_items=affected[:5])
    diagnostics.update({
        "mode": mode,
        "near_count": stop_loss.get("near_count", 0),
        "triggered_count": stop_loss.get("triggered_count", 0),
    })
    message = (
        f"{_display_account(report)} stop-loss {mode}: {len(affected)} affected symbols"
        if triggered
        else f"{_display_account(report)} stop-loss {mode}: no affected symbols"
    )
    return _portfolio_result(
        rule,
        triggered=triggered,
        observed_value=observed,
        threshold=threshold,
        message=message,
        diagnostics=diagnostics,
    )


def _evaluate_concentration(rule: PortfolioRiskAlert, report: Dict[str, Any]) -> Dict[str, Any]:
    """评估"单只标的占比过高"账户级集中度告警。"""
    concentration = report.get("concentration") or {}
    observed = float(concentration.get("top_weight_pct") or 0.0)
    threshold = _threshold(report, "concentration_alert_pct")
    triggered = bool(concentration.get("alert"))
    diagnostics = _base_diagnostics(report, top_items=concentration.get("top_positions") or [])
    diagnostics.update({
        "total_market_value": concentration.get("total_market_value"),
        "top_weight_pct": observed,
    })
    message = f"{_display_account(report)} concentration top weight {observed:.2f}%"
    return _portfolio_result(
        rule,
        triggered=triggered,
        observed_value=observed,
        threshold=threshold,
        message=message,
        diagnostics=diagnostics,
    )


def _evaluate_drawdown(rule: PortfolioRiskAlert, report: Dict[str, Any]) -> Dict[str, Any]:
    """评估账户级回撤告警(最大 / 当前回撤比阈值)。"""
    drawdown = report.get("drawdown") or {}
    observed = float(drawdown.get("max_drawdown_pct") or 0.0)
    threshold = _threshold(report, "drawdown_alert_pct")
    triggered = bool(drawdown.get("alert"))
    diagnostics = _base_diagnostics(report)
    diagnostics.update({
        "series_points": drawdown.get("series_points"),
        "current_drawdown_pct": drawdown.get("current_drawdown_pct"),
        "max_drawdown_pct": observed,
        # 汇率陈旧会让回撤口径不可信, 显式标记供前端排查
        "fx_stale": bool(drawdown.get("fx_stale")),
    })
    message = f"{_display_account(report)} max drawdown {observed:.2f}%"
    return _portfolio_result(
        rule,
        triggered=triggered,
        observed_value=observed,
        threshold=threshold,
        message=message,
        diagnostics=diagnostics,
    )


def _evaluate_price_stale(rule: PortfolioRiskAlert, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """评估持仓价格过期 / 缺失告警, 直接用快照中的 ``price_stale`` 标记。"""
    affected: List[Dict[str, Any]] = []
    for account in snapshot.get("accounts", []) or []:
        for position in account.get("positions", []) or []:
            # price_available=False 等价于 stale, 统一纳入告警清单
            if bool(position.get("price_stale")) or not bool(position.get("price_available", True)):
                affected.append({
                    "account_id": account.get("account_id"),
                    "symbol": position.get("symbol"),
                    "price_stale": bool(position.get("price_stale")),
                    "price_available": bool(position.get("price_available")),
                    "price_source": position.get("price_source"),
                    "price_date": position.get("price_date"),
                })

    diagnostics = _base_diagnostics_from_snapshot(snapshot, top_items=affected[:5])
    observed = float(len(affected))
    message = (
        f"{_display_snapshot_account(snapshot)} stale or missing prices: {len(affected)} symbols"
        if affected
        else f"{_display_snapshot_account(snapshot)} prices are current"
    )
    return _portfolio_result(
        rule,
        triggered=bool(affected),
        observed_value=observed,
        threshold=0.0,
        message=message,
        diagnostics=diagnostics,
        data_timestamp=_parse_date(snapshot.get("as_of")),
        data_source="portfolio_snapshot",
    )


def _portfolio_result(
    rule: PortfolioRiskAlert,
    *,
    triggered: bool,
    observed_value: Optional[float],
    threshold: Optional[float],
    message: str,
    diagnostics: Dict[str, Any],
    record_status: Optional[str] = None,
    data_timestamp: Optional[datetime] = None,
    data_source: str = "portfolio_risk",
) -> Dict[str, Any]:
    """把内部判定结果打成统一的 portfolio evaluation result。"""
    # 未指定时间戳时尽量从 diagnostics 中回填, 让前端不用再查一次
    if data_timestamp is None:
        data_timestamp = _parse_date(diagnostics.get("as_of"))
    status = "triggered" if triggered else "not_triggered"
    return {
        "rule_id": int(rule.metadata.get("persisted_rule_id", 0) or 0),
        "status": status,
        # 触发时 record_status 跟随 status; 否则保留调用方传入的状态
        "record_status": "triggered" if triggered else record_status,
        "triggered": triggered,
        "observed_value": observed_value,
        "threshold": threshold,
        "data_source": data_source,
        "data_timestamp": data_timestamp,
        "reason": message,
        "message": message,
        "diagnostics": json.dumps(diagnostics, ensure_ascii=False, sort_keys=True),
    }


def _base_diagnostics(report: Dict[str, Any], *, top_items: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """构造组合告警通用的诊断字段(账户、货币、时间戳、Top 影响标的等)。"""
    return {
        # 报告层缺失 account_id 时按 "all" 处理, 与路由目标保持一致
        "account_id": report.get("account_id") if report.get("account_id") is not None else "all",
        "currency": report.get("currency"),
        "as_of": report.get("as_of"),
        "price_stale": False,
        "fx_stale": bool((report.get("drawdown") or {}).get("fx_stale")),
        "data_available": True,
        "top_affected_symbols": _top_symbols(top_items or []),
    }


def _base_diagnostics_from_snapshot(
    snapshot: Dict[str, Any],
    *,
    top_items: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """``portfolio_price_stale`` 专用的诊断结构, 数据源来自 portfolio snapshot。"""
    accounts = snapshot.get("accounts", []) or []
    # 仅一个账户快照时展示该账户 ID; 多个时统一标 "all"
    explicit_account = accounts[0].get("account_id") if len(accounts) == 1 else "all"
    affected = top_items or []
    return {
        "account_id": explicit_account,
        "currency": snapshot.get("currency"),
        "as_of": snapshot.get("as_of"),
        "price_stale": any(bool(item.get("price_stale")) for item in affected),
        "fx_stale": bool(snapshot.get("fx_stale")),
        "data_available": all(bool(item.get("price_available")) for item in affected) if affected else True,
        "top_affected_symbols": _top_symbols(affected),
    }


def _top_symbols(items: List[Dict[str, Any]]) -> List[str]:
    """抽取最多 5 个 symbol 字符串, 供前端"主要影响标的"展示。"""
    output: List[str] = []
    for item in items[:5]:
        symbol = str(item.get("symbol") or "").strip()
        if symbol:
            output.append(symbol)
    return output


def _threshold(report: Dict[str, Any], name: str) -> Optional[float]:
    """读取 report.thresholds[name], 失败时返回 None 表示"未配置"。"""
    thresholds = report.get("thresholds") or {}
    value = thresholds.get(name)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _display_account(report: Dict[str, Any]) -> str:
    """把 report.account_id 格式化成 ``account <id|all>`` 字符串用于日志/消息。"""
    account_id = report.get("account_id")
    return "account all" if account_id is None else f"account {account_id}"


def _display_snapshot_account(snapshot: Dict[str, Any]) -> str:
    """为 price_stale 输出"对应哪几个账户"的描述, 简化为单账户 / 全部账户。"""
    accounts = snapshot.get("accounts", []) or []
    if len(accounts) == 1:
        return f"account {accounts[0].get('account_id')}"
    return "account all"


def _parse_date(value: Any) -> Optional[datetime]:
    """把 ISO 字符串/date/datetime 转成统一 ``datetime`` 或 None。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
