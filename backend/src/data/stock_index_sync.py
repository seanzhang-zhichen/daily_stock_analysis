# -*- coding: utf-8 -*-
"""Seed and synchronize the generated stock index resource into the database."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from src.repositories.stock_index_repo import StockIndexRepository

logger = logging.getLogger(__name__)

STOCK_INDEX_FILENAME = "stocks.index.json"
STOCK_INDEX_RESOURCE_PATH = Path(__file__).resolve().parent / "resources" / STOCK_INDEX_FILENAME


def get_stock_index_source_path() -> Path:
    """Return the bundled stock index resource path."""
    return STOCK_INDEX_RESOURCE_PATH


def _tuple_to_entry(item: list[Any]) -> dict[str, Any] | None:
    """Convert compact list-form resource rows into named entry dictionaries."""
    if len(item) < 3:
        return None
    return {
        "canonicalCode": item[0],
        "displayCode": item[1],
        "nameZh": item[2],
        "pinyinFull": item[3] if len(item) > 3 else None,
        "pinyinAbbr": item[4] if len(item) > 4 else None,
        "aliases": item[5] if len(item) > 5 and isinstance(item[5], list) else [],
        "market": item[6] if len(item) > 6 else "CN",
        "assetType": item[7] if len(item) > 7 else "stock",
        "active": item[8] if len(item) > 8 else True,
        "popularity": item[9] if len(item) > 9 else 0,
    }


def load_stock_index_source(path: Path | None = None) -> tuple[list[dict[str, Any]], str]:
    """Load stock index entries and return them with a content hash version."""
    source_path = path or get_stock_index_source_path()
    raw = source_path.read_bytes()
    version = hashlib.sha256(raw).hexdigest()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Unexpected {STOCK_INDEX_FILENAME} payload type: {type(payload).__name__}")

    entries: list[dict[str, Any]] = []
    for item in payload:
        if isinstance(item, list):
            parsed = _tuple_to_entry(item)
        elif isinstance(item, dict):
            parsed = item
        else:
            parsed = None
        if parsed is not None:
            entries.append(parsed)
    return entries, version


def sync_stock_index_to_db(path: Path | None = None) -> int:
    """Upsert stock index resource entries into persistent storage."""
    entries, version = load_stock_index_source(path)
    return StockIndexRepository().upsert_entries(entries, version=version)


def ensure_stock_index_seeded() -> None:
    """Best-effort seed of the stock index when the database table is empty."""
    repo = StockIndexRepository()
    try:
        if repo.count() > 0:
            return
        source_path = get_stock_index_source_path()
        if not source_path.is_file():
            logger.warning("股票索引源文件不存在，跳过 DB seed: %s", source_path)
            return
        count = sync_stock_index_to_db(source_path)
        logger.info("股票索引已从源文件导入数据库: %s (%d 条)", source_path, count)
    except Exception as exc:
        logger.warning("股票索引数据库初始化失败（非致命）: %s", exc)
