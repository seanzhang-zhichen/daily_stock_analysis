#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Grant or revoke the research-operator role for a registered user.

Usage:

    python scripts/grant_research_operator.py --email author@example.com
    python scripts/grant_research_operator.py --email author@example.com --revoke

This role lets a normal To C account write and publish research reports from
the user-facing research studio. It does not grant platform admin access.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
import backend  # noqa: E402,F401

from src.storage import AppUser, DatabaseManager


def main() -> int:
    parser = argparse.ArgumentParser(description="Grant or revoke research-operator role.")
    parser.add_argument("--email", required=True, help="目标用户邮箱")
    parser.add_argument("--revoke", action="store_true", help="撤销研报运营身份")
    args = parser.parse_args()

    email = (args.email or "").strip().lower()
    if not email:
        print("error: --email 不能为空", file=sys.stderr)
        return 2

    target_enabled = not args.revoke
    db_manager = DatabaseManager.get_instance()
    session = db_manager.get_session()
    try:
        user = session.query(AppUser).filter(AppUser.email == email).first()
        if user is None:
            print(f"error: 找不到用户 {email!r}", file=sys.stderr)
            return 1

        if bool(getattr(user, "is_research_operator", False)) == target_enabled:
            state = "research-operator" if target_enabled else "non-research-operator"
            print(f"ok: {email} 已经是 {state}, 无需变更")
            return 0

        user.is_research_operator = target_enabled
        session.add(user)
        session.commit()
        state = "授予" if target_enabled else "撤销"
        print(f"ok: 已{state} {email} 的研报运营身份 (user_id={user.id})")
        return 0
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
