# -*- coding: utf-8 -*-
# 派生自 AlphaSift (commit 9f522747caafd3c0b1ddb7e14d5cf44c8580b6cf)，
# 遵循 Apache-2.0 协议并适配本仓库。
"""按"评分近似"的尾部临界线进行的有限抽样，用作每次选股运行的变体。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from src.services.screening.models import Pick


@dataclass(frozen=True)
class SelectionVariant:
    """一次筛选变体抽样的结果。

    记录最终选中的候选、是否实际发生了轮换，以及抽样池的大小等统计信息。
    """

    picks: list[Pick]
    applied: bool = False
    pool_size: int = 0
    rotated_slots: int = 0


def apply_seeded_selection_variant(
    picks: list[Pick],
    *,
    max_output: int,
    seed: str,
    period: str,
    max_score_gap: float = 1.5,
    rotation_ratio: float = 1.0,
    analyzer_names: list[str] | None = None,
) -> SelectionVariant:
    """在最终评分相近的候选中抽样分配输出名额。

    原始 Top-N 的前半部分以及显著优于临界线的候选保持受保护，不参与轮换。
    不透明的客户端种子只影响剩余的近似评分尾部；硬过滤、风险否决、
    评分值与组合惩罚永不被改变。

    兼容性说明：当客户端未提供种子（空字符串或 None）时，保留原始排序并
    返回严格的 Top-N 切片，避免对期望旧有稳定排序的旧调用方静默应用新的
    基于代码的平局裁决逻辑。
    """
    normalized_seed = str(seed or "").strip()

    # 未提供种子时保留原始顺序，兼容未启用轮换的旧客户端。
    output_count = min(max(int(max_output), 0), len(picks))
    if output_count == 0:
        return SelectionVariant(picks=[])
    if not normalized_seed or output_count < 2 or len(picks) <= output_count:
        # 保留原始顺序，仅裁剪到要求的输出数量。
        return SelectionVariant(picks=_rerank(picks[:output_count]))

    # 上游流水线已产出权威的最终顺序。以该顺序作为受保护头部与
    # 临界线的基准，避免基于代码的平局裁决在轮换开始前就把同分候选
    # 悄悄移出原始 Top-N 边界。
    ordered = list(picks)
    output_count = min(max(int(max_output), 0), len(ordered))

    cutoff_score = float(ordered[output_count - 1].final_score)
    score_gap = max(float(max_score_gap), 0.0)
    minimum_score = cutoff_score - score_gap
    # 保护那些领先原始 Top-N 临界线幅度超过整个允许抽样区间的候选。
    quality_protected = [
        pick
        for pick in ordered[:output_count]
        if float(pick.final_score) > cutoff_score + score_gap
    ]
    remaining_slots = output_count - len(quality_protected)
    requested_ratio = max(float(rotation_ratio), 0.0)
    if requested_ratio == 0.0:
        return SelectionVariant(picks=_rerank(ordered[:output_count]))
    # 轮换只作用于尾部。即使调用方请求 ratio=1，也要把可轮换名额
    # 限制为 floor(N/2)，从而保住原始 Top-N 的前半部分，使第 1 名
    # （以及 Top-3 中的第 2 名）保持稳定。
    max_tail_slots = max(1, output_count // 2)
    rotation_slots = min(
        remaining_slots,
        max_tail_slots,
        max(1, int(round(output_count * requested_ratio))),
    )

    def _was_post_analyzed(pick: Pick) -> bool:
        """判断候选是否已获得与受保护头部同等级的后分析处理，才允许参与轮换。"""
        # 若本次运行配置了分析器，候选必须对所有已配置分析器都有
        # 明确且未跳过的后分析结果，才有资格进入近似临界线轮换，
        # 避免提拔那些从未获得与受保护头部同等级 L3 处理的候选。
        status_map = pick.post_analysis_status or {}
        if not analyzer_names:
            # 未配置分析器时回退到旧行为：仅排除显式标记为 'skipped' 的候选。
            return not any(status == "skipped" for status in status_map.values())

        # 若配置了分析器，则要求每个已配置分析器都为该候选记录了显式的
        # completed 状态。缺失条目或显式的 'not_requested' 说明该候选未
        # 获得同等的 L3 处理，必须从近似临界线轮换中排除。
        for analyzer in analyzer_names:
            s = status_map.get(analyzer)
            # 只允许显式完成了分析器运行的候选。
            if s != "completed":
                return False
        return True

    position_protected_count = remaining_slots - rotation_slots
    quality_protected_codes = {pick.code for pick in quality_protected}
    unprotected = [pick for pick in ordered if pick.code not in quality_protected_codes]
    position_protected = unprotected[:position_protected_count]
    position_protected_codes = {pick.code for pick in position_protected}
    protected = [*quality_protected, *position_protected]
    pool = [
        pick
        for pick in ordered
        if pick.code not in quality_protected_codes
        and pick.code not in position_protected_codes
        if float(pick.final_score) >= minimum_score
        and _was_post_analyzed(pick)
    ]
    if len(pool) <= rotation_slots:
        return SelectionVariant(
            picks=_rerank(ordered[:output_count]),
            pool_size=len(pool),
        )

    varied_pool = sorted(
        pool,
        key=lambda pick: _variant_key(
            normalized_seed,
            period,
            pick.code,
        ),
    )
    chosen_codes = {pick.code for pick in varied_pool[:rotation_slots]}
    chosen_tail = [pick for pick in pool if pick.code in chosen_codes]
    selected_code_set = {
        pick.code
        for pick in [*protected, *chosen_tail]
    }
    # Rotation changes membership only. Preserve the upstream relative order
    # for every selected candidate, including equal-score candidates.
    selected = [
        pick
        for pick in ordered
        if pick.code in selected_code_set
    ][:output_count]
    base_codes = [pick.code for pick in ordered[:output_count]]
    selected_codes = [pick.code for pick in selected]
    changed_count = len(set(selected_codes) - set(base_codes))
    return SelectionVariant(
        picks=_rerank(selected),
        applied=changed_count > 0,
        pool_size=len(pool),
        rotated_slots=changed_count,
    )


def _variant_key(seed: str, period: str, code: str) -> bytes:
    """基于种子、周期与代码生成稳定的变体排序键（SHA-256 摘要）。"""
    return hashlib.sha256(f"{seed}\0{period}\0{code}".encode("utf-8")).digest()


def _rerank(picks: list[Pick]) -> list[Pick]:
    """按输出顺序重新编号候选的 rank 字段。"""
    for index, pick in enumerate(picks, start=1):
        pick.rank = index
    return picks


