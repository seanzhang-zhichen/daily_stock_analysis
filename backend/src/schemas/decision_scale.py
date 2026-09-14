# -*- coding: utf-8 -*-
"""决策评分标准（Decision Scale）模块。

定义了报告与信号中使用的规范评分区间（80/60/40/20 分档），
提供分数归一化、档位映射、元数据生成以及评分-动作对齐等工具函数。

评分区间设计：
- 80-100 分：强烈买入（buy）
- 60-79 分：买入（buy）
- 40-59 分：观望（watch）
- 20-39 分：减仓（reduce）
- 0-19 分：卖出（sell）

使用示例：
    >>> from src.schemas.decision_scale import normalize_score, action_for_score
    >>> normalize_score("75")
    75
    >>> action_for_score(75)
    'buy'
"""

from typing import Any, Optional

from src.report_language import localize_operation_advice, normalize_report_language

# 当前评分标准的版本标识，用于追踪评分规则的演进
CANONICAL_DECISION_SCALE_VERSION = "decision-scale-v1"

# 规范评分区间表：每个元组为 (最低分, 最高分, 对应动作)
# 分数越高越看好，越低越看空
BANDS = (
    (80, 100, "buy"),    # 强烈买入区间
    (60, 79, "buy"),     # 买入区间
    (40, 59, "watch"),   # 观望区间
    (20, 39, "reduce"),  # 减仓区间
    (0, 19, "sell"),     # 卖出区间
)


def normalize_score(value: Any) -> Optional[int]:
    """将任意输入值归一化为 0-100 之间的整数分数。

    参数:
        value: 任意可转换为数值的输入（字符串、浮点数等）。

    返回:
        归一化后的整数分数（0-100），若输入无效或超出范围则返回 ``None``。
    """
    try:
        value = int(float(value))
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= 100 else None


def action_for_score(value: Any) -> Optional[str]:
    """根据分数返回对应的规范动作（buy/watch/reduce/sell）。

    参数:
        value: 任意可转换为数值的输入。

    返回:
        对应分数区间的动作字符串，输入无效时返回 ``None``。
    """
    score = normalize_score(value)
    if score is None:
        return None
    for low, high, action in BANDS:
        if low <= score <= high:
            return action
    return None


def score_band_metadata(value: Any) -> dict[str, Any]:
    """获取分数所在区间的完整元数据信息。

    返回包含评分版本、分数值、区间范围和规范动作的字典，
    用于报告生成和前端展示。

    参数:
        value: 任意可转换为数值的输入。

    返回:
        包含 ``scale_version``、``score``、``score_band``、``canonical_action``
        等字段的字典；输入无效时返回空字典。
    """
    score = normalize_score(value)
    if score is None:
        return {}
    for low, high, action in BANDS:
        if low <= score <= high:
            return {
                "scale_version": CANONICAL_DECISION_SCALE_VERSION,
                "score": score,
                "score_band": f"{low}-{high}",
                "canonical_action": action,
            }
    return {}


def apply_score_action_scale(result: Any) -> list[dict[str, Any]]:
    """将分析结果的默认动作与其规范评分档位对齐。

    根据 ``result.sentiment_score`` 计算规范动作，并更新 ``result`` 的
    ``decision_action``、``decision_type``、``operation_advice`` 等属性。
    同时记录决策护栏（guardrails）信息，便于后续审计和展示。

    参数:
        result: 包含 ``sentiment_score``、``report_language``、
                ``decision_type``、``decision_action`` 等属性的对象。

    返回:
        包含对齐记录的字典列表（通常只有一条记录）。
    """
    # 从结果对象中提取情感分数并计算规范动作
    score = normalize_score(getattr(result, "sentiment_score", None))
    action = action_for_score(score)
    if score is None or action is None:
        return []

    # 获取报告语言设置，用于本地化操作建议
    language = normalize_report_language(getattr(result, "report_language", "zh"))

    # 优先使用 decision_type 作为原始动作，否则回退到 decision_action
    decision_type = str(getattr(result, "decision_type", "") or "").strip().lower()
    before_action = {"buy": "buy", "sell": "sell"}.get(decision_type)
    if before_action is None:
        before_action = str(getattr(result, "decision_action", "") or "watch").strip().lower()

    # 构建对齐记录，记录评分前后的动作变化
    record = {
        "type": "score_action_alignment",
        "score": score,
        "before_action": before_action,
        "after_action": action,
        **score_band_metadata(score),
    }

    # 更新结果对象的决策属性
    result.decision_action = action
    result.decision_type = "buy" if action == "buy" else "sell" if action == "sell" else "hold"

    # 如果动作发生变化，更新本地化的操作建议
    if before_action != action:
        labels = {"buy": "买入", "watch": "观望", "reduce": "减仓", "sell": "卖出"}
        result.operation_advice = localize_operation_advice(labels[action], language)

    # 将本次对齐记录追加到决策护栏列表中
    records = getattr(result, "decision_guardrails", None)
    if not isinstance(records, list):
        records = []
        result.decision_guardrails = records
    records.append(record)

    # 同步更新仪表板展示数据
    dashboard = getattr(result, "dashboard", None)
    if not isinstance(dashboard, dict):
        dashboard = {}
        result.dashboard = dashboard
    dashboard["decision_action"] = action
    dashboard["decision_guardrails"] = list(records)

    return [record]
