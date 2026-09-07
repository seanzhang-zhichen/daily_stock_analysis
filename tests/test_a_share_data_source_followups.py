from unittest.mock import patch

from data_provider.efinance_fetcher import _build_eastmoney_etf_secid
from data_provider.tushare_fetcher import TushareFetcher, _resolve_tushare_http_url
from data_provider.base import DataFetcherManager, BaseFetcher, STANDARD_COLUMNS
from src.services.stock_list_parser import parse_analysis_target
import pandas as pd


def test_eastmoney_etf_secid_uses_exchange_prefix() -> None:
    assert _build_eastmoney_etf_secid("510300") == "1.510300"
    assert _build_eastmoney_etf_secid("159919") == "0.159919"


def test_tushare_http_url_is_optional_and_validated() -> None:
    with patch.dict("os.environ", {}, clear=True):
        assert _resolve_tushare_http_url() is None
    with patch.dict("os.environ", {"TUSHARE_HTTP_URL": " https://gateway.example/api "}):
        assert _resolve_tushare_http_url() == "https://gateway.example/api"
    with patch.dict("os.environ", {"TUSHARE_HTTP_URL": "gateway.example"}):
        try:
            _resolve_tushare_http_url()
        except ValueError:
            pass
        else:
            raise AssertionError("invalid TUSHARE_HTTP_URL should fail validation")


def test_tushare_client_receives_custom_endpoint() -> None:
    with patch.dict("os.environ", {"TUSHARE_HTTP_URL": "https://gateway.example/api"}), patch.object(
        TushareFetcher, "_init_api", return_value=None
    ):
        fetcher = TushareFetcher()
        client = fetcher._build_api_client("token")
    assert client._api_url == "https://gateway.example/api"


class _IndexFetcher(BaseFetcher):
    def __init__(self, name: str, priority: int, frame=None, error: Exception | None = None):
        self.name = name
        self.priority = priority
        self.frame = frame
        self.error = error
        self.calls = []

    def _fetch_raw_data(self, stock_code, start_date, end_date):
        self.calls.append((stock_code, start_date, end_date))
        if self.error:
            raise self.error
        return self.frame

    def _normalize_data(self, df, stock_code):
        return df


def test_manager_index_daily_preserves_identity_and_falls_back_to_tencent():
    frame = pd.DataFrame({column: [1] for column in STANDARD_COLUMNS})
    ak = _IndexFetcher("AkshareFetcher", 1, error=RuntimeError("blocked"))
    tx = _IndexFetcher("TencentFetcher", 2, frame=frame)
    manager = DataFetcherManager(fetchers=[ak, tx])

    result, source = manager.get_daily_data("sh000300", start_date="2026-01-01", end_date="2026-01-02")

    assert source == "TencentFetcher"
    assert tx.calls == [("sh000300", "2026-01-01", "2026-01-02")]
    assert ak.calls == [("sh000300", "2026-01-01", "2026-01-02")]
    assert not result.empty


def test_manager_index_csi_does_not_fan_out_to_stock_provider():
    ak = _IndexFetcher("AkshareFetcher", 1, error=RuntimeError("unsupported"))
    tx = _IndexFetcher("TencentFetcher", 2, frame=pd.DataFrame({"close": [1]}))
    manager = DataFetcherManager(fetchers=[ak, tx])

    result, source = manager.get_daily_data("csi930955", start_date="2026-01-01", end_date="2026-01-02")

    assert source == ""
    assert result.empty
    assert tx.calls == []
