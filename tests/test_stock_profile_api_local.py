from unittest.mock import patch

from fastapi.testclient import TestClient

from fastapi import FastAPI

from api.v1.endpoints.stocks import router


def test_stock_profile_endpoint_returns_partial_blocks():
    payload = {
        "requested_code": "AAPL", "canonical_code": "AAPL", "market": "us",
        "as_of": "2026-09-11T12:00:00+08:00",
        "quote": {"status": "fresh", "data": {"current_price": 100}, "limitations": []},
        "history": {"status": "unavailable", "period": "daily", "data": [], "limitations": ["history_unavailable"]},
        "fundamentals": {"status": "partial", "data": {"status": "partial"}, "limitations": ["fundamentals_partial"]},
        "evidence_quality": {"status": "partial", "available_blocks": 2, "total_blocks": 3},
    }
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/stocks")
    with patch("api.v1.endpoints.stocks.StockProfileService") as service:
        service.return_value.get_profile.return_value = payload
        response = TestClient(app).get("/api/v1/stocks/AAPL/profile")

    assert response.status_code == 200
    assert response.json()["evidence_quality"]["status"] == "partial"
