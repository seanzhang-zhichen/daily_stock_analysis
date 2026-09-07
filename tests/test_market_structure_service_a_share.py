from src.services.market_structure_service import MarketStructureService
from src.core.pipeline import StockAnalysisPipeline


class _Fetcher:
    def get_sector_rankings(self, n=5):
        return ([{"name": "人工智能", "change_pct": 3.2}], [{"name": "银行", "change_pct": -1.0}])

    def get_concept_rankings(self, n=5):
        return ([{"name": "机器人", "change_pct": 2.1}], [])


def _fundamental(boards):
    return {
        "belong_boards": boards,
        "boards": {"status": "ok", "data": {"top": [{"name": "人工智能", "change_pct": 3.2}], "bottom": []}},
    }


def test_builds_a_share_market_structure_from_boards_and_rankings():
    result = MarketStructureService(fetcher_manager=_Fetcher()).build_context(
        code="600519",
        stock_name="测试",
        market="cn",
        fundamental_context=_fundamental([{"name": "人工智能", "type": "行业"}]),
    )
    assert result["market"] == "cn"
    assert result["stock_market_position"]["primary_theme"]["name"] == "人工智能"
    assert result["market_theme_context"]["leading_industries"][0]["name"] == "人工智能"


def test_missing_boards_and_rankings_are_explicitly_degraded():
    class EmptyFetcher:
        def get_sector_rankings(self, n=5):
            return ([], [])

        def get_concept_rankings(self, n=5):
            return ([], [])

    result = MarketStructureService(fetcher_manager=EmptyFetcher()).build_context(
        code="600519", stock_name="测试", market="cn", fundamental_context={}
    )
    assert result["status"] in {"unknown", "partial"}
    assert result["stock_market_position"]["status"] == "unknown"


def test_non_a_share_is_not_supported_without_fetching_rankings():
    class FailFetcher:
        def get_sector_rankings(self, n=5):
            raise AssertionError("must not fetch non-A-share rankings")

    result = MarketStructureService(fetcher_manager=FailFetcher()).build_context(
        code="AAPL", stock_name="Apple", market="us", fundamental_context={}
    )
    assert result["status"] == "not_supported"
    assert result["stock_market_position"]["status"] == "not_supported"


def test_pipeline_helper_injects_context_and_skips_non_a_share():
    class Service:
        def build_context(self, **kwargs):
            return {"schema_version": "market-structure-v1", "status": "ok", "market": kwargs["market"]}

    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.market_structure_service = Service()
    pipeline.fetcher_manager = object()
    assert pipeline._build_market_structure_context(
        code="600519", stock_name="测试", market="cn", fundamental_context={}
    )["status"] == "ok"
    assert pipeline._build_market_structure_context(
        code="AAPL", stock_name="Apple", market="us", fundamental_context={}
    )["status"] == "not_supported"
