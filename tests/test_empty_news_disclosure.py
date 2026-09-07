from types import SimpleNamespace

from src.services.empty_news import (
    empty_news_disclosure,
    empty_news_disclosure_from_stored,
    news_evidence_present,
)


def _result(count=None, known=True, evidence=False):
    return SimpleNamespace(
        news_result_count=count,
        news_result_count_known=known,
        news_evidence_present=evidence,
        market_sentiment="模型生成的情绪判断",
        hot_topics="模型生成的热点判断",
    )


def test_disclosure_distinguishes_unconfigured_and_zero_results():
    assert "未配置" in empty_news_disclosure(_result(), "zh")
    assert "未获取到" in empty_news_disclosure(_result(0), "zh")
    assert empty_news_disclosure(_result(2), "zh") is None


def test_model_sentiment_does_not_count_as_news_evidence():
    assert empty_news_disclosure(_result(0), "zh")
    assert empty_news_disclosure(_result(0, evidence=True), "zh") is None


def test_legacy_stored_record_is_silent_and_localized():
    assert empty_news_disclosure_from_stored({"market_sentiment": "x"}, {}, "zh") is None
    assert "No news data" in empty_news_disclosure(_result(0), "en")
    assert empty_news_disclosure(_result(3), "ko") is None


def test_news_evidence_present_uses_real_sources_only():
    assert not news_evidence_present(None, 0, "")
    assert news_evidence_present(1)
    assert news_evidence_present("persisted article")
