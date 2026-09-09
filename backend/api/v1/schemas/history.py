# -*- coding: utf-8 -*-
"""历史与分析报告相关 Pydantic Schema。

本模块定义历史接口所需的请求/响应模型，既包含列表摘要（``HistoryItem`` 及其分页
包装），也包含完整的结构化分析报告（``AnalysisReport`` 及其嵌套 ``meta`` /
``summary`` / ``strategy`` / ``details``）。

设计要点：
- 报告模型需要兼容旧的历史数据，因此 ``sentiment_score`` 等历史值在 schema 层不
  做范围约束（可能超出 0-100）。
- 部分字段（``report_type`` / ``stock_name`` 等）保持 ``Optional``，以容忍不同
  时期产出的报告格式差异。
"""

from typing import Optional, List, Any, Dict

from pydantic import BaseModel, ConfigDict, Field


class RunDiagnosticComponent(BaseModel):
    """单次分析运行中某一诊断组件的结果。"""

    key: str
    label: str
    status: str
    message: str
    details: Optional[Dict[str, Any]] = None


class RunDiagnosticSummaryResponse(BaseModel):
    """分析运行诊断结果的聚合响应。

    用于排查单次分析失败原因，汇总各组件的状态、原因、可读消息以及一键复制的
    完整诊断文本。
    """

    trace_id: Optional[str] = None
    task_id: Optional[str] = None
    query_id: Optional[str] = None
    stock_code: Optional[str] = None
    trigger_source: Optional[str] = None
    status: str
    status_label: str
    reason: str
    components: Dict[str, RunDiagnosticComponent] = Field(default_factory=dict)
    copy_text: str


class HistoryItem(BaseModel):
    """历史记录摘要（列表展示用）"""

    id: Optional[int] = Field(None, description="分析历史记录主键 ID")
    query_id: str = Field(..., description="分析记录关联 query_id（批量分析时重复）")
    stock_code: str = Field(..., description="股票代码")
    stock_name: Optional[str] = Field(None, description="股票名称")
    report_type: Optional[str] = Field(None, description="报告类型")
    sentiment_score: Optional[int] = Field(
        None,
        description="情绪评分（历史数据可能超出 0-100 范围，读取时不做约束）",
    )
    operation_advice: Optional[str] = Field(None, description="操作建议")
    created_at: Optional[str] = Field(None, description="创建时间")

    class Config:
        """OpenAPI 中为历史记录条目提供示例元数据。"""
        json_schema_extra = {
            "example": {
                "id": 1234,
                "query_id": "abc123",
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "report_type": "detailed",
                "sentiment_score": 75,
                "operation_advice": "持有",
                "created_at": "2024-01-01T12:00:00"
            }
        }


class HistoryListResponse(BaseModel):
    """历史记录列表的分页响应。"""

    total: int = Field(..., description="总记录数")
    page: int = Field(..., description="当前页码")
    limit: int = Field(..., description="每页数量")
    items: List[HistoryItem] = Field(default_factory=list, description="记录列表")

    class Config:
        """OpenAPI 中为分页历史响应提供示例元数据。"""
        json_schema_extra = {
            "example": {
                "total": 100,
                "page": 1,
                "limit": 20,
                "items": []
            }
        }


class DeleteHistoryRequest(BaseModel):
    """根据主键批量删除历史记录的请求体。"""

    record_ids: List[int] = Field(default_factory=list, description="要删除的历史记录主键 ID 列表")


class DeleteHistoryResponse(BaseModel):
    """历史记录删除操作的概要响应。"""

    deleted: int = Field(..., description="实际删除的历史记录数量")


class HistoryTrendPoint(BaseModel):
    """历史趋势图上的单个数据点。

    用于前端绘制单只股票的历史情绪与建议走势曲线。
    """

    id: int
    created_at: Optional[str] = None
    sentiment_score: Optional[int] = None
    operation_advice: Optional[str] = None
    trend_prediction: Optional[str] = None
    analysis_summary: Optional[str] = None


class HistoryTrendResponse(BaseModel):
    """单只股票历史趋势的列表响应。"""

    stock_code: str
    stock_name: Optional[str] = None
    items: List[HistoryTrendPoint] = Field(default_factory=list)


class NewsIntelItem(BaseModel):
    """关联到分析报告（或者从报告派生）的单条新闻条目。"""

    title: str = Field(..., description="新闻标题")
    snippet: str = Field("", description="新闻摘要（最多200字）")
    url: str = Field(..., description="新闻链接")

    class Config:
        """OpenAPI 中为新闻情报条目提供示例元数据。"""
        json_schema_extra = {
            "example": {
                "title": "公司发布业绩快报，营收同比增长 20%",
                "snippet": "公司公告显示，季度营收同比增长 20%...",
                "url": "https://example.com/news/123"
            }
        }


class NewsIntelResponse(BaseModel):
    """新闻情报条目列表响应。"""

    total: int = Field(..., description="新闻条数")
    items: List[NewsIntelItem] = Field(default_factory=list, description="新闻列表")

    class Config:
        """OpenAPI 中为新闻情报列表响应提供示例元数据。"""
        json_schema_extra = {
            "example": {
                "total": 2,
                "items": []
            }
        }


class ReportMeta(BaseModel):
    """用于标识一条已持久化分析报告的元信息。"""

    model_config = ConfigDict(protected_namespaces=("model_validate", "model_dump"))

    id: Optional[int] = Field(None, description="分析历史记录主键 ID（仅历史报告有此字段）")
    query_id: str = Field(..., description="分析记录关联 query_id（批量分析时重复）")
    stock_code: str = Field(..., description="股票代码")
    stock_name: Optional[str] = Field(None, description="股票名称")
    report_type: Optional[str] = Field(None, description="报告类型")
    report_language: Optional[str] = Field(None, description="报告输出语言（zh/en）")
    created_at: Optional[str] = Field(None, description="创建时间")
    current_price: Optional[float] = Field(None, description="分析时股价")
    change_pct: Optional[float] = Field(None, description="分析时涨跌幅(%)")
    model_used: Optional[str] = Field(None, description="分析使用的 LLM 模型")


class ReportSummary(BaseModel):
    """分析报告的高层结论部分。"""

    analysis_summary: Optional[str] = Field(None, description="关键结论")
    operation_advice: Optional[str] = Field(None, description="操作建议")
    trend_prediction: Optional[str] = Field(None, description="趋势预测")
    sentiment_score: Optional[int] = Field(
        None,
        description="情绪评分（历史数据可能超出 0-100 范围，读取时不做约束）",
    )
    sentiment_label: Optional[str] = Field(None, description="情绪标签")


class ReportStrategy(BaseModel):
    """从分析报告中抽取的交易点位建议。"""

    ideal_buy: Optional[str] = Field(None, description="理想买入价")
    secondary_buy: Optional[str] = Field(None, description="第二买入价")
    stop_loss: Optional[str] = Field(None, description="止损价")
    take_profit: Optional[str] = Field(None, description="止盈价")


class ReportDetails(BaseModel):
    """分析报告附带的证据与原始上下文。"""
    empty_news_disclosure: Optional[str] = Field(None, description="新闻证据边界提示")

    news_content: Optional[str] = Field(None, description="新闻摘要")
    raw_result: Optional[Any] = Field(None, description="原始分析结果（JSON）")
    context_snapshot: Optional[Any] = Field(None, description="分析时上下文快照（JSON）")
    financial_report: Optional[Any] = Field(None, description="结构化财报摘要（来自 fundamental_context）")
    dividend_metrics: Optional[Any] = Field(None, description="结构化分红指标（含 TTM 口径）")
    stock_profile: Optional[Any] = Field(None, description="Deep Research 生成的股票基本情况")
    belong_boards: Optional[Any] = Field(None, description="关联板块列表")
    sector_rankings: Optional[Any] = Field(None, description="板块涨跌榜（结构 {top, bottom}）")
    market_structure_context: Optional[Any] = Field(None, description="A股市场结构与题材上下文")
    price_history: List[Any] = Field(default_factory=list, description="历史股价（日线 OHLCV 与均线）")


class AnalysisReport(BaseModel):
    """历史详情接口返回的完整结构化分析报告。"""

    meta: ReportMeta = Field(..., description="元信息")
    summary: ReportSummary = Field(..., description="概览区")
    strategy: Optional[ReportStrategy] = Field(None, description="策略点位区")
    details: Optional[ReportDetails] = Field(None, description="详情区")

    class Config:
        """OpenAPI 中为结构化报告响应提供示例元数据。"""
        json_schema_extra = {
            "example": {
                "meta": {
                    "query_id": "abc123",
                    "stock_code": "600519",
                    "stock_name": "贵州茅台",
                    "report_type": "detailed",
                    "report_language": "zh",
                    "created_at": "2024-01-01T12:00:00"
                },
                "summary": {
                    "analysis_summary": "技术面向好，建议持有",
                    "operation_advice": "持有",
                    "trend_prediction": "看多",
                    "sentiment_score": 75,
                    "sentiment_label": "乐观"
                },
                "strategy": {
                    "ideal_buy": "1800.00",
                    "secondary_buy": "1750.00",
                    "stop_loss": "1700.00",
                    "take_profit": "2000.00"
                },
                "details": None
            }
        }


class MarkdownReportResponse(BaseModel):
    """用于下载/复制场景的 Markdown 渲染版报告响应。"""

    content: str = Field(..., description="Markdown 格式的完整报告内容")

    class Config:
        """OpenAPI 中为 Markdown 报告响应提供示例元数据。"""
        json_schema_extra = {
            "example": {
                "content": "# 📊 贵州茅台 (600519) 分析报告\n\n> 分析日期：**2024-01-01**\n\n..."
            }
        }
