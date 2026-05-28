# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from time import monotonic
from typing import Any, Iterable, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.storage import DatabaseManager, StockIndexEntry, StockIndexMeta

logger = logging.getLogger(__name__)

STOCK_INDEX_META_KEY = "default"
_SEARCH_CACHE_TTL_SECONDS = 300.0
_SEARCH_RESULT_CACHE_MAX_SIZE = 512


@dataclass(frozen=True)
class _CachedStockIndexEntry:
    canonical_code: str
    display_code: str
    name_zh: str
    pinyin_full: str
    pinyin_abbr: str
    aliases: tuple[str, ...]
    aliases_text: str
    market: str
    active: bool
    popularity: int
    canonical_norm: str
    display_norm: str
    name_norm: str
    pinyin_full_norm: str
    pinyin_abbr_norm: str
    aliases_norm: tuple[str, ...]
    aliases_text_norm: str


def normalize_stock_query(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().lower()


def _safe_aliases(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def _entry_normalized_value(entry: Any, attr: str, normalized_attr: str) -> str:
    value = getattr(entry, normalized_attr, None)
    if value is not None:
        return str(value)
    return normalize_stock_query(getattr(entry, attr, "") or "")


def _match_score(query: str, entry: Any) -> tuple[int, str]:
    q = normalize_stock_query(query)
    canonical = _entry_normalized_value(entry, "canonical_code", "canonical_norm")
    display = _entry_normalized_value(entry, "display_code", "display_norm")
    name = _entry_normalized_value(entry, "name_zh", "name_norm")
    pinyin_full = _entry_normalized_value(entry, "pinyin_full", "pinyin_full_norm")
    pinyin_abbr = _entry_normalized_value(entry, "pinyin_abbr", "pinyin_abbr_norm")
    aliases = getattr(entry, "aliases_norm", None)
    if aliases is None:
        aliases = [normalize_stock_query(alias) for alias in _safe_aliases(entry.aliases)]

    if q == canonical:
        return 100, "code"
    if q == display:
        return 99, "code"
    if q == name:
        return 98, "name"
    if any(q == alias for alias in aliases):
        return 97, "alias"
    if q == pinyin_abbr:
        return 96, "pinyin"

    score = 0
    field = "name"
    candidates = (
        (display.startswith(q), 80, "code"),
        (name.startswith(q), 79, "name"),
        (pinyin_abbr.startswith(q), 78, "pinyin"),
        (any(alias.startswith(q) for alias in aliases), 77, "alias"),
        (display.find(q) >= 0, 60, "code"),
        (name.find(q) >= 0, 59, "name"),
        (pinyin_full.find(q) >= 0, 58, "pinyin"),
        (any(alias.find(q) >= 0 for alias in aliases), 57, "alias"),
    )
    for matched, candidate_score, candidate_field in candidates:
        if matched and candidate_score > score:
            score = candidate_score
            field = candidate_field
    return score, field


def _match_type(score: int) -> str:
    if score >= 90:
        return "exact"
    if score >= 70:
        return "prefix"
    if score >= 50:
        return "contains"
    return "fuzzy"


class StockIndexRepository:
    _search_cache_lock = RLock()
    _search_cache_db_url: str | None = None
    _search_cache_loaded_at = 0.0
    _search_cache_entries: tuple[_CachedStockIndexEntry, ...] | None = None
    _search_result_cache: dict[tuple[str, int, bool], list[dict[str, Any]]] = {}

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    @classmethod
    def clear_search_cache(cls) -> None:
        with cls._search_cache_lock:
            cls._search_cache_db_url = None
            cls._search_cache_loaded_at = 0.0
            cls._search_cache_entries = None
            cls._search_result_cache = {}

    def _search_cache_namespace(self) -> str:
        return str(getattr(self.db, "_db_url", ""))

    def _load_search_entries(self) -> tuple[_CachedStockIndexEntry, ...]:
        stmt = select(
            StockIndexEntry.canonical_code,
            StockIndexEntry.display_code,
            StockIndexEntry.name_zh,
            StockIndexEntry.pinyin_full,
            StockIndexEntry.pinyin_abbr,
            StockIndexEntry.aliases,
            StockIndexEntry.aliases_text,
            StockIndexEntry.market,
            StockIndexEntry.active,
            StockIndexEntry.popularity,
        )
        with self.db.get_session() as session:
            rows = session.execute(stmt).all()

        entries: list[_CachedStockIndexEntry] = []
        for row in rows:
            canonical_code = str(row.canonical_code or "")
            display_code = str(row.display_code or "")
            name_zh = str(row.name_zh or "")
            pinyin_full = str(row.pinyin_full or "")
            pinyin_abbr = str(row.pinyin_abbr or "")
            aliases = tuple(_safe_aliases(row.aliases))
            aliases_text = str(row.aliases_text or "")
            aliases_norm = tuple(normalize_stock_query(alias) for alias in aliases)
            entries.append(
                _CachedStockIndexEntry(
                    canonical_code=canonical_code,
                    display_code=display_code,
                    name_zh=name_zh,
                    pinyin_full=pinyin_full,
                    pinyin_abbr=pinyin_abbr,
                    aliases=aliases,
                    aliases_text=aliases_text,
                    market=str(row.market or "CN"),
                    active=bool(row.active),
                    popularity=int(row.popularity or 0),
                    canonical_norm=normalize_stock_query(canonical_code),
                    display_norm=normalize_stock_query(display_code),
                    name_norm=normalize_stock_query(name_zh),
                    pinyin_full_norm=normalize_stock_query(pinyin_full),
                    pinyin_abbr_norm=normalize_stock_query(pinyin_abbr),
                    aliases_norm=aliases_norm,
                    aliases_text_norm=normalize_stock_query(aliases_text),
                )
            )
        return tuple(entries)

    def _get_search_entries(self) -> tuple[_CachedStockIndexEntry, ...]:
        namespace = self._search_cache_namespace()
        now = monotonic()
        cls = type(self)
        with cls._search_cache_lock:
            if (
                cls._search_cache_entries is not None
                and cls._search_cache_db_url == namespace
                and now - cls._search_cache_loaded_at < _SEARCH_CACHE_TTL_SECONDS
            ):
                return cls._search_cache_entries
            entries = self._load_search_entries()
            cls._search_cache_db_url = namespace
            cls._search_cache_loaded_at = now
            cls._search_cache_entries = entries
            cls._search_result_cache = {}
            return entries

    def preload_search_cache(self) -> int:
        return len(self._get_search_entries())

    def count(self) -> int:
        with self.db.get_session() as session:
            return int(session.execute(select(func.count()).select_from(StockIndexEntry)).scalar() or 0)

    def get_meta(self) -> StockIndexMeta | None:
        with self.db.get_session() as session:
            return session.get(StockIndexMeta, STOCK_INDEX_META_KEY)

    def upsert_entries(self, entries: Iterable[dict[str, Any]], *, version: str) -> int:
        prepared = list(entries)

        def write(session: Session) -> int:
            existing_codes = set(session.execute(select(StockIndexEntry.canonical_code)).scalars().all())
            incoming_codes: set[str] = set()
            now = datetime.now()

            for item in prepared:
                canonical_code = str(item.get("canonicalCode") or "").strip()
                display_code = str(item.get("displayCode") or canonical_code).strip()
                name_zh = str(item.get("nameZh") or "").strip()
                if not canonical_code or not display_code or not name_zh:
                    continue

                aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
                aliases_json = json.dumps(aliases, ensure_ascii=False, separators=(",", ":"))
                aliases_text = " ".join(str(alias) for alias in aliases if str(alias or "").strip())
                row = session.get(StockIndexEntry, canonical_code)
                if row is None:
                    row = StockIndexEntry(canonical_code=canonical_code)
                    session.add(row)

                row.display_code = display_code
                row.name_zh = name_zh
                row.pinyin_full = item.get("pinyinFull")
                row.pinyin_abbr = item.get("pinyinAbbr")
                row.aliases = aliases_json
                row.aliases_text = aliases_text
                row.market = str(item.get("market") or "CN")
                row.asset_type = str(item.get("assetType") or "stock")
                row.active = bool(item.get("active", True))
                row.popularity = int(item.get("popularity") or 0)
                row.updated_at = now
                incoming_codes.add(canonical_code)

            stale_codes = existing_codes - incoming_codes
            if stale_codes:
                session.query(StockIndexEntry).filter(StockIndexEntry.canonical_code.in_(stale_codes)).delete(
                    synchronize_session=False
                )

            meta = session.get(StockIndexMeta, STOCK_INDEX_META_KEY)
            if meta is None:
                meta = StockIndexMeta(key=STOCK_INDEX_META_KEY)
                session.add(meta)
            meta.version = version
            meta.total = len(incoming_codes)
            meta.updated_at = now
            return len(incoming_codes)

        count = int(self.db._run_write_transaction("stock_index_sync", write))
        self.clear_search_cache()
        return count

    def search(self, query: str, *, limit: int = 20, active_only: bool = True) -> list[dict[str, Any]]:
        normalized = normalize_stock_query(query)
        if not normalized:
            return []
        limit = max(1, min(int(limit or 20), 50))
        entries = self._get_search_entries()
        cache_key = (normalized, limit, bool(active_only))
        cls = type(self)
        with cls._search_cache_lock:
            cached = cls._search_result_cache.get(cache_key)
            if cached is not None:
                return [dict(item) for item in cached]

        ranked: list[tuple[int, int, _CachedStockIndexEntry, str]] = []
        for row in entries:
            if active_only and not row.active:
                continue
            score, field = _match_score(normalized, row)
            if score > 0:
                ranked.append((score, int(row.popularity or 0), row, field))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)

        result = [
            {
                "canonicalCode": row.canonical_code,
                "displayCode": row.display_code,
                "nameZh": row.name_zh,
                "market": row.market,
                "matchType": _match_type(score),
                "matchField": field,
                "score": score,
            }
            for score, _, row, field in ranked[:limit]
        ]
        with cls._search_cache_lock:
            if len(cls._search_result_cache) >= _SEARCH_RESULT_CACHE_MAX_SIZE:
                cls._search_result_cache = {}
            cls._search_result_cache[cache_key] = [dict(item) for item in result]
        return result

    def get_name_by_code(self, stock_code: str) -> str | None:
        query = normalize_stock_query(stock_code)
        if not query:
            return None
        keys = {query, query.upper()}
        if "." in query:
            base, suffix = query.rsplit(".", 1)
            if suffix.upper() in {"SH", "SZ", "SS", "BJ"} and base.isdigit():
                keys.add(base)
            if suffix.upper() == "HK" and base.isdigit() and 1 <= len(base) <= 5:
                digits = base.zfill(5)
                keys.update({digits, f"HK{digits}"})
        if query.upper().startswith("HK"):
            digits = query[2:]
            if digits.isdigit() and 1 <= len(digits) <= 5:
                digits = digits.zfill(5)
                keys.update({digits, f"HK{digits}"})

        with self.db.get_session() as session:
            row = session.execute(
                select(StockIndexEntry)
                .where(
                    or_(
                        func.upper(StockIndexEntry.canonical_code).in_({key.upper() for key in keys}),
                        func.upper(StockIndexEntry.display_code).in_({key.upper() for key in keys}),
                    )
                )
                .limit(1)
            ).scalar_one_or_none()
        return row.name_zh if row is not None else None
