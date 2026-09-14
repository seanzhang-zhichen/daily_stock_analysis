# -*- coding: utf-8 -*-
"""组合（Portfolio）相关数据模型。"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from src.storage.base import Base


class PortfolioAccount(Base):
    """组合账户元数据表。

    记录投资组合的基本信息，包括：
    - 账户名称、所属券商、市场（cn/hk/us）
    - 基础货币（base_currency）
    - 活跃状态（is_active）
    通过 ``(owner_id, is_active)`` 复合索引支持按用户查询活跃账户列表。
    """

    __tablename__ = 'portfolio_accounts'

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String(64), index=True)
    name = Column(String(64), nullable=False)
    broker = Column(String(64))
    market = Column(String(8), nullable=False, default='cn', index=True)  # cn/hk/us
    base_currency = Column(String(8), nullable=False, default='CNY')
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        # 复合索引：按用户和活跃状态查询账户列表
        Index('ix_portfolio_account_owner_active', 'owner_id', 'is_active'),
    )


class PortfolioTrade(Base):
    """已执行的成交事件，作为估值回放的"事实源"。

    记录每次交易的详细信息，包括：
    - 交易方向（buy/sell）
    - 交易数量、价格、费用、税费
    - 交易日期和时间
    通过 ``trade_uid`` 和 ``dedup_hash`` 实现去重，避免同一笔交易被重复记录。
    """

    __tablename__ = 'portfolio_trades'

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('portfolio_accounts.id'), nullable=False, index=True)
    trade_uid = Column(String(128))
    symbol = Column(String(16), nullable=False, index=True)
    market = Column(String(8), nullable=False, default='cn')
    currency = Column(String(8), nullable=False, default='CNY')
    trade_date = Column(Date, nullable=False, index=True)
    side = Column(String(8), nullable=False)  # buy/sell
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    fee = Column(Float, default=0.0)
    tax = Column(Float, default=0.0)
    note = Column(String(255))
    dedup_hash = Column(String(64), index=True)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        # 唯一约束：同一账户同一交易 UID 只能有一条记录
        UniqueConstraint('account_id', 'trade_uid', name='uix_portfolio_trade_uid'),
        # 唯一约束：同一账户同一去重哈希只能有一条记录
        UniqueConstraint('account_id', 'dedup_hash', name='uix_portfolio_trade_dedup_hash'),
        # 复合索引：按账户和交易日期查询交易历史
        Index('ix_portfolio_trade_account_date', 'account_id', 'trade_date'),
    )


class PortfolioCashLedger(Base):
    """资金存取事件表。

    记录账户的资金流入流出，包括：
    - 入金（direction='in'）：充值、转账等
    - 出金（direction='out'）：提现、转账等
    通过 ``(account_id, event_date)`` 复合索引支持按账户和时间查询资金流水。
    """

    __tablename__ = 'portfolio_cash_ledger'

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('portfolio_accounts.id'), nullable=False, index=True)
    event_date = Column(Date, nullable=False, index=True)
    direction = Column(String(8), nullable=False)  # in/out
    amount = Column(Float, nullable=False)
    currency = Column(String(8), nullable=False, default='CNY')
    note = Column(String(255))
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        # 复合索引：按账户和事件日期查询资金流水
        Index('ix_portfolio_cash_account_date', 'account_id', 'event_date'),
    )


class PortfolioCorporateAction(Base):
    """影响现金或持仓数量的公司行为（分红 / 拆股等）。

    记录公司行为对投资组合的影响，包括：
    - 现金分红（cash_dividend）：每股分红金额
    - 拆股（split_adjustment）：拆股比例
    通过 ``(account_id, effective_date)`` 复合索引支持按账户和生效日期查询公司行为。
    """

    __tablename__ = 'portfolio_corporate_actions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('portfolio_accounts.id'), nullable=False, index=True)
    symbol = Column(String(16), nullable=False, index=True)
    market = Column(String(8), nullable=False, default='cn')
    currency = Column(String(8), nullable=False, default='CNY')
    effective_date = Column(Date, nullable=False, index=True)
    action_type = Column(String(24), nullable=False)  # cash_dividend/split_adjustment
    cash_dividend_per_share = Column(Float)
    split_ratio = Column(Float)
    note = Column(String(255))
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        # 复合索引：按账户和生效日期查询公司行为
        Index('ix_portfolio_ca_account_date', 'account_id', 'effective_date'),
    )


class PortfolioPosition(Base):
    """某账户每个标的最新的持仓快照（按成本法分组）。

    记录账户中每个标的的当前持仓状态，包括：
    - 持仓数量、平均成本、总成本
    - 最新价格、市值、未实现盈亏
    - 估值货币
    通过 ``(account_id, symbol, market, currency, cost_method)`` 唯一约束
    保证同一账户同一标的在同一成本法下只有一条记录。
    """

    __tablename__ = 'portfolio_positions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('portfolio_accounts.id'), nullable=False, index=True)
    cost_method = Column(String(8), nullable=False, default='fifo')
    symbol = Column(String(16), nullable=False, index=True)
    market = Column(String(8), nullable=False, default='cn')
    currency = Column(String(8), nullable=False, default='CNY')
    quantity = Column(Float, nullable=False, default=0.0)
    avg_cost = Column(Float, nullable=False, default=0.0)
    total_cost = Column(Float, nullable=False, default=0.0)
    last_price = Column(Float, nullable=False, default=0.0)
    market_value_base = Column(Float, nullable=False, default=0.0)
    unrealized_pnl_base = Column(Float, nullable=False, default=0.0)
    valuation_currency = Column(String(8), nullable=False, default='CNY')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)

    __table_args__ = (
        # 唯一约束：同一账户同一标的在同一成本法下只有一条记录
        UniqueConstraint(
            'account_id',
            'symbol',
            'market',
            'currency',
            'cost_method',
            name='uix_portfolio_position_account_symbol_market_currency',
        ),
    )


class PortfolioPositionLot(Base):
    """FIFO 回放使用的逐笔剩余批次记录。

    记录每笔买入交易的剩余数量，用于 FIFO 成本计算。
    当卖出时，按买入顺序（open_date）逐笔扣减剩余数量，
    直到卖出数量完全匹配。
    通过 ``(account_id, symbol)`` 复合索引支持按账户和标的查询批次。
    """

    __tablename__ = 'portfolio_position_lots'

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('portfolio_accounts.id'), nullable=False, index=True)
    cost_method = Column(String(8), nullable=False, default='fifo')
    symbol = Column(String(16), nullable=False, index=True)
    market = Column(String(8), nullable=False, default='cn')
    currency = Column(String(8), nullable=False, default='CNY')
    open_date = Column(Date, nullable=False, index=True)
    remaining_quantity = Column(Float, nullable=False, default=0.0)
    unit_cost = Column(Float, nullable=False, default=0.0)
    source_trade_id = Column(Integer, ForeignKey('portfolio_trades.id'))
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)

    __table_args__ = (
        # 复合索引：按账户和标的查询批次
        Index('ix_portfolio_lot_account_symbol', 'account_id', 'symbol'),
    )


class PortfolioDailySnapshot(Base):
    """读时回放生成的账户每日快照。

    记录账户在某一日的完整状态，包括：
    - 现金总额、总市值、总权益
    - 未实现盈亏、已实现盈亏
    - 费用和税费总额
    - 汇率是否过期（fx_stale）
    通过 ``(account_id, snapshot_date, cost_method)`` 唯一约束
    保证同一账户同一日期在同一成本法下只有一条快照。
    """

    __tablename__ = 'portfolio_daily_snapshots'

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('portfolio_accounts.id'), nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    cost_method = Column(String(8), nullable=False, default='fifo')  # fifo/avg
    base_currency = Column(String(8), nullable=False, default='CNY')
    total_cash = Column(Float, nullable=False, default=0.0)
    total_market_value = Column(Float, nullable=False, default=0.0)
    total_equity = Column(Float, nullable=False, default=0.0)
    unrealized_pnl = Column(Float, nullable=False, default=0.0)
    realized_pnl = Column(Float, nullable=False, default=0.0)
    fee_total = Column(Float, nullable=False, default=0.0)
    tax_total = Column(Float, nullable=False, default=0.0)
    fx_stale = Column(Boolean, nullable=False, default=False)
    payload = Column(Text)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        # 唯一约束：同一账户同一日期在同一成本法下只有一条快照
        UniqueConstraint(
            'account_id',
            'snapshot_date',
            'cost_method',
            name='uix_portfolio_snapshot_account_date_method',
        ),
    )


class PortfolioFxRate(Base):
    """跨币种组合估值用的汇率缓存。

    记录不同货币对之间的汇率，用于多币种组合的估值。
    通过 ``(from_currency, to_currency, rate_date)`` 唯一约束
    保证同一货币对同一天只有一条汇率记录。
    ``is_stale`` 标记汇率是否过期，用于提醒用户更新汇率。
    """

    __tablename__ = 'portfolio_fx_rates'

    id = Column(Integer, primary_key=True, autoincrement=True)
    from_currency = Column(String(8), nullable=False, index=True)
    to_currency = Column(String(8), nullable=False, index=True)
    rate_date = Column(Date, nullable=False, index=True)
    rate = Column(Float, nullable=False)
    source = Column(String(32), nullable=False, default='manual')
    is_stale = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        # 唯一约束：同一货币对同一天只有一条汇率记录
        UniqueConstraint(
            'from_currency',
            'to_currency',
            'rate_date',
            name='uix_portfolio_fx_pair_date',
        ),
    )


__all__ = [
    "PortfolioAccount",
    "PortfolioTrade",
    "PortfolioCashLedger",
    "PortfolioCorporateAction",
    "PortfolioPosition",
    "PortfolioPositionLot",
    "PortfolioDailySnapshot",
    "PortfolioFxRate",
]
