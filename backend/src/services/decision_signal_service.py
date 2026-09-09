# -*- coding: utf-8 -*-
"""决策信号（decision signal）的提取、生命周期管理与查询服务。

负责把分析结论沉淀为带用户归属与有效期的信号记录：
新增生效信号、失效反向信号、到期批量过期与历史回填。
"""

from __future__ import annotations

import json
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError

from src.market_context import detect_market
from src.storage import AnalysisHistory, DatabaseManager, DecisionSignalRecord
from src.utils.data_processing import extract_market_structure_context
from src.schemas.decision_scale import action_for_score, score_band_metadata

logger = logging.getLogger(__name__)

# 允许的 action 取值集合, 控制状态机的"动作轴"
ALLOWED_ACTIONS = {"buy", "add", "hold", "reduce", "sell", "watch", "avoid", "alert"}
ALLOWED_STATUSES = {"active", "expired", "invalidated", "closed", "archived"}
# 终态集合, 表示信号已结束流转, 上层不应再据此触发动作
TERMINAL_STATUSES = {"expired", "invalidated", "closed", "archived"}
POSITIVE_ACTIONS = {"buy", "add"}
NEGATIVE_ACTIONS = {"reduce", "sell", "avoid"}

# action -> 中文标签(给前端/通知文案使用, 不能改)
ACTION_LABELS = {
    "buy": "买入",
    "add": "加仓",
    "hold": "持有",
    "reduce": "减仓",
    "sell": "卖出",
    "watch": "观望",
    "avoid": "回避",
    "alert": "风险提醒",
}


class DecisionSignalNotFoundError(LookupError):
    """用户归属的决策信号不存在时抛出, 由 API 层捕获并返回 404。"""


def _json_object(value: Any) -> Dict[str, Any]:
    """把 JSON 字符串 / dict 统一为 dict, 容错地吃掉一切非法输入。"""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_value(value: Any) -> Any:
    """把 JSON 字段反序列化为原生 Python 对象; 非字符串保持原样。"""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _dump_json(value: Any) -> Optional[str]:
    """把可序列化对象写成 JSON 字符串, 空值统一存 NULL。"""
    if value in (None, "", [], {}):
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _text(value: Any, *, limit: Optional[int] = None) -> Optional[str]:
    """把 list/dict/scalar 转成长文本, 必要时按 ``limit`` 截断。"""
    if value in (None, "", [], {}):
        return None
    if isinstance(value, (list, tuple, set)):
        # 列表 / 元组 / 集合: 用 "；" 拼接, 过滤掉空白元素
        result = "；".join(str(item).strip() for item in value if str(item).strip())
    elif isinstance(value, dict):
        # 字典: 渲染成 ``key: value`` 形式, 跳过空值
        result = "；".join(
            f"{key}: {item}" for key, item in value.items() if item not in (None, "", [], {})
        )
    else:
        result = str(value).strip()
    if not result:
        return None
    return result[:limit] if limit else result


def _number(value: Any) -> Optional[float]:
    """把数值字段归一化为正数浮点; 非数值或 0/负值返回 None。"""
    if value is None or isinstance(value, bool):
        # bool 在 Python 里是 int 子类, 单独短路避免 True/False 被误识别
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _confidence(value: Any) -> Optional[float]:
    """把 LLM 输出的 confidence(百分数 / 0-1 / 中文标签)归一化到 [0,1]。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = float(value)
        # 超过 1 视为百分数(用户模型常见输出形式)
        if parsed > 1:
            parsed /= 100
        return max(0.0, min(parsed, 1.0))
    normalized = str(value or "").strip().lower()
    # 中文 / 英文 / 大小写组合的稳健匹配
    if normalized in {"高", "high"}:
        return 0.85
    if normalized in {"中", "medium", "moderate"}:
        return 0.65
    if normalized in {"低", "low"}:
        return 0.4
    return None


def _action(operation_advice: Any, decision_type: Any, score: Any) -> str:
    """把 LLM 给出的 ``操作建议 / decision_type / score`` 解析为标准 action。"""
    advice = str(operation_advice or "").strip().lower()
    exact = {
        "buy": "buy",
        "add": "add",
        "hold": "hold",
        "reduce": "reduce",
        "sell": "sell",
        "watch": "watch",
        "avoid": "avoid",
        "alert": "alert",
    }
    if advice in exact:
        return exact[advice]
    # 优先用中文关键词匹配, 因为 LLM 经常输出中文 operation_advice
    for needles, action in (
        (("加仓", "增持"), "add"),
        (("减仓", "降低仓位"), "reduce"),
        (("卖出", "清仓", "退出", "止损"), "sell"),
        (("回避", "避免", "不建议参与"), "avoid"),
        (("买入", "建仓"), "buy"),
        (("持有", "继续持有"), "hold"),
        (("观望", "观察", "等待"), "watch"),
    ):
        if any(needle in advice for needle in needles):
            return action

    # 退路: 靠 decision_type 和 score 反推, 保持至少有合理默认
    normalized_type = str(decision_type or "").strip().lower()
    if normalized_type == "buy":
        return "buy"
    if normalized_type == "sell":
        return "sell"
    if normalized_type == "hold":
        return "hold"
    try:
        numeric_score = int(score)
    except (TypeError, ValueError):
        numeric_score = 50
    # 60 分以上默认看多, 低于 40 默认看空; 中段归为观望(而不是直接告警)
    return action_for_score(numeric_score) or "watch"


def _horizon(payload: Dict[str, Any], dashboard: Dict[str, Any]) -> str:
    """根据 payload / dashboard 推断信号有效期维度(intraday / 1d / 3d / swing / long)。"""
    explicit = str(payload.get("horizon") or "").strip().lower()
    if explicit in {"intraday", "1d", "3d", "5d", "10d", "swing", "long"}:
        return explicit
    core = _json_object(dashboard.get("core_conclusion"))
    sensitivity = str(core.get("time_sensitivity") or "").lower()
    # 中文 + 英文 token 兼容, 用于推断"何时过期"
    if any(token in sensitivity for token in ("立即", "今日", "intraday", "today")):
        return "intraday"
    if any(token in sensitivity for token in ("本周", "week")):
        return "5d"
    return "3d"


def _expires_at(created_at: datetime, horizon: str) -> datetime:
    """根据 horizon 计算过期时刻; 默认 5 天兜底, 防止未识别 horizon 写入 NULL。"""
    days = {
        "intraday": 1,
        "1d": 2,
        "3d": 5,
        "5d": 8,
        "10d": 15,
        "swing": 30,
        "long": 180,
    }.get(horizon, 5)
    return created_at + timedelta(days=days)


def _extract_record(record: AnalysisHistory) -> Dict[str, Any]:
    """把一条 AnalysisHistory 转成可写入 DecisionSignalRecord 的字段字典。"""
    payload = _json_object(record.raw_result)
    dashboard = _json_object(payload.get("dashboard"))
    core = _json_object(dashboard.get("core_conclusion"))
    intelligence = _json_object(dashboard.get("intelligence"))
    battle_plan = _json_object(dashboard.get("battle_plan"))
    position_strategy = _json_object(battle_plan.get("position_strategy"))
    position_advice = _json_object(core.get("position_advice"))
    market_structure = payload.get("market_structure_context")
    # 部分历史 payload 不带 market_structure_context, 退回到 context_snapshot 解析
    if not isinstance(market_structure, dict):
        market_structure = extract_market_structure_context(record.context_snapshot) or {}
    market_theme = _json_object(market_structure.get("market_theme_context"))
    stock_position = _json_object(market_structure.get("stock_market_position"))
    primary_theme = _json_object(stock_position.get("primary_theme"))

    score = payload.get("sentiment_score", record.sentiment_score)
    canonical_action = str(payload.get("decision_action") or "").strip().lower()
    action = canonical_action if canonical_action in ALLOWED_ACTIONS else _action(
        payload.get("operation_advice", record.operation_advice),
        payload.get("decision_type"),
        score,
    )
    horizon = _horizon(payload, dashboard)
    created_at = record.created_at or datetime.now()
    expires_at = _expires_at(created_at, horizon)
    # 把理想买点 / 副买点汇总成区间, 用于 entry_low / entry_high
    points = [point for point in (record.ideal_buy, record.secondary_buy) if point and point > 0]

    reason = (
        _text(core.get("one_sentence"))
        or _text(payload.get("buy_reason"))
        or _text(record.analysis_summary)
    )
    risk_summary = _text(intelligence.get("risk_alerts")) or _text(payload.get("risk_warning"))
    catalyst_summary = _text(intelligence.get("positive_catalysts"))
    watch_conditions = _text(battle_plan.get("action_checklist"))
    invalidation = _text(position_strategy.get("risk_control")) or risk_summary

    metadata = {
        "report_type": record.report_type,
        "query_id": record.query_id,
        "report_language": payload.get("report_language"),
        "trend_prediction": record.trend_prediction,
        "position_advice": position_advice or None,
        "market_structure_status": market_structure.get("status") if isinstance(market_structure, dict) else None,
        "market_theme_status": market_theme.get("status"),
        "stock_role": stock_position.get("stock_role"),
        "primary_theme": primary_theme.get("name"),
    }
    band = score_band_metadata(score)
    if band:
        metadata["score_scale"] = band

    return {
        "user_id": record.user_id,
        "stock_code": str(record.code or "").strip().upper(),
        "stock_name": _text(record.name, limit=64),
        "market": detect_market(record.code),
        "source_type": "analysis",
        "source_report_id": record.id,
        "trace_id": _text(record.query_id, limit=64),
        "trigger_source": "analysis_history",
        "action": action,
        # 操作建议标签优先用源文本, 没有再落到 ACTION_LABELS 默认值
        "action_label": _text(record.operation_advice, limit=32) or ACTION_LABELS[action],
        "confidence": _confidence(payload.get("confidence") or payload.get("confidence_level")),
        "score": int(score) if isinstance(score, (int, float)) else None,
        "horizon": horizon,
        "entry_low": min(points) if points else None,
        "entry_high": max(points) if len(points) > 1 else None,
        "stop_loss": _number(record.stop_loss),
        "target_price": _number(record.take_profit),
        "invalidation": invalidation,
        "watch_conditions": watch_conditions,
        "reason": reason,
        "risk_summary": risk_summary,
        "catalyst_summary": catalyst_summary,
        "evidence_json": _dump_json({
            "trend_analysis": payload.get("trend_analysis"),
            "technical_analysis": payload.get("technical_analysis"),
            "fundamental_analysis": payload.get("fundamental_analysis"),
            "market_structure": {
                "status": market_structure.get("status"),
                "theme_phase": stock_position.get("theme_phase"),
                "stock_role": stock_position.get("stock_role"),
                "primary_theme": primary_theme.get("name"),
            },
        }),
        "data_quality_json": _dump_json({"data_sources": payload.get("data_sources")}),
        "metadata_json": _dump_json(metadata),
        # 完整的交易计划(stop_loss + take_profit + 双买点)才算 complete, 否则 partial
        "plan_quality": "complete" if record.stop_loss and record.take_profit and points else "partial",
        "status": "active" if expires_at > datetime.now() else "expired",
        "expires_at": expires_at,
        "created_at": created_at,
        "updated_at": datetime.now(),
    }


def _serialize(record: DecisionSignalRecord) -> Dict[str, Any]:
    """把 DecisionSignalRecord 转成 API 返回字典(含 JSON 字段反序列化)。"""
    return {
        "id": record.id,
        "stock_code": record.stock_code,
        "stock_name": record.stock_name,
        "market": record.market,
        "source_type": record.source_type,
        "source_report_id": record.source_report_id,
        "trace_id": record.trace_id,
        "trigger_source": record.trigger_source,
        "action": record.action,
        "action_label": record.action_label,
        "confidence": record.confidence,
        "score": record.score,
        "horizon": record.horizon,
        "entry_low": record.entry_low,
        "entry_high": record.entry_high,
        "stop_loss": record.stop_loss,
        "target_price": record.target_price,
        "invalidation": record.invalidation,
        "watch_conditions": record.watch_conditions,
        "reason": record.reason,
        "risk_summary": record.risk_summary,
        "catalyst_summary": record.catalyst_summary,
        "evidence": _json_value(record.evidence_json),
        "data_quality_summary": _json_value(record.data_quality_json),
        "metadata": _json_value(record.metadata_json),
        "plan_quality": record.plan_quality,
        "status": record.status,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


class DecisionSignalService:
    """用户隔离的决策信号读写服务。"""

    def __init__(
        self,
        db: Optional[DatabaseManager] = None,
        *,
        portfolio_repo: Optional[Any] = None,
    ):
        """允许外部注入 db(测试 / 复用), 缺省走单例 DatabaseManager。"""
        self.db = db or getattr(portfolio_repo, "db", None) or DatabaseManager.get_instance()

    @staticmethod
    def _owner_condition(user_id: Optional[int]):
        """构造 ``user_id`` 隔离的 SQL 条件: None 表示全局信号。"""
        if user_id is None:
            return DecisionSignalRecord.user_id.is_(None)
        return DecisionSignalRecord.user_id == user_id

    @staticmethod
    def _history_owner_condition(user_id: Optional[int]):
        """``AnalysisHistory`` 版本的 user_id 隔离条件, 与 signals 对齐。"""
        if user_id is None:
            return AnalysisHistory.user_id.is_(None)
        return AnalysisHistory.user_id == user_id

    def sync_analysis_history(self, *, user_id: Optional[int], limit: int = 500) -> int:
        """把最近的分析历史幂等地回填为决策信号。"""
        with self.db.get_session() as session:
            histories = list(
                session.execute(
                    select(AnalysisHistory)
                    .where(
                        self._history_owner_condition(user_id),
                        # 市场总览不属于个股信号, 排除掉
                        or_(AnalysisHistory.report_type.is_(None), AnalysisHistory.report_type != "market_review"),
                        AnalysisHistory.code != "MARKET",
                    )
                    .order_by(AnalysisHistory.created_at.desc(), AnalysisHistory.id.desc())
                    .limit(limit)
                ).scalars().all()
            )
            histories.reverse()
            existing_ids = set(
                session.execute(
                    select(DecisionSignalRecord.source_report_id).where(
                        self._owner_condition(user_id),
                        DecisionSignalRecord.source_type == "analysis",
                    )
                ).scalars().all()
            )

        created = 0
        for history in histories:
            if history.id in existing_ids:
                continue
            payload = _extract_record(history)
            try:
                self._create_from_payload(payload)
                created += 1
            except IntegrityError:
                # 并发回填时可能撞唯一索引, 跳过即可
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("回填分析历史 AI 建议失败 record_id=%s: %s", history.id, exc)
        return created

    def _create_from_payload(self, payload: Dict[str, Any]) -> int:
        """写入一条决策信号, 同时在新信号生效时把反向活跃信号置为 invalidated。"""

        def _write(session):
            """事务内写入信号记录；新信号为 active 时同步失效同标的的反向建议。"""
            record = DecisionSignalRecord(**payload)
            session.add(record)
            session.flush()
            if record.status == "active":
                # 新信号生效时, 屏蔽同标的 / 同用户的反向建议, 避免建议打架
                self._invalidate_opposing(session, record)
            session.refresh(record)
            return int(record.id)

        return self.db._run_write_transaction("create_decision_signal", _write)

    @staticmethod
    def _invalidate_opposing(session, current: DecisionSignalRecord) -> None:
        """把同标的 / 同用户的反向活跃信号置为 ``invalidated``。"""
        if current.action in POSITIVE_ACTIONS:
            opposing = NEGATIVE_ACTIONS
        elif current.action in NEGATIVE_ACTIONS:
            opposing = POSITIVE_ACTIONS
        else:
            # 中性 action(hold/watch/alert)暂不触发反向失效, 避免误伤
            return
        rows = session.execute(
            select(DecisionSignalRecord).where(
                DecisionSignalRecord.id != current.id,
                DecisionSignalRecord.user_id == current.user_id,
                DecisionSignalRecord.stock_code == current.stock_code,
                DecisionSignalRecord.status == "active",
                DecisionSignalRecord.action.in_(opposing),
                # 仅失效"早于当前"的旧信号, 后到的反向信号由自己处理
                DecisionSignalRecord.created_at <= current.created_at,
            )
        ).scalars().all()
        now = datetime.now()
        for row in rows:
            row.status = "invalidated"
            row.updated_at = now

    def _expire_due(self, *, user_id: Optional[int]) -> None:
        """把所有 ``expires_at <= now`` 的活跃信号批量置为 expired。"""

        def _write(session):
            """事务内批量把已到期的活跃信号置为 expired 并返回处理条数。"""
            rows = session.execute(
                select(DecisionSignalRecord).where(
                    self._owner_condition(user_id),
                    DecisionSignalRecord.status == "active",
                    DecisionSignalRecord.expires_at.is_not(None),
                    DecisionSignalRecord.expires_at <= datetime.now(),
                )
            ).scalars().all()
            now = datetime.now()
            for row in rows:
                row.status = "expired"
                row.updated_at = now
            return len(rows)

        self.db._run_write_transaction("expire_decision_signals", _write)

    def list_signals(
        self,
        *,
        user_id: Optional[int],
        market: Optional[str] = None,
        stock_code: Optional[str] = None,
        action: Optional[str] = None,
        status: Optional[str] = None,
        created_from: Optional[datetime] = None,
        created_to: Optional[datetime] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """按条件分页查询决策信号, 自动同步分析历史并过期老信号。"""
        # 列表查询前先同步 + 过期, 让调用方无需自行触发
        self.sync_analysis_history(user_id=user_id)
        self._expire_due(user_id=user_id)

        conditions = [self._owner_condition(user_id)]
        if market:
            conditions.append(DecisionSignalRecord.market == market.lower())
        if stock_code:
            conditions.append(DecisionSignalRecord.stock_code == stock_code.strip().upper())
        if action:
            if action not in ALLOWED_ACTIONS:
                raise ValueError("不支持的建议动作")
            conditions.append(DecisionSignalRecord.action == action)
        if status:
            if status not in ALLOWED_STATUSES:
                raise ValueError("不支持的建议状态")
            conditions.append(DecisionSignalRecord.status == status)
        if created_from:
            conditions.append(DecisionSignalRecord.created_at >= created_from)
        if created_to:
            conditions.append(DecisionSignalRecord.created_at <= created_to)

        with self.db.get_session() as session:
            total = session.execute(
                select(func.count(DecisionSignalRecord.id)).where(and_(*conditions))
            ).scalar_one()
            records = session.execute(
                select(DecisionSignalRecord)
                .where(and_(*conditions))
                .order_by(DecisionSignalRecord.created_at.desc(), DecisionSignalRecord.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).scalars().all()
            return {
                "items": [_serialize(item) for item in records],
                "total": total,
                "page": page,
                "page_size": page_size,
            }

    def get_latest_active(
        self,
        *,
        stock_code: str,
        market: Optional[str] = None,
        limit: int = 1,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """兼容告警触发时"按标的 + 市场 + 状态"取最新信号的查询。"""
        items = self.latest(stock_code, user_id=user_id)
        if market:
            # 仅保留与请求市场匹配的记录, 让同一公司在不同市场的信号不互串
            items = [item for item in items if item.get("market") == market.lower()]
        # 把单次返回数量钳制在 [1, 20], 防止调用方错传无穷大
        safe_limit = max(1, min(int(limit), 20))
        return {"items": items[:safe_limit], "total": len(items)}

    def create_signal(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """为告警 worker 提供幂等的"创建信号"入口, 已有同 trace_id 直接返回原记录。"""
        user_id = payload.get("user_id")
        trace_id = _text(payload.get("trace_id"), limit=64)
        source_type = str(payload.get("source_type") or "alert")[:24]
        if trace_id:
            with self.db.get_session() as session:
                existing = session.execute(
                    select(DecisionSignalRecord).where(
                        self._owner_condition(user_id),
                        DecisionSignalRecord.source_type == source_type,
                        DecisionSignalRecord.trace_id == trace_id,
                    ).limit(1)
                ).scalar_one_or_none()
                if existing is not None:
                    return {"item": _serialize(existing), "created": False}

        now = datetime.now()
        horizon = str(payload.get("horizon") or "3d")[:16]
        # 没有 trace_id 时, 用整个 payload 的稳定序列化做种, 保证幂等
        source_seed = trace_id or json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        source_report_id = payload.get("source_report_id")
        if source_report_id is None:
            # 取 sha1 的前 12 位十六进制并转负数, 让 source_report_id 与 DB 主键不冲突
            source_report_id = -int(hashlib.sha1(source_seed.encode("utf-8")).hexdigest()[:12], 16)
        action = str(payload.get("action") or "alert")
        if action not in ALLOWED_ACTIONS:
            # 未知 action 兜底为 alert, 避免下游 API 因为脏数据 500
            action = "alert"
        fields = {
            "user_id": user_id,
            "stock_code": str(payload.get("stock_code") or "").strip().upper(),
            "stock_name": _text(payload.get("stock_name"), limit=64),
            "market": str(payload.get("market") or detect_market(payload.get("stock_code"))).lower(),
            "source_type": source_type,
            "source_report_id": int(source_report_id),
            "trace_id": trace_id,
            "trigger_source": str(payload.get("trigger_source") or "alert")[:64],
            "action": action,
            "action_label": _text(payload.get("action_label"), limit=32) or ACTION_LABELS[action],
            "horizon": horizon,
            "watch_conditions": _text(payload.get("watch_conditions")),
            "reason": _text(payload.get("reason")),
            "risk_summary": _text(payload.get("risk_summary")),
            "metadata_json": _dump_json(payload.get("metadata")),
            # 告警来源的信号通常只有少量字段, 标 minimal 即可
            "plan_quality": "minimal",
            "status": "active",
            "expires_at": _expires_at(now, horizon),
            "created_at": now,
            "updated_at": now,
        }
        record_id = self._create_from_payload(fields)
        # The transaction session may expire ORM attributes on commit; reload
        # through the normal user-scoped query before serializing.
        return {"item": self.get_signal(record_id, user_id=user_id), "created": True}

    def get_signal(self, signal_id: int, *, user_id: Optional[int]) -> Dict[str, Any]:
        """按 ID 取单条决策信号, 校验所有者归属。"""
        with self.db.get_session() as session:
            record = session.execute(
                select(DecisionSignalRecord).where(
                    DecisionSignalRecord.id == signal_id,
                    self._owner_condition(user_id),
                )
            ).scalar_one_or_none()
            if record is None:
                raise DecisionSignalNotFoundError("AI 建议不存在")
            return _serialize(record)

    def latest(self, stock_code: str, *, user_id: Optional[int]) -> list[Dict[str, Any]]:
        """返回指定股票下, 当前用户最新的若干条活跃信号(顺序为新→旧)。"""
        self.sync_analysis_history(user_id=user_id)
        self._expire_due(user_id=user_id)
        with self.db.get_session() as session:
            records = session.execute(
                select(DecisionSignalRecord)
                .where(
                    self._owner_condition(user_id),
                    DecisionSignalRecord.stock_code == stock_code.strip().upper(),
                    DecisionSignalRecord.status == "active",
                )
                .order_by(DecisionSignalRecord.created_at.desc(), DecisionSignalRecord.id.desc())
                .limit(10)
            ).scalars().all()
            return [_serialize(item) for item in records]

    def update_status(
        self,
        signal_id: int,
        *,
        user_id: Optional[int],
        status: str,
    ) -> Dict[str, Any]:
        """关闭 / 失效 / 归档 / 过期一条信号; 仅允许终态。"""
        if status not in TERMINAL_STATUSES:
            # 非终态(例如 active -> active)由系统自动驱动, 人工接口拒绝
            raise ValueError("AI 建议只能关闭、失效、归档或标记过期")

        def _write(session):
            """事务内把单条信号置为终态并返回序列化结果；不存在则抛错。"""
            record = session.execute(
                select(DecisionSignalRecord).where(
                    DecisionSignalRecord.id == signal_id,
                    self._owner_condition(user_id),
                )
            ).scalar_one_or_none()
            if record is None:
                raise DecisionSignalNotFoundError("AI 建议不存在")
            record.status = status
            record.updated_at = datetime.now()
            session.flush()
            session.refresh(record)
            return _serialize(record)

        return self.db._run_write_transaction("update_decision_signal_status", _write)


__all__ = [
    "ALLOWED_ACTIONS",
    "ALLOWED_STATUSES",
    "DecisionSignalNotFoundError",
    "DecisionSignalService",
]
