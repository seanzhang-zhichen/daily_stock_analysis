# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import unicodedata
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.storage import DatabaseManager, StockIndexEntry, StockIndexMeta

logger = logging.getLogger(__name__)

STOCK_INDEX_META_KEY = "default"


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


def _match_score(query: str, entry: StockIndexEntry) -> tuple[int, str]:
    q = normalize_stock_query(query)
    canonical = normalize_stock_query(entry.canonical_code)
    display = normalize_stock_query(entry.display_code)
    name = normalize_stock_query(entry.name_zh)
    pinyin_full = normalize_stock_query(entry.pinyin_full or "")
    pinyin_abbr = normalize_stock_query(entry.pinyin_abbr or "")
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
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

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

        return int(self.db._run_write_transaction("stock_index_sync", write))

    def search(self, query: str, *, limit: int = 20, active_only: bool = True) -> list[dict[str, Any]]:
        normalized = normalize_stock_query(query)
        if not normalized:
            return []
        limit = max(1, min(int(limit or 20), 50))
        like_prefix = f"{normalized}%"
        like_contains = f"%{normalized}%"

        with self.db.get_session() as session:
            conditions = [
                func.lower(StockIndexEntry.canonical_code).like(like_prefix),
                func.lower(StockIndexEntry.display_code).like(like_prefix),
                func.lower(StockIndexEntry.name_zh).like(like_contains),
                func.lower(StockIndexEntry.pinyin_full).like(like_contains),
                func.lower(StockIndexEntry.pinyin_abbr).like(like_prefix),
                func.lower(StockIndexEntry.aliases_text).like(like_contains),
            ]
            stmt = select(StockIndexEntry).where(or_(*conditions))
            if active_only:
                stmt = stmt.where(StockIndexEntry.active.is_(True))
            stmt = stmt.limit(limit * 4)
            rows = list(session.execute(stmt).scalars().all())

        ranked: list[tuple[int, int, StockIndexEntry, str]] = []
        for row in rows:
            score, field = _match_score(normalized, row)
            if score > 0:
                ranked.append((score, int(row.popularity or 0), row, field))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)

        return [
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
