# -*- coding: utf-8 -*-
"""核心业务表：日线、新闻情报、基本面快照、分析历史。"""

from datetime import datetime
from typing import Any, Dict

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    ForeignKey,
)

from src.storage.base import Base


class StockDaily(Base):
    """股票日线数据模型。

    存储每日行情数据与计算的技术指标；支持多股票、多日期唯一约束。
    """
    __tablename__ = 'stock_daily'

    # 主键
    id = Column(Integer, primary_key=True, autoincrement=True)

    # 股票代码（如 600、000001 等）
    code = Column(String(10), nullable=False, index=True)

    # 稳定分析身份（如 sh000300 / sh600519）。可空以兼容存量数据库。
    canonical_id = Column(String(32), nullable=True, index=True)

    # 交易日期
    date = Column(Date, nullable=False, index=True)

    # OHLC 数据
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)

    # 成交数据
    volume = Column(Float)  # 成交量（股）
    amount = Column(Float)  # 成交额（元）
    pct_chg = Column(Float)  # 涨跌幅（%）

    # 技术指标
    ma5 = Column(Float)
    ma10 = Column(Float)
    ma20 = Column(Float)
    volume_ratio = Column(Float)  # 量比

    # 数据来源
    data_source = Column(String(50))  # 记录数据来源（如 AkshareFetcher）

    # 更新时间
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # 唯一约束：同一股票同一日期只能有一条数据
    __table_args__ = (
        UniqueConstraint('code', 'date', name='uix_code_date'),
        Index('ix_code_date', 'code', 'date'),
    )

    def __repr__(self):
        """返回调试友好的日线记录摘要。"""
        return f"<StockDaily(code={self.code}, date={self.date}, close={self.close})>"

    def to_dict(self) -> Dict[str, Any]:
        """将日线记录序列化为字典，供 API / 分析上下文使用。"""
        return {
            'code': self.code,
            'canonical_id': self.canonical_id,
            'date': self.date,
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'close': self.close,
            'volume': self.volume,
            'amount': self.amount,
            'pct_chg': self.pct_chg,
            'ma5': self.ma5,
            'ma10': self.ma10,
            'ma20': self.ma20,
            'volume_ratio': self.volume_ratio,
            'data_source': self.data_source,
        }


class NewsIntel(Base):
    """新闻情报数据模型。

    存储抓取到的新闻条目，用于后续分析、查询与回放；``(code, published_date)``
    索引便于按个股快速拉取近期新闻。
    """
    __tablename__ = 'news_intel'

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 关联用户查询操作
    query_id = Column(String(64), index=True)

    # 股票信息
    code = Column(String(10), nullable=False, index=True)
    name = Column(String(50))

    # 搜索上下文
    dimension = Column(String(32), index=True)  # latest_news / risk_check / earnings / market_analysis / industry
    query = Column(String(255))
    provider = Column(String(32), index=True)

    # 新闻内容
    title = Column(String(300), nullable=False)
    snippet = Column(Text)
    url = Column(String(768), nullable=False)
    source = Column(String(100))
    published_date = Column(DateTime, index=True)

    # 入库时间
    fetched_at = Column(DateTime, default=datetime.now, index=True)
    query_source = Column(String(32), index=True)  # bot/web/cli/system
    requester_platform = Column(String(20))
    requester_user_id = Column(String(64))
    requester_user_name = Column(String(64))
    requester_chat_id = Column(String(64))
    requester_message_id = Column(String(64))
    requester_query = Column(String(255))

    __table_args__ = (
        UniqueConstraint('url', name='uix_news_url'),
        Index('ix_news_code_pub', 'code', 'published_date'),
    )

    def __repr__(self) -> str:
        """返回调试友好的新闻记录摘要，避免打印完整正文。"""
        return f"<NewsIntel(code={self.code}, title={self.title[:20]}...)>"


class FundamentalSnapshot(Base):
    """基本面上下文快照（P0 write-only）。

    仅用于写入，主链路不依赖读取该表，便于后续回测 / 画像扩展。
    """
    __tablename__ = 'fundamental_snapshot'

    id = Column(Integer, primary_key=True, autoincrement=True)
    query_id = Column(String(64), nullable=False, index=True)
    code = Column(String(10), nullable=False, index=True)
    payload = Column(Text, nullable=False)
    source_chain = Column(Text)
    coverage = Column(Text)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        Index('ix_fundamental_snapshot_query_code', 'query_id', 'code'),
        Index('ix_fundamental_snapshot_created', 'created_at'),
    )

    def __repr__(self) -> str:
        """返回调试友好的基本面快照摘要。"""
        return f"<FundamentalSnapshot(query_id={self.query_id}, code={self.code})>"


INTELLIGENCE_ITEM_NULL_SCOPE_VALUE = "__all__"


class IntelligenceSource(Base):
    """可配置的 RSS/Atom/NewsNow 情报源（用于 A 股场景）。"""

    __tablename__ = "intelligence_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True, index=True)
    source_type = Column(String(32), nullable=False, default="rss", index=True)
    url = Column(String(1000), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    scope_type = Column(String(32), nullable=False, default="market", index=True)
    scope_value = Column(String(64), index=True)
    market = Column(String(32), nullable=False, default="cn", index=True)
    description = Column(Text)
    last_status = Column(String(32))
    last_error = Column(Text)
    last_fetched_at = Column(DateTime, index=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)


class IntelligenceItem(Base):
    """持久化的归一化情报条目。"""

    __tablename__ = "intelligence_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(Integer, ForeignKey("intelligence_sources.id", ondelete="SET NULL"), nullable=True, index=True)
    source_name = Column(String(100), index=True)
    source_type = Column(String(32), nullable=False, default="rss", index=True)
    title = Column(String(300), nullable=False)
    summary = Column(Text)
    url = Column(String(1000), nullable=False, index=True)
    source = Column(String(100))
    published_at = Column(DateTime, index=True)
    fetched_at = Column(DateTime, default=datetime.now, index=True)
    scope_type = Column(String(32), nullable=False, default="market", index=True)
    scope_value = Column(String(64), nullable=False, default=INTELLIGENCE_ITEM_NULL_SCOPE_VALUE, index=True)
    market = Column(String(32), nullable=False, default="cn", index=True)
    raw_payload = Column(Text)

    __table_args__ = (
        UniqueConstraint("source_id", "url", "scope_type", "scope_value", "market", name="uix_intel_item_source_scope_url"),
        Index("ix_intel_item_scope_time", "scope_type", "scope_value", "market", "published_at"),
        Index("ix_intel_item_fetch_time", "fetched_at"),
    )


class AnalysisHistory(Base):
    """分析结果历史记录模型。

    保存每次分析结果，支持按 ``query_id`` / 股票代码检索，附带用于回测的
    狙击点位（``ideal_buy`` / ``secondary_buy`` / ``stop_loss`` / ``take_profit``）。
    """
    __tablename__ = 'analysis_history'

    id = Column(Integer, primary_key=True, autoincrement=True)

    # To C 多用户隔离: 由 endpoint 注入 current_user.id。
    user_id = Column(Integer, index=True, nullable=True)

    # 关联查询链路
    query_id = Column(String(64), index=True)

    # 股票信息
    code = Column(String(10), nullable=False, index=True)
    name = Column(String(50))
    report_type = Column(String(16), index=True)

    # 核心结论
    sentiment_score = Column(Integer)
    operation_advice = Column(String(20))
    trend_prediction = Column(String(50))
    analysis_summary = Column(Text)

    # 详细数据
    raw_result = Column(Text)
    news_content = Column(Text)
    context_snapshot = Column(Text)

    # 狙击点位（用于回测）
    ideal_buy = Column(Float)
    secondary_buy = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)

    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        Index('ix_analysis_code_time', 'code', 'created_at'),
    )

    def to_dict(self) -> Dict[str, Any]:
        """将分析历史记录序列化为字典，供 API 与回测管线使用。"""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'query_id': self.query_id,
            'code': self.code,
            'name': self.name,
            'report_type': self.report_type,
            'sentiment_score': self.sentiment_score,
            'operation_advice': self.operation_advice,
            'trend_prediction': self.trend_prediction,
            'analysis_summary': self.analysis_summary,
            'raw_result': self.raw_result,
            'news_content': self.news_content,
            'context_snapshot': self.context_snapshot,
            'ideal_buy': self.ideal_buy,
            'secondary_buy': self.secondary_buy,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class ScreeningRun(Base):
    """内置选股（screening）运行结果与载荷的持久化记录。"""

    __tablename__ = "screening_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    run_id = Column(String(64), nullable=False, unique=True, index=True)
    strategy = Column(String(64), nullable=False, index=True)
    market = Column(String(16), nullable=False, index=True)
    snapshot_source = Column(String(64), index=True)
    snapshot_count = Column(Integer)
    after_filter_count = Column(Integer)
    candidate_count = Column(Integer, nullable=False, default=0)
    llm_ranked = Column(Boolean)
    daily_enriched = Column(Boolean)
    source_errors_json = Column(Text)
    warnings_json = Column(Text)
    result_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)

    __table_args__ = (
        Index("ix_screening_run_strategy_created", "strategy", "created_at"),
        Index("ix_screening_run_market_created", "market", "created_at"),
    )


class StockIndexEntry(Base):
    """本地股票搜索索引条目，覆盖代码、中文名、拼音和别名。"""

    __tablename__ = 'stock_index'

    canonical_code = Column(String(32), primary_key=True)
    display_code = Column(String(32), nullable=False, index=True)
    name_zh = Column(String(128), nullable=False, index=True)
    pinyin_full = Column(String(255), nullable=True, index=True)
    pinyin_abbr = Column(String(64), nullable=True, index=True)
    aliases = Column(Text, nullable=True)
    aliases_text = Column(Text, nullable=True)
    market = Column(String(16), nullable=False, index=True)
    asset_type = Column(String(16), nullable=False, index=True)
    active = Column(Boolean, nullable=False, default=True, index=True)
    popularity = Column(Integer, nullable=True, default=0)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)

    __table_args__ = (
        Index('ix_stock_index_display_active', 'display_code', 'active'),
        Index('ix_stock_index_name_active', 'name_zh', 'active'),
        Index('ix_stock_index_pinyin_active', 'pinyin_abbr', 'pinyin_full', 'active'),
    )

    def to_suggestion_dict(self) -> Dict[str, Any]:
        """序列化为前端搜索建议所需的最小字段集。"""
        return {
            "canonicalCode": self.canonical_code,
            "displayCode": self.display_code,
            "nameZh": self.name_zh,
            "market": self.market,
        }


class StockIndexMeta(Base):
    """股票索引同步元数据，用于判断本地索引版本和规模。"""

    __tablename__ = 'stock_index_meta'

    key = Column(String(64), primary_key=True)
    version = Column(String(64), nullable=False)
    total = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)


__all__ = [
    "StockDaily",
    "NewsIntel",
    "FundamentalSnapshot",
    "AnalysisHistory",
    "ScreeningRun",
    "StockIndexEntry",
    "StockIndexMeta",
]
