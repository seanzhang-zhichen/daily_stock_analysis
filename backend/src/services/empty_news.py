"""新闻面证据状态与面向用户的披露文案。

本模块用于判断新闻面证据是否存在，并根据不同情况生成
面向用户的披露文案（中文/英文/韩文），告知用户当前分析
是否纳入了新闻维度证据。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional, Tuple


# 多语言披露文案映射：key 为语言代码，value 为 (未配置文案, 无数据文案)
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
        "⚠️ 스 검색 채널이 설정되지 않아 이번 분석에 뉴스 근거가 반영되지 않았습니다.",
        "⚠️ 이번 실행에서 이용 가능한 뉴스 데이터를 가져오지 못해 아래 결론에 스 근거가 반영되지 않았습니다.",
    ),
}

def news_evidence_present(*sources: Any) -> bool:
    """判断是否真正消费了任何新闻面证据来源。

    参数:
        *sources: 可变数量的新闻面证据来源。

    返回:
        如果任一来源包含有效证据则返回 True，否则返回 False。
    """
    for source in sources:
        # 布尔值直接判断
        if source is None or isinstance(source, bool):
            if source:
                return True
        # 数值类型：大于 0 视为有效
        elif isinstance(source, (int, float)):
            if source > 0:
                return True
        # 字符串类型：非空且去除空白后有效
        elif str(source).strip():
            return True
    return False


def persisted_news_result_state(raw_result: Any, context_snapshot: Any = None) -> Tuple[Optional[int], bool]:
    """还原持久化的三态新闻数量，而不去猜测历史遗留记录。

    参数:
        raw_result: 原始结果对象，可能包含新闻数量信息。
        context_snapshot: 上下文快照，作为备选数据源。

    返回:
        一个元组 (news_result_count, known)：
        - news_result_count: 新闻结果数量，可能为 None。
        - known: 是否已知新闻数量状态。
    """
    # 优先从 raw_result 中提取新闻数量
    if isinstance(raw_result, Mapping):
        # 如果明确标记为未知，返回 None, False
        if raw_result.get("news_result_count_known") is False:
            return None, False
        if "news_result_count" in raw_result:
            return raw_result.get("news_result_count"), True
    # 从 context_snapshot 中备选提取
    if isinstance(context_snapshot, Mapping):
        if "news_result_count" in context_snapshot:
            return context_snapshot.get("news_result_count"), True
        enhanced = context_snapshot.get("enhanced_context")
        if isinstance(enhanced, Mapping) and "news_result_count" in enhanced:
            return enhanced.get("news_result_count"), True
    return None, False


def persisted_news_evidence_present(raw_result: Any, news_result_count: Optional[int]) -> bool:
    """根据持久化结果判断本次是否纳入了新闻面证据。

    参数:
        raw_result: 原始结果对象。
        news_result_count: 新闻结果数量。

    返回:
        如果纳入了新闻面证据则返回 True，否则返回 False。
    """
    # 优先使用 raw_result 中显式标记的 news_evidence_present 字段
    if isinstance(raw_result, Mapping) and "news_evidence_present" in raw_result:
        return bool(raw_result.get("news_evidence_present"))
    # 回退到根据新闻数量判断
    return bool(news_result_count)


def _disclosure(count: Optional[int], known: bool, evidence: bool, language: str) -> Optional[str]:
    """按已知状态与证据情况选择对应的披露文案，无需披露时返回 None。

    参数:
        count: 新闻结果数量。
        known: 是否已知新闻数量状态。
        evidence: 是否已纳入新闻面证据。
        language: 语言代码。

    返回:
        对应的披露文案字符串；无需披露时返回 None。
    """
    # 如果已知且已纳入证据，无需披露
    if not known or evidence:
        return None
    # 根据语言获取对应的未配置/无数据文案
    not_configured, zero_results = _DISCLOSURES.get(language, _DISCLOSURES["zh"])
    if count is None:
        return not_configured
    if count == 0:
        return zero_results
    return None


def empty_news_disclosure(result: Any, language: str = "zh") -> Optional[str]:
    """根据分析结果对象生成新闻面缺失披露文案。

    参数:
        result: 分析结果对象，可以是字典或具有相关属性的对象。
        language: 语言代码，默认中文。

    返回:
        披露文案字符串；无需披露时返回 None。
    """
    # 字典类型：使用持久化结果状态函数提取信息
    if isinstance(result, Mapping):
        count, known = persisted_news_result_state(result)
        evidence = persisted_news_evidence_present(result, count)
    else:
        # 对象类型：通过属性访问获取信息
        count = getattr(result, "news_result_count", None)
        known = getattr(result, "news_result_count_known", True)
        evidence = bool(getattr(result, "news_evidence_present", False))
    return _disclosure(count, bool(known), evidence, language)


def empty_news_disclosure_from_stored(
    raw_result: Any,
    context_snapshot: Any,
    language: str = "zh",
) -> Optional[str]:
    """根据已存储的原始结果与上下文快照生成新闻面缺失披露文案。

    参数:
        raw_result: 已存储的原始结果。
        context_snapshot: 上下文快照。
        language: 语言代码，默认中文。

    返回:
        披露文案字符串；无需披露时返回 None。
    """
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
