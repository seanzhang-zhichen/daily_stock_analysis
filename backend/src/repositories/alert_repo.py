# -*- coding: utf-8 -*-
"""告警中心（Alert Center）P1 API 表的数据访问层。

提供告警规则（``AlertRuleRecord``）、触发历史（``AlertTriggerRecord``）、
发送流水（``AlertNotificationRecord``）以及去重冷却状态
（``AlertCooldownRecord``）的 CRUD 与查询能力。

主要被 ``backend/api/v1/endpoints/alert*.py`` 调用；写侧（P2+）由告警调度
服务在运行时填充触发与发送流水。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, delete, desc, func, select

from src.storage import (
    AlertCooldownRecord,
    AlertNotificationRecord,
    AlertRuleRecord,
    AlertTriggerRecord,
    DatabaseManager,
)


class AlertRepository:
    """告警规则与只读告警历史的数据访问层。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        """支持测试时注入自定义 ``db_manager``，运行时使用全局单例。"""
        self.db = db_manager or DatabaseManager.get_instance()

    def create_rule(self, fields: Dict[str, Any]) -> AlertRuleRecord:
        """创建一条告警规则并返回持久化后的 ORM 行。"""
        with self.db.get_session() as session:
            row = AlertRuleRecord(**fields)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def get_rule(self, rule_id: int, *, user_id: Optional[int] = None) -> Optional[AlertRuleRecord]:
        """按 ID 取单条告警规则；传入 ``user_id`` 时限定为该用户的规则。"""
        with self.db.get_session() as session:
            conditions = [AlertRuleRecord.id == rule_id]
            if user_id is not None:
                conditions.append(AlertRuleRecord.user_id == user_id)
            return session.execute(
                select(AlertRuleRecord).where(and_(*conditions)).limit(1)
            ).scalar_one_or_none()

    def update_rule(self, rule_id: int, fields: Dict[str, Any], *, user_id: Optional[int] = None) -> Optional[AlertRuleRecord]:
        """按字段集更新告警规则；规则不存在或不属于当前用户时返回 ``None``。"""
        with self.db.get_session() as session:
            conditions = [AlertRuleRecord.id == rule_id]
            if user_id is not None:
                conditions.append(AlertRuleRecord.user_id == user_id)
            row = session.execute(
                select(AlertRuleRecord).where(and_(*conditions)).limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            for key, value in fields.items():
                setattr(row, key, value)
            row.updated_at = datetime.now()
            session.commit()
            session.refresh(row)
            return row

    def delete_rule(self, rule_id: int, *, user_id: Optional[int] = None) -> bool:
        """删除一条告警规则；返回是否真的删除了一行。"""
        with self.db.get_session() as session:
            conditions = [AlertRuleRecord.id == rule_id]
            if user_id is not None:
                conditions.append(AlertRuleRecord.user_id == user_id)
            result = session.execute(delete(AlertRuleRecord).where(and_(*conditions)))
            session.commit()
            return bool(result.rowcount)

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
    ) -> Tuple[List[AlertRuleRecord], int]:
        """分页列出告警规则，支持按启用状态、类型、对象、来源、用户过滤。"""
        conditions = []
        if enabled is not None:
            conditions.append(AlertRuleRecord.enabled.is_(enabled))
        if alert_type:
            conditions.append(AlertRuleRecord.alert_type == alert_type)
        if target_scope:
            conditions.append(AlertRuleRecord.target_scope == target_scope)
        if target:
            conditions.append(AlertRuleRecord.target == target)
        if source:
            conditions.append(AlertRuleRecord.source == source)
        if user_id is not None:
            conditions.append(AlertRuleRecord.user_id == user_id)

        where_clause = and_(*conditions) if conditions else True
        offset = (page - 1) * page_size
        with self.db.get_session() as session:
            total = session.execute(
                select(func.count(AlertRuleRecord.id)).select_from(AlertRuleRecord).where(where_clause)
            ).scalar() or 0
            rows = session.execute(
                select(AlertRuleRecord)
                .where(where_clause)
                .order_by(desc(AlertRuleRecord.updated_at), desc(AlertRuleRecord.id))
                .offset(offset)
                .limit(page_size)
            ).scalars().all()
            return list(rows), int(total)

    def list_enabled_rules(self, *, limit: int = 1000) -> List[AlertRuleRecord]:
        """拉取所有启用中的告警规则，供调度器遍历触发评估。"""
        # 防止调用方传超大 limit 拖垮数据库
        safe_limit = max(1, min(int(limit), 1000))
        with self.db.get_session() as session:
            rows = session.execute(
                select(AlertRuleRecord)
                .where(AlertRuleRecord.enabled.is_(True))
                .order_by(desc(AlertRuleRecord.updated_at), desc(AlertRuleRecord.id))
                .limit(safe_limit)
            ).scalars().all()
            return list(rows)

    def create_trigger(self, fields: Dict[str, Any]) -> AlertTriggerRecord:
        """写入一条告警触发历史行；缺少必要字段时抛 ``ValueError``。"""
        self._validate_trigger_fields(fields)

        with self.db.get_session() as session:
            row = AlertTriggerRecord(**fields)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def create_trigger_if_absent(self, fields: Dict[str, Any]) -> Tuple[AlertTriggerRecord, bool]:
        """仅当 ``(rule_id, target, data_timestamp, status='triggered')`` 未存在时写入。

        返回 ``(row, created)``：``created=True`` 表示本次新插入，否则复用已有行。
        调用方应在确认本次触发已通过去重判定后再调用本方法，避免把非去重审计行
        错误归并。
        """
        self._validate_trigger_fields(fields)

        rule_id = fields.get("rule_id")
        data_timestamp = fields.get("data_timestamp")
        if fields.get("status") != "triggered" or rule_id is None or data_timestamp is None:
            raise ValueError(
                "create_trigger_if_absent requires triggered status, rule_id, and data_timestamp"
            )

        with self.db.get_session() as session:
            query = select(AlertTriggerRecord).where(
                AlertTriggerRecord.rule_id == rule_id,
                AlertTriggerRecord.target == fields.get("target"),
                AlertTriggerRecord.status == "triggered",
                AlertTriggerRecord.data_timestamp == data_timestamp,
            )
            data_source = fields.get("data_source")
            # data_source 为 None 时需要单独处理, 否则会被解释为 ``data_source = NULL`` 失效
            if data_source is None:
                query = query.where(AlertTriggerRecord.data_source.is_(None))
            else:
                query = query.where(AlertTriggerRecord.data_source == data_source)

            existing = session.execute(
                query.order_by(AlertTriggerRecord.id.asc()).limit(1)
            ).scalar_one_or_none()
            if existing is not None:
                return existing, False

            row = AlertTriggerRecord(**fields)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row, True

    @staticmethod
    def _validate_trigger_fields(fields: Dict[str, Any]) -> None:
        """校验触发记录的必填字段；缺失时抛出 ``ValueError``。"""
        if not fields.get("target"):
            raise ValueError("alert trigger target is required")
        if not fields.get("status"):
            raise ValueError("alert trigger status is required")

    def record_notification_attempt(self, fields: Dict[str, Any]) -> AlertNotificationRecord:
        """写入一条告警发送流水；channel 必填。"""
        if not fields.get("channel"):
            raise ValueError("alert notification channel is required")

        with self.db.get_session() as session:
            row = AlertNotificationRecord(**fields)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def get_active_cooldown(
        self,
        *,
        rule_id: int,
        target: str,
        severity: Optional[str],
        now: Optional[datetime] = None,
    ) -> Optional[AlertCooldownRecord]:
        """获取当前仍处于冷却期内的告警冷却记录；不存在则返回 ``None``。"""
        now_value = now or datetime.now()
        with self.db.get_session() as session:
            return session.execute(
                select(AlertCooldownRecord)
                .where(
                    AlertCooldownRecord.rule_id == rule_id,
                    AlertCooldownRecord.target == target,
                    AlertCooldownRecord.severity == severity,
                    AlertCooldownRecord.state == "active",
                    AlertCooldownRecord.cooldown_until > now_value,
                )
                .order_by(desc(AlertCooldownRecord.cooldown_until), desc(AlertCooldownRecord.id))
                .limit(1)
            ).scalar_one_or_none()

    def upsert_cooldown(
        self,
        *,
        rule_id: int,
        rule_key: Optional[str],
        target: str,
        severity: Optional[str],
        last_triggered_at: datetime,
        cooldown_until: datetime,
        reason: Optional[str] = None,
        state: str = "active",
    ) -> AlertCooldownRecord:
        """按 ``(rule_id, target, severity)`` upsert 一条告警冷却状态。"""
        with self.db.get_session() as session:
            row = session.execute(
                select(AlertCooldownRecord)
                .where(
                    AlertCooldownRecord.rule_id == rule_id,
                    AlertCooldownRecord.target == target,
                    AlertCooldownRecord.severity == severity,
                )
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                row = AlertCooldownRecord(
                    rule_id=rule_id,
                    rule_key=rule_key,
                    target=target,
                    severity=severity,
                )
                session.add(row)
            row.rule_key = rule_key
            row.last_triggered_at = last_triggered_at
            row.cooldown_until = cooldown_until
            row.reason = reason
            row.state = state
            row.updated_at = datetime.now()
            session.commit()
            session.refresh(row)
            return row

    def get_rule_cooldown_summary(
        self,
        *,
        rule_id: int,
        target: str,
        severity: Optional[str],
    ) -> Optional[AlertCooldownRecord]:
        """取规则/对象/严重度对应的最新一条告警冷却记录（用于展示）。"""
        with self.db.get_session() as session:
            return session.execute(
                select(AlertCooldownRecord)
                .where(
                    AlertCooldownRecord.rule_id == rule_id,
                    AlertCooldownRecord.target == target,
                    AlertCooldownRecord.severity == severity,
                )
                .order_by(desc(AlertCooldownRecord.updated_at), desc(AlertCooldownRecord.id))
                .limit(1)
            ).scalar_one_or_none()

    def list_triggers(
        self,
        *,
        rule_id: Optional[int] = None,
        target: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        user_id: Optional[int] = None,
    ) -> Tuple[List[AlertTriggerRecord], int]:
        """分页列出告警触发历史；可按规则、对象、状态、用户过滤。"""
        conditions = []
        if rule_id is not None:
            conditions.append(AlertTriggerRecord.rule_id == rule_id)
        if target:
            conditions.append(AlertTriggerRecord.target == target)
        if status:
            conditions.append(AlertTriggerRecord.status == status)
        if user_id is not None:
            conditions.append(AlertRuleRecord.user_id == user_id)

        where_clause = and_(*conditions) if conditions else True
        offset = (page - 1) * page_size
        with self.db.get_session() as session:
            count_query = select(func.count(AlertTriggerRecord.id)).select_from(AlertTriggerRecord)
            rows_query = select(AlertTriggerRecord)
            if user_id is not None:
                # 仅返回所属用户规则的触发历史, 通过 JOIN 规则表过滤
                count_query = count_query.join(
                    AlertRuleRecord, AlertTriggerRecord.rule_id == AlertRuleRecord.id
                )
                rows_query = rows_query.join(
                    AlertRuleRecord, AlertTriggerRecord.rule_id == AlertRuleRecord.id
                )
            total = session.execute(count_query.where(where_clause)).scalar() or 0
            rows = session.execute(
                rows_query
                .where(where_clause)
                .order_by(desc(AlertTriggerRecord.triggered_at), desc(AlertTriggerRecord.id))
                .offset(offset)
                .limit(page_size)
            ).scalars().all()
            return list(rows), int(total)

    def list_notifications(
        self,
        *,
        trigger_id: Optional[int] = None,
        channel: Optional[str] = None,
        success: Optional[bool] = None,
        user_id: Optional[int] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[AlertNotificationRecord], int]:
        """分页列出告警发送流水，可按触发 ID、渠道、是否成功、用户过滤。"""
        conditions = []
        if trigger_id is not None:
            conditions.append(AlertNotificationRecord.trigger_id == trigger_id)
        if channel:
            conditions.append(AlertNotificationRecord.channel == channel)
        if success is not None:
            conditions.append(AlertNotificationRecord.success.is_(success))
        if user_id is not None:
            conditions.append(AlertRuleRecord.user_id == user_id)

        where_clause = and_(*conditions) if conditions else True
        offset = (page - 1) * page_size
        with self.db.get_session() as session:
            count_query = select(func.count(AlertNotificationRecord.id)).select_from(
                AlertNotificationRecord
            )
            rows_query = select(AlertNotificationRecord)
            if user_id is not None:
                # 多表 JOIN, 让通知按其触发的规则所属用户隔离
                count_query = count_query.join(
                    AlertTriggerRecord,
                    AlertNotificationRecord.trigger_id == AlertTriggerRecord.id,
                ).join(AlertRuleRecord, AlertTriggerRecord.rule_id == AlertRuleRecord.id)
                rows_query = rows_query.join(
                    AlertTriggerRecord,
                    AlertNotificationRecord.trigger_id == AlertTriggerRecord.id,
                ).join(AlertRuleRecord, AlertTriggerRecord.rule_id == AlertRuleRecord.id)
            total = session.execute(count_query.where(where_clause)).scalar() or 0
            rows = session.execute(
                rows_query
                .where(where_clause)
                .order_by(desc(AlertNotificationRecord.created_at), desc(AlertNotificationRecord.id))
                .offset(offset)
                .limit(page_size)
            ).scalars().all()
            return list(rows), int(total)
