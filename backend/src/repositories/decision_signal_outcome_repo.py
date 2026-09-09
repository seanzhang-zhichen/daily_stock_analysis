"""决策信号（Decision Signal）后验结果的数据访问层。

``DecisionSignalOutcomeRecord`` 记录每条 ``DecisionSignalRecord`` 在某评估窗口、
某引擎版本下的实际走势命中情况，供回测统计与信号质量复盘使用。

主要能力：

- ``get`` 按 ``(signal_id, horizon, engine_version)`` 唯一键取单条结果；
- ``upsert`` 同唯一键 upsert；返回 ``(row, created)``；
- ``list`` 支持按 ``signal_id`` / ``horizon`` 分页浏览。
"""

from __future__ import annotations

from typing import Any, Optional
from sqlalchemy import and_, desc, select

from src.storage import DatabaseManager, DecisionSignalOutcomeRecord


class DecisionSignalOutcomeRepository:
    """决策信号后验结果的数据访问层（仓储模式）。

    所有方法都通过 :class:`DatabaseManager` 的 session context manager
    操作，确保事务边界与连接释放统一收口。
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        """初始化仓储；支持测试时注入 mock ``db_manager``。

        Args:
            db_manager: 数据库管理器，``None`` 时使用 :meth:`DatabaseManager.get_instance` 单例。
        """
        # 缺省走全局单例，方便业务层无依赖注入即可使用
        self.db = db_manager or DatabaseManager.get_instance()

    def get(self, signal_id: int, horizon: str, engine_version: str):
        """按唯一键 ``(signal_id, horizon, engine_version)`` 取单条记录。

        Args:
            signal_id: 决策信号主键 ID。
            horizon: 评估窗口（``5d`` / ``20d`` 等）。
            engine_version: 当时生成信号的引擎版本号。

        Returns:
            命中的 :class:`DecisionSignalOutcomeRecord`，否则 ``None``。
        """
        with self.db.get_session() as session:
            return session.execute(select(DecisionSignalOutcomeRecord).where(
                DecisionSignalOutcomeRecord.signal_id == signal_id,
                DecisionSignalOutcomeRecord.horizon == horizon,
                DecisionSignalOutcomeRecord.engine_version == engine_version,
            )).scalar_one_or_none()

    def upsert(self, fields: dict[str, Any]):
        """按唯一键 upsert 一条后验结果。

        行为：

        - 若记录不存在 → 新增；
        - 若记录已存在 → 更新除 ``id`` / ``created_at`` 外的全部字段。

        Args:
            fields: 至少包含 ``signal_id`` / ``horizon`` / ``engine_version`` 三元组，
            其余字段将被原样写入模型。

        Returns:
            ``(row, created)``：``created=True`` 表示本次新插入，否则为更新。
        """
        with self.db.get_session() as session:
            # 先用唯一键定位现有行，避免与底层 unique constraint 冲突
            row = session.execute(select(DecisionSignalOutcomeRecord).where(
                DecisionSignalOutcomeRecord.signal_id == fields["signal_id"],
                DecisionSignalOutcomeRecord.horizon == fields["horizon"],
                DecisionSignalOutcomeRecord.engine_version == fields["engine_version"],
            )).scalar_one_or_none()
            # ``created`` 用于告知调用方是新增还是覆盖
            created = row is None
            if row is None:
                row = DecisionSignalOutcomeRecord(**fields)
                session.add(row)
            else:
                # id / created_at 是不可变主键/时间戳, 跳过更新以避免误覆盖
                for key, value in fields.items():
                    if key not in {"id", "created_at"}:
                        setattr(row, key, value)
            session.commit()
            # refresh 后返回 ORM 行，避免后续访问触发懒加载
            session.refresh(row)
            return row, created

    def list(self, *, signal_id: Optional[int] = None, horizon: Optional[str] = None, page: int = 1, page_size: int = 20):
        """分页列出后验结果，按 ``updated_at`` 倒序。

        Args:
            signal_id: 可选过滤条件；``None`` 表示不过滤。
            horizon: 可选过滤条件（评估窗口）；空字符串视为不过滤。
            page: 页码（从 1 开始）。
            page_size: 每页大小。

        Returns:
            ``(rows, total)``：当前页行列表 + 过滤后的总行数。
        """
        with self.db.get_session() as session:
            query = select(DecisionSignalOutcomeRecord)
            count_query = select(DecisionSignalOutcomeRecord.id)
            conditions = []
            # 累加可选过滤条件
            if signal_id is not None: conditions.append(DecisionSignalOutcomeRecord.signal_id == signal_id)
            if horizon: conditions.append(DecisionSignalOutcomeRecord.horizon == horizon)
            if conditions:
                query = query.where(and_(*conditions)); count_query = count_query.where(and_(*conditions))
            # 总数与分页结果分别走独立查询，避免被 offset/limit 影响
            total = len(session.execute(count_query).scalars().all())
            rows = session.execute(query.order_by(desc(DecisionSignalOutcomeRecord.updated_at)).offset((page-1)*page_size).limit(page_size)).scalars().all()
            return list(rows), total
