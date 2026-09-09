"""Agent 工具消费新闻证据（news evidence）时使用的运行时累加器。"""

from __future__ import annotations

import threading
from contextvars import ContextVar, Token
from typing import Optional


class NewsEvidenceAccumulator:
    """线程安全的新闻证据计数累加器。

    通过 ContextVar 绑定到当前执行上下文，供 Agent 工具在运行期间
    记录已消费的新闻证据条数。
    """

    def __init__(self) -> None:
        """初始化线程安全的累加器：锁 + 计数归零。"""
        self._lock = threading.Lock()
        self._total = 0

    def record(self, count: int) -> None:
        """累加新闻证据条数，非法输入按 0 处理。"""
        try:
            value = max(0, int(count))
        except (TypeError, ValueError):
            value = 0
        with self._lock:
            self._total += value

    @property
    def total(self) -> int:
        """返回当前累计条数。"""
        with self._lock:
            return self._total

    def resolve(self, *, search_available: bool) -> Optional[int]:
        """新闻搜索可用时返回累计条数，否则返回 None。"""
        return self.total if search_available else None


# 绑定到当前执行上下文的累加器，支持异步/并发场景下的隔离
_CURRENT: ContextVar[Optional[NewsEvidenceAccumulator]] = ContextVar(
    "news_evidence_accumulator", default=None
)


def activate_news_evidence_scope() -> Token:
    """开启一个新的新闻证据作用域，返回用于恢复的 Token。"""
    return _CURRENT.set(NewsEvidenceAccumulator())


def get_current_news_evidence() -> Optional[NewsEvidenceAccumulator]:
    """获取当前上下文中的新闻证据累加器，不存在时返回 None。"""
    return _CURRENT.get()


def reset_news_evidence_scope(token: Optional[Token]) -> None:
    """依据 Token 恢复新闻证据作用域。"""
    if token is not None:
        _CURRENT.reset(token)


def record_news_evidence(count: int) -> None:
    """在当前作用域内记录新闻证据条数，作用域不存在时静默忽略。"""
    accumulator = _CURRENT.get()
    if accumulator is not None:
        accumulator.record(count)


__all__ = [
    "activate_news_evidence_scope",
    "get_current_news_evidence",
    "reset_news_evidence_scope",
    "record_news_evidence",
]
