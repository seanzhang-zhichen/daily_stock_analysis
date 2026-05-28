# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.storage import AppPlatformSetting, Base
from src.users.config import load_user_mode_settings
from src.users.platform_settings import (
    get_platform_setting_value,
    serialize_platform_settings,
    upsert_platform_settings,
)


class TestPlatformSettings(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_env = {
            key: os.environ.get(key)
            for key in (
                "USER_PUBLIC_REGISTRATION_ENABLED",
                "USER_INVITE_CODES",
                "PAYMENT_ENABLED",
                "ORDER_EXPIRE_MINUTES",
            )
        }
        for key in self._saved_env:
            os.environ.pop(key, None)
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.SessionLocal()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(self.engine)
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_env_fallback_when_db_setting_missing(self):
        os.environ["USER_PUBLIC_REGISTRATION_ENABLED"] = "false"
        os.environ["ORDER_EXPIRE_MINUTES"] = "45"

        settings = load_user_mode_settings(self.db)

        self.assertFalse(settings.public_registration_enabled)
        self.assertEqual(get_platform_setting_value(self.db, "ORDER_EXPIRE_MINUTES"), 45)

    def test_db_setting_overrides_env_for_user_mode(self):
        os.environ["USER_PUBLIC_REGISTRATION_ENABLED"] = "false"
        os.environ["USER_INVITE_CODES"] = "ENVONLY"
        self.db.add(AppPlatformSetting(key="USER_PUBLIC_REGISTRATION_ENABLED", value="true"))
        self.db.add(AppPlatformSetting(key="USER_INVITE_CODES", value="DB1,DB2"))
        self.db.add(AppPlatformSetting(key="USER_TERMS_VERSION", value="2026-06-01"))
        self.db.commit()

        settings = load_user_mode_settings(self.db)

        self.assertTrue(settings.public_registration_enabled)
        self.assertEqual(settings.invite_codes, ("DB1", "DB2"))
        self.assertEqual(settings.terms_version, "2026-06-01")

    def test_upsert_normalizes_and_serializes_payment_settings(self):
        result = upsert_platform_settings(
            self.db,
            [
                {"key": "PAYMENT_ENABLED", "value": True},
                {"key": "ORDER_EXPIRE_MINUTES", "value": "30"},
            ],
            admin_id=1,
        )

        by_key = {item["key"]: item for item in result}
        self.assertTrue(by_key["PAYMENT_ENABLED"]["value"])
        self.assertEqual(by_key["PAYMENT_ENABLED"]["source"], "db")
        self.assertEqual(by_key["ORDER_EXPIRE_MINUTES"]["value"], 30)
        self.assertEqual(get_platform_setting_value(self.db, "ORDER_EXPIRE_MINUTES"), 30)

    def test_invalid_platform_setting_value_rejected(self):
        with self.assertRaises(ValueError):
            upsert_platform_settings(self.db, [{"key": "ORDER_EXPIRE_MINUTES", "value": "0"}])

    def test_serialize_contains_operational_categories(self):
        items = serialize_platform_settings(self.db)
        categories = {item["category"] for item in items}

        self.assertIn("registration", categories)
        self.assertIn("risk_control", categories)
        self.assertIn("payment", categories)
        self.assertIn("compliance", categories)


if __name__ == "__main__":
    unittest.main()
