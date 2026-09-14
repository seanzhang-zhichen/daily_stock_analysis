# -*- coding: utf-8 -*-
"""
===================================
报告引擎 - Pydantic Schema
===================================

定义用于校验 LLM JSON 输出的 AnalysisReportSchema。
与 src/analyzer.py 中的 SYSTEM_PROMPT 对齐。
使用 Optional 做宽松解析；业务层的完整性检查另行处理。

本模块包含 LLM 分析报告的所有 Pydantic 模型，
用于校验、解析和结构化 LLM 返回的 JSON 数据。
模型设计遵循宽松解析原则，允许字段缺失，
业务层的完整性检查在后续流程中处理。
"""

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class PositionAdvice(BaseModel):
    """针对空仓与持仓两种情形的仓位建议。

    提供针对不同持仓状态的具体操作建议，
    帮助用户根据自身情况做出决策。

    Attributes:
        no_position: 空仓时的建议
        has_position: 持仓时的建议
    """

    no_position: Optional[str] = None
    """空仓时的操作建议，如'观望'、'轻仓介入'。"""

    has_position: Optional[str] = None
    """持仓时的操作建议，如'持有'、'减仓'、'加仓'。"""


class CoreConclusion(BaseModel):
    """核心结论块。

    包含分析的核心结论，是一句话总结和关键信号。

    Attributes:
        one_sentence: 一句话总结
        signal_type: 信号类型
        time_sensitivity: 时间敏感度
        position_advice: 仓位建议
    """

    one_sentence: Optional[str] = None
    """一句话总结核心结论，简洁明了。"""

    signal_type: Optional[str] = None
    """信号类型，如'买入'、'卖出'、'观望'。"""

    time_sensitivity: Optional[str] = None
    """时间敏感度，如'短期'、'中期'、'长期'。"""

    position_advice: Optional[PositionAdvice] = None
    """针对不同持仓状态的仓位建议。"""


class TrendStatus(BaseModel):
    """趋势状态。

    描述市场或个股的趋势状态，包括均线排列、趋势方向和评分。

    Attributes:
        ma_alignment: 均线排列状态
        is_bullish: 是否处于多头趋势
        trend_score: 趋势评分
    """

    ma_alignment: Optional[str] = None
    """均线排列状态，如'多头排列'、'空头排列'、'纠缠'。"""

    is_bullish: Optional[bool] = None
    """是否处于多头趋势，True 表示多头，False 表示空头。"""

    trend_score: Optional[Union[int, float, str]] = None
    """趋势评分，可以是数值或描述性字符串。"""


class PricePosition(BaseModel):
    """价格位置（可能包含 N/A 字符串）。

    描述当前价格相对于各条均线的位置，
    以及支撑位和阻力位。

    Attributes:
        current_price: 当前价格
        ma5: 5日均线
        ma10: 10日均线
        ma20: 20日均线
        bias_ma5: 偏离5日均线程度
        bias_status: 偏离状态
        support_level: 支撑位
        resistance_level: 阻力位
    """

    current_price: Optional[Union[int, float, str]] = None
    """当前价格，可能为数值或 'N/A' 字符串。"""

    ma5: Optional[Union[int, float, str]] = None
    """5日均线价格，可能为数值或 'N/A' 字符串。"""

    ma10: Optional[Union[int, float, str]] = None
    """10日均线价格，可能为数值或 'N/A' 字符串。"""

    ma20: Optional[Union[int, float, str]] = None
    """20日均线价格，可能为数值或 'N/A' 字符串。"""

    bias_ma5: Optional[Union[int, float, str]] = None
    """偏离5日均线的程度，可能为数值或 'N/A' 字符串。"""

    bias_status: Optional[str] = None
    """偏离状态描述，如'超买'、'超卖'、'正常'。"""

    support_level: Optional[Union[int, float, str]] = None
    """支撑位价格，可能为数值或 'N/A' 字符串。"""

    resistance_level: Optional[Union[int, float, str]] = None
    """阻力位价格，可能为数值或 'N/A' 字符串。"""


class VolumeAnalysis(BaseModel):
    """成交量分析。

    描述成交量相关指标，包括量比、成交量状态、
    换手率和成交量含义。

    Attributes:
        volume_ratio: 量比
        volume_status: 成交量状态
        turnover_rate: 换手率
        volume_meaning: 成交量含义
    """

    volume_ratio: Optional[Union[int, float, str]] = None
    """量比，当前成交量与过去一段时间平均成交量的比值。"""

    volume_status: Optional[str] = None
    """成交量状态描述，如'放量'、'缩量'、'平量'。"""

    turnover_rate: Optional[Union[int, float, str]] = None
    """换手率，可能为数值或 'N/A' 字符串。"""

    volume_meaning: Optional[str] = None
    """成交量的含义解读，如'资金活跃'、'资金流出'。"""


class ChipStructure(BaseModel):
    """筹码结构。

    描述筹码分布情况，包括获利比例、平均成本和集中度。

    Attributes:
        profit_ratio: 获利比例
        avg_cost: 平均成本
        concentration: 集中度
        chip_health: 筹码健康度
    """

    profit_ratio: Optional[Union[int, float, str]] = None
    """获利比例，表示当前价格下获利筹码的占比。"""

    avg_cost: Optional[Union[int, float, str]] = None
    """平均成本，所有筹码的平均买入成本。"""

    concentration: Optional[Union[int, float, str]] = None
    """筹码集中度，表示筹码分布的集中程度。"""

    chip_health: Optional[str] = None
    """筹码健康度描述，如'健康'、'分散'、'集中'。"""


class DataPerspective(BaseModel):
    """数据视角块。

    聚合趋势、价格、成交量和筹码四个维度的分析数据，
    提供结构化的技术面分析结果。

    Attributes:
        trend_status: 趋势状态
        price_position: 价格位置
        volume_analysis: 成交量分析
        chip_structure: 筹码结构
    """

    trend_status: Optional[TrendStatus] = None
    """趋势状态，包括均线排列、多头判断和趋势评分。"""

    price_position: Optional[PricePosition] = None
    """价格位置，包括当前价格、各条均线、支撑位和阻力位。"""

    volume_analysis: Optional[VolumeAnalysis] = None
    """成交量分析，包括量比、换手率等指标。"""

    chip_structure: Optional[ChipStructure] = None
    """筹码结构，包括获利比例、平均成本等。"""


class Intelligence(BaseModel):
    """情报块。

    汇总与标的相关的最新资讯、风险预警、
    积极催化剂和情绪总结。

    Attributes:
        latest_news: 最新新闻摘要
        risk_alerts: 风险预警列表
        positive_catalysts: 积极催化剂列表
        earnings_outlook: 盈利展望
        sentiment_summary: 情绪总结
    """

    latest_news: Optional[str] = None
    """最新新闻摘要，概括近期与标的相关的重要新闻。"""

    risk_alerts: Optional[List[str]] = None
    """风险预警列表，列出需要关注的风险因素。"""

    positive_catalysts: Optional[List[str]] = None
    """积极催化剂列表，列出可能推动价格上涨的因素。"""

    earnings_outlook: Optional[str] = None
    """盈利展望，对未来盈利能力的预测和评估。"""

    sentiment_summary: Optional[str] = None
    """市场情绪总结，概括当前市场对标的的整体情绪。"""


class SniperPoints(BaseModel):
    """狙击点位（ideal_buy、stop_loss 等）。

    提供关键价格点位建议，包括理想买入点、
    次要买入点、止损位和止盈位。

    Attributes:
        ideal_buy: 理想买入点位
        secondary_buy: 次要买入点位
        stop_loss: 止损点位
        take_profit: 止盈点位
    """

    ideal_buy: Optional[Union[str, int, float]] = None
    """理想买入点位，可以是价格或描述性字符串。"""

    secondary_buy: Optional[Union[str, int, float]] = None
    """次要买入点位，作为理想点位的补充。"""

    stop_loss: Optional[Union[str, int, float]] = None
    """止损点位，用于控制下行风险。"""

    take_profit: Optional[Union[str, int, float]] = None
    """止盈点位，用于锁定利润。"""


class PositionStrategy(BaseModel):
    """仓位策略。

    提供具体的仓位建议和风险控制措施。

    Attributes:
        suggested_position: 建议仓位
        entry_plan: 入场计划
        risk_control: 风险控制措施
    """

    suggested_position: Optional[str] = None
    """建议仓位，如'满仓'、'半仓'、'空仓'。"""

    entry_plan: Optional[str] = None
    """入场计划，描述何时、以何种方式建仓。"""

    risk_control: Optional[str] = None
    """风险控制措施，描述如何管理下行风险。"""


class BattlePlan(BaseModel):
    """作战计划块。

    整合狙击点位、仓位策略和行动清单，
    形成完整的交易执行计划。

    Attributes:
        sniper_points: 狙击点位
        position_strategy: 仓位策略
        action_checklist: 行动清单
    """

    sniper_points: Optional[SniperPoints] = None
    """关键价格点位建议，包括买入、止损、止盈点。"""

    position_strategy: Optional[PositionStrategy] = None
    """仓位策略，包括建议仓位和风险控制。"""

    action_checklist: Optional[List[str]] = None
    """行动清单，列出执行交易前需要完成的步骤。"""


class Dashboard(BaseModel):
    """仪表盘块。

    聚合核心结论、数据视角、情报和作战计划，
    形成完整的分析仪表盘视图。

    Attributes:
        core_conclusion: 核心结论
        data_perspective: 数据视角
        intelligence: 情报
        battle_plan: 作战计划
    """

    core_conclusion: Optional[CoreConclusion] = None
    """核心结论，包括一句话总结、信号类型和仓位建议。"""

    data_perspective: Optional[DataPerspective] = None
    """数据视角，包括趋势、价格、成交量和筹码分析。"""

    intelligence: Optional[Intelligence] = None
    """情报，包括新闻、风险预警和情绪总结。"""

    battle_plan: Optional[BattlePlan] = None
    """作战计划，包括狙击点位、仓位策略和行动清单。"""


class AnalysisReportSchema(BaseModel):
    """
    LLM 报告 JSON 的顶层 schema。
    与 SYSTEM_PROMPT 的输出格式对齐。

    这是 LLM 分析报告的顶层模型，包含所有可能的字段。
    使用 ConfigDict(extra="allow") 允许 LLM 返回额外字段，
    以兼容不同版本的提示词。

    Attributes:
        stock_name: 股票名称
        sentiment_score: 情绪评分
        trend_prediction: 趋势预测
        operation_advice: 操作建议
        decision_type: 决策类型
        confidence_level: 信心水平
        dashboard: 仪表盘
        analysis_summary: 分析摘要
        key_points: 关键要点
        risk_warning: 风险提示
        buy_reason: 买入理由
        trend_analysis: 趋势分析
        short_term_outlook: 短期展望
        medium_term_outlook: 中期展望
        technical_analysis: 技术分析
        ma_analysis: 均线分析
        volume_analysis: 成交量分析
        pattern_analysis: 形态分析
        fundamental_analysis: 基本面分析
        sector_position: 行业地位
        company_highlights: 公司亮点
        stock_profile: 股票概况
        news_summary: 新闻摘要
        market_sentiment: 市场情绪
        hot_topics: 热点话题
        search_performed: 是否执行搜索
        data_sources: 数据来源
    """

    model_config = ConfigDict(extra="allow")  # 允许 LLM 返回额外字段

    stock_name: Optional[str] = None
    """股票名称，如'平安银行'。"""

    sentiment_score: Optional[int] = Field(None, ge=0, le=100)
    """情绪评分，范围 0-100，越高表示情绪越乐观。"""

    trend_prediction: Optional[str] = None
    """趋势预测，如'上涨'、'下跌'、'震荡'。"""

    operation_advice: Optional[str] = None
    """操作建议，如'买入'、'卖出'、'持有'。"""

    decision_type: Optional[str] = None
    """决策类型，如'激进'、'保守'、'中性'。"""

    confidence_level: Optional[str] = None
    """信心水平，如'高'、'中'、'低'。"""

    dashboard: Optional[Dashboard] = None
    """仪表盘，包含核心结论、数据视角、情报和作战计划。"""

    analysis_summary: Optional[str] = None
    """分析摘要，对整个分析的概括性总结。"""

    key_points: Optional[str] = None
    """关键要点，列出分析中的核心发现。"""

    risk_warning: Optional[str] = None
    """风险提示，列出需要注意的风险因素。"""

    buy_reason: Optional[str] = None
    """买入理由，解释为什么建议买入。"""

    trend_analysis: Optional[str] = None
    """趋势分析，对价格趋势的详细分析。"""

    short_term_outlook: Optional[str] = None
    """短期展望，对未来几天或几周的走势预测。"""

    medium_term_outlook: Optional[str] = None
    """中期展望，对未来几个月的走势预测。"""

    technical_analysis: Optional[str] = None
    """技术分析，基于技术指标的分析结论。"""

    ma_analysis: Optional[str] = None
    """均线分析，基于移动平均线系统的分析。"""

    volume_analysis: Optional[str] = None
    """成交量分析，基于成交量变化的分析。"""

    pattern_analysis: Optional[str] = None
    """形态分析，基于价格形态的分析。"""

    fundamental_analysis: Optional[str] = None
    """基本面分析，基于公司基本面的分析。"""

    sector_position: Optional[str] = None
    """行业地位，描述公司在行业中的竞争地位。"""

    company_highlights: Optional[str] = None
    """公司亮点，描述公司的核心竞争力和优势。"""

    stock_profile: Optional[Dict[str, Any]] = None
    """股票概况，包含股票的基本信息和元数据。"""

    news_summary: Optional[str] = None
    """新闻摘要，概括与股票相关的重要新闻。"""

    market_sentiment: Optional[str] = None
    """市场情绪，描述当前市场的整体情绪状态。"""

    hot_topics: Optional[str] = None
    """热点话题，列出当前市场的热点讨论话题。"""

    search_performed: Optional[bool] = None
    """是否执行了搜索，True 表示使用了实时搜索数据。"""

    data_sources: Optional[str] = None
    """数据来源，描述分析所依赖的数据来源。"""
