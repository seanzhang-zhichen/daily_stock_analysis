"""Tests for the LLM usage dashboard API."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_database_manager
from api.v1.endpoints.usage import router


class FakeUsageDbManager:
    def get_llm_usage_summary(self, from_dt, to_dt):
        return {
            "total_calls": 1,
            "total_prompt_tokens": 30,
            "total_completion_tokens": 70,
            "total_tokens": 100,
            "by_call_type": [{
                "call_type": "analysis", "calls": 1, "prompt_tokens": 30,
                "completion_tokens": 70, "total_tokens": 100,
            }],
            "by_model": [{
                "model": "openai/gpt-test", "calls": 1, "prompt_tokens": 30,
                "completion_tokens": 70, "total_tokens": 100, "max_total_tokens": 100,
            }],
        }

    def get_llm_usage_records(self, from_dt, to_dt, limit=50):
        return [{
            "id": 7,
            "called_at": datetime(2026, 6, 11, 9, 30, 0),
            "call_type": "analysis",
            "model": "openai/gpt-test",
            "stock_code": "600519",
            "prompt_tokens": 30,
            "completion_tokens": 70,
            "total_tokens": 100,
        }]


class UsageDashboardApiTestCase(unittest.TestCase):
    def test_dashboard_returns_summary_and_recent_calls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = FastAPI()
            app.include_router(router, prefix="/api/v1/usage")
            app.dependency_overrides[get_database_manager] = lambda: FakeUsageDbManager()
            response = TestClient(app).get("/api/v1/usage/dashboard?period=today&limit=10")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["period"], "today")
        self.assertEqual(body["total_prompt_tokens"], 30)
        self.assertEqual(body["by_model"][0]["max_total_tokens"], 100)
        self.assertEqual(body["recent_calls"][0]["stock_code"], "600519")


if __name__ == "__main__":
    unittest.main()
