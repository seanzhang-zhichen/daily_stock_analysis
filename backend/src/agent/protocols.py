# -*- coding: utf-8 -*-
"""
共享协议 —— 多 Agent 通信所需的通用数据结构。

提供所有 Agent、运行器（runner）与编排器（orchestrator）共享的基础类型。
这些类型刻意设计为纯 dataclass（不依赖 ORM），
以便序列化、记录日志以及跨进程传递。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ============================================================
# Enums
# ============================================================

class Signal(str, Enum):
    """标准化的交易信号标签。"""
    STRONG_BUY = "strong_buy"
    BUY = "buy"
    HOLD = "hold"
    SELL = "sell"
    STRONG_SELL = "strong_sell"


_CANONICAL_DECISION_SIGNAL_MAP: Dict[str, str] = {
    "strong_buy": "buy",
    "buy": "buy",
    "hold": "hold",
    "sell": "sell",
    "strong_sell": "sell",
}


def normalize_decision_signal(signal: Any, default: str = "hold") -> str:
    """把面向模型的信号标签映射为仪表盘使用的稳定枚举。"""
    if not isinstance(signal, str):
        return default
    normalized = signal.strip().lower()
    return _CANONICAL_DECISION_SIGNAL_MAP.get(normalized, default)


class StageStatus(str, Enum):
    """流水线阶段的生命周期状态。"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# ============================================================
# AgentContext — shared state bag for a single analysis run
# ============================================================

@dataclass
class AgentContext:
    """单次运行中贯穿所有 Agent 的共享上下文。

    任意 Agent 均可读写此上下文；编排器负责初始化字段并收集最终结果。
    """

    # --- identity ---
    query: str = ""
    stock_code: str = ""
    stock_name: str = ""
    session_id: str = ""

    # --- collected data (populated by data-fetching stages) ---
    data: Dict[str, Any] = field(default_factory=dict)
    # 常见键："realtime_quote", "daily_history", "trend_result",
    #               "chip_distribution", "news_context"

    # --- opinions from individual agents ---
    opinions: List["AgentOpinion"] = field(default_factory=list)

    # --- risk flags raised by RiskAgent ---
    risk_flags: List[Dict[str, Any]] = field(default_factory=list)

    # --- arbitrary metadata ---
    meta: Dict[str, Any] = field(default_factory=dict)
    # e.g. {"skills_requested": [...], "user_platform": "feishu"}

    # --- timing ---
    created_at: float = field(default_factory=time.time)

    # -----------------------------------------------------------------
    # 便捷辅助方法
    # -----------------------------------------------------------------

    def add_opinion(self, opinion: "AgentOpinion") -> None:
        """追加一条意见，若时间戳缺失则自动填充。"""
        if opinion.timestamp == 0:
            opinion.timestamp = time.time()
        self.opinions.append(opinion)

    def add_risk_flag(self, category: str, description: str, severity: str = "medium") -> None:
        """追加一条结构化风险标记，并记录捕获时间戳。"""
        self.risk_flags.append({
            "category": category,
            "description": description,
            "severity": severity,
            "timestamp": time.time(),
        })

    def get_data(self, key: str, default: Any = None) -> Any:
        """从共享数据容器读取一个值。"""
        return self.data.get(key, default)

    def set_data(self, key: str, value: Any) -> None:
        """向共享数据容器写入一个值，供下游 Agent 使用。"""
        self.data[key] = value

    @property
    def has_risk_flags(self) -> bool:
        """返回是否有任何 Agent 提出了风险标记。"""
        return len(self.risk_flags) > 0


# ============================================================
# AgentOpinion —— 单个 Agent 的结构化输出
# ============================================================

@dataclass
class AgentOpinion:
    """某个 Agent 对一只股票的分析意见。

    参与多 Agent 流程的每个 Agent 都应产出一条 ``AgentOpinion``，
    并追加到 ``AgentContext.opinions`` 中。
    """

    agent_name: str = ""
    signal: str = ""  # free-form or Signal enum value
    confidence: float = 0.0  # 0.0 – 1.0
    reasoning: str = ""
    key_levels: Dict[str, float] = field(default_factory=dict)
    # e.g. {"support": 1800.0, "resistance": 1950.0, "stop_loss": 1760.0}
    raw_data: Dict[str, Any] = field(default_factory=dict)
    # Agent 希望传递给下游的任意附加数据
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        """把置信度夹在 [0.0, 1.0] 区间。"""
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

    @property
    def signal_enum(self) -> Optional[Signal]:
        """尝试把 ``signal`` 解析为 ``Signal`` 枚举；未知时返回 None。"""
        try:
            return Signal(self.signal)
        except ValueError:
            return None


# ============================================================
# StageResult — return type from a single pipeline stage
# ============================================================

@dataclass
class StageResult:
    """单个流水线阶段（Agent 执行）的结果。

    供编排器判断是继续、重试还是中止。
    """

    stage_name: str = ""
    status: StageStatus = StageStatus.PENDING
    opinion: Optional[AgentOpinion] = None
    error: Optional[str] = None
    duration_s: float = 0.0
    tokens_used: int = 0
    tool_calls_count: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        """仅当阶段完成时返回 True。"""
        return self.status == StageStatus.COMPLETED


# ============================================================
# AgentRunStats — aggregate statistics for an entire run
# ============================================================

@dataclass
class AgentRunStats:
    """一次流水线中所有 Agent 的聚合运行统计。

    由编排器收集，并呈现在日志、API 响应与进度回调中。
    """

    total_stages: int = 0
    completed_stages: int = 0
    failed_stages: int = 0
    skipped_stages: int = 0
    total_tokens: int = 0
    total_tool_calls: int = 0
    total_duration_s: float = 0.0
    models_used: List[str] = field(default_factory=list)
    stage_results: List[StageResult] = field(default_factory=list)

    def record_stage(self, result: StageResult) -> None:
        """记录一个阶段结果并更新计数器。

        处理所有 ``StageStatus`` 取值，包括 RUNNING/PENDING
        （计入总数，但不归类为完成/失败/跳过）。
        """
        self.stage_results.append(result)
        self.total_stages += 1
        self.total_tokens += result.tokens_used
        self.total_tool_calls += result.tool_calls_count
        self.total_duration_s += result.duration_s

        if result.status == StageStatus.COMPLETED:
            self.completed_stages += 1
        elif result.status == StageStatus.FAILED:
            self.failed_stages += 1
        elif result.status == StageStatus.SKIPPED:
            self.skipped_stages += 1
        # RUNNING / PENDING are counted in total_stages but not in any sub-counter

    def to_dict(self) -> Dict[str, Any]:
        """把聚合运行统计序列化，供 API 与日志使用。"""
        return {
            "total_stages": self.total_stages,
            "completed_stages": self.completed_stages,
            "failed_stages": self.failed_stages,
            "skipped_stages": self.skipped_stages,
            "total_tokens": self.total_tokens,
            "total_tool_calls": self.total_tool_calls,
            "total_duration_s": round(self.total_duration_s, 2),
            "models_used": self.models_used,
        }
