"""新闻面证据状态与面向用户的披露文案。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional, Tuple


_DISCLOSURES = {
    "zh": (
        "⚠️ 未配置搜索渠道，本次分析未纳入新闻面证据。",
        "⚠️ 本次未获取到可用的新闻面数据，以下结论未纳入新闻维度证据。",
    ),
    "en": (
        "⚠️ No news search channel is configured; this analysis does not incorporate news-based evidence.",
        "⚠️ No news data could be retrieved for this run; the conclusions below do not incorporate news-based evidence.",
    ),
    "ko": (
        "⚠️ 뉴스 검색 채널이 설정되지 않아 이번 분석에 뉴스 근거가 반영되지 않았습니다.",
        "⚠️ 이번 실행에서 이용 가능한 뉴스 데이터를 가져오지 못해 아래 결론에 뉴스 근거가 반영되지 않았습니다.",
    ),
}

def news_evidence_present(*sources: Any) -> bool:
    """判断是否真正消费了任何新闻面证据来源。"""
    for source in sources:
        if source is None or isinstance(source, bool):
            if source:
                return True
        elif isinstance(source, (int, float)):
            if source > 0:
                return True
        elif str(source).strip():
            return True
    return False


def persisted_news_result_state(raw_result: Any, context_snapshot: Any = None) -> Tuple[Optional[int], bool]:
    """还原持久化的三态新闻数量，而不去猜测历史遗留记录。"""
    if isinstance(raw_result, Mapping):
        if raw_result.get("news_result_count_known") is False:
            return None, False
        if "news_result_count" in raw_result:
            return raw_result.get("news_result_count"), True
    if isinstance(context_snapshot, Mapping):
        if "news_result_count" in context_snapshot:
            return context_snapshot.get("news_result_count"), True
        enhanced = context_snapshot.get("enhanced_context")
        if isinstance(enhanced, Mapping) and "news_result_count" in enhanced:
            return enhanced.get("news_result_count"), True
    return None, False


def persisted_news_evidence_present(raw_result: Any, news_result_count: Optional[int]) -> bool:
    """根据持久化结果判断本次是否纳入了新闻面证据。"""
    if isinstance(raw_result, Mapping) and "news_evidence_present" in raw_result:
        return bool(raw_result.get("news_evidence_present"))
    return bool(news_result_count)


def _disclosure(count: Optional[int], known: bool, evidence: bool, language: str) -> Optional[str]:
    """按已知状态与证据情况选择对应的披露文案，无需披露时返回 None。"""
    if not known or evidence:
        return None
    not_configured, zero_results = _DISCLOSURES.get(language, _DISCLOSURES["zh"])
    if count is None:
        return not_configured
    if count == 0:
        return zero_results
    return None


def empty_news_disclosure(result: Any, language: str = "zh") -> Optional[str]:
    """根据分析结果对象生成新闻面缺失披露文案。"""
    if isinstance(result, Mapping):
        count, known = persisted_news_result_state(result)
        evidence = persisted_news_evidence_present(result, count)
    else:
        count = getattr(result, "news_result_count", None)
        known = getattr(result, "news_result_count_known", True)
        evidence = bool(getattr(result, "news_evidence_present", False))
    return _disclosure(count, bool(known), evidence, language)


def empty_news_disclosure_from_stored(
    raw_result: Any,
    context_snapshot: Any,
    language: str = "zh",
) -> Optional[str]:
    """根据已存储的原始结果与上下文快照生成新闻面缺失披露文案。"""
    count, known = persisted_news_result_state(raw_result, context_snapshot)
    return _disclosure(
        count,
        known,
        persisted_news_evidence_present(raw_result, count),
        language,
    )


__all__ = [
    "empty_news_disclosure",
    "empty_news_disclosure_from_stored",
    "news_evidence_present",
    "persisted_news_result_state",
    "persisted_news_evidence_present",
]
