from types import SimpleNamespace
from unittest.mock import patch

from src.core.pipeline import StockAnalysisPipeline, _a_share_intelligence_scope_values


def _pipeline():
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.config = SimpleNamespace(get_effective_news_window_days=lambda: 3)
    return pipeline


def test_a_share_intelligence_aliases_cover_exchange_spellings() -> None:
    assert _a_share_intelligence_scope_values("600519") == [
        "600519", "SH600519", "sh600519", "600519.SH", "600519.sh",
    ]
    assert "SZ000001" in _a_share_intelligence_scope_values("000001")
    assert _a_share_intelligence_scope_values("AAPL") == []


def test_local_intelligence_prefers_symbol_and_deduplicates_market_items() -> None:
    calls = []

    class FakeService:
        def __init__(self, config):
            assert config is _pipeline_instance.config

        def refresh_auto_sources(self):
            return {"ok": True}

        def list_items(self, **filters):
            calls.append(filters)
            if filters.get("scope_type") == "symbol" and filters.get("scope_value") == "600519":
                return {"items": [{"title": "茅台公告", "summary": "业绩预告", "url": "https://example.test/moutai", "source": "RSS"}]}
            if filters.get("scope_type") == "market":
                return {"items": [
                    {"title": "重复公告", "url": "https://example.test/moutai"},
                    {"title": "市场热点", "summary": "消费板块", "url": "https://example.test/market", "source": "NewsNow"},
                ]}
            return {"items": []}

    _pipeline_instance = _pipeline()
    with patch("src.services.intelligence_service.IntelligenceService", FakeService):
        context = _pipeline_instance._load_persisted_intelligence_context(code="600519", stock_name="贵州茅台")

    assert context is not None
    assert "茅台公告" in context
    assert "市场热点" in context
    assert "重复公告" not in context
    assert calls[0]["scope_value"] == "600519"
    assert calls[-1]["scope_type"] == "market"


def test_local_intelligence_failure_is_fail_open() -> None:
    class FailingService:
        def __init__(self, config):
            pass

        def refresh_auto_sources(self):
            raise RuntimeError("source unavailable")

    with patch("src.services.intelligence_service.IntelligenceService", FailingService):
        assert _pipeline()._load_persisted_intelligence_context(code="000001", stock_name="平安银行") is None
