# -*- coding: utf-8 -*-
# Derived from AlphaSift revision 9f522747caafd3c0b1ddb7e14d5cf44c8580b6cf.
# Licensed under Apache-2.0 and modified for daily_stock_analysis.
"""外部数据源调用的共享防护（超时控制与异常传播）。

为筛选器中调用的外部数据源包裹一层带超时的工作线程，使得一次抓取最长占用
``timeout_sec`` 秒；超时则抛出 :class:`SourceCallTimeout`，调用方可以快速降级
到备用源，避免单个"卡死"的源拖累整个筛选管线。

注意：Python 无法真正"中断"一个卡在原生代码里的线程，因此这里采用 daemon
线程 + join(timeout) 的最佳实践；超时后线程会随主进程退出而被回收。
"""

from __future__ import annotations

import os
from queue import Queue
import threading
from typing import Any, Callable, TypeVar

# 泛型 ``T`` 用于在 ``call_with_timeout`` 中保留原函数的返回类型
T = TypeVar("T")


class SourceCallTimeout(TimeoutError):
    """当源包装函数超过调用方侧设定的超时时间时抛出。"""


def parse_source_timeout_seconds(
    specific_env: str,
    *,
    default: float,
    fallback_env: str = "SCREENING_SOURCE_CALL_TIMEOUT_SEC",
) -> float | None:
    """解析数据源超时配置；返回 ``None`` 表示关闭超时保护。

    解析优先级：

    1. ``specific_env``；
    2. ``fallback_env``（通用兜底）；
    3. 调用方传入的 ``default``。

    同时识别 ``0`` / ``false`` / ``off`` / ``none`` / ``disabled`` 等"关闭"语义，
    把它们归一为 ``None``，让上层能够以一致方式关闭保护。
    """
    raw = os.getenv(specific_env)
    if raw is None:
        raw = os.getenv(fallback_env)
    if raw is None:
        return float(default)
    cleaned = raw.strip().lower()
    # 显式列出"关闭"语义，避免歧义
    if cleaned in {"", "0", "false", "off", "none", "disabled"}:
        return None
    timeout = float(cleaned)
    # 非正值同样视为"关闭"
    return timeout if timeout > 0 else None


def call_with_timeout(
    func: Callable[..., T],
    *args: Any,
    timeout_sec: float | None,
    label: str,
    **kwargs: Any,
) -> T:
    """用线程 + 超时等待执行 ``func``；超时即抛 :class:`SourceCallTimeout`。

    Python 无法强制中断一个已经卡在第三方库内的线程。这里把工作线程设为 daemon，
    确保 CLI 进程不会因为某个卡死的包装调用而无法正常退出，并让调用方在超时后
    立即走降级路径。

    Args:
        func: 实际执行的可调用对象。
        *args: 透传给 ``func`` 的位置参数。
        timeout_sec: 超时秒数；为 ``None`` 时跳过包装直接调用。
        label: 用于线程命名 / 异常消息的可读标签，便于排障。
        **kwargs: 透传给 ``func`` 的关键字参数。

    Returns:
        ``func`` 的返回值。

    Raises:
        SourceCallTimeout: 工作线程超过 ``timeout_sec`` 仍未结束。
        BaseException: 工作线程内抛出的任何异常会被原样向上抛出。
    """
    # 未配置超时 → 跳过包装，让上层直接同步执行
    if timeout_sec is None:
        return func(*args, **kwargs)

    # 用 Queue 把"成功结果 / 失败异常"两种情况统一从一个线程传出来
    result_queue: Queue[tuple[bool, object]] = Queue(maxsize=1)

    def run() -> None:
        """在工作线程中执行 func，把成功结果或异常放进队列传回主线程。"""
        try:
            result_queue.put((True, func(*args, **kwargs)))
        except BaseException as exc:  # noqa: BLE001 - propagate worker failures to caller.
            # 故意捕获 BaseException 以吞下 KeyboardInterrupt 等系统信号到队列
            result_queue.put((False, exc))

    # daemon=True 是关键：即使超时后线程仍卡住，主进程退出时也会被回收
    worker = threading.Thread(target=run, name=f"screening-source:{label}", daemon=True)
    worker.start()
    worker.join(float(timeout_sec))
    if worker.is_alive():
        # 线程仍然存活说明已超时；抛错让调用方降级，daemon=True 保证它不会拖死主进程
        raise SourceCallTimeout(f"{label} timed out after {float(timeout_sec):g}s")

    # 正常完成；ok=True 时 payload 是返回值，ok=False 时是异常实例
    ok, payload = result_queue.get_nowait()
    if ok:
        return payload  # type: ignore[return-value]
    # 把工作线程内捕获到的错误重新抛出，保留原始堆栈
    raise payload


