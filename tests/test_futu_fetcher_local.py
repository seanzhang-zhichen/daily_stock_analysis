from unittest.mock import Mock, patch

import pandas as pd

from data_provider.futu_fetcher import FutuFetcher


def test_futu_fetcher_closes_context_and_maps_quote():
    context = Mock()
    context.get_market_snapshot.return_value = (0, pd.DataFrame([{"name": "腾讯控股", "last_price": 500, "change_rate": 1.2}]))
    module = Mock(OpenQuoteContext=Mock(return_value=context), RET_OK=0)
    with patch.dict("sys.modules", {"futu": module}):
        quote = FutuFetcher("127.0.0.1", 11111).get_realtime_quote("HK00700")
    assert quote.price == 500
    context.get_market_snapshot.assert_called_once_with(["HK.00700"])
    context.close.assert_called_once()
