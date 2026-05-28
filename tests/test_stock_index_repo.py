# -*- coding: utf-8 -*-
import unittest

from src.repositories.stock_index_repo import StockIndexRepository
from src.storage import DatabaseManager


class TestStockIndexRepository(unittest.TestCase):
    def setUp(self):
        DatabaseManager.reset_instance()
        StockIndexRepository.clear_search_cache()
        self.db = DatabaseManager(db_url="sqlite:///:memory:")
        self.repo = StockIndexRepository(self.db)
        self.repo.upsert_entries(
            [
                {
                    "canonicalCode": "600519.SH",
                    "displayCode": "600519",
                    "nameZh": "贵州茅台",
                    "pinyinFull": "guizhoumaotai",
                    "pinyinAbbr": "gzmt",
                    "aliases": ["茅台"],
                    "market": "CN",
                    "assetType": "stock",
                    "active": True,
                    "popularity": 100,
                },
                {
                    "canonicalCode": "600000.SH",
                    "displayCode": "600000",
                    "nameZh": "浦发银行",
                    "pinyinFull": "pufayinhang",
                    "pinyinAbbr": "pfyh",
                    "aliases": [],
                    "market": "CN",
                    "assetType": "stock",
                    "active": True,
                    "popularity": 50,
                },
                {
                    "canonicalCode": "00700.HK",
                    "displayCode": "00700",
                    "nameZh": "腾讯控股",
                    "pinyinFull": "tengxunkonggu",
                    "pinyinAbbr": "txkg",
                    "aliases": ["腾讯"],
                    "market": "HK",
                    "assetType": "stock",
                    "active": True,
                    "popularity": 90,
                },
                {
                    "canonicalCode": "DELISTED",
                    "displayCode": "DELISTED",
                    "nameZh": "退市样例",
                    "pinyinFull": "tuishiyangli",
                    "pinyinAbbr": "tsyl",
                    "aliases": [],
                    "market": "US",
                    "assetType": "stock",
                    "active": False,
                    "popularity": 999,
                },
            ],
            version="test",
        )

    def tearDown(self):
        StockIndexRepository.clear_search_cache()
        DatabaseManager.reset_instance()

    def test_search_matches_code_name_pinyin_and_alias_from_cache(self):
        code_results = self.repo.search("600", limit=10)
        self.assertEqual([item["canonicalCode"] for item in code_results], ["600519.SH", "600000.SH"])
        self.assertEqual(code_results[0]["matchType"], "prefix")
        self.assertEqual(code_results[0]["matchField"], "code")

        name_results = self.repo.search("茅台", limit=10)
        self.assertEqual(name_results[0]["canonicalCode"], "600519.SH")
        self.assertEqual(name_results[0]["matchField"], "alias")

        pinyin_results = self.repo.search("tx", limit=10)
        self.assertEqual(pinyin_results[0]["canonicalCode"], "00700.HK")
        self.assertEqual(pinyin_results[0]["matchField"], "pinyin")

    def test_search_result_cache_avoids_reloading_entries(self):
        first = self.repo.search("600", limit=10)
        self.assertEqual(len(first), 2)

        def fail_load():
            raise AssertionError("search entries should be reused from cache")

        self.repo._load_search_entries = fail_load
        second = self.repo.search("600", limit=10)
        self.assertEqual(second, first)

    def test_search_cache_is_cleared_after_upsert(self):
        self.assertEqual(self.repo.search("长电", limit=10), [])

        self.repo.upsert_entries(
            [
                {
                    "canonicalCode": "600584.SH",
                    "displayCode": "600584",
                    "nameZh": "长电科技",
                    "pinyinFull": "changdiankexueji",
                    "pinyinAbbr": "cdkj",
                    "aliases": [],
                    "market": "CN",
                    "assetType": "stock",
                    "active": True,
                    "popularity": 80,
                }
            ],
            version="test2",
        )

        refreshed = self.repo.search("长电", limit=10)
        self.assertEqual([item["canonicalCode"] for item in refreshed], ["600584.SH"])

    def test_inactive_entries_are_hidden_by_default(self):
        self.assertEqual(self.repo.search("DELISTED", limit=10), [])
        results = self.repo.search("DELISTED", limit=10, active_only=False)
        self.assertEqual(results[0]["canonicalCode"], "DELISTED")


if __name__ == "__main__":
    unittest.main()
