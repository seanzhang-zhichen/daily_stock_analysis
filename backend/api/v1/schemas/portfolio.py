# -*- coding: utf-8 -*-
"""组合（Portfolio）模块对外的 Pydantic 契约。

组合接口覆盖账户、交易流水、现金流水、公司行为、持仓快照、券商导入、汇率刷新
和风险概览。请求模型用于写入事件，响应模型尽量贴近前端表格/看板展示形状。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class PortfolioAccountCreateRequest(BaseModel):
    """创建组合账户的请求载荷。

    用于创建一个新的投资组合账户，包含账户名称、券商、市场、基础货币等信息。

    Attributes:
        name: 账户名称，必填，1-64 字符
        broker: 券商名称，可选，最大 64 字符
        market: 市场标识，可选值 "cn" / "hk" / "us" / "jp" / "kr" / "tw"，默认 "cn"
        base_currency: 基础货币，默认 "CNY"，3-8 字符
        owner_id: 所有者 ID，可选，最大 64 字符
    """

    name: str = Field(..., min_length=1, max_length=64)
    broker: Optional[str] = Field(None, max_length=64)
    market: Literal["cn", "hk", "us", "jp", "kr", "tw"] = "cn"
    base_currency: str = Field("CNY", min_length=3, max_length=8)
    owner_id: Optional[str] = Field(None, max_length=64)


class PortfolioAccountUpdateRequest(BaseModel):
    """组合账户的局部更新请求体。

    用于对已有组合账户进行部分字段更新，所有字段均为可选。

    Attributes:
        name: 账户名称，可选，1-64 字符
        broker: 券商名称，可选，最大 64 字符
        market: 市场标识，可选
        base_currency: 基础货币，可选，3-8 字符
        owner_id: 所有者 ID，可选，最大 64 字符
        is_active: 是否激活，可选
    """

    name: Optional[str] = Field(None, min_length=1, max_length=64)
    broker: Optional[str] = Field(None, max_length=64)
    market: Optional[Literal["cn", "hk", "us", "jp", "kr", "tw"]] = None
    base_currency: Optional[str] = Field(None, min_length=3, max_length=8)
    owner_id: Optional[str] = Field(None, max_length=64)
    is_active: Optional[bool] = None


class PortfolioAccountItem(BaseModel):
    """组合账户列表接口返回的账户元数据。

    包含账户的基本信息，用于账户列表展示。

    Attributes:
        id: 账户唯一标识
        owner_id: 所有者 ID，可选
        name: 账户名称
        broker: 券商名称，可选
        market: 市场标识
        base_currency: 基础货币
        is_active: 是否激活
        created_at: 创建时间（ISO 格式），可选
        updated_at: 最后更新时间（ISO 格式），可选
    """

    id: int
    owner_id: Optional[str] = None
    name: str
    broker: Optional[str] = None
    market: str
    base_currency: str
    is_active: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class PortfolioAccountListResponse(BaseModel):
    """组合账户列表的响应体。

    用于返回用户所有组合账户的列表。

    Attributes:
        accounts: 组合账户列表
    """

    accounts: List[PortfolioAccountItem] = Field(default_factory=list)


class PortfolioTradeCreateRequest(BaseModel):
    """记录一笔买入/卖出交易事件的请求载荷。

    用于记录一笔交易操作，包括股票代码、交易日期、买卖方向、数量、价格等信息。

    Attributes:
        account_id: 关联的账户 ID
        symbol: 股票代码，必填，1-16 字符
        trade_date: 交易日期
        side: 买卖方向，"buy"（买入）或 "sell"（卖出）
        quantity: 交易数量，必须大于 0
        price: 成交价格，必须大于 0
        fee: 交易手续费，默认 0，必须大于等于 0
        tax: 交易税费，默认 0，必须大于等于 0
        market: 市场标识，可选
        currency: 交易货币，可选，3-8 字符
        trade_uid: 交易唯一标识，可选，最大 128 字符
        note: 备注，可选，最大 255 字符
    """

    account_id: int
    symbol: str = Field(..., min_length=1, max_length=16)
    trade_date: date
    side: Literal["buy", "sell"]
    quantity: float = Field(..., gt=0)
    price: float = Field(..., gt=0)
    fee: float = Field(0.0, ge=0)
    tax: float = Field(0.0, ge=0)
    market: Optional[Literal["cn", "hk", "us", "jp", "kr", "tw"]] = None
    currency: Optional[str] = Field(None, min_length=3, max_length=8)
    trade_uid: Optional[str] = Field(None, max_length=128)
    note: Optional[str] = Field(None, max_length=255)


class PortfolioCashLedgerCreateRequest(BaseModel):
    """记录现金存取事件的请求载荷。

    用于记录账户的现金存入或取出操作。

    Attributes:
        account_id: 关联的账户 ID
        event_date: 事件日期
        direction: 方向，"in"（存入）或 "out"（取出）
        amount: 金额，必须大于 0
        currency: 货币，可选，3-8 字符
        note: 备注，可选，最大 255 字符
    """

    account_id: int
    event_date: date
    direction: Literal["in", "out"]
    amount: float = Field(..., gt=0)
    currency: Optional[str] = Field(None, min_length=3, max_length=8)
    note: Optional[str] = Field(None, max_length=255)


class PortfolioCorporateActionCreateRequest(BaseModel):
    """记录分红或拆合股调整事件的请求载荷。

    用于记录公司行为事件，如现金分红、股票拆分或合并等。

    Attributes:
        account_id: 关联的账户 ID
        symbol: 股票代码，必填，1-16 字符
        effective_date: 生效日期
        action_type: 行为类型，"cash_dividend"（现金分红）或 "split_adjustment"（拆合股调整）
        market: 市场标识，可选
        currency: 货币，可选，3-8 字符
        cash_dividend_per_share: 每股现金分红金额，可选，必须大于等于 0
        split_ratio: 拆股比例，可选，必须大于 0（如 2 表示 1 拆 2）
        note: 备注，可选，最大 255 字符
    """

    account_id: int
    symbol: str = Field(..., min_length=1, max_length=16)
    effective_date: date
    action_type: Literal["cash_dividend", "split_adjustment"]
    market: Optional[Literal["cn", "hk", "us", "jp", "kr", "tw"]] = None
    currency: Optional[str] = Field(None, min_length=3, max_length=8)
    cash_dividend_per_share: Optional[float] = Field(None, ge=0)
    split_ratio: Optional[float] = Field(None, gt=0)
    note: Optional[str] = Field(None, max_length=255)


class PortfolioEventCreatedResponse(BaseModel):
    """组合事件表写入成功后的通用响应。

    当交易、现金流水或公司行为等事件成功写入后返回此响应。

    Attributes:
        id: 新创建事件的唯一标识
    """

    id: int


class PortfolioDeleteResponse(BaseModel):
    """组合资源删除结果的统计响应。

    用于返回删除操作的结果统计。

    Attributes:
        deleted: 实际删除的记录数量
    """

    deleted: int


class PortfolioTradeListItem(BaseModel):
    """交易历史列表中的一笔标准化交易记录。

    包含交易的所有关键信息，用于交易历史列表展示。

    Attributes:
        id: 交易记录唯一标识
        account_id: 关联的账户 ID
        trade_uid: 交易唯一标识，可选
        symbol: 股票代码
        market: 市场标识
        currency: 交易货币
        trade_date: 交易日期（ISO 格式）
        side: 买卖方向
        quantity: 交易数量
        price: 成交价格
        fee: 交易手续费
        tax: 交易税费
        note: 备注，可选
        created_at: 创建时间（ISO 格式），可选
    """

    id: int
    account_id: int
    trade_uid: Optional[str] = None
    symbol: str
    market: str
    currency: str
    trade_date: str
    side: str
    quantity: float
    price: float
    fee: float
    tax: float
    note: Optional[str] = None
    created_at: Optional[str] = None


class PortfolioTradeListResponse(BaseModel):
    """交易历史的分页响应。

    用于返回交易历史记录的分页结果。

    Attributes:
        items: 当前页的交易记录列表
        total: 符合条件的记录总数
        page: 当前页码（从 1 开始）
        page_size: 每页数量
    """

    items: List[PortfolioTradeListItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class PortfolioCashLedgerListItem(BaseModel):
    """现金流水列表中的一条存取记录。

    包含现金存取的所有关键信息，用于现金流水列表展示。

    Attributes:
        id: 现金流水记录唯一标识
        account_id: 关联的账户 ID
        event_date: 事件日期（ISO 格式）
        direction: 方向（"in" 或 "out"）
        amount: 金额
        currency: 货币
        note: 备注，可选
        created_at: 创建时间（ISO 格式），可选
    """

    id: int
    account_id: int
    event_date: str
    direction: str
    amount: float
    currency: str
    note: Optional[str] = None
    created_at: Optional[str] = None


class PortfolioCashLedgerListResponse(BaseModel):
    """现金流水的分页响应。

    用于返回现金流水记录的分页结果。

    Attributes:
        items: 当前页的现金流水记录列表
        total: 符合条件的记录总数
        page: 当前页码（从 1 开始）
        page_size: 每页数量
    """

    items: List[PortfolioCashLedgerListItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class PortfolioCorporateActionListItem(BaseModel):
    """公司行为列表中的一条事件记录。

    包含公司行为的所有关键信息，用于公司行为列表展示。

    Attributes:
        id: 公司行为记录唯一标识
        account_id: 关联的账户 ID
        symbol: 股票代码
        market: 市场标识
        currency: 货币
        effective_date: 生效日期（ISO 格式）
        action_type: 行为类型
        cash_dividend_per_share: 每股现金分红金额，可选
        split_ratio: 拆股比例，可选
        note: 备注，可选
        created_at: 创建时间（ISO 格式），可选
    """

    id: int
    account_id: int
    symbol: str
    market: str
    currency: str
    effective_date: str
    action_type: str
    cash_dividend_per_share: Optional[float] = None
    split_ratio: Optional[float] = None
    note: Optional[str] = None
    created_at: Optional[str] = None


class PortfolioCorporateActionListResponse(BaseModel):
    """公司行为的分页响应。

    用于返回公司行为记录的分页结果。

    Attributes:
        items: 当前页的公司行为记录列表
        total: 符合条件的记录总数
        page: 当前页码（从 1 开始）
        page_size: 每页数量
    """

    items: List[PortfolioCorporateActionListItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class PortfolioPositionItem(BaseModel):
    """单只持仓的当前估值快照。

    包含某只股票的持仓数量、成本、当前价格、市值、盈亏等关键信息，
    用于持仓列表和估值展示。

    Attributes:
        symbol: 股票代码
        market: 市场标识
        currency: 交易货币
        quantity: 持仓数量
        avg_cost: 平均成本
        total_cost: 总成本
        last_price: 最新价格
        market_value_base: 按基础货币计算的市值
        unrealized_pnl_base: 按基础货币计算的未实现盈亏
        unrealized_pnl_pct: 未实现盈亏百分比，可选
        valuation_currency: 估值货币
        price_source: 价格来源，默认 "unknown"
        price_provider: 价格提供方，可选
        price_date: 价格日期（ISO 格式），可选
        price_stale: 价格是否过期，默认 False
        price_available: 价格是否可用，默认 True
        data_quality: 数据质量，默认 "ok"
        limitations: 数据限制说明列表
    """

    symbol: str
    market: str
    currency: str
    quantity: float
    avg_cost: float
    total_cost: float
    last_price: float
    market_value_base: float
    unrealized_pnl_base: float
    unrealized_pnl_pct: Optional[float] = None
    valuation_currency: str
    price_source: str = "unknown"
    price_provider: Optional[str] = None
    price_date: Optional[str] = None
    price_stale: bool = False
    price_available: bool = True
    data_quality: str = "ok"
    limitations: List[str] = Field(default_factory=list)


class PortfolioPositionAnalysisRequest(BaseModel):
    """为某只持仓提交分析任务的请求载荷。

    用于触发对某只持仓股票的分析任务。

    Attributes:
        account_id: 可选的账户 ID；当该标的在多个账户中持有时需要指定
        analysis_phase: 分析阶段，"auto"（自动） / "premarket"（盘前） / "intraday"（盘中） / "postmarket"（盘后），默认 "auto"
        force: 是否强制刷新分析输入，默认 False
    """

    account_id: Optional[int] = Field(
        None,
        description="Optional account id; required when the symbol is held in multiple accounts",
    )
    analysis_phase: Literal["auto", "premarket", "intraday", "postmarket"] = "auto"
    force: bool = Field(False, description="Force refresh analysis inputs without bypassing duplicate tasks")


class PortfolioAccountSnapshot(BaseModel):
    """账户级别的持仓、现金与盈亏快照。

    包含某个账户的整体资产状况，包括持仓、现金、盈亏等汇总信息。

    Attributes:
        account_id: 账户唯一标识
        account_name: 账户名称
        owner_id: 所有者 ID，可选
        broker: 券商名称，可选
        market: 市场标识
        base_currency: 基础货币
        as_of: 快照时间（ISO 格式）
        cost_method: 成本计算方法
        total_cash: 总现金
        total_market_value: 总市值
        total_equity: 总权益
        realized_pnl: 已实现盈亏
        unrealized_pnl: 未实现盈亏
        fee_total: 总手续费
        tax_total: 总税费
        fx_stale: 汇率是否过期
        data_quality: 数据质量，默认 "ok"
        limitations: 数据限制说明列表
        positions: 持仓列表
    """

    account_id: int
    account_name: str
    owner_id: Optional[str] = None
    broker: Optional[str] = None
    market: str
    base_currency: str
    as_of: str
    cost_method: str
    total_cash: float
    total_market_value: float
    total_equity: float
    realized_pnl: float
    unrealized_pnl: float
    fee_total: float
    tax_total: float
    fx_stale: bool
    data_quality: str = "ok"
    limitations: List[str] = Field(default_factory=list)
    positions: List[PortfolioPositionItem] = Field(default_factory=list)


class PortfolioSnapshotResponse(BaseModel):
    """跨账户聚合的组合级总览快照。

    包含所有账户的汇总信息，用于组合级总览展示。

    Attributes:
        as_of: 快照时间（ISO 格式）
        cost_method: 成本计算方法
        currency: 基础货币
        account_count: 账户数量
        total_cash: 总现金
        total_market_value: 总市值
        total_equity: 总权益
        realized_pnl: 已实现盈亏
        unrealized_pnl: 未实现盈亏
        fee_total: 总手续费
        tax_total: 总税费
        fx_stale: 汇率是否过期
        data_quality: 数据质量，默认 "ok"
        limitations: 数据限制说明列表
        accounts: 账户快照列表
    """

    as_of: str
    cost_method: str
    currency: str
    account_count: int
    total_cash: float
    total_market_value: float
    total_equity: float
    realized_pnl: float
    unrealized_pnl: float
    fee_total: float
    tax_total: float
    fx_stale: bool
    data_quality: str = "ok"
    limitations: List[str] = Field(default_factory=list)
    accounts: List[PortfolioAccountSnapshot] = Field(default_factory=list)


class PortfolioImportTradeItem(BaseModel):
    """从券商对账单解析后的一条交易记录。

    包含从券商对账单解析出的交易信息，用于导入前的预览和确认。

    Attributes:
        trade_date: 交易日期（ISO 格式）
        symbol: 股票代码
        side: 买卖方向，"buy" 或 "sell"
        quantity: 交易数量
        price: 成交价格
        fee: 交易手续费
        tax: 交易税费
        trade_uid: 交易唯一标识，可选
        dedup_hash: 去重哈希值，用于防止重复导入
        currency: 交易货币，可选
    """

    trade_date: str
    symbol: str
    side: Literal["buy", "sell"]
    quantity: float
    price: float
    fee: float
    tax: float
    trade_uid: Optional[str] = None
    dedup_hash: str
    currency: Optional[str] = None


class PortfolioImportParseResponse(BaseModel):
    """上传/导入券商对账单后的解析结果。

    用于返回券商对账单解析后的预览结果，包括解析成功和失败的数量统计。

    Attributes:
        broker: 券商标识
        record_count: 解析出的记录总数
        skipped_count: 跳过的记录数
        error_count: 错误记录数
        records: 解析出的交易记录列表
        errors: 错误信息列表
    """

    broker: str
    record_count: int
    skipped_count: int
    error_count: int
    records: List[PortfolioImportTradeItem] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class PortfolioImportCommitResponse(BaseModel):
    """导入解析结果落库后的提交结果。

    用于返回导入数据提交到数据库后的结果统计。

    Attributes:
        account_id: 目标账户 ID
        record_count: 解析出的记录总数
        inserted_count: 成功插入的记录数
        duplicate_count: 重复跳过的记录数
        failed_count: 失败的记录数
        dry_run: 是否为试运行（不实际写入）
        errors: 错误信息列表
    """

    account_id: int
    record_count: int
    inserted_count: int
    duplicate_count: int
    failed_count: int
    dry_run: bool
    errors: List[str] = Field(default_factory=list)


class PortfolioImportBrokerItem(BaseModel):
    """支持的券商解析器元数据。

    包含支持的券商导入解析器的基本信息。

    Attributes:
        broker: 券商标识
        aliases: 券商别名列表
        display_name: 券商显示名称，可选
    """

    broker: str
    aliases: List[str] = Field(default_factory=list)
    display_name: Optional[str] = None


class PortfolioImportBrokerListResponse(BaseModel):
    """支持的券商解析器列表响应。

    用于返回所有支持的券商导入解析器列表。

    Attributes:
        brokers: 券商解析器列表
    """

    brokers: List[PortfolioImportBrokerItem] = Field(default_factory=list)


class PortfolioFxRefreshResponse(BaseModel):
    """为组合估值刷新汇率的结果响应。

    用于返回汇率刷新操作的结果统计。

    Attributes:
        as_of: 刷新时间（ISO 格式）
        account_count: 账户数量
        refresh_enabled: 是否启用刷新
        disabled_reason: 禁用原因，可选
        pair_count: 汇率对数量
        updated_count: 成功更新的汇率对数量
        stale_count: 过期汇率对数量
        error_count: 错误数量
    """

    as_of: str
    account_count: int
    refresh_enabled: bool
    disabled_reason: Optional[str] = None
    pair_count: int
    updated_count: int
    stale_count: int
    error_count: int


class PortfolioDecisionSignalRiskItem(BaseModel):
    """组合维度的 AI 建议风险关联项。

    描述组合中某只股票的 AI 决策信号风险信息。

    Attributes:
        account_id: 关联的账户 ID，可选
        symbol: 股票代码
        market: 市场标识
        signal: AI 决策信号详情，Dict 格式
    """

    account_id: Optional[int] = None
    symbol: str
    market: str
    signal: Dict[str, Any] = Field(default_factory=dict)


class PortfolioDecisionSignalRiskBlock(BaseModel):
    """组合维度的 AI 建议风险块（聚合多个标的的信号风险）。

    聚合组合中所有股票的 AI 决策信号风险信息。

    Attributes:
        available: 是否可用，默认 True
        total: 风险项总数
        actions: 按操作类型分组的统计，Dict 格式
        items: 风险关联项列表
    """

    available: bool = True
    total: int = 0
    actions: Dict[str, int] = Field(default_factory=dict)
    items: List[PortfolioDecisionSignalRiskItem] = Field(default_factory=list)


class PortfolioRiskResponse(BaseModel):
    """按风险维度分组的组合风险摘要响应。

    用于返回组合的多维度风险分析结果，包括集中度、回撤、止损等风险指标。

    Attributes:
        as_of: 快照时间（ISO 格式）
        account_id: 关联的账户 ID，可选
        cost_method: 成本计算方法
        currency: 基础货币
        thresholds: 风险阈值配置，Dict 格式
        concentration: 持仓集中度风险指标，Dict 格式
        sector_concentration: 行业集中度风险指标，Dict 格式
        drawdown: 回撤风险指标，Dict 格式
        stop_loss: 止损风险指标，Dict 格式
        decision_signal_risk: AI 决策信号风险块
    """

    as_of: str
    account_id: Optional[int] = None
    cost_method: str
    currency: str
    thresholds: Dict[str, Any] = Field(default_factory=dict)
    concentration: Dict[str, Any] = Field(default_factory=dict)
    sector_concentration: Dict[str, Any] = Field(default_factory=dict)
    drawdown: Dict[str, Any] = Field(default_factory=dict)
    stop_loss: Dict[str, Any] = Field(default_factory=dict)
    decision_signal_risk: PortfolioDecisionSignalRiskBlock = Field(default_factory=PortfolioDecisionSignalRiskBlock)
