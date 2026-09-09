# -*- coding: utf-8 -*-
"""Agent 工具运行时的协作式取消（cooperative cancellation）辅助模块。

为 Agent 在执行具体工具时提供"可被外部打断"的轻量机制：通过
``contextvars`` 携带一个取消事件，并在工具内部的合适检查点调用
``check_tool_execution()`` 来安全退出，避免在 LLM 超时后工具仍在阻塞。

主要能力：
- 提供跨异步/线程上下文传递的取消事件存储（``TOOL_CANCEL_EVENT``）
- 定义工具被取消时统一抛出的异常类型（``ToolExecutionCancelled``）
- 提供查询与检查取消状态的两个便捷函数
"""

from __future__ import annotations

import contextvars
import threading
from typing import Optional


TOOL_CANCEL_EVENT: "contextvars.ContextVar[Optional[threading.Event]]" = contextvars.ContextVar(
    "tool_cancel_event", default=None
    # 当前 Agent 调用上下文中的取消事件；未设置时表示不启用取消机制。
)


class ToolExecutionCancelled(Exception):
    """工具检查点抛出的异常：表示外层 Agent 调用预算已用尽，需要立刻终止。"""


def is_tool_cancellation_requested() -> bool:
    """判断当前正在运行的 Agent 工具是否应当停止工作。

    通过 ``contextvars`` 取出当前上下文的取消事件并检测是否已被置位；
    事件为 ``None`` 时视作未启用取消，直接返回 ``False``。
    """
    event = TOOL_CANCEL_EVENT.get()
    return event is not None and event.is_set()


def check_tool_execution() -> None:
    """在工具内部的安全检查点调用：若已请求取消则抛出 ``ToolExecutionCancelled``。

    工具应在自己可控的边界（如循环迭代、I/O 前后）调用本函数，以便
    Agent 外层超时（timeout）后能尽快停止而不必强行 kill。
    """
    if is_tool_cancellation_requested():
        raise ToolExecutionCancelled("Tool execution timed out and was cancelled")
