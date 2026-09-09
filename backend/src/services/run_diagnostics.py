# -*- coding: utf-8 -*-
"""单次分析任务的诊断上下文（轻量级、fail-open）。

负责在一次分析请求的整个生命周期中收集并暴露以下运行态证据，供 API
层与 SSE 通道回传给前端，也供告警/历史服务复用：

- 当前 trace 的 provider 调用记录（行情、新闻、基本面等数据源尝试）
- LLM 调用记录（含降级链路）
- 通知发送结果
- 报告历史持久化结果

第一阶段刻意把诊断状态放在内存中并保持 fail-open：写入失败不应阻塞
业务流程；未来引入专用诊断存储时，可在不动调用方的前提下平滑替换
持久化后端。

被调用方：分析服务 (`src/services/analysis_service.py`)、SSE 通道、
`build_run_diagnostic_summary()` 用户面汇总等。
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# 进程级 ContextVar：保存当前活跃的诊断上下文。
# 使用 ContextVar 而非线程局部变量，是为了在 asyncio/线程混合的代码路径中
# 自动跟随当前调用链，避免线程切换后丢失上下文。
_CURRENT_CONTEXT: ContextVar[Optional["RunDiagnosticContext"]] = ContextVar(
    "run_diagnostic_context",
    default=None,
)

# 敏感信息脱敏规则：按"长→短、最具体→最通用"的顺序排列，确保更精确的
# 模式先行匹配。每条规则为 (正则, 替换串或可调用对象) 元组。
_SECRET_REDACTIONS = (
    (
        re.compile(r"(?i)\b(authorization)\s*[:=]\s*(?:(?:Bearer|Basic|Token)\s+)?[^\s,&;]+"),
        lambda match: f"{match.group(1)}=<redacted>",
    ),
    (
        re.compile(r"(https?://)([^/\s:@]+):([^@\s/]+)@"),
        r"\1<redacted>:<redacted>@",
    ),
    (
        re.compile(r"https?://[^\s]+?(?:token|key|secret|webhook)[^\s]*", re.IGNORECASE),
        "<redacted-url>",
    ),
    (
        re.compile(
            r"(?i)([\"']?)"
            r"([A-Z0-9_]*?(?:api[_-]?key|access[_-]?token|token|secret|password|passwd|cookie))"
            r"\1\s*:\s*([\"'])([^\"']+)\3"
        ),
        lambda match: f"{match.group(1)}{match.group(2)}{match.group(1)}: {match.group(3)}<redacted>{match.group(3)}",
    ),
    (
        re.compile(
            r"(?i)\b([A-Z0-9_]*?(?:api[_-]?key|access[_-]?token|token|secret|password|passwd|cookie))"
            r"\s*=\s*([^\s,&;]+)"
        ),
        lambda match: f"{match.group(1)}=<redacted>",
    ),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password|passwd|cookie)"
            r"\s*:\s*([^\s,&;]+)"
        ),
        lambda match: f"{match.group(1)}=<redacted>",
    ),
    (
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
        "Bearer <redacted>",
    ),
)
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(authorization|api[_-]?key|access[_-]?token|(?:^|[_-])(?:auth|refresh|session|bearer)?[_-]?token$|secret|password|passwd|cookie|"
    r"webhook|sendkey|prompt|raw[_-]?prompt|raw[_-]?response|headers?|proxy)"
)
# URL 含 webhook/token/key/secret/sendkey 等关键字时整段替换为占位符。
_WEBHOOK_URL_RE = re.compile(r"https?://[^\s]+?(?:webhook|token|key|secret|sendkey)[^\s]*", re.IGNORECASE)
# 本地绝对路径（含 Linux /home/Users/root/var/opt/etc/tmp 与 Windows C:\）替换为占位符。
_LOCAL_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![\w:/.-])(?:/(?:home|Users|root|var|tmp|opt|etc)/[^\s,;]+|[A-Za-z]:\\[^\s,;]+)"
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password|passwd|cookie|webhook|sendkey|"
    r"prompt|raw[_-]?prompt|raw[_-]?response)\s*[:=]\s*([^\s,&;]+)"
)


def build_trace_id() -> str:
    """生成紧凑的 trace id，用于日志、API 响应与 SSE 通道关联。"""
    return uuid.uuid4().hex


def sanitize_diagnostic_text(value: Any, *, max_length: int = 300) -> Optional[str]:
    """对诊断文本做敏感信息清洗并按 ``max_length`` 截断。

    脱敏对象包括：Authorization/Bearer 头、URL 中的 token/key/secret、
    含凭据的 URL（user:pass@host）、key=value 形式赋值、绝对本地路径。
    输入为 ``None`` 或经折叠后为空时返回 ``None``。
    """
    if value is None:
        return None

    # 先把多空白/换行折叠成单空格，便于后续正则按 token 边界匹配
    text = " ".join(str(value).split())
    if not text:
        return None

    for pattern, replacement in _SECRET_REDACTIONS:
        text = pattern.sub(replacement, text)
    text = _WEBHOOK_URL_RE.sub("<redacted-url>", text)
    text = _LOCAL_ABSOLUTE_PATH_RE.sub("<redacted-path>", text)
    # 把 "key=value" 形式中的 value 替换为占位符，避免事件流泄漏
    text = _SENSITIVE_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=<redacted>", text)

    if len(text) > max_length:
        return f"{text[:max_length].rstrip()}..."
    return text


def safe_diagnostic_key(value: Any) -> str:
    """把任意对象规整成诊断元数据可用的安全 key。

    先做文本脱敏，再只保留字母数字下划线，方便作为字段名出现在 JSON 中。
    """
    text = sanitize_diagnostic_text(value, max_length=80) or ""
    return re.sub(r"[^A-Za-z0-9_]+", "_", text.strip().lower()).strip("_")[:80]


def sanitize_diagnostic_metadata(value: Any, *, depth: int = 0) -> Any:
    """递归清洗诊断元数据，确保暴露到 API/SSE 的负载不含敏感字段。

    规则：
    - 超过 3 层嵌套直接截断为 ``<truncated>``，避免深结构意外泄漏；
    - dict 中超过 20 个键会被截断并添加 ``truncated`` 标记；
    - 命中敏感 key 的值统一置为 ``<redacted>``；
    - 基础类型（int/float/bool）原样保留；其他调用 ``sanitize_diagnostic_text``。
    """
    if depth > 3:
        return "<truncated>"
    if isinstance(value, Mapping):
        sanitized: Dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 20:
                sanitized["truncated"] = True
                break
            safe_key = safe_diagnostic_key(key)
            if not safe_key:
                continue
            if _SENSITIVE_KEY_RE.search(str(key)):
                sanitized[safe_key] = "<redacted>"
                continue
            safe_value = sanitize_diagnostic_metadata(item, depth=depth + 1)
            # 过滤掉空值/空容器，减小响应体积
            if safe_value not in (None, "", [], {}):
                sanitized[safe_key] = safe_value
        return sanitized
    if isinstance(value, list):
        items = [sanitize_diagnostic_metadata(item, depth=depth + 1) for item in value[:8]]
        return [item for item in items if item not in (None, "", [], {})]
    if isinstance(value, tuple):
        return sanitize_diagnostic_metadata(list(value), depth=depth)
    if isinstance(value, (int, float, bool)):
        return value
    return sanitize_diagnostic_text(value, max_length=160)


@dataclass
class ProviderRun:
    """一次 provider 调用尝试的完整记录（trace 维度）。"""

    trace_id: str
    data_type: str
    provider: str
    operation: str
    success: bool
    latency_ms: Optional[int] = None
    error_type: Optional[str] = None
    error_message_sanitized: Optional[str] = None
    fallback_from: Optional[str] = None
    fallback_to: Optional[str] = None
    cache_hit: Optional[bool] = None
    stale_seconds: Optional[int] = None
    record_count: Optional[int] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict，仅暴露已填充字段，避免向前端下发冗余 null 键。"""
        # 仅暴露已填充字段，避免向前端下发冗余 null 键
        payload = {
            "trace_id": self.trace_id,
            "data_type": self.data_type,
            "provider": self.provider,
            "operation": self.operation,
            "success": self.success,
            "latency_ms": self.latency_ms,
            "error_type": self.error_type,
            "error_message_sanitized": self.error_message_sanitized,
            "fallback_from": self.fallback_from,
            "fallback_to": self.fallback_to,
            "cache_hit": self.cache_hit,
            "stale_seconds": self.stale_seconds,
            "record_count": self.record_count,
            "created_at": self.created_at,
        }
        return {key: value for key, value in payload.items() if value is not None}


@dataclass
class LLMRun:
    """一次 LLM 调用结果的完整记录（trace 维度）。"""

    trace_id: str
    provider: Optional[str] = None
    model: Optional[str] = None
    call_type: str = "analysis"
    success: bool = True
    tokens: Optional[int] = None
    duration_ms: Optional[int] = None
    fallback_model: Optional[str] = None
    error_type: Optional[str] = None
    error_message_sanitized: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict 并剔除值为 None 的键。"""
        payload = {
            "trace_id": self.trace_id,
            "provider": self.provider,
            "model": self.model,
            "call_type": self.call_type,
            "success": self.success,
            "tokens": self.tokens,
            "duration_ms": self.duration_ms,
            "fallback_model": self.fallback_model,
            "error_type": self.error_type,
            "error_message_sanitized": self.error_message_sanitized,
            "created_at": self.created_at,
        }
        return {key: value for key, value in payload.items() if value is not None}


@dataclass
class NotificationRun:
    """一次通知发送结果的完整记录（trace 维度）。"""

    trace_id: str
    channel: str
    status: str
    success: bool
    attempts: int = 1
    error_message_sanitized: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict 并剔除值为 None 的键。"""
        payload = {
            "trace_id": self.trace_id,
            "channel": self.channel,
            "status": self.status,
            "success": self.success,
            "attempts": self.attempts,
            "error_message_sanitized": self.error_message_sanitized,
            "created_at": self.created_at,
        }
        return {key: value for key, value in payload.items() if value is not None}


@dataclass
class HistoryRun:
    """一次报告历史持久化结果的完整记录（trace 维度）。"""

    trace_id: str
    report_saved: bool
    metadata_saved: Optional[bool] = None
    analysis_history_id: Optional[int] = None
    error_message_sanitized: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict 并剔除值为 None 的键。"""
        payload = {
            "trace_id": self.trace_id,
            "report_saved": self.report_saved,
            "metadata_saved": self.metadata_saved,
            "analysis_history_id": self.analysis_history_id,
            "error_message_sanitized": self.error_message_sanitized,
            "created_at": self.created_at,
        }
        return {key: value for key, value in payload.items() if value is not None}


@dataclass
class RunDiagnosticComponent:
    """面向用户的单个诊断组件状态。"""

    key: str
    label: str
    status: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict 并剔除空值键（None/空 dict/空 list）。"""
        payload = {
            "key": self.key,
            "label": self.label,
            "status": self.status,
            "message": self.message,
            "details": self.details,
        }
        return {key: value for key, value in payload.items() if value not in (None, {}, [])}


@dataclass
class RunDiagnosticSummary:
    """单次分析运行的用户面诊断汇总。

    汇总层把 provider_runs / llm_runs / notification_runs / history_runs
    折叠成用户可读的"组件状态 + 顶层 status + 简短原因"。``copy_text``
    字段额外提供可复制的纯文本格式，方便贴到工单里。
    """

    status: str
    status_label: str
    reason: str
    trace_id: Optional[str] = None
    task_id: Optional[str] = None
    query_id: Optional[str] = None
    stock_code: Optional[str] = None
    trigger_source: Optional[str] = None
    components: Dict[str, RunDiagnosticComponent] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict：折叠组件状态，并附带可一键复制的纯文本字段。"""
        payload = {
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "query_id": self.query_id,
            "stock_code": self.stock_code,
            "trigger_source": self.trigger_source,
            "status": self.status,
            "status_label": self.status_label,
            "reason": self.reason,
            "components": {
                key: component.to_dict()
                for key, component in self.components.items()
            },
        }
        # 一键复制的纯文本字段，便于用户上报问题
        payload["copy_text"] = format_copyable_diagnostics(payload)
        return {key: value for key, value in payload.items() if value is not None}


@dataclass
class RunDiagnosticContext:
    """单次分析运行的诊断状态容器。

    由调用方通过 ``activate_run_diagnostic_context`` 激活，配合
    ``ContextVar`` 在异步/线程调用链中自动传递。本类负责：

    - 记录 provider、LLM、通知、历史的尝试与结果；
    - 维护"已开始 → 已结束"的事件配对索引（``*_pending_attempt_index_by_key``）；
    - 把事件经过清洗后通过可选的 ``event_sink`` 推送给 SSE/日志等下游。
    """

    trace_id: str
    task_id: Optional[str] = None
    query_id: Optional[str] = None
    stock_code: Optional[str] = None
    trigger_source: Optional[str] = None
    scope: Optional[str] = None
    provider_runs: List[ProviderRun] = field(default_factory=list)
    llm_runs: List[LLMRun] = field(default_factory=list)
    notification_runs: List[NotificationRun] = field(default_factory=list)
    history_runs: List[HistoryRun] = field(default_factory=list)
    # 可选事件回调（接收清洗后的字典），通常指向 SSE 写入函数
    event_sink: Optional[Callable[[Dict[str, Any]], None]] = None
    flow_event_index: int = 0
    # 各 data_type 的 provider 尝试序号自增器
    provider_attempt_index_by_type: Dict[str, int] = field(default_factory=dict)
    # "已开始但尚未结束"的 provider 尝试索引队列，用于配对 started/finished
    provider_pending_attempt_index_by_key: Dict[str, List[int]] = field(default_factory=dict)
    llm_attempt_index_by_type: Dict[str, int] = field(default_factory=dict)
    llm_pending_attempt_index_by_key: Dict[str, List[int]] = field(default_factory=dict)
    llm_pending_attempt_index_by_call_type: Dict[str, List[int]] = field(default_factory=dict)

    def record_provider_run(self, provider_run: ProviderRun) -> None:
        """登记一次 provider 结束事件：与同 key 的 started 配对或自增分配序号。"""
        self.provider_runs.append(provider_run)
        data_type_key = _safe_event_key(provider_run.data_type) or "provider"
        pending_key = _provider_pending_key(
            provider_run.data_type,
            provider_run.provider,
            provider_run.operation,
        )
        pending_indexes = self.provider_pending_attempt_index_by_key.get(pending_key) or []
        if pending_indexes:
            # 已有 pending started，先到先配对，保证同一次尝试的 started/finished 对齐
            attempt_index = pending_indexes.pop(0)
            if pending_indexes:
                self.provider_pending_attempt_index_by_key[pending_key] = pending_indexes
            else:
                self.provider_pending_attempt_index_by_key.pop(pending_key, None)
        else:
            # 没有匹配的 started，直接自增分配一个序号（多用于仅记录结果的场景）
            attempt_index = self.provider_attempt_index_by_type.get(data_type_key, 0) + 1
            self.provider_attempt_index_by_type[data_type_key] = attempt_index
        self._emit_flow_event(_provider_flow_event(self, provider_run, attempt_index))

    def record_provider_run_started(
        self,
        *,
        data_type: str,
        provider: str,
        operation: str,
    ) -> None:
        """登记一次 provider 开始事件：仅推 live 事件，不持久化诊断。"""
        data_type_key = _safe_event_key(data_type) or "provider"
        attempt_index = self.provider_attempt_index_by_type.get(data_type_key, 0) + 1
        self.provider_attempt_index_by_type[data_type_key] = attempt_index
        pending_key = _provider_pending_key(data_type, provider, operation)
        pending_indexes = self.provider_pending_attempt_index_by_key.get(pending_key) or []
        pending_indexes.append(attempt_index)
        self.provider_pending_attempt_index_by_key[pending_key] = pending_indexes
        self._emit_flow_event(
            _provider_started_flow_event(
                self,
                data_type=data_type,
                provider=provider,
                operation=operation,
                index=attempt_index,
            )
        )

    def record_llm_run(self, llm_run: LLMRun) -> None:
        """登记一次 LLM 结束事件：尝试与 started 配对，再无匹配则自增。"""
        self.llm_runs.append(llm_run)
        call_type_key = _safe_event_key(llm_run.call_type) or "analysis"
        pending_key = _llm_pending_key(llm_run.call_type, llm_run.provider, llm_run.model)
        pending_indexes = self.llm_pending_attempt_index_by_key.get(pending_key) or []
        if pending_indexes:
            attempt_index = pending_indexes.pop(0)
            if pending_indexes:
                self.llm_pending_attempt_index_by_key[pending_key] = pending_indexes
            else:
                self.llm_pending_attempt_index_by_key.pop(pending_key, None)
            self._remove_llm_pending_call_type_index(call_type_key, attempt_index)
        else:
            # 先按 call_type 兜底匹配，再退回直接自增
            call_type_pending_indexes = self.llm_pending_attempt_index_by_call_type.get(call_type_key) or []
            if call_type_pending_indexes:
                attempt_index = call_type_pending_indexes.pop(0)
                if call_type_pending_indexes:
                    self.llm_pending_attempt_index_by_call_type[call_type_key] = call_type_pending_indexes
                else:
                    self.llm_pending_attempt_index_by_call_type.pop(call_type_key, None)
                self._remove_llm_pending_exact_index(attempt_index)
            else:
                attempt_index = self.llm_attempt_index_by_type.get(call_type_key, 0) + 1
                self.llm_attempt_index_by_type[call_type_key] = attempt_index
        self._emit_flow_event(_llm_flow_event(self, llm_run, attempt_index))

    def _remove_llm_pending_call_type_index(self, call_type_key: str, attempt_index: int) -> None:
        """从 call_type 维度的待配对队列中移除指定序号。"""
        pending_indexes = self.llm_pending_attempt_index_by_call_type.get(call_type_key) or []
        if attempt_index not in pending_indexes:
            return
        pending_indexes = [index for index in pending_indexes if index != attempt_index]
        if pending_indexes:
            self.llm_pending_attempt_index_by_call_type[call_type_key] = pending_indexes
        else:
            self.llm_pending_attempt_index_by_call_type.pop(call_type_key, None)

    def _remove_llm_pending_exact_index(self, attempt_index: int) -> None:
        """从所有 exact 维度待配对队列中移除指定序号。"""
        for pending_key, pending_indexes in list(self.llm_pending_attempt_index_by_key.items()):
            if attempt_index not in pending_indexes:
                continue
            pending_indexes = [index for index in pending_indexes if index != attempt_index]
            if pending_indexes:
                self.llm_pending_attempt_index_by_key[pending_key] = pending_indexes
            else:
                self.llm_pending_attempt_index_by_key.pop(pending_key, None)

    def record_llm_run_started(
        self,
        *,
        call_type: str = "analysis",
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        """登记一次 LLM 开始事件：仅推 live 事件，不持久化诊断。"""
        call_type_key = _safe_event_key(call_type) or "analysis"
        attempt_index = self.llm_attempt_index_by_type.get(call_type_key, 0) + 1
        self.llm_attempt_index_by_type[call_type_key] = attempt_index
        pending_key = _llm_pending_key(call_type, provider, model)
        pending_indexes = self.llm_pending_attempt_index_by_key.get(pending_key) or []
        pending_indexes.append(attempt_index)
        self.llm_pending_attempt_index_by_key[pending_key] = pending_indexes
        call_type_pending_indexes = self.llm_pending_attempt_index_by_call_type.get(call_type_key) or []
        call_type_pending_indexes.append(attempt_index)
        self.llm_pending_attempt_index_by_call_type[call_type_key] = call_type_pending_indexes
        self._emit_flow_event(
            _llm_started_flow_event(
                self,
                call_type=call_type,
                provider=provider,
                model=model,
                index=attempt_index,
            )
        )

    def record_notification_run(self, notification_run: NotificationRun) -> None:
        """登记一次通知发送结果并推 flow 事件。"""
        self.notification_runs.append(notification_run)
        self._emit_flow_event(_notification_flow_event(self, notification_run, len(self.notification_runs)))

    def record_history_run(self, history_run: HistoryRun) -> None:
        """登记一次历史持久化结果并推 flow 事件。"""
        self.history_runs.append(history_run)
        self._emit_flow_event(_history_flow_event(self, history_run, len(self.history_runs)))

    def _emit_flow_event(self, event: Dict[str, Any]) -> None:
        """把 flow 事件经脱敏后交给 event_sink；任何异常都不能影响业务流程。"""
        if self.event_sink is None:
            return
        try:
            self.flow_event_index += 1
            event_payload = sanitize_diagnostic_metadata(event)
            event_payload = dict(event_payload) if isinstance(event_payload, Mapping) else {}
            # 顺序自增的稳定 id，方便前端按顺序渲染与去重
            event_payload["id"] = event_payload.get("id") or f"flow_{self.flow_event_index:04d}"
            self.event_sink(event_payload)
        except Exception as exc:  # pragma: no cover - defensive fail-open guard
            logger.warning("run-flow event sink failed: %s", exc)

    def snapshot(self) -> Dict[str, Any]:
        """把当前上下文序列化成可序列化字典（用于持久化/快照/导出）。"""
        return {
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "query_id": self.query_id,
            "stock_code": self.stock_code,
            "trigger_source": self.trigger_source,
            "scope": self.scope,
            "provider_runs": [run.to_dict() for run in self.provider_runs],
            "llm_runs": [run.to_dict() for run in self.llm_runs],
            "notification_runs": [run.to_dict() for run in self.notification_runs],
            "history_runs": [run.to_dict() for run in self.history_runs],
        }


def get_current_diagnostic_context() -> Optional[RunDiagnosticContext]:
    """获取当前 ContextVar 上的诊断上下文（不在 active 状态时为 None）。"""
    return _CURRENT_CONTEXT.get()


def activate_run_diagnostic_context(
    *,
    trace_id: Optional[str] = None,
    task_id: Optional[str] = None,
    query_id: Optional[str] = None,
    stock_code: Optional[str] = None,
    trigger_source: Optional[str] = None,
    scope: Optional[str] = None,
    event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Token:
    """激活一个新的诊断上下文并返回 ContextVar reset token。

    若未显式传入 trace_id，则按 query_id → task_id → 随机 UUID 的顺序兜底，
    保证每个 trace 都有可关联的唯一 ID。
    """
    context = RunDiagnosticContext(
        trace_id=trace_id or query_id or task_id or build_trace_id(),
        task_id=task_id,
        query_id=query_id,
        stock_code=stock_code,
        trigger_source=trigger_source,
        scope=scope,
        event_sink=event_sink,
    )
    return _CURRENT_CONTEXT.set(context)


def reset_run_diagnostic_context(token: Optional[Token]) -> None:
    """关闭诊断上下文；token 为 None 时静默忽略（便于流程兜底调用）。"""
    if token is None:
        return
    try:
        _CURRENT_CONTEXT.reset(token)
    except Exception as exc:  # pragma: no cover - defensive fail-open guard
        logger.warning("run diagnostic context reset failed: %s", exc)


def current_diagnostic_snapshot() -> Optional[Dict[str, Any]]:
    """读取当前上下文的快照；若出错返回 None，遵循 fail-open 策略。"""
    context = get_current_diagnostic_context()
    if context is None:
        return None
    try:
        return context.snapshot()
    except Exception as exc:  # pragma: no cover - defensive fail-open guard
        logger.warning("run diagnostic snapshot failed: %s", exc)
        return None


# data_type 的中文展示名映射，便于诊断 UI 渲染可读标签
_DATA_TYPE_LABELS = {
    "realtime_quote": "实时行情",
    "daily_data": "日线K线",
    "daily_bars": "日线K线",
    "technical": "技术指标",
    "news": "新闻舆情",
    "news_search": "新闻舆情",
    "fundamental": "基本面",
    "fundamentals": "基本面",
    "belong_boards": "所属板块",
    "chip": "筹码结构",
}


def _safe_event_key(value: Any) -> str:
    """``safe_diagnostic_key`` 的内部别名，强调"用于事件 key"的语义。"""
    return safe_diagnostic_key(value)


def _clean_metadata(value: Dict[str, Any]) -> Dict[str, Any]:
    """去掉 None/空串/空容器键值对，减小事件体积。"""
    return {
        str(key): item
        for key, item in value.items()
        if item not in (None, "", [], {})
    }


def _provider_pending_key(data_type: Any, provider: Any, operation: Any) -> str:
    """构造 provider 维度"已开始/结束"配对所用的稳定 key。"""
    return "|".join(
        (
            _safe_event_key(data_type) or "provider",
            _safe_event_key(provider) or "unknown",
            _safe_event_key(operation) or "operation",
        )
    )


def _llm_pending_key(call_type: Any, provider: Any, model: Any) -> str:
    """构造 LLM 维度配对 key；当前实现按 call_type 配对，provider/model 仅占位。"""
    _ = (provider, model)
    return _safe_event_key(call_type) or "analysis"


def _flow_status_for_success(success: bool, *, fallback: bool = False, skipped: bool = False) -> str:
    """把布尔 success/fallback/skipped 折叠成前端约定的字符串状态。"""
    if skipped:
        return "skipped"
    if success:
        return "fallback" if fallback else "success"
    return "failed"


def _started_at_from_end_and_duration(end: Any, duration_ms: Optional[int]) -> Optional[str]:
    """由结束时间与耗时反推开始时间，便于 UI 渲染进度条与计时。

    仅在 ``duration_ms`` 合法（非负）且 ``end`` 可被解析为 ``datetime`` 时
    返回 ISO 字符串，否则返回 None——避免出现负数或乱码开始时间。
    """
    if duration_ms is None or duration_ms < 0:
        return None
    if isinstance(end, datetime):
        parsed = end
    elif isinstance(end, str) and "T" in end:
        # 兼容 ISO Z 后缀，统一归一为带时区后再解析
        normalized = end[:-1] + "+00:00" if end.endswith("Z") else end
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
    else:
        return None
    return (parsed - timedelta(milliseconds=duration_ms)).isoformat()


def _provider_started_flow_event(
    context: RunDiagnosticContext,
    *,
    data_type: str,
    provider: str,
    operation: str,
    index: int,
) -> Dict[str, Any]:
    """构造"provider 开始"flow 事件：状态为 running、attempts=1。"""
    data_type_key = _safe_event_key(data_type) or "provider"
    provider_key = _safe_event_key(provider) or "unknown"
    label = _DATA_TYPE_LABELS.get(data_type_key, data_type_key)
    # 节点 ID 把 data_type/provider/序号 拼在一起，保证唯一稳定
    node_id = f"provider_{data_type_key}_{provider_key}_{index}"
    timestamp = datetime.now().isoformat()
    message = f"{label} {provider} 调用中"
    return {
        "timestamp": timestamp,
        "severity": "info",
        "type": "provider_run_started",
        "node_id": node_id,
        "title": f"{label}开始",
        "message": sanitize_diagnostic_text(message, max_length=220),
        "metadata": _clean_metadata(
            {
                "trace_id": context.trace_id,
                "provider": provider,
                "data_type": data_type,
                "operation": operation,
                "node": {
                    "id": node_id,
                    "lane": "data_source",
                    "kind": "data_source",
                    "label": f"{label} · {provider}",
                    "status": "running",
                    "provider": provider,
                    "started_at": timestamp,
                    "attempts": 1,
                    "message": message,
                },
            }
        ),
    }


def _provider_flow_event(
    context: RunDiagnosticContext,
    run: ProviderRun,
    index: int,
) -> Dict[str, Any]:
    """构造"provider 结束"flow 事件：成功/降级/失败分别渲染对应文案与节点状态。"""
    data_type = _safe_event_key(run.data_type) or "provider"
    provider_key = _safe_event_key(run.provider) or "unknown"
    label = _DATA_TYPE_LABELS.get(data_type, data_type)
    fallback = bool(run.fallback_from or run.fallback_to)
    status = _flow_status_for_success(run.success, fallback=fallback)
    node_id = f"provider_{data_type}_{provider_key}_{index}"
    started_at = _started_at_from_end_and_duration(run.created_at, run.latency_ms)
    message = (
        f"{label} {run.provider} 成功"
        if run.success
        else f"{label} {run.provider} 失败：{run.error_message_sanitized or run.error_type or '未知错误'}"
    )
    return {
        "timestamp": run.created_at,
        "severity": "success" if run.success else "warning",
        "type": "provider_run",
        "node_id": node_id,
        "title": f"{label}{'成功' if run.success else '失败'}",
        "message": sanitize_diagnostic_text(message, max_length=220),
        "metadata": _clean_metadata(
            {
                "trace_id": context.trace_id,
                "provider": run.provider,
                "data_type": run.data_type,
                "operation": run.operation,
                "duration_ms": run.latency_ms,
                "record_count": run.record_count,
                "fallback_from": run.fallback_from,
                "fallback_to": run.fallback_to,
                "error_type": run.error_type,
                "node": {
                    "id": node_id,
                    "lane": "data_source",
                    "kind": "data_source",
                    "label": f"{label} · {run.provider}",
                    "status": status,
                    "provider": run.provider,
                    "started_at": started_at,
                    "ended_at": run.created_at,
                    "duration_ms": run.latency_ms,
                    "record_count": run.record_count,
                    "message": message,
                },
            }
        ),
    }


def _llm_started_flow_event(
    context: RunDiagnosticContext,
    *,
    call_type: str,
    provider: Optional[str],
    model: Optional[str],
    index: int,
) -> Dict[str, Any]:
    """构造"LLM 开始"flow 事件：节点 label/状态统一为"LLM 生成 / running"。"""
    call_type_key = _safe_event_key(call_type) or "analysis"
    display_model = model or provider or "unknown"
    node_id = f"llm_{call_type_key}_{index}"
    timestamp = datetime.now().isoformat()
    message = f"LLM {display_model} 调用中"
    return {
        "timestamp": timestamp,
        "severity": "info",
        "type": "llm_run_started",
        "node_id": node_id,
        "title": "LLM 开始",
        "message": sanitize_diagnostic_text(message, max_length=220),
        "metadata": _clean_metadata(
            {
                "trace_id": context.trace_id,
                "provider": provider,
                "model": model,
                "call_type": call_type,
                "node": {
                    "id": node_id,
                    "lane": "analysis",
                    "kind": "model",
                    "label": "LLM 生成",
                    "status": "running",
                    "provider": display_model,
                    "started_at": timestamp,
                    "attempts": 1,
                    "message": message,
                },
            }
        ),
    }


def _llm_flow_event(
    context: RunDiagnosticContext,
    run: LLMRun,
    index: int,
) -> Dict[str, Any]:
    """构造"LLM 结束"flow 事件：含 fallback/失败时的文案与节点状态。"""
    call_type = _safe_event_key(run.call_type) or "analysis"
    model = run.model or run.provider or "unknown"
    # index>1 或显式提供 fallback_model 时，视为经过降级链路
    status = _flow_status_for_success(run.success, fallback=bool(run.fallback_model or index > 1))
    node_id = f"llm_{call_type}_{index}"
    started_at = _started_at_from_end_and_duration(run.created_at, run.duration_ms)
    message = (
        f"LLM {model} 成功"
        if run.success
        else f"LLM {model} 失败：{run.error_message_sanitized or run.error_type or '未知错误'}"
    )
    return {
        "timestamp": run.created_at,
        "severity": "success" if run.success else "danger",
        "type": "llm_run",
        "node_id": node_id,
        "title": f"LLM {'成功' if run.success else '失败'}",
        "message": sanitize_diagnostic_text(message, max_length=220),
        "metadata": _clean_metadata(
            {
                "trace_id": context.trace_id,
                "provider": run.provider,
                "model": run.model,
                "call_type": run.call_type,
                "duration_ms": run.duration_ms,
                "fallback_model": run.fallback_model,
                "error_type": run.error_type,
                "node": {
                    "id": node_id,
                    "lane": "analysis",
                    "kind": "model",
                    "label": "LLM 生成",
                    "status": status,
                    "provider": model,
                    "started_at": started_at,
                    "ended_at": run.created_at,
                    "duration_ms": run.duration_ms,
                    "message": message,
                },
            }
        ),
    }


def _history_flow_event(
    context: RunDiagnosticContext,
    run: HistoryRun,
    index: int,
) -> Dict[str, Any]:
    """构造"历史保存"flow 事件：第一次保存节点 ID 用 ``history_save`` 便于锚定。"""
    node_id = "history_save" if index == 1 else f"history_save_{index}"
    status = "success" if run.report_saved else "failed"
    message = "报告历史已保存" if run.report_saved else f"报告历史保存失败：{run.error_message_sanitized or '未知错误'}"
    return {
        "timestamp": run.created_at,
        "severity": "success" if run.report_saved else "danger",
        "type": "history_run",
        "node_id": node_id,
        "title": "历史保存成功" if run.report_saved else "历史保存失败",
        "message": sanitize_diagnostic_text(message, max_length=220),
        "metadata": _clean_metadata(
            {
                "trace_id": context.trace_id,
                "metadata_saved": run.metadata_saved,
                "analysis_history_id": run.analysis_history_id,
                "node": {
                    "id": node_id,
                    "lane": "artifact",
                    "kind": "artifact",
                    "label": "保存报告",
                    "status": status,
                    "message": message,
                },
            }
        ),
    }


def _notification_flow_event(
    context: RunDiagnosticContext,
    run: NotificationRun,
    index: int,
) -> Dict[str, Any]:
    """构造"通知发送"flow 事件：覆盖成功/跳过/失败三类状态。"""
    channel = run.channel or "unknown"
    channel_key = _safe_event_key(channel) or "unknown"
    # 通知未配置或本次主动跳过都属于"软失败"，不应被算作告警失败
    skipped = run.status in {"skipped", "not_configured"}
    status = _flow_status_for_success(run.success, skipped=skipped)
    node_id = f"notification_{channel_key}_{index}"
    if status == "success":
        title = "通知发送成功"
        message = f"{channel} 通知发送成功"
    elif status == "skipped":
        title = "通知跳过"
        message = f"{channel} 通知跳过"
    else:
        title = "通知失败"
        message = f"{channel} 通知失败：{run.error_message_sanitized or run.status or '未知错误'}"
    return {
        "timestamp": run.created_at,
        "severity": "success" if status == "success" else ("warning" if status == "skipped" else "danger"),
        "type": "notification_run",
        "node_id": node_id,
        "title": title,
        "message": sanitize_diagnostic_text(message, max_length=220),
        "metadata": _clean_metadata(
            {
                "trace_id": context.trace_id,
                "channel": channel,
                "status": run.status,
                "attempts": run.attempts,
                "node": {
                    "id": node_id,
                    "lane": "artifact",
                    "kind": "notification",
                    "label": f"推送通知 · {channel}",
                    "status": status,
                    "provider": channel,
                    "attempts": run.attempts,
                    "message": message,
                },
            }
        ),
    }


def record_provider_run(
    *,
    data_type: str,
    provider: str,
    operation: str,
    success: bool,
    latency_ms: Optional[int] = None,
    error_type: Optional[str] = None,
    error_message: Optional[Any] = None,
    fallback_from: Optional[str] = None,
    fallback_to: Optional[str] = None,
    cache_hit: Optional[bool] = None,
    stale_seconds: Optional[int] = None,
    record_count: Optional[int] = None,
) -> None:
    """向当前活跃上下文登记一次 provider 调用结果；任何异常不影响调用方。

    设计为无副作用：未激活上下文时直接返回；写入失败仅写日志。
    """
    context = get_current_diagnostic_context()
    if context is None:
        return

    try:
        context.record_provider_run(
            ProviderRun(
                trace_id=context.trace_id,
                data_type=data_type,
                provider=provider,
                operation=operation,
                success=success,
                latency_ms=latency_ms,
                error_type=error_type,
                error_message_sanitized=sanitize_diagnostic_text(error_message),
                fallback_from=fallback_from,
                fallback_to=fallback_to,
                cache_hit=cache_hit,
                stale_seconds=stale_seconds,
                record_count=record_count,
            )
        )
    except Exception as exc:  # pragma: no cover - defensive fail-open guard
        logger.warning("provider diagnostic record failed: %s", exc)


def record_provider_run_started(
    *,
    data_type: str,
    provider: str,
    operation: str,
) -> None:
    """推一条 provider 启动 live 事件；不影响持久化诊断与调用方流程。"""
    context = get_current_diagnostic_context()
    if context is None:
        return

    try:
        context.record_provider_run_started(
            data_type=data_type,
            provider=provider,
            operation=operation,
        )
    except Exception as exc:  # pragma: no cover - defensive fail-open guard
        logger.warning("provider started diagnostic record failed: %s", exc)


def record_llm_run(
    *,
    success: bool,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    call_type: str = "analysis",
    tokens: Optional[int] = None,
    duration_ms: Optional[int] = None,
    fallback_model: Optional[str] = None,
    error_type: Optional[str] = None,
    error_message: Optional[Any] = None,
) -> None:
    """向当前活跃上下文登记一次 LLM 调用结果；遵循 fail-open。"""
    context = get_current_diagnostic_context()
    if context is None:
        return

    try:
        context.record_llm_run(
            LLMRun(
                trace_id=context.trace_id,
                provider=provider,
                model=model,
                call_type=call_type,
                success=success,
                tokens=tokens,
                duration_ms=duration_ms,
                fallback_model=fallback_model,
                error_type=error_type,
                error_message_sanitized=sanitize_diagnostic_text(error_message),
            )
        )
    except Exception as exc:  # pragma: no cover - defensive fail-open guard
        logger.warning("llm diagnostic record failed: %s", exc)


def record_llm_run_started(
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    call_type: str = "analysis",
) -> None:
    """推一条 LLM 启动 live 事件；不影响持久化诊断与调用方流程。"""
    context = get_current_diagnostic_context()
    if context is None:
        return

    try:
        context.record_llm_run_started(
            provider=provider,
            model=model,
            call_type=call_type,
        )
    except Exception as exc:  # pragma: no cover - defensive fail-open guard
        logger.warning("llm started diagnostic record failed: %s", exc)


def record_notification_run(
    *,
    channel: str,
    status: str,
    success: bool,
    attempts: int = 1,
    error_message: Optional[Any] = None,
) -> None:
    """向当前活跃上下文登记一次通知发送结果。"""
    context = get_current_diagnostic_context()
    if context is None:
        return

    try:
        context.record_notification_run(
            NotificationRun(
                trace_id=context.trace_id,
                channel=channel,
                status=status,
                success=success,
                attempts=attempts,
                error_message_sanitized=sanitize_diagnostic_text(error_message),
            )
        )
    except Exception as exc:  # pragma: no cover - defensive fail-open guard
        logger.warning("notification diagnostic record failed: %s", exc)


def record_history_run(
    *,
    report_saved: bool,
    metadata_saved: Optional[bool] = None,
    analysis_history_id: Optional[int] = None,
    error_message: Optional[Any] = None,
) -> None:
    """向当前活跃上下文登记一次报告历史持久化结果。"""
    context = get_current_diagnostic_context()
    if context is None:
        return

    try:
        context.record_history_run(
            HistoryRun(
                trace_id=context.trace_id,
                report_saved=report_saved,
                metadata_saved=metadata_saved,
                analysis_history_id=analysis_history_id,
                error_message_sanitized=sanitize_diagnostic_text(error_message),
            )
        )
    except Exception as exc:  # pragma: no cover - defensive fail-open guard
        logger.warning("history diagnostic record failed: %s", exc)


# 顶层 status 的中文标签；"unknown" 表示没有足够证据
_SUMMARY_STATUS_LABELS = {
    "normal": "正常",
    "degraded": "部分降级",
    "failed": "失败",
    "unknown": "未知",
}
# 分析上下文输入块状态的中文文案；为前端"为什么这块是灰的"提供依据
_ANALYSIS_INPUT_STATUS_MESSAGES = {
    "missing": "未进入本次分析输入",
    "partial": "本次分析输入仅部分可用",
    "fallback": "本次分析输入使用降级数据",
    "stale": "本次分析输入使用过期数据",
    "estimated": "本次分析输入使用估算数据",
    "fetch_failed": "输入块显示抓取失败",
    "not_supported": "输入块标记为不支持",
}


def _as_dict(value: Any) -> Dict[str, Any]:
    """把任意值规整成 dict，非 dict 视为空。"""
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    """把任意值规整成 list，非 list 视为空。"""
    return value if isinstance(value, list) else []


def _component(
    key: str,
    label: str,
    status: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
) -> RunDiagnosticComponent:
    """构造一个诊断组件对象，统一清洗 details 字段（去掉 None）。"""
    clean_details = {
        key: value
        for key, value in (details or {}).items()
        if value is not None
    }
    return RunDiagnosticComponent(
        key=key,
        label=label,
        status=status,
        message=message,
        details=clean_details,
    )


def _analysis_context_overview(context_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """读取 analysis_context_pack_overview，兼容 camelCase 旧字段名。"""
    overview = context_snapshot.get("analysis_context_pack_overview")
    if not isinstance(overview, dict):
        overview = context_snapshot.get("analysisContextPackOverview")
    return overview if isinstance(overview, dict) else {}


def _analysis_input_block(
    context_snapshot: Dict[str, Any],
    block_key: str,
) -> Dict[str, Any]:
    """按 block_key 在 overview 中查找对应输入块，兼容 list 与 dict 两种结构。"""
    blocks = _analysis_context_overview(context_snapshot).get("blocks")
    if isinstance(blocks, list):
        for block in blocks:
            if isinstance(block, dict) and block.get("key") == block_key:
                return block
    if isinstance(blocks, dict):
        block = blocks.get(block_key)
        if isinstance(block, dict):
            return block
    return {}


def _analysis_input_status_message(block: Dict[str, Any]) -> Optional[str]:
    """把 analysis input 块的内部状态翻译成可读中文描述。"""
    status = str(block.get("status") or "").strip()
    if status == "available" or not status:
        return None
    return _ANALYSIS_INPUT_STATUS_MESSAGES.get(status, f"输入块状态为 {status}")


def _list_text(value: Any, *, limit: int = 5) -> List[str]:
    """抽取 value 中的字符串元素，去空、去重，按出现顺序截前 limit 个。"""
    if not isinstance(value, list):
        return []
    result: List[str] = []
    for item in value:
        text = str(item).strip() if item is not None else ""
        if text and text not in result:
            result.append(text)
    return result[:limit]


def _reconcile_daily_provider_with_analysis_input(
    component: RunDiagnosticComponent,
    context_snapshot: Dict[str, Any],
) -> RunDiagnosticComponent:
    """把 provider 成功但分析输入标记为降级的日线块降级为 degraded。

    若 provider 报告"成功"但分析上下文的日线块记录了非 available 的状态
    （如 stale / partial），前端需要看到"成功 + 降级"复合原因。
    """
    input_block = _analysis_input_block(context_snapshot, "daily_bars")
    input_message = _analysis_input_status_message(input_block)
    if not input_message or component.status not in {"ok", "degraded"}:
        return component

    details = dict(component.details or {})
    details.update(
        {
            "provider_run_status": component.status,
            "analysis_input_block": "daily_bars",
            "analysis_input_status": input_block.get("status"),
            "analysis_input_source": input_block.get("source"),
            "analysis_input_missing_reasons": _list_text(
                input_block.get("missing_reasons")
            ),
            "evidence_scope": "provider_run_vs_analysis_input",
        }
    )
    provider = details.get("provider") or "unknown"
    return _component(
        component.key,
        component.label,
        "degraded",
        f"{component.label}{provider} 成功，但{input_message}",
        details,
    )


def _provider_component(
    *,
    key: str,
    label: str,
    data_type: str,
    provider_runs: List[Dict[str, Any]],
) -> RunDiagnosticComponent:
    """基于 provider_runs 中同 data_type 的记录，汇总出组件级状态。"""
    runs = [
        run for run in provider_runs
        if isinstance(run, dict) and run.get("data_type") == data_type
    ]
    if not runs:
        return _component(key, label, "unknown", f"{label}未记录诊断信息")

    successes = [run for run in runs if run.get("success") is True]
    failures = [run for run in runs if run.get("success") is False]
    last_run = runs[-1]
    if successes:
        # 取最后一次成功作为主结果；若中间有失败则整体降级
        success_run = successes[-1]
        provider = success_run.get("provider") or "unknown"
        record_count = success_run.get("record_count")
        details = {
            "provider": provider,
            "attempts": len(runs),
            "record_count": record_count,
            "fallback_to": next(
                (run.get("fallback_to") for run in failures if run.get("fallback_to")),
                None,
            ),
        }
        details = {key: value for key, value in details.items() if value is not None}
        if failures:
            return _component(
                key,
                label,
                "degraded",
                f"{label}{provider} 成功，前置数据源失败后已继续",
                details,
            )
        return _component(
            key,
            label,
            "ok",
            f"{label}{provider} 成功",
            details,
        )

    # 全失败时把最后一次尝试的错误透出
    message = (
        last_run.get("error_message_sanitized")
        or last_run.get("error_type")
        or "所有数据源尝试失败"
    )
    return _component(
        key,
        label,
        "failed",
        f"{label}失败：{message}",
        {
            "attempts": len(runs),
            "provider": last_run.get("provider"),
            "error_type": last_run.get("error_type"),
        },
    )


def _news_component(context_snapshot: Dict[str, Any], raw_result: Dict[str, Any]) -> RunDiagnosticComponent:
    """综合分析输入块、检索结果与快照判断新闻组件的状态。

    优先使用检索结果计数（"是否拿到新闻"），其次使用输入块状态，
    最后看是否存在新闻快照作为兜底，避免对老报告误判为 unknown。
    """
    label = "新闻搜索"
    input_block = _analysis_input_block(context_snapshot, "news")
    input_message = _analysis_input_status_message(input_block)
    has_retrieval_news = "news_retrieval_content" in context_snapshot
    has_snapshot_news = has_retrieval_news or "news_content" in context_snapshot
    news_result_count = context_snapshot.get("news_result_count")
    if isinstance(news_result_count, int):
        if news_result_count > 0:
            if input_message:
                return _component(
                    "news",
                    label,
                    "degraded",
                    f"新闻检索返回 {news_result_count} 条结果，但新闻{input_message}；报告页相关资讯可能来自后续检索或历史持久化",
                    {
                        "record_count": news_result_count,
                        "analysis_input_block": "news",
                        "analysis_input_status": input_block.get("status"),
                        "analysis_input_missing_reasons": _list_text(
                            input_block.get("missing_reasons")
                        ),
                        "evidence_scope": "retrieval_vs_analysis_input",
                    },
                )
            return _component(
                "news",
                label,
                "ok",
                f"新闻检索返回 {news_result_count} 条结果",
                {"record_count": news_result_count},
            )
        return _component("news", label, "degraded", "新闻搜索无结果", {"record_count": 0})
    if input_message:
        return _component(
            "news",
            label,
            "unknown",
            f"新闻{input_message}；报告页相关资讯可能来自后续检索或历史持久化",
            {
                "analysis_input_block": "news",
                "analysis_input_status": input_block.get("status"),
                "analysis_input_missing_reasons": _list_text(
                    input_block.get("missing_reasons")
                ),
                "evidence_scope": "analysis_input_only",
            },
        )
    if has_snapshot_news and not has_retrieval_news:
        return _component("news", label, "unknown", "新闻检索未记录原始证据，可能未尝试或未启用")
    return _component("news", label, "unknown", "新闻搜索未记录诊断信息")


def _llm_component(diagnostics: Dict[str, Any], raw_result: Dict[str, Any]) -> RunDiagnosticComponent:
    """根据 LLM 运行记录或原始结果，汇总 LLM 组件状态。"""
    label = "LLM"
    runs = [
        run for run in _as_list(diagnostics.get("llm_runs"))
        if isinstance(run, dict)
    ]
    if runs:
        successes = [run for run in runs if run.get("success") is True]
        failures = [run for run in runs if run.get("success") is False]
        last_run = runs[-1]
        if successes:
            success_run = successes[-1]
            model = success_run.get("model") or raw_result.get("model_used") or "unknown"
            # 失败或显式 fallback_model 都意味着发生过降级
            status = "degraded" if failures or success_run.get("fallback_model") else "ok"
            message = f"LLM {model} 成功"
            if status == "degraded":
                message = f"LLM {model} 成功，期间发生过失败或模型切换"
            return _component(
                "llm",
                label,
                status,
                message,
                {
                    "model": model,
                    "tokens": success_run.get("tokens"),
                    "duration_ms": success_run.get("duration_ms"),
                    "fallback_model": success_run.get("fallback_model"),
                },
            )
        return _component(
            "llm",
            label,
            "failed",
            f"LLM 失败：{last_run.get('error_message_sanitized') or last_run.get('error_type') or '未知错误'}",
            {"model": last_run.get("model"), "error_type": last_run.get("error_type")},
        )

    # 没有 LLM 运行记录时尝试从 raw_result 中推断（兼容老报告）
    if raw_result:
        if raw_result.get("success") is False:
            return _component(
                "llm",
                label,
                "failed",
                f"LLM 失败：{sanitize_diagnostic_text(raw_result.get('error_message')) or '未知错误'}",
            )
        model = raw_result.get("model_used")
        if model:
            return _component("llm", label, "ok", f"LLM {model} 成功", {"model": model})
        if raw_result.get("analysis_summary"):
            return _component("llm", label, "ok", "LLM 成功，模型未记录")
    return _component("llm", label, "unknown", "LLM 未记录诊断信息")


def _notification_component(diagnostics: Dict[str, Any]) -> RunDiagnosticComponent:
    """综合多次通知结果判断通知组件状态。

    优先用成功/失败/跳过的组合渲染降级文案；全部跳过且未失败时返回
    "not_configured" 或 "skipped"，区别对待"未启用"与"主动跳过"。
    """
    label = "通知"
    runs = [
        run for run in _as_list(diagnostics.get("notification_runs"))
        if isinstance(run, dict)
    ]
    if not runs:
        return _component("notification", label, "unknown", "通知结果未记录")

    skipped = [run for run in runs if run.get("status") in {"skipped", "not_configured"}]
    successes = [run for run in runs if run.get("success") is True]
    failures = [run for run in runs if run.get("success") is False and run not in skipped]
    channels = [run.get("channel") for run in runs if run.get("channel")]
    if successes and failures:
        return _component(
            "notification",
            label,
            "degraded",
            "部分通知渠道失败，其余渠道已发送",
            {"channels": channels, "failed": [run.get("channel") for run in failures]},
        )
    if successes:
        return _component(
            "notification",
            label,
            "ok",
            "通知发送成功",
            {"channels": channels},
        )
    if skipped and not failures:
        status = "not_configured" if any(run.get("status") == "not_configured" for run in skipped) else "skipped"
        return _component(
            "notification",
            label,
            status,
            "通知未配置或本次跳过",
            {"channels": channels},
        )
    last_failure = failures[-1] if failures else runs[-1]
    return _component(
        "notification",
        label,
        "failed",
        f"通知失败：{last_failure.get('error_message_sanitized') or last_failure.get('status') or '未知错误'}",
        {"channels": channels},
    )


def _history_component(
    diagnostics: Dict[str, Any],
    report_saved: Optional[bool],
) -> RunDiagnosticComponent:
    """结合 history_runs 与外部传入的 report_saved 标志判断历史保存状态。"""
    label = "历史保存"
    runs = [
        run for run in _as_list(diagnostics.get("history_runs"))
        if isinstance(run, dict)
    ]
    if runs:
        last_run = runs[-1]
        if last_run.get("report_saved") is True:
            return _component(
                "history",
                label,
                "ok",
                "报告历史已保存",
                {"analysis_history_id": last_run.get("analysis_history_id")},
            )
        return _component(
            "history",
            label,
            "failed",
            f"报告历史保存失败：{last_run.get('error_message_sanitized') or '未知错误'}",
        )
    # 兜底：使用外部传入的报告保存标志
    if report_saved is True:
        return _component("history", label, "ok", "报告历史已保存")
    if report_saved is False:
        return _component("history", label, "failed", "报告历史保存失败")
    return _component("history", label, "unknown", "历史保存未记录诊断信息")


def build_run_diagnostic_summary(
    *,
    context_snapshot: Optional[Any] = None,
    raw_result: Optional[Any] = None,
    report_saved: Optional[bool] = None,
    query_id: Optional[str] = None,
    stock_code: Optional[str] = None,
) -> Dict[str, Any]:
    """把持久化或内存中的诊断证据折叠成用户面汇总（顶层 status 与 reason）。

    顶层状态聚合规则（优先级从高到低）：
    1. LLM 失败或历史保存失败 → failed
    2. 任一组件失败/降级 → degraded
    3. 全部 unknown 或没有核心诊断记录 → unknown
    4. 其余 → normal
    """
    snapshot = _as_dict(context_snapshot)
    raw = _as_dict(raw_result)
    diagnostics = _as_dict(snapshot.get("diagnostics"))
    provider_runs = [
        run for run in _as_list(diagnostics.get("provider_runs"))
        if isinstance(run, dict)
    ]
    llm_runs = [
        run for run in _as_list(diagnostics.get("llm_runs"))
        if isinstance(run, dict)
    ]

    daily_data_component = _provider_component(
        key="daily_data",
        label="日线数据",
        data_type="daily_data",
        provider_runs=provider_runs,
    )
    components = {
        "realtime_quote": _provider_component(
            key="realtime_quote",
            label="实时行情",
            data_type="realtime_quote",
            provider_runs=provider_runs,
        ),
        "daily_data": _reconcile_daily_provider_with_analysis_input(
            daily_data_component,
            snapshot,
        ),
        "news": _news_component(snapshot, raw),
        "llm": _llm_component(diagnostics, raw),
        "notification": _notification_component(diagnostics),
        "history": _history_component(diagnostics, report_saved),
    }

    has_evidence = bool(snapshot or raw or diagnostics or report_saved is not None)
    has_core_diagnostic_runs = bool(provider_runs or llm_runs)
    if not has_evidence or not diagnostics:
        status = "unknown"
    elif components["llm"].status == "failed" or components["history"].status == "failed":
        status = "failed"
    elif any(component.status in {"failed", "degraded"} for component in components.values()):
        status = "degraded"
    elif all(component.status == "unknown" for component in components.values()):
        status = "unknown"
    elif not has_core_diagnostic_runs:
        status = "unknown"
    else:
        status = "normal"

    # 顶层 reason 优先取失败组件文案，其次取降级文案，最后退化为状态标签
    if status == "unknown":
        reason = "旧报告或诊断证据不足，无法判断本次运行状态"
    else:
        reason = next(
            (
                component.message
                for component in components.values()
                if component.status == "failed"
            ),
            next(
                (
                    component.message
                    for component in components.values()
                    if component.status == "degraded"
                ),
                _SUMMARY_STATUS_LABELS[status],
            ),
        )

    # 三个来源（diagnostics / snapshot / raw）逐级回退找 ID，确保尽量带回
    trace_id = diagnostics.get("trace_id") or snapshot.get("trace_id") or raw.get("trace_id")
    resolved_query_id = query_id or diagnostics.get("query_id") or snapshot.get("query_id") or raw.get("query_id")
    resolved_stock_code = (
        stock_code
        or diagnostics.get("stock_code")
        or snapshot.get("stock_code")
        or raw.get("code")
        or raw.get("stock_code")
    )

    return RunDiagnosticSummary(
        trace_id=trace_id,
        task_id=diagnostics.get("task_id"),
        query_id=resolved_query_id,
        stock_code=resolved_stock_code,
        trigger_source=diagnostics.get("trigger_source") or snapshot.get("trigger_source"),
        status=status,
        status_label=_SUMMARY_STATUS_LABELS[status],
        reason=reason,
        components=components,
    ).to_dict()


def format_copyable_diagnostics(summary: Dict[str, Any]) -> str:
    """把诊断汇总格式化为可贴到工单/聊天里的纯文本。"""
    components = _as_dict(summary.get("components"))

    def _component_line(key: str) -> str:
        """把单个诊断组件渲染为“key: status - message”格式的一行文本。"""
        component = _as_dict(components.get(key))
        message = sanitize_diagnostic_text(component.get("message"), max_length=160) or "unknown"
        return f"{key}: {component.get('status', 'unknown')} - {message}"

    lines = [
        f"trace_id: {summary.get('trace_id') or 'unknown'}",
        f"query_id: {summary.get('query_id') or 'unknown'}",
        f"stock_code: {summary.get('stock_code') or 'unknown'}",
        f"trigger_source: {summary.get('trigger_source') or 'unknown'}",
        f"data_status: {summary.get('status', 'unknown')}",
        _component_line("realtime_quote"),
        _component_line("daily_data"),
        _component_line("news"),
        _component_line("llm"),
        _component_line("notification"),
        _component_line("history"),
        f"reason: {sanitize_diagnostic_text(summary.get('reason'), max_length=160) or 'unknown'}",
    ]
    return "\n".join(lines)
