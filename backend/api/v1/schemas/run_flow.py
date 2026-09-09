"""公开的、经脱敏处理的"分析运行流"（run-flow）契约。

该 schema 描述一次 A 股个股分析任务的运行流程图：泳道（lane）、节点（node）、
边（edge）与事件（event）。响应体用于前端可视化看板，且只携带对外可见的状态，
不泄露内部实现细节（如 provider 密钥、原始堆栈等）。
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# 节点/边的运行状态枚举：pending 待执行 / running 执行中 / success 成功 /
# failed 失败 / degraded 降级完成 / fallback 走兜底分支 / skipped 跳过 / unknown 未知
RunFlowStatus = Literal[
    "pending", "running", "success", "failed", "degraded", "fallback", "skipped", "unknown"
]


class RunFlowLane(BaseModel):
    """运行流程图中的一个泳道（如数据采集、AI 分析、报告生成等）。"""

    id: str
    label: str
    order: int


class RunFlowNode(BaseModel):
    """运行流程图中的一个执行节点。"""

    id: str
    lane: str
    kind: str
    label: str
    status: RunFlowStatus
    provider: Optional[str] = None
    duration_ms: Optional[int] = Field(None, ge=0)
    message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RunFlowEdge(BaseModel):
    """运行流程图中节点之间的连线，区分数据流、控制流与兜底回退。"""

    id: str
    source: str
    target: str
    kind: Literal["data", "control", "fallback"]
    status: RunFlowStatus
    label: Optional[str] = None


class RunFlowEvent(BaseModel):
    """运行过程中产生的离散事件，用于前端时间线展示。"""

    id: str
    type: str
    severity: Literal["info", "success", "warning", "danger"]
    node_id: Optional[str] = None
    title: str
    message: Optional[str] = None


class RunFlowSnapshot(BaseModel):
    """一次分析任务运行流程的完整快照（泳道 + 节点 + 边 + 事件）。"""

    task_id: str
    trace_id: Optional[str] = None
    stock_code: str
    stock_name: Optional[str] = None
    status: RunFlowStatus
    lanes: List[RunFlowLane]
    nodes: List[RunFlowNode]
    edges: List[RunFlowEdge]
    events: List[RunFlowEvent]
