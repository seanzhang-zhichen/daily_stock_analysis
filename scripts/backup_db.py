#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scripts/backup_db.py — SQLite 数据库备份 (Phase 6)。

职责:

1. 使用 SQLite 在线备份 API (``sqlite3.Connection.backup``) 对运行中的数据库
   做热备份, 不影响写入, 无需停服。
2. 备份文件命名格式: ``stock_analysis_YYYYMMDD_HHMMSS.db``；可选 gzip 压缩。
3. 保留最近 N 份备份 (默认 7 份), 自动清理过旧备份。
4. 云存储上传钩子: 设置 ``BACKUP_UPLOAD_SCRIPT`` 环境变量后, 备份完成时调用
   外部脚本 (如 ``rclone copy <file> oss:dsa-backups/``), 保持本脚本轻量。

使用示例::

    # 默认参数 (dry-run, 查看将备份哪些文件)
    python scripts/backup_db.py --dry-run

    # 真实备份到指定目录, 保留 14 份
    python scripts/backup_db.py --backup-dir /var/backups/dsa --retain 14

    # 备份并 gzip 压缩
    python scripts/backup_db.py --compress

定时任务示例 (crontab UTC+8 每日 03:00)::

    0 3 * * * cd /opt/dsa && /opt/dsa/venv/bin/python scripts/backup_db.py \
        --backup-dir /var/backups/dsa --retain 7 --compress >> /var/log/dsa_backup.log 2>&1
"""

from __future__ import annotations

import argparse
import gzip
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("backup_db")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_DEFAULT_DB_PATH = "./data/stock_analysis.db"
_DEFAULT_BACKUP_DIR = "./data/backups"
_DEFAULT_RETAIN = 7


def _resolve_db_path(cli_path: str | None) -> Path:
    raw = cli_path or os.getenv("DATABASE_PATH") or _DEFAULT_DB_PATH
    return Path(raw).expanduser().resolve()


def _resolve_backup_dir(cli_dir: str | None) -> Path:
    raw = cli_dir or os.getenv("BACKUP_DIR") or _DEFAULT_BACKUP_DIR
    p = Path(raw).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _backup_sqlite(src: Path, dst: Path) -> None:
    """使用 SQLite 在线备份 API 备份数据库。"""
    src_conn = sqlite3.connect(str(src))
    dst_conn = sqlite3.connect(str(dst))
    try:
        src_conn.backup(dst_conn, pages=256)
        logger.info("SQLite backup completed: %s -> %s", src, dst)
    finally:
        dst_conn.close()
        src_conn.close()


def _compress(src: Path) -> Path:
    """gzip 压缩备份文件, 返回压缩后路径。"""
    gz_path = src.with_suffix(src.suffix + ".gz")
    with open(src, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    src.unlink()
    logger.info("Compressed to %s", gz_path)
    return gz_path


def _prune_old_backups(backup_dir: Path, stem_prefix: str, retain: int) -> list[Path]:
    """删除超出保留数量的旧备份 (按文件名字典序排序, 保留最新 N 份)。"""
    pattern = f"{stem_prefix}_*.db*"
    existing = sorted(backup_dir.glob(pattern))
    to_delete = existing[: max(0, len(existing) - retain)]
    for f in to_delete:
        f.unlink()
        logger.info("Pruned old backup: %s", f)
    return to_delete


def _upload_hook(backup_file: Path) -> None:
    """调用外部上传脚本 (BACKUP_UPLOAD_SCRIPT 环境变量)。

    脚本以备份文件绝对路径作为第一个参数调用, 示例::

        BACKUP_UPLOAD_SCRIPT="rclone copy"
        # 等价于: rclone copy /var/backups/dsa/stock_analysis_20260519_030000.db.gz oss:dsa-backups/
    """
    upload_script = (os.getenv("BACKUP_UPLOAD_SCRIPT") or "").strip()
    if not upload_script:
        return
    cmd = upload_script.split() + [str(backup_file)]
    logger.info("Running upload hook: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        logger.error("Upload hook failed (rc=%d): %s", result.returncode, result.stderr)
    else:
        logger.info("Upload hook succeeded: %s", result.stdout.strip() or "(no output)")


def run_backup(
    db_path: Path,
    backup_dir: Path,
    retain: int = _DEFAULT_RETAIN,
    compress: bool = False,
    dry_run: bool = False,
) -> Path | None:
    """执行一次备份, 返回备份文件路径 (dry-run 时返回 None)。"""
    if not db_path.exists():
        logger.error("Database not found: %s", db_path)
        sys.exit(1)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = db_path.stem  # e.g. "stock_analysis"
    backup_name = f"{stem}_{ts}.db"
    backup_path = backup_dir / backup_name

    logger.info(
        "Backup start | db=%s | dest=%s | retain=%d | compress=%s | dry_run=%s",
        db_path, backup_path, retain, compress, dry_run,
    )

    if dry_run:
        logger.info("[dry-run] Would create backup at %s", backup_path)
        existing = sorted(backup_dir.glob(f"{stem}_*.db*"))
        logger.info("[dry-run] Current backups (%d): %s", len(existing), [f.name for f in existing])
        to_prune = existing[: max(0, len(existing) + 1 - retain)]
        if to_prune:
            logger.info("[dry-run] Would prune: %s", [f.name for f in to_prune])
        return None

    _backup_sqlite(db_path, backup_path)

    if compress:
        backup_path = _compress(backup_path)

    _prune_old_backups(backup_dir, stem, retain)
    _upload_hook(backup_path)

    size_kb = backup_path.stat().st_size // 1024
    logger.info("Backup done | file=%s | size=%d KB", backup_path.name, size_kb)
    return backup_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="DSA SQLite 数据库备份工具 (Phase 6)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help=f"数据库文件路径 (默认: DATABASE_PATH 环境变量 或 {_DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--backup-dir",
        default=None,
        help=f"备份存放目录 (默认: BACKUP_DIR 环境变量 或 {_DEFAULT_BACKUP_DIR})",
    )
    parser.add_argument(
        "--retain",
        type=int,
        default=int(os.getenv("BACKUP_RETAIN", str(_DEFAULT_RETAIN))),
        help="保留最近 N 份备份 (默认: 7)",
    )
    parser.add_argument(
        "--compress",
        action="store_true",
        default=os.getenv("BACKUP_COMPRESS", "").lower() in ("1", "true", "yes"),
        help="备份后 gzip 压缩",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="不实际创建文件, 仅输出将要执行的操作",
    )
    args = parser.parse_args()

    db_path = _resolve_db_path(args.db_path)
    backup_dir = _resolve_backup_dir(args.backup_dir)

    run_backup(
        db_path=db_path,
        backup_dir=backup_dir,
        retain=args.retain,
        compress=args.compress,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
