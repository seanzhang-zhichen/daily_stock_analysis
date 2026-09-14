from types import SimpleNamespace

from data_provider.base import DataFetcherManager


def test_us_sources_follow_fetcher_priority():
    manager = object.__new__(DataFetcherManager)
    manager._fetchers = [
        SimpleNamespace(name="YfinanceFetcher", priority=1),
        SimpleNamespace(name="FinnhubFetcher", priority=4),
        SimpleNamespace(name="LongbridgeFetcher", priority=3),
    ]

    ordered = manager._order_us_sources_by_priority(
        ["FinnhubFetcher", "YfinanceFetcher", "LongbridgeFetcher"],
        pin_first=False,
    )

    assert ordered == ["YfinanceFetcher", "LongbridgeFetcher", "FinnhubFetcher"]


def test_us_sources_keep_explicit_primary_pinned():
    manager = object.__new__(DataFetcherManager)
    manager._fetchers = [
        SimpleNamespace(name="YfinanceFetcher", priority=9),
        SimpleNamespace(name="FinnhubFetcher", priority=1),
    ]

    ordered = manager._order_us_sources_by_priority(
        ["YfinanceFetcher", "FinnhubFetcher"],
        pin_first=True,
    )

    assert ordered == ["YfinanceFetcher", "FinnhubFetcher"]
