#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scripts/grant_admin.py — 引导首个平台管理员 (Phase 5/6)。

用法:

    python scripts/grant_admin.py --email you@example.com
    python scripts/grant_admin.py --email you@example.com --revoke

设计:
- 直接操作 ``app_users.is_admin`` 字段, 不走 API 鉴权 (因为首个 admin 还没存在)。
- 仅本地 / 服务器命令行使用; 生产环境请通过 SSH 进入容器后跑。
- ``--revoke`` 用于撤销管理员权限。

依赖: 与正常应用启动一致, 读 ``.env`` 中的 DB 配置。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure project root on path so we can import src.*
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.storage import AppUser, DatabaseManager


def main() -> int:
    parser = argparse.ArgumentParser(description="Grant or revoke platform admin role.")
    parser.add_argument("--email", required=True, help="目标用户邮箱")
    parser.add_argument(
        "--revoke",
        action="store_true",
        help="改为撤销 admin 角色 (默认是授予)",
    )
    args = parser.parse_args()

    email = (args.email or "").strip().lower()
    if not email:
        print("error: --email 不能为空", file=sys.stderr)
        return 2

    target_admin = not args.revoke

    db_manager = DatabaseManager.get_instance()
    session = db_manager.get_session()
    try:
        user = session.query(AppUser).filter(AppUser.email == email).first()
        if user is None:
            print(f"error: 找不到用户 {email!r}", file=sys.stderr)
            return 1

        if bool(getattr(user, "is_admin", False)) == target_admin:
            state = "admin" if target_admin else "non-admin"
            print(f"ok: {email} 已经是 {state}, 无需变更")
            return 0

        user.is_admin = target_admin
        session.add(user)
        session.commit()
        state = "授予" if target_admin else "撤销"
        print(f"ok: 已{state} {email} 的平台管理员权限 (user_id={user.id})")
        return 0
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
