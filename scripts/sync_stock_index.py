#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import backend  # noqa: E402,F401

from src.data.stock_index_sync import get_stock_index_source_path, sync_stock_index_to_db  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="同步股票搜索索引到数据库")
    parser.add_argument(
        "--source",
        type=Path,
        default=get_stock_index_source_path(),
        help="股票索引源文件路径，默认 backend/src/data/resources/stocks.index.json",
    )
    args = parser.parse_args()

    if not args.source.is_file():
        print(f"源文件不存在: {args.source}", file=sys.stderr)
        return 1

    count = sync_stock_index_to_db(args.source)
    print(f"股票索引已同步到数据库: {count} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
