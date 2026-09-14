"""研报产物（ResearchArtifact）构建服务。

将传统（legacy）分析报告转换为符合 ResearchArtifact 合约的加性产物，
支持从原始结果、上下文快照等多来源提取信息，构建结构化的研究产物。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable


def _strings(value: Any) -> list[str]:
    """将输入值转换为字符串列表。

    支持字符串、可迭代对象（排除 dict 和 bytes）的转换，
    空字符串和空白字符串会被过滤。

    Args:
        value: 输入值，可以是字符串、列表等。

    Returns:
        过滤后的字符串列表。
    """
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, Iterable) and not isinstance(value, (dict, bytes)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def build_research_artifact(report: Dict[str, Any]) -> Dict[str, Any]:
    """从当前和遗留字段构建确定性的、fail-open 的研究产物。

    从报告的 raw_result、context_snapshot 等字段中提取关键信息，
    组装成结构化的 ResearchArtifact，便于前端展示和下游消费。

    Args:
        report: 分析报告字典，包含 raw_result、context_snapshot 等字段。

    Returns:
        符合 ResearchArtifact 合约的字典，包含 version、stock_code、
        thesis、evidence、risks、invalidation_conditions 等字段。
    """
    raw = report.get("raw_result") if isinstance(report.get("raw_result"), dict) else {}
    context = report.get("context_snapshot") if isinstance(report.get("context_snapshot"), dict) else {}
    decision = raw.get("decision_signal") if isinstance(raw.get("decision_signal"), dict) else {}
    thesis = _strings(report.get("analysis_summary"))
    advice = str(report.get("operation_advice") or "").strip()
    if advice:
        thesis.append(advice)
    risks = _strings(decision.get("risks") or raw.get("risks") or report.get("risk_points"))
    invalidators = _strings(
        decision.get("invalidation_conditions")
        or decision.get("invalidators")
        or raw.get("invalidation_conditions")
    )
    evidence = []
    for title, key in (("趋势判断", "trend_prediction"), ("新闻摘要", "news_content")):
        detail = str(report.get(key) or "").strip()
        if detail:
            evidence.append({"title": title, "detail": detail})
    sources = []
    diagnostics = context.get("diagnostics") if isinstance(context.get("diagnostics"), dict) else {}
    for item in diagnostics.get("provider_runs", []) if isinstance(diagnostics.get("provider_runs"), list) else []:
        provider = str(item.get("provider") or "").strip() if isinstance(item, dict) else ""
        if provider and provider not in sources:
            sources.append(provider)
    return {
        "version": "1.0",
        "stock_code": str(report.get("stock_code") or ""),
        "stock_name": report.get("stock_name"),
        "generated_at": report.get("created_at"),
        "thesis": thesis,
        "evidence": evidence,
        "risks": risks,
        "invalidation_conditions": invalidators,
        "data_quality": {
            "has_context_snapshot": bool(context),
            "has_raw_result": bool(raw),
            "evidence_count": len(evidence),
        },
        "sources": sources,
    }
