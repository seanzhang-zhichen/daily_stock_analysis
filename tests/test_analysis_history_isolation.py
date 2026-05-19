# -*- coding: utf-8 -*-
"""analysis_history 在 To C 模式下的 user_id 隔离测试。

确保:
- 写入时 user_id 落库
- 读取时 user_id 过滤生效, 不会泄露其它用户记录
- 删除时不能跨用户误删
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import create_engine

from src.repositories.analysis_repo import AnalysisRepository
from src.storage import AnalysisHistory, Base, DatabaseManager


def _make_result(code: str, name: str = "Test") -> SimpleNamespace:
    return SimpleNamespace(
        code=code,
        name=name,
        sentiment_score=70,
        operation_advice="hold",
        trend_prediction="neutral",
        analysis_summary="summary",
        ideal_buy=None,
        secondary_buy=None,
        stop_loss=None,
        take_profit=None,
    )


class TestAnalysisHistoryIsolation(unittest.TestCase):
    def setUp(self) -> None:
        # 直接构造一个独立的 DatabaseManager 单例, 不污染全局 config。
        DatabaseManager._instance = None  # noqa: SLF001 - reset between tests
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        # 用 monkeypatch 让 get_session 走我们的内存 engine
        from sqlalchemy.orm import sessionmaker

        self._SessionLocal = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )

        # 构造一个轻量 DatabaseManager 替身, 只暴露真实方法用到的接口
        self.db_manager = DatabaseManager.__new__(DatabaseManager)
        self.db_manager.engine = self.engine

        from contextlib import contextmanager

        @contextmanager
        def _scope():
            session = self._SessionLocal()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        # 真实 DatabaseManager 提供 get_session / session_scope 两个上下文
        self.db_manager.get_session = _scope  # type: ignore[assignment]
        self.db_manager.session_scope = _scope  # type: ignore[assignment]
        # _run_write_transaction 在真实实现里也走 session_scope, 这里直接走 session
        def _run_write(_name, fn):
            with _scope() as session:
                return fn(session)

        self.db_manager._run_write_transaction = _run_write  # type: ignore[assignment]

        # repo 用上面的 stub
        self.repo = AnalysisRepository(db_manager=self.db_manager)

    def tearDown(self) -> None:
        self.engine.dispose()
        DatabaseManager._instance = None  # noqa: SLF001

    # ------------------------------------------------------------------
    # 写入与读取的 user_id 过滤
    # ------------------------------------------------------------------
    def test_save_persists_user_id(self):
        self.repo.save(_make_result("000001"), query_id="q1", report_type="single", user_id=42)
        with self._SessionLocal() as s:
            rows = s.query(AnalysisHistory).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].user_id, 42)

    def test_list_filters_by_user_id(self):
        self.repo.save(_make_result("000001"), query_id="q-u1", report_type="single", user_id=1)
        self.repo.save(_make_result("000002"), query_id="q-u2", report_type="single", user_id=2)
        self.repo.save(_make_result("000003"), query_id="q-legacy", report_type="single", user_id=None)

        u1_rows = self.repo.get_list(user_id=1)
        u2_rows = self.repo.get_list(user_id=2)
        all_rows = self.repo.get_list()

        self.assertEqual([r.code for r in u1_rows], ["000001"])
        self.assertEqual([r.code for r in u2_rows], ["000002"])
        # 仓储层未传 user_id 时返回全部, 仅供后台维护/测试路径使用
        self.assertEqual({r.code for r in all_rows}, {"000001", "000002", "000003"})

    def test_get_by_query_id_filters_by_user(self):
        self.repo.save(_make_result("X"), query_id="shared", report_type="single", user_id=1)
        self.repo.save(_make_result("Y"), query_id="shared", report_type="single", user_id=2)

        u1_view = self.repo.get_by_query_id("shared", user_id=1)
        u2_view = self.repo.get_by_query_id("shared", user_id=2)
        self.assertIsNotNone(u1_view)
        self.assertIsNotNone(u2_view)
        self.assertEqual(u1_view.code, "X")
        self.assertEqual(u2_view.code, "Y")
        # 不存在的 user 拿不到
        self.assertIsNone(self.repo.get_by_query_id("shared", user_id=999))

    def test_get_paginated_filters_by_user(self):
        for i in range(3):
            self.repo.save(_make_result(f"A{i}"), query_id=f"q-a{i}", report_type="single", user_id=1)
        for i in range(2):
            self.repo.save(_make_result(f"B{i}"), query_id=f"q-b{i}", report_type="single", user_id=2)

        rows, total = self.db_manager.get_analysis_history_paginated.__call__(
            user_id=1,
            limit=10,
        ) if hasattr(self.db_manager, "get_analysis_history_paginated") else (None, None)
        # DatabaseManager 替身没有原方法, 直接调真实方法做行为校验
        from src.storage import DatabaseManager as RealDM

        # 临时把真实方法绑到 stub 上
        self.db_manager.get_analysis_history_paginated = RealDM.get_analysis_history_paginated.__get__(
            self.db_manager
        )
        rows1, total1 = self.db_manager.get_analysis_history_paginated(user_id=1, limit=10)
        rows2, total2 = self.db_manager.get_analysis_history_paginated(user_id=2, limit=10)
        self.assertEqual(total1, 3)
        self.assertEqual(total2, 2)
        self.assertTrue(all(r.user_id == 1 for r in rows1))
        self.assertTrue(all(r.user_id == 2 for r in rows2))

    def test_delete_does_not_cross_users(self):
        self.repo.save(_make_result("A"), query_id="q-a", report_type="single", user_id=1)
        self.repo.save(_make_result("B"), query_id="q-b", report_type="single", user_id=2)

        from src.storage import DatabaseManager as RealDM

        # 重新绑定 delete + get_by_id 方法
        self.db_manager.delete_analysis_history_records = RealDM.delete_analysis_history_records.__get__(
            self.db_manager
        )
        self.db_manager.get_analysis_history_by_id = RealDM.get_analysis_history_by_id.__get__(
            self.db_manager
        )

        with self._SessionLocal() as s:
            row_a = s.query(AnalysisHistory).filter_by(code="A").first()
            row_b = s.query(AnalysisHistory).filter_by(code="B").first()
            id_a, id_b = row_a.id, row_b.id

        # user 1 尝试删除 user 2 的记录 - 应该被拒绝
        deleted = self.db_manager.delete_analysis_history_records([id_b], user_id=1)
        self.assertEqual(deleted, 0)
        with self._SessionLocal() as s:
            still_b = s.query(AnalysisHistory).filter_by(id=id_b).first()
        self.assertIsNotNone(still_b)

        # user 1 删除自己的 A
        deleted = self.db_manager.delete_analysis_history_records([id_a], user_id=1)
        self.assertEqual(deleted, 1)

        # get_by_id 也应做 user 过滤
        self.assertIsNone(self.db_manager.get_analysis_history_by_id(id_b, user_id=1))
        self.assertIsNotNone(self.db_manager.get_analysis_history_by_id(id_b, user_id=2))


if __name__ == "__main__":
    unittest.main()
