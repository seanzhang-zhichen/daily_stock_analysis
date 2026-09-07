"""Focused regression tests for the built-in screening port."""

from __future__ import annotations

import unittest
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_config_dep, get_current_user, get_database_manager
from api.v1.endpoints.screening import router as screening_router
from src.config import Config
from src.core.config_registry import get_registered_field_keys
from src.services.screening.strategy import list_strategies
from src.services.screening.hotspot import HotspotStock, HotspotSummary, _set_summary_leaders
from src.services.screening_service import ScreeningService
from src.services import screening_service as screening_service_module
from src.storage import DatabaseManager


class TestScreeningPort(unittest.TestCase):
    def setUp(self) -> None:
        DatabaseManager._instance = None  # noqa: SLF001
        self.db = DatabaseManager(db_url="sqlite:///:memory:")

    def tearDown(self) -> None:
        self.db._engine.dispose()  # noqa: SLF001
        DatabaseManager._instance = None  # noqa: SLF001

    def test_builtin_strategies_are_discoverable(self) -> None:
        names = {item.name for item in list_strategies()}
        self.assertIn("dual_low", names)
        self.assertIn("volume_breakout", names)
        self.assertGreaterEqual(len(names), 10)

    def test_screening_is_a_platform_default_without_a_config_switch(self) -> None:
        with patch.dict("os.environ", {"SCREENING_ENABLED": "false"}):
            status = ScreeningService(Config()).status()

        self.assertTrue(status["enabled"])
        self.assertNotIn("SCREENING_ENABLED", get_registered_field_keys())

    def test_screening_history_is_isolated_by_user(self) -> None:
        payload = {
            "run_id": "screening-run-1",
            "strategy": "dual_low",
            "market": "cn",
            "candidate_count": 1,
            "candidates": [{"code": "600519"}],
        }
        self.assertEqual(self.db.save_screening_run(payload, user_id=7), 1)
        self.assertEqual(len(self.db.list_screening_runs(user_id=7)), 1)
        self.assertEqual(self.db.list_screening_runs(user_id=8), [])
        self.assertIsNone(self.db.get_screening_run("screening-run-1", user_id=8))

    def test_service_history_uses_current_user_scope(self) -> None:
        self.db.save_screening_run(
            {
                "run_id": "screening-run-2",
                "strategy": "quality_value",
                "market": "cn",
                "candidate_count": 0,
            },
            user_id=11,
        )
        service = ScreeningService(
            Config(),
            db_manager=self.db,
            user_id=12,
        )
        self.assertEqual(service.history()["runs"], [])

    def test_screening_api_exposes_strategies_and_user_history(self) -> None:
        api_db = SimpleNamespace(
            save_screening_run=lambda *_args, **_kwargs: 1,
            list_screening_runs=lambda **_kwargs: [
                {"run_id": "screening-run-api", "strategy": "dual_low", "market": "cn"}
            ],
            get_screening_run=lambda *_args, **_kwargs: None,
        )
        app = FastAPI()
        app.include_router(screening_router, prefix="/api/v1/screening")
        app.dependency_overrides[get_config_dep] = Config
        app.dependency_overrides[get_database_manager] = lambda: api_db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=21)

        with TestClient(app) as client:
            status = client.get("/api/v1/screening/status")
            strategies = client.get("/api/v1/screening/strategies")
            history = client.get("/api/v1/screening/history")

        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.json()["enabled"])
        self.assertTrue(status.json()["available"])
        self.assertGreaterEqual(strategies.json()["strategy_count"], 10)
        self.assertEqual(history.json()["runs"][0]["run_id"], "screening-run-api")

    def test_hotspot_cache_defaults_to_ten_minutes_and_expires(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SCREENING_HOTSPOT_CACHE_TTL_SEC", None)
            self.assertEqual(screening_service_module._screening_hotspot_cache_ttl_seconds(), 600.0)

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"SCREENING_DATA_DIR": temp_dir, "SCREENING_HOTSPOT_CACHE_TTL_SEC": "600"},
            clear=False,
        ):
            cached_at = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
            payload = {
                "cached_at": cached_at,
                "provider": "akshare",
                "hotspots": [{"topic": f"topic-{i}", "heat_score": 80 - i} for i in range(3)],
            }
            with open(os.path.join(temp_dir, "hotspots.json"), "w", encoding="utf-8") as handle:
                json.dump(payload, handle)

            self.assertIsNone(
                screening_service_module._load_screening_hotspot_cache(provider="akshare", top=3)
            )
            stale = screening_service_module._load_screening_hotspot_cache(
                provider="akshare", top=3, allow_stale=True
            )
            self.assertIsNotNone(stale)
            self.assertTrue(stale["stale"])
            self.assertTrue(stale["fallback_used"])

    def test_hotspot_cache_ttl_zero_disables_fresh_cache_reuse(self) -> None:
        with patch.dict(os.environ, {"SCREENING_HOTSPOT_CACHE_TTL_SEC": "0"}, clear=False):
            self.assertIsNone(screening_service_module._screening_hotspot_cache_ttl_seconds())

    def test_hotspot_summary_exposes_up_to_ten_leaders(self) -> None:
        summary = HotspotSummary(topic="AI算力")
        stocks = [
            HotspotStock(
                code=f"60000{i:02d}",
                name=f"股票{i}",
                role="核心龙头" if i == 0 else "助攻",
                hot_stock_score=100 - i,
            )
            for i in range(12)
        ]

        _set_summary_leaders(summary, stocks)

        self.assertEqual(len(summary.leader_stocks), 10)
        self.assertEqual(len(summary.leaders), 10)
        self.assertEqual(summary.leaders[-1], "股票9")


if __name__ == "__main__":
    unittest.main()
