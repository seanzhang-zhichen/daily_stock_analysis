# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from threading import RLock
from typing import Dict, Iterable

from src.data.stock_mapping import is_meaningful_stock_name
from src.repositories.stock_index_repo import StockIndexRepository

logger = logging.getLogger(__name__)

_STOCK_INDEX_CACHE: Dict[str, str] | None = None
_STOCK_INDEX_CACHE_LOCK = RLock()


def _add_lookup_key(keys: set[str], value: str) -> None:
    candidate = str(value or "").strip()
    if not candidate:
        return
    keys.add(candidate)
    keys.add(candidate.upper())


def _build_lookup_keys(canonical_code: str, display_code: str) -> Iterable[str]:
    keys: set[str] = set()
    _add_lookup_key(keys, canonical_code)
    _add_lookup_key(keys, display_code)

    canonical_upper = str(canonical_code or "").strip().upper()
    display_upper = str(display_code or "").strip().upper()

    if "." in canonical_upper:
        base, suffix = canonical_upper.rsplit(".", 1)
        if suffix in {"SH", "SZ", "SS", "BJ"} and base.isdigit():
            _add_lookup_key(keys, base)
        elif suffix == "HK" and base.isdigit() and 1 <= len(base) <= 5:
            digits = base.zfill(5)
            _add_lookup_key(keys, digits)
            _add_lookup_key(keys, f"HK{digits}")

    for candidate in (canonical_upper, display_upper):
        if candidate.startswith("HK"):
            digits = candidate[2:]
            if digits.isdigit() and 1 <= len(digits) <= 5:
                digits = digits.zfill(5)
                _add_lookup_key(keys, digits)
                _add_lookup_key(keys, f"HK{digits}")

    return keys


def get_stock_name_index_map() -> Dict[str, str]:
    """Lazily load and cache the generated stock-name index."""
    global _STOCK_INDEX_CACHE

    if _STOCK_INDEX_CACHE is not None:
        return _STOCK_INDEX_CACHE

    with _STOCK_INDEX_CACHE_LOCK:
        if _STOCK_INDEX_CACHE is not None:
            return _STOCK_INDEX_CACHE

        try:
            repo = StockIndexRepository()
            stock_name_map: Dict[str, str] = {}
            if repo.count() > 0:
                with repo.db.get_session() as session:
                    from src.storage import StockIndexEntry

                    for row in session.query(StockIndexEntry).all():
                        if not is_meaningful_stock_name(row.name_zh, row.display_code or row.canonical_code):
                            continue
                        for key in _build_lookup_keys(row.canonical_code, row.display_code):
                            stock_name_map[key] = row.name_zh.strip()
            _STOCK_INDEX_CACHE = stock_name_map
            return _STOCK_INDEX_CACHE
        except Exception as exc:
            logger.debug("[股票名称] 读取数据库股票索引失败: %s", exc)
            _STOCK_INDEX_CACHE = {}
        return _STOCK_INDEX_CACHE


def get_index_stock_name(stock_code: str) -> str | None:
    """Resolve a stock name from the database stock index."""
    code = str(stock_code or "").strip()
    if not code:
        return None

    stock_name_map = get_stock_name_index_map()
    for key in _build_lookup_keys(code, code):
        name = stock_name_map.get(key)
        if is_meaningful_stock_name(name, code):
            return name

    return None


def _clear_stock_index_cache_for_tests() -> None:
    global _STOCK_INDEX_CACHE
    with _STOCK_INDEX_CACHE_LOCK:
        _STOCK_INDEX_CACHE = None
