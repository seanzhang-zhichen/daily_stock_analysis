# -*- coding: utf-8 -*-
"""带版本号的市场结构（market-structure）上下文，供报告、Agent 与 API 共享。

本模块定义了市场结构分析的 Pydantic 模型，包括：
- 市场主题上下文（MarketThemeContext）：大盘层级的主题、板块分析
- 个股市场位置（StockMarketPosition）：个股在主题和板块中的定位
- 市场结构总上下文（MarketStructureContext）：聚合上述两个子视图

这些模型用于在报告生成、Agent 推理和 API 响应之间共享结构化的市场分析数据。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# Schema 版本常量，用于向后兼容和数据迁移
MARKET_THEME_SCHEMA_VERSION = "market-theme-v1"
"""市场主题上下文的 Schema 版本号。"""

STOCK_MARKET_POSITION_SCHEMA_VERSION = "stock-market-position-v1"
"""个股市场位置上下文的 Schema 版本号。"""

MARKET_STRUCTURE_SCHEMA_VERSION = "market-structure-v1"
"""市场结构总上下文的 Schema 版本号。"""

# 类型别名定义
MarketStructureStatus = Literal["ok", "partial", "unknown", "not_supported"]
"""市场结构数据状态：正常、部分缺失、未知、不支持。"""

ThemeRankSource = Literal["industry", "concept", "mixed", "unknown"]
"""主题排名来源：行业、概念、混合、未知。"""

ThemePhase = Literal["warming", "accelerating", "cooling", "unknown"]
"""主题阶段：预热、加速、冷却、未知。"""

StockRole = Literal["leader", "follower", "edge", "unknown"]
"""个股角色：领涨龙头、跟随者、边缘、未知。"""


class MarketStructureSource(BaseModel):
    """单个数据源快照信息：仅作为诊断元数据，不参与运行时 provider 路由。

    记录数据来源的标识、状态和提示信息，用于问题排查和数据溯源。

    Attributes:
        provider: 数据源标识，如 "akshare"、"yfinance"
        dataset: 数据集标识，如 "quote.realtime"
        status: 来源可用性快照状态
        message: 来源提示信息，仅用于展示和排障
    """

    provider: str = Field(..., description="数据源标识，仅作快照元数据，不参与运行时 provider/model 路由")
    """数据提供商的标识字符串，如 'akshare'、'yfinance'。"""

    dataset: str = Field(..., description="数据集标识，仅用于历史可追溯快照")
    """数据集的标识字符串，如 'quote.realtime'、'kline.daily'。"""

    status: str = Field("ok", description="来源可用性快照")
    """来源的可用性状态，如 'ok'、'error'、'timeout'。"""

    message: Optional[str] = Field(None, description="来源提示，仅展示/排障用")
    """可选的提示信息，用于展示或问题排查。"""


class MarketStructureDataQuality(BaseModel):
    """整体数据质量快照：缺失字段、来源、错误信息聚合。

    汇总数据采集中发现的所有质量问题，包括缺失字段、
    数据源状态和错误信息，用于监控和告警。

    Attributes:
        status: 数据质量快照状态
        missing_fields: 缺失的字段列表
        sources: 数据来源快照列表
        errors: 错误信息列表
    """

    status: MarketStructureStatus = Field("unknown", description="数据质量快照状态（展示语义）")
    """数据质量总体状态：ok（正常）、partial（部分缺失）、unknown（未知）、not_supported（不支持）。"""

    missing_fields: List[str] = Field(default_factory=list)
    """缺失的字段名列表，用于标识数据不完整的地方。"""

    sources: List[MarketStructureSource] = Field(default_factory=list)
    """数据来源快照列表，记录各数据源的可用性状态。"""

    errors: List[str] = Field(default_factory=list)
    """错误信息列表，记录数据采集过程中遇到的错误。"""


class RankedThemeItem(BaseModel):
    """主题榜单中的单个条目：含涨幅/排名/来源等基础维度。

    表示某个主题或板块在榜单中的基本信息，
    可用于行业排名、概念排名等场景。

    Attributes:
        name: 主题或板块名称
        change_pct: 涨跌幅百分比
        rank: 排名
        source: 排名来源类型
        code: 主题或板块代码
        updated_at: 数据更新时间
    """

    name: str
    """主题或板块的名称，如'半导体'、'新能源汽车'。"""

    change_pct: Optional[float] = None
    """涨跌幅百分比，如 5.23 表示上涨 5.23%。"""

    rank: Optional[int] = None
    """在榜单中的排名，数字越小排名越靠前。"""

    source: ThemeRankSource = "unknown"
    """排名来源：industry（行业）、concept（概念）、mixed（混合）、unknown（未知）。"""

    code: Optional[str] = None
    """主题或板块的代码标识。"""

    updated_at: Optional[str] = None
    """数据更新时间戳，通常为 ISO 格式字符串。"""


class MarketThemeItem(RankedThemeItem):
    """市场主题条目：在榜单基础信息上叠加所处阶段（phase）与强度评分。

    继承自 RankedThemeItem，增加了主题阶段和强度评分，
    用于更细致地描述市场主题的运行状态。

    Attributes:
        phase: 主题当前所处阶段
        strength_score: 主题强度评分
        reason: 阶段判断的原因说明
    """

    phase: ThemePhase = "unknown"
    """主题当前所处阶段：warming（预热）、accelerating（加速）、cooling（冷却）、unknown（未知）。"""

    strength_score: Optional[int] = None
    """主题强度评分，数值越高表示该主题越强。"""

    reason: Optional[str] = None
    """阶段判断的原因说明，解释为什么给出当前阶段。"""


class ThemeBreadth(BaseModel):
    """主题广度统计：活跃主题数、领涨行业/概念数、滞后主题数。

    量化市场主题的活跃程度，帮助判断市场整体热度。

    Attributes:
        active_count: 活跃主题数量
        leading_industry_count: 领涨行业数量
        leading_concept_count: 领涨概念数量
        lagging_count: 滞后主题数量
    """

    active_count: int = 0
    """当前活跃的主题数量，反映市场热点数量。"""

    leading_industry_count: int = 0
    """领涨的行业板块数量，反映主流资金方向。"""

    leading_concept_count: int = 0
    """领涨的概念板块数量，反映题材炒作热度。"""

    lagging_count: int = 0
    """表现滞后的主题数量，与市场整体形成对比。"""


class MarketThemeContext(BaseModel):
    """市场主题上下文。

    汇总大盘层级的活跃主题、领涨/滞后板块及数据质量信息，供报告与 Agent 共享。
    该模型聚合了市场主题的完整视图，包括活跃主题列表、领涨行业、领涨概念、
    滞后主题、热点成分股、领涨股、主题广度统计和数据质量信息。

    Attributes:
        schema_version: Schema 版本号
        status: 数据状态
        market: 市场代码
        trade_date: 交易日
        active_themes: 活跃主题列表
        leading_industries: 领涨行业列表
        leading_concepts: 领涨概念列表
        lagging_themes: 滞后主题列表
        hotspot_constituents: 热点成分股
        leader_stocks: 领涨股
        theme_breadth: 主题广度统计
        data_quality: 数据质量信息
    """

    schema_version: str = MARKET_THEME_SCHEMA_VERSION
    """Schema 版本号，用于向后兼容。"""

    status: MarketStructureStatus = "unknown"
    """数据状态：ok（正常）、partial（部分缺失）、unknown（未知）、not_supported（不支持）。"""

    market: str = "cn"
    """市场代码，如 'cn'（中国）、'hk'（香港）、'us'（美国）。"""

    trade_date: Optional[str] = None
    """交易日，格式通常为 YYYY-MM-DD。"""

    active_themes: List[MarketThemeItem] = Field(default_factory=list)
    """当前活跃的主题列表，包含主题名称、阶段、强度等信息。"""

    leading_industries: List[RankedThemeItem] = Field(default_factory=list)
    """领涨的行业板块列表。"""

    leading_concepts: List[RankedThemeItem] = Field(default_factory=list)
    """领涨的概念板块列表。"""

    lagging_themes: List[RankedThemeItem] = Field(default_factory=list)
    """表现滞后的主题列表，与市场热点形成对比。"""

    hotspot_constituents: List[Any] = Field(default_factory=list)
    """热点主题的成分股列表，用于深入分析热点。"""

    leader_stocks: List[Any] = Field(default_factory=list)
    """各主题的领涨股列表，用于发现龙头。"""

    theme_breadth: ThemeBreadth = Field(default_factory=ThemeBreadth)
    """主题广度统计，量化市场主题的活跃程度。"""

    data_quality: MarketStructureDataQuality = Field(default_factory=MarketStructureDataQuality)
    """数据质量信息，记录数据采集过程中的质量问题。"""


class StockBoardPosition(BaseModel):
    """个股关联的板块位置：板块名称、类型、代码、排名、涨幅及来源。

    描述个股在某个板块中的位置信息，包括板块名称、类型、代码、
    排名、涨跌幅和数据来源。

    Attributes:
        name: 板块名称
        type: 板块类型
        code: 板块代码
        rank: 在板块中的排名
        change_pct: 板块涨跌幅
        source: 排名来源
    """

    name: str
    """板块名称，如'半导体'、'新能源汽车'。"""

    type: Optional[str] = None
    """板块类型，如'industry'（行业）、'concept'（概念）。"""

    code: Optional[str] = None
    """板块的代码标识。"""

    rank: Optional[int] = None
    """个股在板块中的排名，数字越小排名越靠前。"""

    change_pct: Optional[float] = None
    """板块的涨跌幅百分比。"""

    source: ThemeRankSource = "unknown"
    """排名来源：industry（行业）、concept（概念）、mixed（混合）、unknown（未知）。"""


class PrimaryTheme(BaseModel):
    """个股所属的主要主题：主题名、来源、阶段、排名、涨幅。

    描述个股所属的主要主题信息，用于分析个股与主题的关联。

    Attributes:
        name: 主题名称
        source: 主题来源
        phase: 主题阶段
        rank: 主题排名
        change_pct: 主题涨跌幅
    """

    name: str
    """主题名称，如'半导体'、'新能源汽车'。"""

    source: ThemeRankSource = "unknown"
    """主题来源：industry（行业）、concept（概念）、mixed（混合）、unknown（未知）。"""

    phase: ThemePhase = "unknown"
    """主题阶段：warming（预热）、accelerating（加速）、cooling（冷却）、unknown（未知）。"""

    rank: Optional[int] = None
    """主题在榜单中的排名。"""

    change_pct: Optional[float] = None
    """主题的涨跌幅百分比。"""


class MarketStructureRiskTag(BaseModel):
    """市场结构层面的风险标签：标的代码与人类可读提示。

    用于标识和描述市场结构中的风险点，
    帮助用户快速识别潜在风险。

    Attributes:
        code: 风险标签代码
        message: 风险描述信息
    """

    code: str
    """风险标签的代码标识，用于程序识别和处理。"""

    message: str
    """风险的人类可读描述，用于展示给用户。"""


class StockMarketPosition(BaseModel):
    """个股在市场结构中的位置：所属主题、关联板块、角色/阶段、风险标签。

    描述个股在整个市场结构中的定位，包括所属主题、关联板块、
    个股角色、主题阶段和风险标签等信息。

    Attributes:
        schema_version: Schema 版本号
        status: 数据状态
        stock_code: 股票代码
        stock_name: 股票名称
        market: 市场代码
        primary_theme: 主要主题
        related_boards: 关联板块列表
        stock_role: 个股角色
        theme_phase: 主题阶段
        risk_tags: 风险标签列表
        missing_fields: 缺失字段列表
    """

    schema_version: str = STOCK_MARKET_POSITION_SCHEMA_VERSION
    """Schema 版本号，用于向后兼容。"""

    status: MarketStructureStatus = "unknown"
    """数据状态：ok（正常）、partial（部分缺失）、unknown（未知）、not_supported（不支持）。"""

    stock_code: str
    """股票代码，如 '000001.SZ'。"""

    stock_name: Optional[str] = None
    """股票名称，如 '平安银行'。"""

    market: str = "cn"
    """市场代码，如 'cn'（中国）、'hk'（香港）、'us'（美国）。"""

    primary_theme: Optional[PrimaryTheme] = None
    """个股所属的主要主题信息。"""

    related_boards: List[StockBoardPosition] = Field(default_factory=list)
    """个股关联的板块位置列表。"""

    stock_role: StockRole = "unknown"
    """个股角色：leader（领涨龙头）、follower（跟随者）、edge（边缘）、unknown（未知）。"""

    theme_phase: ThemePhase = "unknown"
    """主题阶段：warming（预热）、accelerating（加速）、cooling（冷却）、unknown（未知）。"""

    risk_tags: List[MarketStructureRiskTag] = Field(default_factory=list)
    """与该个股相关的风险标签列表。"""

    missing_fields: List[str] = Field(default_factory=list)
    """缺失的字段名列表，用于标识数据不完整的地方。"""


class MarketStructureContext(BaseModel):
    """市场结构总上下文：聚合大盘主题与个股市场位置两个子视图。

    这是市场结构分析的顶层模型，包含：
    - 市场主题上下文（MarketThemeContext）：大盘层级的主题分析
    - 个股市场位置（StockMarketPosition）：个股在主题和板块中的定位

    Attributes:
        schema_version: Schema 版本号
        status: 数据状态
        market: 市场代码
        trade_date: 交易日
        market_theme_context: 市场主题上下文
        stock_market_position: 个股市场位置
    """

    schema_version: str = MARKET_STRUCTURE_SCHEMA_VERSION
    """Schema 版本号，用于向后兼容。"""

    status: MarketStructureStatus = "unknown"
    """数据状态：ok（正常）、partial（部分缺失）、unknown（未知）、not_supported（不支持）。"""

    market: str = "cn"
    """市场代码，如 'cn'（中国）、'hk'（香港）、'us'（美国）。"""

    trade_date: Optional[str] = None
    """交易日，格式通常为 YYYY-MM-DD。"""

    market_theme_context: MarketThemeContext
    """市场主题上下文，包含活跃主题、领涨板块等信息。"""

    stock_market_position: StockMarketPosition
    """个股市场位置，包含个股所属主题、关联板块、角色等信息。"""


def dump_market_structure_model(model: BaseModel) -> Dict[str, Any]:
    """返回使用稳定 snake_case 键、剔除 None 字段的字典，便于跨层传输与持久化。

    使用 Pydantic 的 model_dump 方法，设置 exclude_none=True 以剔除值为 None 的字段，
    减少空字段噪声，保证下游消费稳定。

    Args:
        model: 待序列化的 Pydantic BaseModel 实例。

    Returns:
        包含模型数据的字典，键为 snake_case 格式，已剔除 None 值。
    """
    # exclude_none=True 减少空字段噪声，保证下游消费稳定
    return model.model_dump(exclude_none=True)
