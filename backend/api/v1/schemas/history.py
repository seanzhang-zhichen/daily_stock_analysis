# -*- coding: utf-8 -*-
"""History and persisted analysis-report schemas.

历史接口既返回列表摘要，也返回结构化分析报告。这里的报告模型需要兼容旧历史
数据，因此部分字段保持 Optional，情绪评分等历史值也不在 schema 层做过窄约束。
"""

from typing import Optional, List, Any

from pydantic import BaseModel, ConfigDict, Field


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
        """Document OpenAPI example metadata for history item responses."""
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
    """Paginated history-list response."""
    
    total: int = Field(..., description="总记录数")
    page: int = Field(..., description="当前页码")
    limit: int = Field(..., description="每页数量")
    items: List[HistoryItem] = Field(default_factory=list, description="记录列表")
    
    class Config:
        """Document OpenAPI example metadata for paginated history responses."""
        json_schema_extra = {
            "example": {
                "total": 100,
                "page": 1,
                "limit": 20,
                "items": []
            }
        }


class DeleteHistoryRequest(BaseModel):
    """Request body for deleting one or more history records by primary key."""

    record_ids: List[int] = Field(default_factory=list, description="要删除的历史记录主键 ID 列表")


class DeleteHistoryResponse(BaseModel):
    """Deletion summary for history records."""

    deleted: int = Field(..., description="实际删除的历史记录数量")


class NewsIntelItem(BaseModel):
    """One news item attached to or derived from an analysis report."""

    title: str = Field(..., description="新闻标题")
    snippet: str = Field("", description="新闻摘要（最多200字）")
    url: str = Field(..., description="新闻链接")

    class Config:
        """Document OpenAPI example metadata for news intelligence items."""
        json_schema_extra = {
            "example": {
                "title": "公司发布业绩快报，营收同比增长 20%",
                "snippet": "公司公告显示，季度营收同比增长 20%...",
                "url": "https://example.com/news/123"
            }
        }


class NewsIntelResponse(BaseModel):
    """List response for news intelligence items."""

    total: int = Field(..., description="新闻条数")
    items: List[NewsIntelItem] = Field(default_factory=list, description="新闻列表")

    class Config:
        """Document OpenAPI example metadata for news intelligence list responses."""
        json_schema_extra = {
            "example": {
                "total": 2,
                "items": []
            }
        }


class ReportMeta(BaseModel):
    """Metadata that identifies a persisted analysis report."""

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
    """High-level conclusion section of an analysis report."""
    
    analysis_summary: Optional[str] = Field(None, description="关键结论")
    operation_advice: Optional[str] = Field(None, description="操作建议")
    trend_prediction: Optional[str] = Field(None, description="趋势预测")
    sentiment_score: Optional[int] = Field(
        None,
        description="情绪评分（历史数据可能超出 0-100 范围，读取时不做约束）",
    )
    sentiment_label: Optional[str] = Field(None, description="情绪标签")


class ReportStrategy(BaseModel):
    """Suggested trading levels extracted from the analysis report."""
    
    ideal_buy: Optional[str] = Field(None, description="理想买入价")
    secondary_buy: Optional[str] = Field(None, description="第二买入价")
    stop_loss: Optional[str] = Field(None, description="止损价")
    take_profit: Optional[str] = Field(None, description="止盈价")


class ReportDetails(BaseModel):
    """Detailed evidence and raw context attached to an analysis report."""
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
    """Structured full analysis report returned by history detail APIs."""

    meta: ReportMeta = Field(..., description="元信息")
    summary: ReportSummary = Field(..., description="概览区")
    strategy: Optional[ReportStrategy] = Field(None, description="策略点位区")
    details: Optional[ReportDetails] = Field(None, description="详情区")

    class Config:
        """Document OpenAPI example metadata for structured report responses."""
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
    """Markdown-rendered report response for download/copy workflows."""

    content: str = Field(..., description="Markdown 格式的完整报告内容")

    class Config:
        """Document OpenAPI example metadata for markdown report responses."""
        json_schema_extra = {
            "example": {
                "content": "# 📊 贵州茅台 (600519) 分析报告\n\n> 分析日期：**2024-01-01**\n\n..."
            }
        }
