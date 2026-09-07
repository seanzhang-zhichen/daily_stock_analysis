from unittest.mock import Mock, patch

import pandas as pd

from data_provider.akshare_fetcher import AkshareFetcher
from src.services.stock_list_parser import ParseStatus, parse_analysis_target
from src.services.task_queue import _dedupe_stock_code_key


def test_explicit_index_forms_have_stable_canonical_identity():
    target = parse_analysis_target("000300.SH")
    assert target.status == ParseStatus.INDEX
    assert target.canonical_id == "sh000300"
    assert target.exchange == "SH"

    csi = parse_analysis_target("930955.CSI")
    assert csi.status == ParseStatus.INDEX
    assert csi.canonical_id == "csi930955"
    assert csi.exchange == "CSI"


def test_bare_numeric_code_remains_stock():
    target = parse_analysis_target("000300")
    assert target.status == ParseStatus.STOCK
    assert target.canonical_id == "000300"


def test_unregistered_csi_is_explicitly_unsupported():
    target = parse_analysis_target("930956.CSI")
    assert target.status == ParseStatus.UNSUPPORTED


def test_akshare_index_route_uses_canonical_symbol():
    fetcher = AkshareFetcher.__new__(AkshareFetcher)
    fetcher._history_call_timeout = 1
    fetcher._set_random_user_agent = Mock()
    fetcher._enforce_rate_limit = Mock()
    target = parse_analysis_target("sh000300")
    frame = pd.DataFrame({"date": ["2026-01-01"], "open": [1], "close": [1], "high": [1], "low": [1], "volume": [1]})
    fake_akshare = Mock()
    fake_akshare.stock_zh_index_daily_em = Mock()
    with patch.dict("sys.modules", {"akshare": fake_akshare}), patch(
        "data_provider.akshare_fetcher._akshare_call_with_timeout", return_value=frame
    ) as call:
        result = fetcher._fetch_index_data(target, "2026-01-01", "2026-01-02")
    assert result["date"].tolist() == frame["date"].tolist()
    assert result["close"].tolist() == frame["close"].tolist()
    assert call.call_args.kwargs["symbol"] == "sh000300"


def test_akshare_raw_fetch_dispatches_registered_index_before_stock_paths():
    fetcher = AkshareFetcher.__new__(AkshareFetcher)
    expected = pd.DataFrame({"date": ["2026-01-01"]})
    with patch.object(fetcher, "_fetch_index_data", return_value=expected) as route:
        result = fetcher._fetch_raw_data("sh000300", "2026-01-01", "2026-01-02")
    assert result.equals(expected)
    route.assert_called_once()


def test_task_dedupe_keeps_index_separate_from_same_bare_code():
    assert _dedupe_stock_code_key("sh000300") != _dedupe_stock_code_key("000300")
