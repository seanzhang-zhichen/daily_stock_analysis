# -*- coding: utf-8 -*-
"""Tests for SUPER_ADMIN_* environment bootstrap."""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.storage import AppUser, Base
from src.users.bootstrap_admin import (
    SUPER_ADMIN_EMAIL_ENV,
    SUPER_ADMIN_PASSWORD_ENV,
    SUPER_ADMIN_SYNC_PASSWORD_ENV,
    bootstrap_super_admin_from_env,
)
from src.users.passwords import hash_password, verify_password


@contextmanager
def _super_admin_env(**values: str):
    keys = {
        SUPER_ADMIN_EMAIL_ENV,
        SUPER_ADMIN_PASSWORD_ENV,
        SUPER_ADMIN_SYNC_PASSWORD_ENV,
        "USER_TERMS_VERSION",
    }
    previous = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ.pop(key, None)
        os.environ.update(values)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class SuperAdminBootstrapTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.SessionLocal()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_creates_verified_admin_user_from_env(self) -> None:
        with _super_admin_env(
            SUPER_ADMIN_EMAIL="Boss@Example.com",
            SUPER_ADMIN_PASSWORD="secret123",
            USER_TERMS_VERSION="2099-01-01",
        ):
            result = bootstrap_super_admin_from_env(self.db)

        user = self.db.query(AppUser).filter(AppUser.email == "boss@example.com").one()
        self.assertTrue(result.created)
        self.assertTrue(result.granted_admin)
        self.assertTrue(result.password_updated)
        self.assertTrue(user.is_admin)
        self.assertEqual(user.status, "active")
        self.assertIsNotNone(user.email_verified_at)
        self.assertEqual(user.terms_version, "2099-01-01")
        self.assertTrue(verify_password("secret123", user.password_hash))

    def test_promotes_existing_user_without_overwriting_password_by_default(self) -> None:
        user = AppUser(
            email="admin@example.com",
            password_hash=hash_password("original123"),
            status="disabled",
            plan_code="free",
            email_verified_at=None,
            is_admin=False,
            deletion_requested_at=datetime.utcnow(),
        )
        self.db.add(user)
        self.db.commit()

        with _super_admin_env(
            SUPER_ADMIN_EMAIL="admin@example.com",
            SUPER_ADMIN_PASSWORD="newsecret123",
        ):
            result = bootstrap_super_admin_from_env(self.db)

        self.db.refresh(user)
        self.assertFalse(result.created)
        self.assertTrue(result.granted_admin)
        self.assertFalse(result.password_updated)
        self.assertTrue(user.is_admin)
        self.assertEqual(user.status, "active")
        self.assertIsNone(user.deletion_requested_at)
        self.assertIsNotNone(user.email_verified_at)
        self.assertTrue(verify_password("original123", user.password_hash))
        self.assertFalse(verify_password("newsecret123", user.password_hash))

    def test_sync_password_updates_existing_user_when_enabled(self) -> None:
        user = AppUser(
            email="admin@example.com",
            password_hash=hash_password("original123"),
            status="active",
            plan_code="free",
            is_admin=True,
        )
        self.db.add(user)
        self.db.commit()

        with _super_admin_env(
            SUPER_ADMIN_EMAIL="admin@example.com",
            SUPER_ADMIN_PASSWORD="newsecret123",
            SUPER_ADMIN_SYNC_PASSWORD="true",
        ):
            result = bootstrap_super_admin_from_env(self.db)

        self.db.refresh(user)
        self.assertFalse(result.created)
        self.assertFalse(result.granted_admin)
        self.assertTrue(result.password_updated)
        self.assertTrue(verify_password("newsecret123", user.password_hash))

    def test_missing_password_skips_creating_new_user(self) -> None:
        with _super_admin_env(SUPER_ADMIN_EMAIL="missing@example.com"):
            with self.assertLogs("src.users.bootstrap_admin", level="WARNING"):
                result = bootstrap_super_admin_from_env(self.db)

        self.assertEqual(result.skipped_reason, "invalid_password")
        self.assertIsNone(
            self.db.query(AppUser).filter(AppUser.email == "missing@example.com").first()
        )


if __name__ == "__main__":
    unittest.main()
