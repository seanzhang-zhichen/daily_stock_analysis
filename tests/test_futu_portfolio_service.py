from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from src.brokers.futu import portfolio as service


class _TradeContext:
    def __init__(self, *, accounts, positions, **kwargs) -> None:
        self.accounts = accounts
        self.positions = positions
        self.closed = False
        self.position_queries = []

    def get_acc_list(self):
        return 0, pd.DataFrame(self.accounts)

    def position_list_query(self, **kwargs):
        self.position_queries.append(kwargs)
        return 0, pd.DataFrame(self.positions.get(kwargs["acc_id"], []))

    def close(self) -> None:
        self.closed = True


class _QuoteContext:
    def __init__(self, *, stock_types, **kwargs) -> None:
        self.stock_types = stock_types
        self.closed = False

    def get_stock_basicinfo(self, market, *, stock_type, code_list):
        del market, stock_type
        return 0, pd.DataFrame([
            {"code": code, "stock_type": self.stock_types[code]}
            for code in code_list
            if code in self.stock_types
        ])

    def close(self) -> None:
        self.closed = True


def _api(accounts, positions, stock_types):
    trade_contexts = []
    quote_contexts = []

    def open_trade_context(**kwargs):
        context = _TradeContext(accounts=accounts, positions=positions, **kwargs)
        trade_contexts.append(context)
        return context

    def open_quote_context(**kwargs):
        context = _QuoteContext(stock_types=stock_types, **kwargs)
        quote_contexts.append(context)
        return context

    enum = SimpleNamespace(
        NONE="NONE",
        FUTUSECURITIES="FUTUSECURITIES",
        REAL="REAL",
        STOCK="STOCK",
        US="US",
        HK="HK",
        SH="SH",
        SZ="SZ",
    )
    return SimpleNamespace(
        OpenSecTradeContext=open_trade_context,
        OpenQuoteContext=open_quote_context,
        Market=enum,
        RET_OK=0,
        SecurityFirm=enum,
        SecurityType=enum,
        TrdEnv=enum,
        TrdMarket=enum,
    ), trade_contexts, quote_contexts


def _account(acc_id: int, env="REAL", role="NORMAL", status="ACTIVE"):
    return {
        "acc_id": acc_id,
        "trd_env": env,
        "acc_role": role,
        "acc_status": status,
        "security_firm": "FUTUSECURITIES",
    }


def test_loads_real_long_stocks_and_closes_contexts() -> None:
    api, trade_contexts, quote_contexts = _api(
        [_account(1001), _account(2002, env="SIMULATE")],
        {1001: [
            {"code": "US.AAPL", "qty": 10, "position_side": "LONG"},
            {"code": "HK.00700", "qty": 20, "position_side": "LONG"},
            {"code": "SZ.000001", "qty": 8, "position_side": "LONG"},
            {"code": "US.TSLA", "qty": 2, "position_side": "SHORT"},
            {"code": "US.ZERO", "qty": 0, "position_side": "LONG"},
        ]},
        {"US.AAPL": "STOCK", "HK.00700": "STOCK", "SZ.000001": "STOCK"},
    )
    with patch.dict("os.environ", {"FUTU_SECURITY_FIRM": "FUTUSECURITIES"}, clear=True), patch.object(
        service, "_load_futu_api", return_value=api
    ):
        result = service.load_futu_stock_codes()

    assert result == ["AAPL", "HK00700", "000001"]
    assert all(context.closed for context in trade_contexts + quote_contexts)
    assert trade_contexts[-1].position_queries[0]["refresh_cache"] is True


def test_merges_normal_and_master_accounts_and_deduplicates() -> None:
    api, _, _ = _api(
        [_account(1001), _account(2002, role="MASTER")],
        {
            1001: [{"code": "US.AAPL", "qty": 1, "position_side": "LONG"}],
            2002: [{"code": "US.AAPL", "qty": 2, "position_side": "LONG"}],
        },
        {"US.AAPL": "STOCK"},
    )
    with patch.dict("os.environ", {}, clear=True), patch.object(service, "_load_futu_api", return_value=api):
        assert service.load_futu_stock_codes() == ["AAPL"]


def test_rejects_unknown_security_type_instead_of_returning_partial_results() -> None:
    api, _, _ = _api(
        [_account(1001)],
        {1001: [{"code": "US.AAPL", "qty": 1, "position_side": "LONG"}]},
        {},
    )
    with patch.dict("os.environ", {}, clear=True), patch.object(service, "_load_futu_api", return_value=api), pytest.raises(
        service.FutuPortfolioError, match="无法确认证券类型"
    ):
        service.load_futu_stock_codes()


@pytest.mark.parametrize(
    ("futu_code", "expected"),
    [
        ("US.MSFT", "MSFT"),
        ("US.BRK.B", "BRK.B"),
        ("HK.700", "HK00700"),
        ("SH.600519", "600519"),
        ("SZ.000001", "000001"),
        ("HK.BAD", None),
        ("JP.9984", None),
    ],
)
def test_to_analysis_code(futu_code: str, expected: str | None) -> None:
    assert service._to_analysis_code(futu_code) == expected


@pytest.mark.parametrize("host", ["::1", "[::1]", "2001:db8::1"])
def test_connection_settings_rejects_ipv6(host: str) -> None:
    with patch.dict("os.environ", {"FUTU_OPEND_HOST": host}, clear=True), pytest.raises(
        service.FutuPortfolioError, match="仅支持 IPv4"
    ):
        service._connection_settings()


def test_configured_account_must_match_an_active_real_account() -> None:
    api, _, _ = _api([_account(1001)], {}, {})
    with patch.dict("os.environ", {"FUTU_ACC_ID": "9999"}, clear=True), pytest.raises(
        service.FutuPortfolioError, match="FUTU_ACC_ID 未匹配"
    ):
        service._discover_real_accounts(api, "127.0.0.1", 11111)
