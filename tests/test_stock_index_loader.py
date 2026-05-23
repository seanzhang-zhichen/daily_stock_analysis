# -*- coding: utf-8 -*-
import unittest

from src.data import stock_index_loader
from src.repositories.stock_index_repo import StockIndexRepository
from src.storage import DatabaseManager


class TestStockIndexLoader(unittest.TestCase):
    def setUp(self):
        DatabaseManager.reset_instance()
        self.db = DatabaseManager(db_url="sqlite:///:memory:")
        self.repo = StockIndexRepository(self.db)
        stock_index_loader._clear_stock_index_cache_for_tests()

    def tearDown(self):
        stock_index_loader._clear_stock_index_cache_for_tests()
        DatabaseManager.reset_instance()

    def _seed(self, entries):
        self.repo.upsert_entries(entries, version="test")

    def test_get_index_stock_name_supports_display_canonical_and_hk_keys(self):
        self._seed([
            {
                "canonicalCode": "000001.SZ",
                "displayCode": "000001",
                "nameZh": "平安银行",
                "pinyinFull": "pinganyinhang",
                "pinyinAbbr": "payh",
                "aliases": [],
                "market": "CN",
                "assetType": "stock",
                "active": True,
                "popularity": 100,
            },
            {
                "canonicalCode": "00700.HK",
                "displayCode": "00700",
                "nameZh": "腾讯控股",
                "pinyinFull": "tengxunkonggu",
                "pinyinAbbr": "txkg",
                "aliases": [],
                "market": "HK",
                "assetType": "stock",
                "active": True,
                "popularity": 100,
            },
            {
                "canonicalCode": "AAPL",
                "displayCode": "AAPL",
                "nameZh": "苹果",
                "pinyinFull": "pingguo",
                "pinyinAbbr": "pg",
                "aliases": [],
                "market": "US",
                "assetType": "stock",
                "active": True,
                "popularity": 100,
            },
        ])

        self.assertEqual(stock_index_loader.get_index_stock_name("000001"), "平安银行")
        self.assertEqual(stock_index_loader.get_index_stock_name("000001.SZ"), "平安银行")
        self.assertEqual(stock_index_loader.get_index_stock_name("HK00700"), "腾讯控股")
        self.assertEqual(stock_index_loader.get_index_stock_name("00700"), "腾讯控股")
        self.assertEqual(stock_index_loader.get_index_stock_name("700.HK"), "腾讯控股")
        self.assertEqual(stock_index_loader.get_index_stock_name("aapl"), "苹果")

    def test_get_stock_name_index_map_is_cached_after_first_load(self):
        self._seed([
            {
                "canonicalCode": "000001.SZ",
                "displayCode": "000001",
                "nameZh": "平安银行",
                "market": "CN",
                "assetType": "stock",
                "active": True,
            }
        ])

        first = stock_index_loader.get_stock_name_index_map()
        self._seed([
            {
                "canonicalCode": "000001.SZ",
                "displayCode": "000001",
                "nameZh": "变更后名称",
                "market": "CN",
                "assetType": "stock",
                "active": True,
            }
        ])
        second = stock_index_loader.get_stock_name_index_map()

        self.assertIs(first, second)
        self.assertEqual(stock_index_loader.get_index_stock_name("000001"), "平安银行")

    def test_get_index_stock_name_returns_none_when_index_empty(self):
        self.assertEqual(stock_index_loader.get_stock_name_index_map(), {})
        self.assertIsNone(stock_index_loader.get_index_stock_name("000001"))


if __name__ == "__main__":
    unittest.main()
