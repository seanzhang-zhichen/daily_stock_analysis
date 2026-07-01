# -*- coding: utf-8 -*-
"""API tests for stock selection endpoints."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_current_user, get_database_manager
from api.v1.endpoints.stock_selection import router


def _build_test_client() -> TestClient:
    """Build a small app that only mounts stock selection endpoints."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/stock-selection")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[get_database_manager] = lambda: object()
    return TestClient(app)


class StockSelectionApiTestCase(unittest.TestCase):
    def test_run_stock_selection_returns_service_result(self) -> None:
        result_payload = {
            "strategy": "near_new_high",
            "params": {"lookback_days": 120, "min_high_position": 0.85, "recent_high_days": 15},
            "items": [
                {
                    "code": "600519",
                    "name": "贵州茅台",
                    "market": "CN",
                    "strategy": "near_new_high",
                    "latest_date": "2026-06-30",
                    "latest_close": 118.0,
                    "window_high": 120.0,
                    "window_high_date": "2026-06-26",
                    "days_since_high": 2,
                    "distance_to_high_pct": -1.6667,
                    "window_return_pct": 18.0,
                    "volatility_pct": 21.5,
                    "score": 77.0,
                    "source": "unit",
                }
            ],
            "diagnostics": {
                "total": 1,
                "processed": 1,
                "matched": 1,
                "no_data": 0,
                "insufficient_data": 0,
                "errors": 0,
                "skipped_unsupported_market": 0,
            },
            "generated_at": "2026-06-30T12:00:00",
            "target_date": None,
        }

        service_cls = MagicMock()
        service_cls.return_value.select.return_value = SimpleNamespace(to_dict=lambda: result_payload)
        with patch("api.v1.endpoints.stock_selection.StockSelectionService", service_cls):
            with _build_test_client() as client:
                response = client.post(
                    "/api/v1/stock-selection/run",
                    json={
                        "strategy": "new_high",
                        "stock_codes": ["600519"],
                        "limit": 5,
                        "lookback_days": 120,
                    },
                )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["strategy"], "near_new_high")
        self.assertEqual(payload["items"][0]["code"], "600519")
        service_cls.return_value.select.assert_called_once()
        self.assertEqual(service_cls.return_value.select.call_args.kwargs["strategy_name"], "new_high")
        self.assertEqual(service_cls.return_value.select.call_args.kwargs["stock_codes"], ["600519"])

    def test_run_stock_selection_maps_value_error_to_400(self) -> None:
        service_cls = MagicMock()
        service_cls.return_value.select.side_effect = ValueError("bad params")
        with patch("api.v1.endpoints.stock_selection.StockSelectionService", service_cls):
            with _build_test_client() as client:
                response = client.post("/api/v1/stock-selection/run", json={"strategy": "missing"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["error"], "invalid_params")


if __name__ == "__main__":
    unittest.main()
