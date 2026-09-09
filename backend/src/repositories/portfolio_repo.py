# -*- coding: utf-8 -*-
"""组合（Portfolio）数据访问层。

负责组合账户（``PortfolioAccount``）、成交（``PortfolioTrade``）、资金流水
（``PortfolioCashLedger``）、公司行为（``PortfolioCorporateAction``）、持仓快照
（``PortfolioPosition`` / ``PortfolioPositionLot`` / ``PortfolioDailySnapshot``）
以及汇率缓存（``PortfolioFxRate``）等 P0 域表的 CRUD 与查询。

主要特性：

- 写操作使用 ``portfolio_write_session`` 串行化（``BEGIN IMMEDIATE``），避免
  SQLite 多写入并发导致账本错乱或缓存失效竞态；
- 写后自动失效受影响账户从变更日起的派生缓存（持仓/快照）；
- 唯一约束冲突会被翻译成 ``DuplicateTradeUidError`` /
  ``DuplicateTradeDedupHashError``，方便上层识别导入重复；
- 提供 ``*_in_session`` 系列方法供调用方在已有事务中复用写入流程。

供组合服务层（``backend/src/services/portfolio``）与 API 端点
（``backend/api/v1/endpoints/portfolio.py``）调用。
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import and_, delete, desc, func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from src.storage import (
    DatabaseManager,
    PortfolioAccount,
    PortfolioCashLedger,
    PortfolioCorporateAction,
    PortfolioDailySnapshot,
    PortfolioFxRate,
    PortfolioPosition,
    PortfolioPositionLot,
    PortfolioTrade,
    StockDaily,
)

logger = logging.getLogger(__name__)


class DuplicateTradeUidError(Exception):
    """账户内 ``trade_uid`` 与已有记录冲突时抛出。"""


class DuplicateTradeDedupHashError(Exception):
    """账户内 ``dedup_hash`` 与已有记录冲突时抛出。"""


class PortfolioBusyError(Exception):
    """SQLite 写串行化未能拿到账本锁时抛出，调用方应快速重试。"""


class PortfolioRepository:
    """组合 P0 域的数据访问层。

    提供组合账户、成交/资金/公司行为等事件流、估值快照以及汇率缓存的写入与
    查询能力；上层（组合服务、估值计算器、回测/风险监控）通过它与数据库解耦。
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        """测试时传入自定义 ``db_manager``，运行时使用全局单例。"""
        self.db = db_manager or DatabaseManager.get_instance()

    # ------------------------------------------------------------------
    # Account CRUD
    # ------------------------------------------------------------------
    def create_account(
        self,
        *,
        name: str,
        broker: Optional[str],
        market: str,
        base_currency: str,
        owner_id: Optional[str] = None,
    ) -> PortfolioAccount:
        """创建一个处于激活状态的组合账户，并返回刷新后的 ORM 行。"""
        with self.db.get_session() as session:
            row = PortfolioAccount(
                owner_id=owner_id,
                name=name,
                broker=broker,
                market=market,
                base_currency=base_currency,
                is_active=True,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def get_account(self, account_id: int, include_inactive: bool = False) -> Optional[PortfolioAccount]:
        """查询单个账户；默认隐藏软删除（``is_active=False``）的记录。"""
        with self.db.get_session() as session:
            return self.get_account_in_session(
                session=session,
                account_id=account_id,
                include_inactive=include_inactive,
            )

    def list_accounts(
        self,
        include_inactive: bool = False,
        owner_id: Optional[str] = None,
    ) -> List[PortfolioAccount]:
        """列出账户，可选按 owner 过滤，并可包含已停用账户。"""
        with self.db.get_session() as session:
            query = select(PortfolioAccount)
            if not include_inactive:
                query = query.where(PortfolioAccount.is_active.is_(True))
            if owner_id is not None:
                query = query.where(PortfolioAccount.owner_id == owner_id)
            rows = session.execute(query.order_by(PortfolioAccount.id.asc())).scalars().all()
            return list(rows)

    def get_account_in_session(
        self,
        *,
        session: Any,
        account_id: int,
        include_inactive: bool = False,
    ) -> Optional[PortfolioAccount]:
        """会话内（事务中）查询账户，供调用方在已有事务里复用。"""
        conditions = [PortfolioAccount.id == account_id]
        if not include_inactive:
            conditions.append(PortfolioAccount.is_active.is_(True))
        return session.execute(
            select(PortfolioAccount).where(and_(*conditions)).limit(1)
        ).scalar_one_or_none()

    def update_account(self, account_id: int, fields: Dict[str, Any]) -> Optional[PortfolioAccount]:
        """按字段集更新可变账户属性；账户不存在时返回 ``None``。"""
        with self.db.get_session() as session:
            row = session.execute(
                select(PortfolioAccount).where(PortfolioAccount.id == account_id).limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            for key, value in fields.items():
                setattr(row, key, value)
            row.updated_at = datetime.now()
            session.commit()
            session.refresh(row)
            return row

    def deactivate_account(self, account_id: int) -> bool:
        """软删除账户（保留历史事件可被查询），返回是否成功。"""
        with self.db.get_session() as session:
            row = session.execute(
                select(PortfolioAccount).where(PortfolioAccount.id == account_id).limit(1)
            ).scalar_one_or_none()
            if row is None:
                return False
            row.is_active = False
            row.updated_at = datetime.now()
            session.commit()
            return True

    # ------------------------------------------------------------------
    # Event writes
    # ------------------------------------------------------------------
    @contextmanager
    def portfolio_write_session(self):
        """打开一个 Portfolio 写事务，串行化 SQLite 写入。

        SQLite 下使用 ``BEGIN IMMEDIATE``：并发写请求会立刻以
        ``PortfolioBusyError`` 失败，让调用方快速重试，而不是让多个事务相互阻塞
        导致缓存失效逻辑错乱。
        """
        session = self.db.get_session()
        try:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        except OperationalError as exc:
            session.close()
            if self._is_sqlite_locked_error(exc):
                raise PortfolioBusyError("Portfolio ledger is busy; please retry shortly.") from exc
            raise

        try:
            yield session
            session.commit()
        except OperationalError as exc:
            session.rollback()
            if self._is_sqlite_locked_error(exc):
                raise PortfolioBusyError("Portfolio ledger is busy; please retry shortly.") from exc
            raise
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def add_trade(
        self,
        *,
        account_id: int,
        trade_uid: Optional[str],
        symbol: str,
        market: str,
        currency: str,
        trade_date: date,
        side: str,
        quantity: float,
        price: float,
        fee: float,
        tax: float,
        note: Optional[str] = None,
        dedup_hash: Optional[str] = None,
    ) -> PortfolioTrade:
        """在一个独立的事务里写入成交记录，并返回脱离 Session 的 ORM 行。"""
        with self.portfolio_write_session() as session:
            row = self.add_trade_in_session(
                session=session,
                account_id=account_id,
                trade_uid=trade_uid,
                symbol=symbol,
                market=market,
                currency=currency,
                trade_date=trade_date,
                side=side,
                quantity=quantity,
                price=price,
                fee=fee,
                tax=tax,
                note=note,
                dedup_hash=dedup_hash,
            )
            session.expunge(row)
            return row

    def add_cash_ledger(
        self,
        *,
        account_id: int,
        event_date: date,
        direction: str,
        amount: float,
        currency: str,
        note: Optional[str] = None,
    ) -> PortfolioCashLedger:
        """写入一条资金流水，并使相关派生缓存失效。"""
        with self.portfolio_write_session() as session:
            row = self.add_cash_ledger_in_session(
                session=session,
                account_id=account_id,
                event_date=event_date,
                direction=direction,
                amount=amount,
                currency=currency,
                note=note,
            )
            session.expunge(row)
            return row

    def add_corporate_action(
        self,
        *,
        account_id: int,
        symbol: str,
        market: str,
        currency: str,
        effective_date: date,
        action_type: str,
        cash_dividend_per_share: Optional[float] = None,
        split_ratio: Optional[float] = None,
        note: Optional[str] = None,
    ) -> PortfolioCorporateAction:
        """写入一条公司行为（分红/拆股等），并使受影响的快照失效。"""
        with self.portfolio_write_session() as session:
            row = self.add_corporate_action_in_session(
                session=session,
                account_id=account_id,
                symbol=symbol,
                market=market,
                currency=currency,
                effective_date=effective_date,
                action_type=action_type,
                cash_dividend_per_share=cash_dividend_per_share,
                split_ratio=split_ratio,
                note=note,
            )
            session.expunge(row)
            return row

    def get_trade_account_id(self, trade_id: int) -> Optional[int]:
        """返回成交记录所属的 ``account_id``；记录不存在时返回 ``None``。"""
        with self.db.get_session() as session:
            row = session.execute(
                select(PortfolioTrade).where(PortfolioTrade.id == trade_id).limit(1)
            ).scalar_one_or_none()
            return int(row.account_id) if row is not None else None

    def get_cash_ledger_account_id(self, entry_id: int) -> Optional[int]:
        """返回资金流水所属的 ``account_id``；记录不存在时返回 ``None``。"""
        with self.db.get_session() as session:
            row = session.execute(
                select(PortfolioCashLedger).where(PortfolioCashLedger.id == entry_id).limit(1)
            ).scalar_one_or_none()
            return int(row.account_id) if row is not None else None

    def get_corporate_action_account_id(self, action_id: int) -> Optional[int]:
        """返回公司行为所属的 ``account_id``；记录不存在时返回 ``None``。"""
        with self.db.get_session() as session:
            row = session.execute(
                select(PortfolioCorporateAction).where(PortfolioCorporateAction.id == action_id).limit(1)
            ).scalar_one_or_none()
            return int(row.account_id) if row is not None else None

    def delete_trade(self, trade_id: int) -> bool:
        """删除一笔成交记录，并清理从成交日起的所有派生缓存。"""
        with self.portfolio_write_session() as session:
            return self.delete_trade_in_session(session=session, trade_id=trade_id)

    def delete_cash_ledger(self, entry_id: int) -> bool:
        """删除一条资金流水，并清理从事件日起的派生缓存。"""
        with self.portfolio_write_session() as session:
            return self.delete_cash_ledger_in_session(session=session, entry_id=entry_id)

    def delete_corporate_action(self, action_id: int) -> bool:
        """删除一条公司行为，并清理受影响的派生组合状态。"""
        with self.portfolio_write_session() as session:
            return self.delete_corporate_action_in_session(session=session, action_id=action_id)

    def has_trade_uid(self, account_id: int, trade_uid: Optional[str]) -> bool:
        """检查账户内是否已存在相同 ``trade_uid`` 的成交记录。"""
        uid = (trade_uid or "").strip()
        if not uid:
            return False
        with self.db.get_session() as session:
            return self.has_trade_uid_in_session(session=session, account_id=account_id, trade_uid=uid)

    def has_trade_dedup_hash(self, account_id: int, dedup_hash: Optional[str]) -> bool:
        """检查账户内是否已存在相同 ``dedup_hash`` 的成交记录。"""
        hash_value = (dedup_hash or "").strip()
        if not hash_value:
            return False
        with self.db.get_session() as session:
            return self.has_trade_dedup_hash_in_session(
                session=session,
                account_id=account_id,
                dedup_hash=hash_value,
            )

    def has_trade_uid_in_session(self, *, session: Any, account_id: int, trade_uid: str) -> bool:
        """会话内的 ``trade_uid`` 唯一性检查，供导入批事务复用。"""
        row = session.execute(
            select(PortfolioTrade.id).where(
                and_(
                    PortfolioTrade.account_id == account_id,
                    PortfolioTrade.trade_uid == trade_uid,
                )
            ).limit(1)
        ).scalar_one_or_none()
        return row is not None

    def has_trade_dedup_hash_in_session(self, *, session: Any, account_id: int, dedup_hash: str) -> bool:
        """会话内的 ``dedup_hash`` 检查，供导入批事务复用。"""
        row = session.execute(
            select(PortfolioTrade.id).where(
                and_(
                    PortfolioTrade.account_id == account_id,
                    PortfolioTrade.dedup_hash == dedup_hash,
                )
            ).limit(1)
        ).scalar_one_or_none()
        return row is not None

    def add_trade_in_session(
        self,
        *,
        session: Any,
        account_id: int,
        trade_uid: Optional[str],
        symbol: str,
        market: str,
        currency: str,
        trade_date: date,
        side: str,
        quantity: float,
        price: float,
        fee: float,
        tax: float,
        note: Optional[str] = None,
        dedup_hash: Optional[str] = None,
    ) -> PortfolioTrade:
        """在调用方传入的会话中插入成交，并把唯一约束冲突翻译为业务异常。"""
        row = PortfolioTrade(
            account_id=account_id,
            trade_uid=trade_uid,
            symbol=symbol,
            market=market,
            currency=currency,
            trade_date=trade_date,
            side=side,
            quantity=quantity,
            price=price,
            fee=fee,
            tax=tax,
            note=note,
            dedup_hash=dedup_hash,
        )
        session.add(row)
        self._invalidate_account_cache_in_session(
            session=session,
            account_id=account_id,
            from_date=trade_date,
        )
        try:
            session.flush()
        except IntegrityError as exc:
            raise self._translate_trade_integrity_error(
                exc=exc,
                account_id=account_id,
                trade_uid=trade_uid,
                dedup_hash=dedup_hash,
            ) from exc
        session.refresh(row)
        return row

    def add_cash_ledger_in_session(
        self,
        *,
        session: Any,
        account_id: int,
        event_date: date,
        direction: str,
        amount: float,
        currency: str,
        note: Optional[str] = None,
    ) -> PortfolioCashLedger:
        """在调用方传入的会话中插入一条资金流水。"""
        row = PortfolioCashLedger(
            account_id=account_id,
            event_date=event_date,
            direction=direction,
            amount=amount,
            currency=currency,
            note=note,
        )
        session.add(row)
        self._invalidate_account_cache_in_session(
            session=session,
            account_id=account_id,
            from_date=event_date,
        )
        session.flush()
        session.refresh(row)
        return row

    def add_corporate_action_in_session(
        self,
        *,
        session: Any,
        account_id: int,
        symbol: str,
        market: str,
        currency: str,
        effective_date: date,
        action_type: str,
        cash_dividend_per_share: Optional[float] = None,
        split_ratio: Optional[float] = None,
        note: Optional[str] = None,
    ) -> PortfolioCorporateAction:
        """在调用方传入的会话中插入一条公司行为。"""
        row = PortfolioCorporateAction(
            account_id=account_id,
            symbol=symbol,
            market=market,
            currency=currency,
            effective_date=effective_date,
            action_type=action_type,
            cash_dividend_per_share=cash_dividend_per_share,
            split_ratio=split_ratio,
            note=note,
        )
        session.add(row)
        self._invalidate_account_cache_in_session(
            session=session,
            account_id=account_id,
            from_date=effective_date,
        )
        session.flush()
        session.refresh(row)
        return row

    def delete_trade_in_session(self, *, session: Any, trade_id: int) -> bool:
        """会话内删除成交记录，仅清理受影响账户自成交日及之后的派生快照。"""
        row = session.execute(
            select(PortfolioTrade).where(PortfolioTrade.id == trade_id).limit(1)
        ).scalar_one_or_none()
        if row is None:
            return False
        self._invalidate_account_cache_in_session(
            session=session,
            account_id=int(row.account_id),
            from_date=row.trade_date,
        )
        session.delete(row)
        session.flush()
        return True

    def delete_cash_ledger_in_session(self, *, session: Any, entry_id: int) -> bool:
        """会话内删除资金流水记录，并清理依赖缓存。"""
        row = session.execute(
            select(PortfolioCashLedger).where(PortfolioCashLedger.id == entry_id).limit(1)
        ).scalar_one_or_none()
        if row is None:
            return False
        self._invalidate_account_cache_in_session(
            session=session,
            account_id=int(row.account_id),
            from_date=row.event_date,
        )
        session.delete(row)
        session.flush()
        return True

    def delete_corporate_action_in_session(self, *, session: Any, action_id: int) -> bool:
        """会话内删除公司行为记录，并清理依赖缓存。"""
        row = session.execute(
            select(PortfolioCorporateAction).where(PortfolioCorporateAction.id == action_id).limit(1)
        ).scalar_one_or_none()
        if row is None:
            return False
        self._invalidate_account_cache_in_session(
            session=session,
            account_id=int(row.account_id),
            from_date=row.effective_date,
        )
        session.delete(row)
        session.flush()
        return True

    # ------------------------------------------------------------------
    # Event reads
    # ------------------------------------------------------------------
    def list_trades(self, account_id: int, as_of: date) -> List[PortfolioTrade]:
        """列出截至 ``as_of`` 的所有成交记录，按账本顺序返回。"""
        with self.db.get_session() as session:
            return self.list_trades_in_session(session=session, account_id=account_id, as_of=as_of)

    def list_trades_in_session(
        self,
        *,
        session: Any,
        account_id: int,
        as_of: date,
    ) -> List[PortfolioTrade]:
        """会话内列出成交记录，供估值计算器在已有事务中复用。"""
        rows = session.execute(
            select(PortfolioTrade)
            .where(
                and_(
                    PortfolioTrade.account_id == account_id,
                    PortfolioTrade.trade_date <= as_of,
                )
            )
            .order_by(PortfolioTrade.trade_date.asc(), PortfolioTrade.id.asc())
        ).scalars().all()
        return list(rows)

    def list_cash_ledger(self, account_id: int, as_of: date) -> List[PortfolioCashLedger]:
        """列出截至 ``as_of`` 的所有资金流水，按事件顺序返回。"""
        with self.db.get_session() as session:
            return self.list_cash_ledger_in_session(session=session, account_id=account_id, as_of=as_of)

    def list_cash_ledger_in_session(
        self,
        *,
        session: Any,
        account_id: int,
        as_of: date,
    ) -> List[PortfolioCashLedger]:
        """会话内列出资金流水，供组合重建使用。"""
        rows = session.execute(
            select(PortfolioCashLedger)
            .where(
                and_(
                    PortfolioCashLedger.account_id == account_id,
                    PortfolioCashLedger.event_date <= as_of,
                )
            )
            .order_by(PortfolioCashLedger.event_date.asc(), PortfolioCashLedger.id.asc())
        ).scalars().all()
        return list(rows)

    def list_corporate_actions(self, account_id: int, as_of: date) -> List[PortfolioCorporateAction]:
        """列出生效日不晚于 ``as_of`` 的所有公司行为。"""
        with self.db.get_session() as session:
            return self.list_corporate_actions_in_session(session=session, account_id=account_id, as_of=as_of)

    def list_corporate_actions_in_session(
        self,
        *,
        session: Any,
        account_id: int,
        as_of: date,
    ) -> List[PortfolioCorporateAction]:
        """会话内列出公司行为，供估值回放使用。"""
        rows = session.execute(
            select(PortfolioCorporateAction)
            .where(
                and_(
                    PortfolioCorporateAction.account_id == account_id,
                    PortfolioCorporateAction.effective_date <= as_of,
                )
            )
            .order_by(PortfolioCorporateAction.effective_date.asc(), PortfolioCorporateAction.id.asc())
        ).scalars().all()
        return list(rows)

    def get_first_activity_date(self, *, account_id: int, as_of: date) -> Optional[date]:
        """返回该账户内（截至 ``as_of``）三类事件中最早的日期。"""
        with self.db.get_session() as session:
            first_trade = session.execute(
                select(func.min(PortfolioTrade.trade_date)).where(
                    and_(
                        PortfolioTrade.account_id == account_id,
                        PortfolioTrade.trade_date <= as_of,
                    )
                )
            ).scalar_one()
            first_cash = session.execute(
                select(func.min(PortfolioCashLedger.event_date)).where(
                    and_(
                        PortfolioCashLedger.account_id == account_id,
                        PortfolioCashLedger.event_date <= as_of,
                    )
                )
            ).scalar_one()
            first_action = session.execute(
                select(func.min(PortfolioCorporateAction.effective_date)).where(
                    and_(
                        PortfolioCorporateAction.account_id == account_id,
                        PortfolioCorporateAction.effective_date <= as_of,
                    )
                )
            ).scalar_one()

            candidates = [item for item in (first_trade, first_cash, first_action) if item is not None]
            if not candidates:
                return None
            return min(candidates)

    def query_trades(
        self,
        *,
        account_id: Optional[int],
        date_from: Optional[date],
        date_to: Optional[date],
        symbols: Optional[List[str]],
        side: Optional[str],
        page: int,
        page_size: int,
        owner_id: Optional[str] = None,
    ) -> Tuple[List[PortfolioTrade], int]:
        """分页查询成交记录，可选按账户 owner 限定；返回 (rows, total)。"""
        with self.db.get_session() as session:
            conditions = []
            if account_id is not None:
                conditions.append(PortfolioTrade.account_id == account_id)
            if date_from is not None:
                conditions.append(PortfolioTrade.trade_date >= date_from)
            if date_to is not None:
                conditions.append(PortfolioTrade.trade_date <= date_to)
            if symbols:
                conditions.append(PortfolioTrade.symbol.in_(symbols))
            if side:
                conditions.append(PortfolioTrade.side == side)
            if owner_id is not None:
                owner_subq = select(PortfolioAccount.id).where(
                    and_(
                        PortfolioAccount.owner_id == owner_id,
                        PortfolioAccount.is_active.is_(True),
                    )
                )
                conditions.append(PortfolioTrade.account_id.in_(owner_subq))
            else:
                active_subq = select(PortfolioAccount.id).where(
                    PortfolioAccount.is_active.is_(True)
                )
                conditions.append(PortfolioTrade.account_id.in_(active_subq))

            data_query = select(PortfolioTrade)
            count_query = select(func.count()).select_from(PortfolioTrade)
            if conditions:
                where_clause = and_(*conditions)
                data_query = data_query.where(where_clause)
                count_query = count_query.where(where_clause)

            total = int(session.execute(count_query).scalar_one() or 0)
            rows = session.execute(
                data_query
                .order_by(PortfolioTrade.trade_date.desc(), PortfolioTrade.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).scalars().all()
            return list(rows), total

    def query_cash_ledger(
        self,
        *,
        account_id: Optional[int],
        date_from: Optional[date],
        date_to: Optional[date],
        direction: Optional[str],
        page: int,
        page_size: int,
        owner_id: Optional[str] = None,
    ) -> Tuple[List[PortfolioCashLedger], int]:
        """分页查询资金流水，可选按 owner 限定；返回 (rows, total)。"""
        with self.db.get_session() as session:
            conditions = []
            if account_id is not None:
                conditions.append(PortfolioCashLedger.account_id == account_id)
            if date_from is not None:
                conditions.append(PortfolioCashLedger.event_date >= date_from)
            if date_to is not None:
                conditions.append(PortfolioCashLedger.event_date <= date_to)
            if direction:
                conditions.append(PortfolioCashLedger.direction == direction)
            if owner_id is not None:
                owner_subq = select(PortfolioAccount.id).where(
                    and_(
                        PortfolioAccount.owner_id == owner_id,
                        PortfolioAccount.is_active.is_(True),
                    )
                )
                conditions.append(PortfolioCashLedger.account_id.in_(owner_subq))
            else:
                active_subq = select(PortfolioAccount.id).where(
                    PortfolioAccount.is_active.is_(True)
                )
                conditions.append(PortfolioCashLedger.account_id.in_(active_subq))

            data_query = select(PortfolioCashLedger)
            count_query = select(func.count()).select_from(PortfolioCashLedger)
            if conditions:
                where_clause = and_(*conditions)
                data_query = data_query.where(where_clause)
                count_query = count_query.where(where_clause)

            total = int(session.execute(count_query).scalar_one() or 0)
            rows = session.execute(
                data_query
                .order_by(PortfolioCashLedger.event_date.desc(), PortfolioCashLedger.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).scalars().all()
            return list(rows), total

    def query_corporate_actions(
        self,
        *,
        account_id: Optional[int],
        date_from: Optional[date],
        date_to: Optional[date],
        symbols: Optional[List[str]],
        action_type: Optional[str],
        page: int,
        page_size: int,
        owner_id: Optional[str] = None,
    ) -> Tuple[List[PortfolioCorporateAction], int]:
        """分页查询公司行为，支持按 symbol/类型/时间范围过滤。"""
        with self.db.get_session() as session:
            conditions = []
            if account_id is not None:
                conditions.append(PortfolioCorporateAction.account_id == account_id)
            if date_from is not None:
                conditions.append(PortfolioCorporateAction.effective_date >= date_from)
            if date_to is not None:
                conditions.append(PortfolioCorporateAction.effective_date <= date_to)
            if symbols:
                conditions.append(PortfolioCorporateAction.symbol.in_(symbols))
            if action_type:
                conditions.append(PortfolioCorporateAction.action_type == action_type)
            if owner_id is not None:
                owner_subq = select(PortfolioAccount.id).where(
                    and_(
                        PortfolioAccount.owner_id == owner_id,
                        PortfolioAccount.is_active.is_(True),
                    )
                )
                conditions.append(PortfolioCorporateAction.account_id.in_(owner_subq))
            else:
                active_subq = select(PortfolioAccount.id).where(
                    PortfolioAccount.is_active.is_(True)
                )
                conditions.append(PortfolioCorporateAction.account_id.in_(active_subq))

            data_query = select(PortfolioCorporateAction)
            count_query = select(func.count()).select_from(PortfolioCorporateAction)
            if conditions:
                where_clause = and_(*conditions)
                data_query = data_query.where(where_clause)
                count_query = count_query.where(where_clause)

            total = int(session.execute(count_query).scalar_one() or 0)
            rows = session.execute(
                data_query
                .order_by(PortfolioCorporateAction.effective_date.desc(), PortfolioCorporateAction.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).scalars().all()
            return list(rows), total

    # ------------------------------------------------------------------
    # Price / FX
    # ------------------------------------------------------------------
    def get_latest_close(self, symbol: str, as_of: date) -> Optional[float]:
        """返回 ``as_of`` 之前的最新收盘价（不含日期）。"""
        close = self.get_latest_close_with_date(symbol=symbol, as_of=as_of)
        return close[0] if close is not None else None

    def get_latest_close_with_date(self, symbol: str, as_of: date) -> Optional[Tuple[float, date]]:
        """返回 ``as_of`` 之前的最新收盘价与对应日期，用于估值陈旧度判断。"""
        with self.db.get_session() as session:
            row = session.execute(
                select(StockDaily)
                .where(
                    and_(
                        StockDaily.code == symbol,
                        StockDaily.date <= as_of,
                    )
                )
                .order_by(desc(StockDaily.date))
                .limit(1)
            ).scalar_one_or_none()
            if row is None or row.close is None:
                return None
            return float(row.close), row.date

    def save_fx_rate(
        self,
        *,
        from_currency: str,
        to_currency: str,
        rate_date: date,
        rate: float,
        source: str = "manual",
        is_stale: bool = False,
    ) -> None:
        """为指定货币对按日期 upsert 一条汇率记录。"""
        with self.db.get_session() as session:
            existing = session.execute(
                select(PortfolioFxRate).where(
                    and_(
                        PortfolioFxRate.from_currency == from_currency,
                        PortfolioFxRate.to_currency == to_currency,
                        PortfolioFxRate.rate_date == rate_date,
                    )
                ).limit(1)
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    PortfolioFxRate(
                        from_currency=from_currency,
                        to_currency=to_currency,
                        rate_date=rate_date,
                        rate=rate,
                        source=source,
                        is_stale=is_stale,
                    )
                )
            else:
                existing.rate = rate
                existing.source = source
                existing.is_stale = is_stale
                existing.updated_at = datetime.now()
            session.commit()

    def get_latest_fx_rate(
        self,
        *,
        from_currency: str,
        to_currency: str,
        as_of: date,
    ) -> Optional[PortfolioFxRate]:
        """返回指定货币对在 ``as_of`` 之前的最新汇率记录。"""
        with self.db.get_session() as session:
            row = session.execute(
                select(PortfolioFxRate)
                .where(
                    and_(
                        PortfolioFxRate.from_currency == from_currency,
                        PortfolioFxRate.to_currency == to_currency,
                        PortfolioFxRate.rate_date <= as_of,
                    )
                )
                .order_by(desc(PortfolioFxRate.rate_date))
                .limit(1)
            ).scalar_one_or_none()
            return row

    def list_daily_snapshots_for_risk(
        self,
        *,
        as_of: date,
        cost_method: str,
        account_id: Optional[int] = None,
        owner_id: Optional[str] = None,
        lookback_days: int = 180,
    ) -> List[PortfolioDailySnapshot]:
        """按日期升序加载快照行，供风险监控使用。"""
        with self.db.get_session() as session:
            query = select(PortfolioDailySnapshot).where(
                and_(
                    PortfolioDailySnapshot.snapshot_date <= as_of,
                    PortfolioDailySnapshot.cost_method == cost_method,
                )
            )
            if account_id is not None:
                query = query.where(PortfolioDailySnapshot.account_id == account_id)
            active_accounts = select(PortfolioAccount.id).where(
                PortfolioAccount.is_active.is_(True)
            )
            if owner_id is not None:
                active_accounts = active_accounts.where(PortfolioAccount.owner_id == owner_id)
            query = query.where(PortfolioDailySnapshot.account_id.in_(active_accounts))
            rows = session.execute(
                query.order_by(
                    PortfolioDailySnapshot.snapshot_date.asc(),
                    PortfolioDailySnapshot.account_id.asc(),
                )
            ).scalars().all()
            if lookback_days <= 0:
                return list(rows)
            # 只保留最近 N 个日历日窗口内的快照, 用于风险指标计算
            cutoff_ordinal = as_of.toordinal() - lookback_days
            return [row for row in rows if row.snapshot_date.toordinal() >= cutoff_ordinal]

    # ------------------------------------------------------------------
    # Snapshot / position cache
    # ------------------------------------------------------------------
    def replace_positions_and_lots(
        self,
        *,
        account_id: int,
        cost_method: str,
        positions: Iterable[Dict[str, Any]],
        lots: Iterable[Dict[str, Any]],
        valuation_currency: str,
    ) -> None:
        """按账户与成本法覆盖写入最新的持仓与批次缓存。"""
        with self.db.get_session() as session:
            session.execute(
                delete(PortfolioPosition).where(
                    and_(
                        PortfolioPosition.account_id == account_id,
                        PortfolioPosition.cost_method == cost_method,
                    )
                )
            )
            session.execute(
                delete(PortfolioPositionLot).where(
                    and_(
                        PortfolioPositionLot.account_id == account_id,
                        PortfolioPositionLot.cost_method == cost_method,
                    )
                )
            )

            for item in positions:
                session.add(
                    PortfolioPosition(
                        account_id=account_id,
                        cost_method=cost_method,
                        symbol=item["symbol"],
                        market=item["market"],
                        currency=item["currency"],
                        quantity=float(item["quantity"]),
                        avg_cost=float(item["avg_cost"]),
                        total_cost=float(item["total_cost"]),
                        last_price=float(item["last_price"]),
                        market_value_base=float(item["market_value_base"]),
                        unrealized_pnl_base=float(item["unrealized_pnl_base"]),
                        valuation_currency=valuation_currency,
                    )
                )

            for lot in lots:
                session.add(
                    PortfolioPositionLot(
                        account_id=account_id,
                        cost_method=cost_method,
                        symbol=lot["symbol"],
                        market=lot["market"],
                        currency=lot["currency"],
                        open_date=lot["open_date"],
                        remaining_quantity=float(lot["remaining_quantity"]),
                        unit_cost=float(lot["unit_cost"]),
                        source_trade_id=lot.get("source_trade_id"),
                    )
                )

            session.commit()

    def _invalidate_account_cache_in_session(self, *, session: Any, account_id: int, from_date: date) -> None:
        """清理可能受账本变更影响的派生组合缓存。"""
        session.execute(
            delete(PortfolioPositionLot).where(PortfolioPositionLot.account_id == account_id)
        )
        session.execute(
            delete(PortfolioPosition).where(PortfolioPosition.account_id == account_id)
        )
        session.execute(
            delete(PortfolioDailySnapshot).where(
                and_(
                    PortfolioDailySnapshot.account_id == account_id,
                    PortfolioDailySnapshot.snapshot_date >= from_date,
                )
            )
        )

    @staticmethod
    def _is_sqlite_locked_error(exc: OperationalError) -> bool:
        """识别应被翻译为 ``PortfolioBusyError`` 的 SQLite 锁错误。"""
        err_text = str(getattr(exc, "orig", exc)).lower()
        return any(
            token in err_text
            for token in (
                "database is locked",
                "database schema is locked",
                "database table is locked",
            )
        )

    @staticmethod
    def _translate_trade_integrity_error(
        *,
        exc: IntegrityError,
        account_id: int,
        trade_uid: Optional[str],
        dedup_hash: Optional[str],
    ) -> Exception:
        """把底层唯一约束错误映射到领域特定异常。"""
        err_text = str(getattr(exc, "orig", exc)).lower()
        if trade_uid and ("uix_portfolio_trade_uid" in err_text or "unique" in err_text):
            return DuplicateTradeUidError(
                f"Duplicate trade_uid for account_id={account_id}: {trade_uid}"
            )
        if dedup_hash and (
            "uix_portfolio_trade_dedup_hash" in err_text
            or "portfolio_trades.account_id, portfolio_trades.dedup_hash" in err_text
            or ("unique" in err_text and "dedup_hash" in err_text)
        ):
            return DuplicateTradeDedupHashError(
                f"Duplicate dedup_hash for account_id={account_id}: {dedup_hash}"
            )
        return exc

    def upsert_daily_snapshot(
        self,
        *,
        account_id: int,
        snapshot_date: date,
        cost_method: str,
        base_currency: str,
        total_cash: float,
        total_market_value: float,
        total_equity: float,
        unrealized_pnl: float,
        realized_pnl: float,
        fee_total: float,
        tax_total: float,
        fx_stale: bool,
        payload: str,
    ) -> None:
        """插入或更新一条组合的每日快照行。"""
        with self.db.get_session() as session:
            existing = session.execute(
                select(PortfolioDailySnapshot).where(
                    and_(
                        PortfolioDailySnapshot.account_id == account_id,
                        PortfolioDailySnapshot.snapshot_date == snapshot_date,
                        PortfolioDailySnapshot.cost_method == cost_method,
                    )
                ).limit(1)
            ).scalar_one_or_none()

            if existing is None:
                session.add(
                    PortfolioDailySnapshot(
                        account_id=account_id,
                        snapshot_date=snapshot_date,
                        cost_method=cost_method,
                        base_currency=base_currency,
                        total_cash=total_cash,
                        total_market_value=total_market_value,
                        total_equity=total_equity,
                        unrealized_pnl=unrealized_pnl,
                        realized_pnl=realized_pnl,
                        fee_total=fee_total,
                        tax_total=tax_total,
                        fx_stale=fx_stale,
                        payload=payload,
                    )
                )
            else:
                existing.base_currency = base_currency
                existing.total_cash = total_cash
                existing.total_market_value = total_market_value
                existing.total_equity = total_equity
                existing.unrealized_pnl = unrealized_pnl
                existing.realized_pnl = realized_pnl
                existing.fee_total = fee_total
                existing.tax_total = tax_total
                existing.fx_stale = fx_stale
                existing.payload = payload
                existing.updated_at = datetime.now()
            session.commit()

    def replace_positions_lots_and_snapshot(
        self,
        *,
        account_id: int,
        snapshot_date: date,
        cost_method: str,
        base_currency: str,
        total_cash: float,
        total_market_value: float,
        total_equity: float,
        unrealized_pnl: float,
        realized_pnl: float,
        fee_total: float,
        tax_total: float,
        fx_stale: bool,
        payload: str,
        positions: Iterable[Dict[str, Any]],
        lots: Iterable[Dict[str, Any]],
        valuation_currency: str,
    ) -> None:
        """在同一个事务里原子地刷新持仓缓存与日终快照。"""
        with self.db.get_session() as session:
            session.execute(
                delete(PortfolioPosition).where(
                    and_(
                        PortfolioPosition.account_id == account_id,
                        PortfolioPosition.cost_method == cost_method,
                    )
                )
            )
            session.execute(
                delete(PortfolioPositionLot).where(
                    and_(
                        PortfolioPositionLot.account_id == account_id,
                        PortfolioPositionLot.cost_method == cost_method,
                    )
                )
            )

            for item in positions:
                session.add(
                    PortfolioPosition(
                        account_id=account_id,
                        cost_method=cost_method,
                        symbol=item["symbol"],
                        market=item["market"],
                        currency=item["currency"],
                        quantity=float(item["quantity"]),
                        avg_cost=float(item["avg_cost"]),
                        total_cost=float(item["total_cost"]),
                        last_price=float(item["last_price"]),
                        market_value_base=float(item["market_value_base"]),
                        unrealized_pnl_base=float(item["unrealized_pnl_base"]),
                        valuation_currency=valuation_currency,
                    )
                )

            for lot in lots:
                session.add(
                    PortfolioPositionLot(
                        account_id=account_id,
                        cost_method=cost_method,
                        symbol=lot["symbol"],
                        market=lot["market"],
                        currency=lot["currency"],
                        open_date=lot["open_date"],
                        remaining_quantity=float(lot["remaining_quantity"]),
                        unit_cost=float(lot["unit_cost"]),
                        source_trade_id=lot.get("source_trade_id"),
                    )
                )

            existing = session.execute(
                select(PortfolioDailySnapshot).where(
                    and_(
                        PortfolioDailySnapshot.account_id == account_id,
                        PortfolioDailySnapshot.snapshot_date == snapshot_date,
                        PortfolioDailySnapshot.cost_method == cost_method,
                    )
                ).limit(1)
            ).scalar_one_or_none()

            if existing is None:
                session.add(
                    PortfolioDailySnapshot(
                        account_id=account_id,
                        snapshot_date=snapshot_date,
                        cost_method=cost_method,
                        base_currency=base_currency,
                        total_cash=total_cash,
                        total_market_value=total_market_value,
                        total_equity=total_equity,
                        unrealized_pnl=unrealized_pnl,
                        realized_pnl=realized_pnl,
                        fee_total=fee_total,
                        tax_total=tax_total,
                        fx_stale=fx_stale,
                        payload=payload,
                    )
                )
            else:
                existing.base_currency = base_currency
                existing.total_cash = total_cash
                existing.total_market_value = total_market_value
                existing.total_equity = total_equity
                existing.unrealized_pnl = unrealized_pnl
                existing.realized_pnl = realized_pnl
                existing.fee_total = fee_total
                existing.tax_total = tax_total
                existing.fx_stale = fx_stale
                existing.payload = payload
                existing.updated_at = datetime.now()

            session.commit()
