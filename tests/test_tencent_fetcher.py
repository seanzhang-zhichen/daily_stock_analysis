from unittest.mock import patch

from data_provider.tencent_fetcher import TencentFetcher, _to_tencent_symbol


def test_tencent_symbol_supports_a_share_markets():
    assert _to_tencent_symbol("600519") == "sh600519"
    assert _to_tencent_symbol("000001") == "sz000001"
    assert _to_tencent_symbol("920748") == "bj920748"
    assert _to_tencent_symbol("000016.SH") == "sh000016"
    assert _to_tencent_symbol("AAPL") == ""


def test_tencent_fetcher_parses_daily_kline_response():
    payload = {
        "data": {
            "sz000001": {
                "qfqday": [
                    ["2026-05-06", "10.00", "10.50", "10.80", "9.90", "12345", "67890"],
                    ["2026-05-07", "10.50", "10.70", "10.90", "10.30", "22345", "77890"],
                ]
            }
        }
    }

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    with patch("data_provider.tencent_fetcher.requests.get", return_value=Response()) as request:
        df = TencentFetcher().get_daily_data("000001", start_date="2026-05-01", end_date="2026-05-10")

    assert len(df) == 2
    assert float(df.iloc[0]["volume"]) == 1234500.0
    assert request.call_args.kwargs["params"]["param"].startswith("sz000001,day,2026-05-01,2026-05-10,")


def test_tencent_priority_can_be_overridden(monkeypatch):
    monkeypatch.setenv("TENCENT_PRIORITY", "2")
    assert TencentFetcher().priority == 2
