"""基于持久化诊断数据构建一份精简、安全的 A 股分析 run-flow 快照。

把单条历史报告记录里的 ``context_snapshot`` / ``raw_result`` 解构成"分析流图"
（lanes + nodes + edges + events）结构，供前端 run-flow 页面回放展示。

设计要点：
- 不输出任何原始 prompt、密钥、URL 或响应体，只暴露脱敏后的元信息
- 兼容老格式报告：字段不全时仍返回一张"骨架图"，而不是抛错
- 复用 :func:`sanitize_diagnostic_text` 做日志级别的字符串清理
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.services.run_diagnostics import sanitize_diagnostic_text
from src.utils.data_processing import parse_json_field


# run-flow 页面顶层"泳道"，顺序即渲染顺序；新增泳道时同时调整前端布局
_LANES = [
    {"id": "entry", "label": "入口", "order": 1},
    {"id": "data", "label": "数据来源", "order": 2},
    {"id": "analysis", "label": "分析引擎", "order": 3},
    {"id": "artifact", "label": "报告产物", "order": 4},
]


def _mapping(value: Any) -> Dict[str, Any]:
    """把可能为 JSON 字符串或 dict 的字段统一解析为 dict；失败时回落到空 dict。"""
    parsed = parse_json_field(value) if isinstance(value, str) else value
    return dict(parsed) if isinstance(parsed, dict) else {}


def _status(success: Any, fallback: bool = False) -> str:
    """根据 success 布尔与 fallback 标记推导节点展示状态。"""
    if success is True:
        return "fallback" if fallback else "success"
    if success is False:
        return "failed"
    return "unknown"


def _message(value: Any) -> Optional[str]:
    """清洗并截短一条用户可见的诊断消息（缺值/空串返回 ``None``）。"""
    return sanitize_diagnostic_text(value, max_length=180) if value else None


def build_history_run_flow_snapshot(record: Any) -> Dict[str, Any]:
    """从单条历史报告记录构建脱敏后的 run-flow 快照。

    兼容老格式报告（字段缺失时仍返回骨架图），绝不输出原始 prompt、密钥、URL
    或响应体。
    """
    snapshot = _mapping(getattr(record, "context_snapshot", None))
    raw = _mapping(getattr(record, "raw_result", None))
    diagnostics = _mapping(snapshot.get("diagnostics"))
    # trace_id 多处冗余，按优先级 fallback
    trace_id = diagnostics.get("trace_id") or snapshot.get("trace_id") or raw.get("trace_id")
    query_id = diagnostics.get("query_id") or getattr(record, "query_id", None) or "unknown"
    nodes: List[Dict[str, Any]] = [{
        "id": "request", "lane": "entry", "kind": "entry", "label": "分析请求",
        "status": "success", "message": "已生成历史分析记录",
        "metadata": {"query_id": query_id, "report_type": getattr(record, "report_type", None)},
    }]
    edges: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = [{
        "id": "event_request", "type": "request_completed", "severity": "success",
        "node_id": "request", "title": "分析完成",
    }]
    previous = "request"

    provider_runs = diagnostics.get("provider_runs") if isinstance(diagnostics.get("provider_runs"), list) else []
    for index, run in enumerate(provider_runs, start=1):
        if not isinstance(run, dict):
            continue
        provider = str(run.get("provider") or "unknown")[:80]
        data_type = str(run.get("data_type") or "data")[:40]
        node_id = f"provider_{index}"
        node_status = _status(run.get("success"), bool(run.get("fallback_from")))
        nodes.append({
            "id": node_id, "lane": "data", "kind": "data_source",
            "label": f"{data_type}: {provider}", "status": node_status, "provider": provider,
            # 仅展示正整数毫秒数；非整型/负值视为未知
            "duration_ms": run.get("latency_ms") if isinstance(run.get("latency_ms"), int) and run["latency_ms"] >= 0 else None,
            "message": _message(run.get("error_message_sanitized") or run.get("error_message")),
            "metadata": {"data_type": data_type, "record_count": run.get("record_count")},
        })
        edges.append({"id": f"edge_{previous}_{node_id}", "source": previous, "target": node_id, "kind": "data", "status": node_status})
        # 失败的数据源节点显示为 danger 级别事件，便于 UI 高亮
        events.append({"id": f"event_{node_id}", "type": "provider_attempt", "severity": "danger" if node_status == "failed" else "success", "node_id": node_id, "title": f"{data_type} 数据获取"})
        previous = node_id

    # 只取最后一次 LLM 调用结果作为展示；中间尝试视为内部细节
    llm_runs = diagnostics.get("llm_runs") if isinstance(diagnostics.get("llm_runs"), list) else []
    llm_run = llm_runs[-1] if llm_runs and isinstance(llm_runs[-1], dict) else {}
    llm_status = _status(llm_run.get("success")) if llm_run else "unknown"
    model = str(llm_run.get("model") or raw.get("model_used") or "unknown")[:120]
    nodes.append({
        "id": "llm", "lane": "analysis", "kind": "model", "label": "LLM 报告生成",
        "status": llm_status, "provider": model,
        "duration_ms": llm_run.get("duration_ms") if isinstance(llm_run.get("duration_ms"), int) and llm_run["duration_ms"] >= 0 else None,
        "message": _message(llm_run.get("error_message_sanitized") or llm_run.get("error_message")), "metadata": {},
    })
    edges.append({"id": f"edge_{previous}_llm", "source": previous, "target": "llm", "kind": "control", "status": llm_status})
    events.append({"id": "event_llm", "type": "llm_generation", "severity": "danger" if llm_status == "failed" else "success", "node_id": "llm", "title": "报告生成"})

    nodes.append({"id": "history", "lane": "artifact", "kind": "artifact", "label": "历史报告", "status": "success", "message": "报告已保存", "metadata": {"record_id": getattr(record, "id", None)}})
    edges.append({"id": "edge_llm_history", "source": "llm", "target": "history", "kind": "data", "status": "success"})
    # 综合状态：有失败节点 → failed；有 fallback/unknown 节点 → degraded；否则 success
    overall = "failed" if any(node["status"] == "failed" for node in nodes) else ("degraded" if any(node["status"] in {"fallback", "unknown"} for node in nodes) else "success")
    return {"task_id": str(diagnostics.get("task_id") or query_id), "trace_id": trace_id, "stock_code": str(getattr(record, "code", None) or raw.get("code") or "unknown"), "stock_name": getattr(record, "name", None) or raw.get("name"), "status": overall, "lanes": _LANES, "nodes": nodes, "edges": edges, "events": events}
