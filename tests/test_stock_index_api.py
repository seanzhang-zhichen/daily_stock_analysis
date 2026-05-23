# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.app import create_app


class TestStockIndexApi(unittest.TestCase):
    def test_search_is_public_and_returns_repository_results(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("src.data.stock_index_sync.ensure_stock_index_seeded"):
                app = create_app(static_dir=Path(temp_dir))
                with patch("api.v1.endpoints.stocks.StockIndexRepository") as repo_cls:
                    repo_cls.return_value.search.return_value = [
                        {
                            "canonicalCode": "600519.SH",
                            "displayCode": "600519",
                            "nameZh": "贵州茅台",
                            "market": "CN",
                            "matchType": "prefix",
                            "matchField": "code",
                            "score": 80,
                        }
                    ]
                    with TestClient(app) as client:
                        response = client.get("/api/v1/stocks/search?q=600&limit=5")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["canonicalCode"], "600519.SH")
        repo_cls.return_value.search.assert_called_once_with("600", limit=5)

    def test_search_rate_limit_returns_429(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("src.data.stock_index_sync.ensure_stock_index_seeded"):
                app = create_app(static_dir=Path(temp_dir))
                with patch("api.v1.endpoints.stocks._check_search_rate_limit", return_value=False):
                    with TestClient(app) as client:
                        response = client.get("/api/v1/stocks/search?q=600")

        self.assertEqual(response.status_code, 429)
        payload = response.json()
        detail = payload.get("detail", payload)
        self.assertEqual(detail["error"], "rate_limited")


if __name__ == "__main__":
    unittest.main()
