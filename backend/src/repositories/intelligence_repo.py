"""本地 A 股情报池的持久化访问层。

将情报源（IntelligenceSource）与情报条目（IntelligenceItem）的增删改查封装在仓储类中，
对外供 API 层（`backend/api/v1/endpoints/intelligence.py`）与定时抓取任务调用。

主要能力：
- 情报源与情报条目的 CRUD（含按市场 / 范围 / 关键字 / 时间窗的过滤与分页）
- 基于 (source_id, url, scope_type, scope_value, market) 的复合唯一约束做幂等 upsert
- 按抓取时间的留存清理（apply_retention）
"""
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional
from sqlalchemy import and_, delete, desc, func, or_, select
from src.storage import DatabaseManager, IntelligenceItem, IntelligenceSource, INTELLIGENCE_ITEM_NULL_SCOPE_VALUE


class IntelligenceRepository:
    """情报源与情报条目的仓储封装。

    每个方法都通过 `DatabaseManager.get_session()` 拿到一个上下文会话，
    负责自动提交 / 回滚与连接归还，调用方无需关心底层 session 生命周期。
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        """构造仓储实例。

        Args:
            db_manager: 可选的数据库管理器；未传入时使用全局单例，便于业务侧简化调用。
        """
        self.db = db_manager or DatabaseManager.get_instance()

    def create_source(self, fields: Dict[str, Any]) -> IntelligenceSource:
        """创建一条情报源记录。

        Args:
            fields: 与 `IntelligenceSource` 字段一一对应的字典。

        Returns:
            持久化完成并 refresh 后的 ORM 对象（包含数据库自增 id 等字段）。
        """
        with self.db.get_session() as session:
            row = IntelligenceSource(**fields); session.add(row); session.commit(); session.refresh(row); return row

    def get_source(self, source_id: int):
        """按主键 id 查询情报源。

        Args:
            source_id: 情报源主键 id。

        Returns:
            命中的 `IntelligenceSource`，未命中返回 None。
        """
        with self.db.get_session() as session:
            return session.execute(select(IntelligenceSource).where(IntelligenceSource.id == source_id)).scalar_one_or_none()

    def get_source_by_name(self, name: str):
        """按唯一名称查询情报源，用于按名字解析/判重。

        Args:
            name: 情报源名称。

        Returns:
            命中的 `IntelligenceSource`，未命中返回 None。
        """
        with self.db.get_session() as session:
            return session.execute(select(IntelligenceSource).where(IntelligenceSource.name == name)).scalar_one_or_none()

    def list_sources(self, *, enabled=None, market=None, page=1, page_size=100):
        """分页查询情报源，按 `updated_at` 倒序。

        Args:
            enabled: 可选过滤是否启用；None 表示不施加该过滤。
            market: 可选的市场代码过滤（如 "cn"）。
            page: 页码（从 1 开始）。
            page_size: 每页大小。

        Returns:
            (rows, total) 二元组：当前页 ORM 行列表与符合条件总条数。
        """
        conditions = []
        if enabled is not None: conditions.append(IntelligenceSource.enabled.is_(enabled))
        if market: conditions.append(IntelligenceSource.market == market)
        where = and_(*conditions) if conditions else True
        with self.db.get_session() as session:
            total = session.execute(select(func.count(IntelligenceSource.id)).where(where)).scalar() or 0
            rows = session.execute(select(IntelligenceSource).where(where).order_by(desc(IntelligenceSource.updated_at)).offset((page-1)*page_size).limit(page_size)).scalars().all()
            return list(rows), int(total)

    def update_source_status(self, source_id: int, *, status: str, error: Optional[str] = None, fetched_at=None):
        """更新情报源的最近一次抓取结果。

        会同步刷新 `updated_at`；若源不存在则静默跳过（不抛异常）。

        Args:
            source_id: 情报源主键 id。
            status: 本次抓取的状态字符串。
            error: 本次抓取的错误信息；None 表示无错误。
            fetched_at: 本次抓取时间；未传入时不覆盖原值。
        """
        with self.db.get_session() as session:
            row = session.get(IntelligenceSource, source_id)
            if row:
                row.last_status, row.last_error = status, error
                if fetched_at is not None: row.last_fetched_at = fetched_at
                row.updated_at = datetime.now(); session.commit()

    def set_source_enabled(self, source_id: int, enabled: bool) -> Optional[IntelligenceSource]:
        """Set a source's enabled state and return its refreshed row."""
        with self.db.get_session() as session:
            row = session.get(IntelligenceSource, source_id)
            if row is None:
                return None
            row.enabled = bool(enabled)
            row.updated_at = datetime.now()
            session.commit()
            session.refresh(row)
            return row

    def delete_source(self, source_id: int) -> bool:
        """Delete a source while retaining already ingested items as evidence."""
        with self.db.get_session() as session:
            row = session.get(IntelligenceSource, source_id)
            if row is None:
                return False
            session.execute(
                IntelligenceItem.__table__.update()
                .where(IntelligenceItem.source_id == source_id)
                .values(source_id=None)
            )
            session.delete(row)
            session.commit()
            return True

    def upsert_items(self, items: Iterable[Dict[str, Any]]) -> int:
        """批量 upsert 情报条目。

        判定唯一的复合键为 `(source_id, url, scope_type, scope_value, market)`，
        同一组合若已存在则更新 summary 与 fetched_at，否则新增。仅返回本次新增条数。

        Args:
            items: 与 `IntelligenceItem` 字段一一对应的字典迭代器。

        Returns:
            本次实际新增的条目数。
        """
        saved = 0
        with self.db.get_session() as session:
            for fields in items:
                # 标题或链接缺失视为无效数据，直接丢弃避免污染唯一约束
                if not fields.get("title") or not fields.get("url"): continue
                # scope_value 为空时落到统一的占位值，便于在唯一约束上对齐
                scope = fields.get("scope_value") or INTELLIGENCE_ITEM_NULL_SCOPE_VALUE
                existing = session.execute(select(IntelligenceItem).where(
                    IntelligenceItem.source_id == fields.get("source_id"), IntelligenceItem.url == fields["url"],
                    IntelligenceItem.scope_type == fields.get("scope_type", "market"), IntelligenceItem.scope_value == scope,
                    IntelligenceItem.market == fields.get("market", "cn"))).scalar_one_or_none()
                if existing:
                    existing.summary = fields.get("summary") or existing.summary; existing.fetched_at = fields.get("fetched_at") or datetime.now()
                else:
                    fields = dict(fields); fields["scope_value"] = scope; session.add(IntelligenceItem(**fields)); saved += 1
            session.commit()
        return saved

    def list_items(self, *, market="cn", scope_type=None, scope_value=None, query=None, days=None, page=1, page_size=50):
        """分页查询情报条目，按发布时间或抓取时间倒序。

        Args:
            market: 市场代码，默认 "cn"。
            scope_type: 可选的作用域类型过滤。
            scope_value: 可选的作用域值过滤。
            query: 可选的标题/摘要关键字。
            days: 可选的最近天数窗口；< 1 视为 1。
            page: 页码（从 1 开始）。
            page_size: 每页大小。

        Returns:
            (rows, total) 二元组：当前页 ORM 行列表与符合条件总条数。
        """
        conditions = [IntelligenceItem.market == market]
        if scope_type: conditions.append(IntelligenceItem.scope_type == scope_type)
        if scope_value: conditions.append(IntelligenceItem.scope_value == scope_value)
        if query: conditions.append(or_(IntelligenceItem.title.like(f"%{query}%"), IntelligenceItem.summary.like(f"%{query}%")))
        # days < 1 时强制下限为 1，避免 timedelta 抛异常
        if days: conditions.append(IntelligenceItem.fetched_at >= datetime.now() - timedelta(days=max(1, days)))
        where = and_(*conditions)
        with self.db.get_session() as session:
            total = session.execute(select(func.count(IntelligenceItem.id)).where(where)).scalar() or 0
            rows = session.execute(select(IntelligenceItem).where(where).order_by(desc(func.coalesce(IntelligenceItem.published_at, IntelligenceItem.fetched_at))).offset((page-1)*page_size).limit(page_size)).scalars().all()
            return list(rows), int(total)

    def apply_retention(self, retention_days: int) -> int:
        """按抓取时间清理过期条目。

        Args:
            retention_days: 保留天数；< 1 强制按 1 天处理。

        Returns:
            实际删除的条目数。
        """
        with self.db.get_session() as session:
            result = session.execute(delete(IntelligenceItem).where(IntelligenceItem.fetched_at < datetime.now() - timedelta(days=max(1, retention_days)))); session.commit(); return int(result.rowcount or 0)
