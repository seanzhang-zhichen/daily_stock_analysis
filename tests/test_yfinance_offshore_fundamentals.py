from types import SimpleNamespace
from unittest.mock import patch

from data_provider.base import DataFetcherManager
from data_provider.yfinance_fundamental_adapter import YfinanceFundamentalAdapter


def test_yfinance_adapter_maps_us_growth_and_boards():
    ticker = SimpleNamespace(
        get_info=lambda: {
            "revenueGrowth": 0.12,
            "returnOnEquity": 0.2,
            "totalRevenue": 1000,
            "sector": "Technology",
            "industry": "Software",
            "currency": "USD",
        }
    )
    with patch("yfinance.Ticker", return_value=ticker):
        result = YfinanceFundamentalAdapter().get_fundamental_bundle("AAPL")

    assert result["status"] == "partial"
    assert result["growth"]["revenue_yoy"] == 12.0
    assert result["earnings"]["financial_report"]["currency"] == "USD"
    assert [item["name"] for item in result["belong_boards"]] == ["Technology", "Software"]


def test_yfinance_adapter_converts_internal_hk_symbol():
    ticker = SimpleNamespace(get_info=lambda: {})
    with patch("yfinance.Ticker", return_value=ticker) as factory:
        YfinanceFundamentalAdapter().get_fundamental_bundle("HK00700")

    factory.assert_called_once_with("0700.HK")


def test_offshore_context_keeps_shared_block_shape():
    manager = object.__new__(DataFetcherManager)
    manager._yfinance_fundamental_adapter = SimpleNamespace(
        get_fundamental_bundle=lambda _code: {
            "status": "partial",
            "growth": {"roe": 20.0},
            "earnings": {},
            "institution": {},
            "belong_boards": [{"name": "Technology", "type": "行业"}],
            "source_chain": ["growth:yfinance"],
            "errors": [],
        }
    )
    manager._run_with_retry = lambda func, *_args: (func(), None, 1)

    with patch("src.config.get_config", return_value=SimpleNamespace(fundamental_fetch_timeout_seconds=5)):
        result = manager._build_offshore_fundamental_context("AAPL", market="us")

    assert result["market"] == "us"
    assert result["growth"]["status"] in {"ok", "partial"}
    assert result["boards"]["data"]["boards"][0]["name"] == "Technology"
    assert result["capital_flow"]["status"] == "not_supported"
