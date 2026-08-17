from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend import main
from src.brokers.futu.portfolio import FutuPortfolioError


def test_parse_arguments_accepts_case_insensitive_futu_portfolio() -> None:
    with patch.object(sys, "argv", ["main.py", "--portfolio", "FUTU"]):
        args = main.parse_arguments()

    assert args.portfolio == "futu"


def test_resolve_portfolio_stock_codes_uses_futu_loader() -> None:
    with patch(
        "src.brokers.futu.portfolio.load_futu_stock_codes",
        return_value=["aapl", "HK01810", "600519"],
    ) as loader:
        result = main._resolve_portfolio_stock_codes(SimpleNamespace(portfolio="futu"))

    assert result == ["AAPL", "HK01810", "600519"]
    loader.assert_called_once_with()


def test_resolve_portfolio_stock_codes_returns_none_when_disabled() -> None:
    assert main._resolve_portfolio_stock_codes(SimpleNamespace()) is None


def test_run_full_analysis_propagates_futu_load_failure() -> None:
    error = FutuPortfolioError("OpenD unavailable")
    with patch(
        "src.brokers.futu.portfolio.load_futu_stock_codes",
        side_effect=error,
    ), pytest.raises(FutuPortfolioError, match="OpenD unavailable"):
        main.run_full_analysis(SimpleNamespace(), SimpleNamespace(portfolio="futu"))
