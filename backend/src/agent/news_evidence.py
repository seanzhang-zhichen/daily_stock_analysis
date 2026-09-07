"""Runtime accumulator for news evidence consumed by Agent tools."""

from __future__ import annotations

import threading
from contextvars import ContextVar, Token
from typing import Optional


class NewsEvidenceAccumulator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = 0

    def record(self, count: int) -> None:
        try:
            value = max(0, int(count))
        except (TypeError, ValueError):
            value = 0
        with self._lock:
            self._total += value

    @property
    def total(self) -> int:
        with self._lock:
            return self._total

    def resolve(self, *, search_available: bool) -> Optional[int]:
        return self.total if search_available else None


_CURRENT: ContextVar[Optional[NewsEvidenceAccumulator]] = ContextVar(
    "news_evidence_accumulator", default=None
)


def activate_news_evidence_scope() -> Token:
    return _CURRENT.set(NewsEvidenceAccumulator())


def get_current_news_evidence() -> Optional[NewsEvidenceAccumulator]:
    return _CURRENT.get()


def reset_news_evidence_scope(token: Optional[Token]) -> None:
    if token is not None:
        _CURRENT.reset(token)


def record_news_evidence(count: int) -> None:
    accumulator = _CURRENT.get()
    if accumulator is not None:
        accumulator.record(count)


__all__ = [
    "activate_news_evidence_scope",
    "get_current_news_evidence",
    "reset_news_evidence_scope",
    "record_news_evidence",
]
