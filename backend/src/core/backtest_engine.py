# -*- coding: utf-8 -*-
"""回测评估引擎（纯逻辑层）。

本模块刻意保持与数据库无关：它只处理普通数值或"看起来像日线 OHLC Bar"的对象，
便于被仓库层、测试桩等任意调用方复用，评分规则保持纯粹与确定性。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence


OVERALL_SENTINEL_CODE = "__overall__"


class DailyBarLike(Protocol):
    """表征单根日线 OHLC Bar 的协议（Protocol）。

    只需提供 date/high/low/close 字段即可被引擎当成日线数据使用。
    """

    date: date
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]


class BacktestResultLike(Protocol):
    """行为上类似于已落库的 BacktestResult 的协议（Protocol）。

    用于聚合统计时读取各评估字段，使仓库行对象或测试桩都能被复用。
    """

    eval_status: str
    position_recommendation: Optional[str]
    outcome: Optional[str]
    direction_correct: Optional[bool]
    stock_return_pct: Optional[float]
    simulated_return_pct: Optional[float]
    hit_stop_loss: Optional[bool]
    hit_take_profit: Optional[bool]
    first_hit: Optional[str]
    first_hit_trading_days: Optional[int]
    operation_advice: Optional[str]


@dataclass(frozen=True)
class EvaluationConfig:
    """单次回测评估窗口的运行时参数（knobs）。

    ``neutral_band_pct`` 定义被视为"inconclusive（方向不明）"的价格波动带，
    小于该幅度的涨跌不计入方向对错；``engine_version`` 会随结果一起持久化，
    以便后续评分规则变更时，新旧评估结果仍可共存、便于对比。
    """

    eval_window_days: int
    neutral_band_pct: float = 2.0
    engine_version: str = "v1"


class BacktestEngine:
    """仅做多（long-only）的日线回测引擎。

    引擎刻意接受 Protocol 风格的输入而非 ORM 模型：仓库层可传入数据库行对象，
    测试可传入小型桩对象，而评分规则始终保持纯粹与确定性，便于复现与单测。
    """

    # 操作建议关键词（中文 + 英文），命中即代表相应的多空意图
    _BULLISH_KEYWORDS = (
        "买入",
        "加仓",
        "强烈买入",
        "增持",
        "建仓",
        "strong buy",
        "buy",
        "add",
    )
    _BEARISH_KEYWORDS = (
        "卖出",
        "减仓",
        "强烈卖出",
        "清仓",
        "strong sell",
        "sell",
        "reduce",
    )
    _HOLD_KEYWORDS = (
        "持有",
        "震荡观望",
        "洗盘观察",
        "持有观察",
        "hold",
        "range-bound watch",
        "shakeout watch",
        "hold and watch",
    )
    _WAIT_KEYWORDS = (
        "观望",
        "等待",
        "wait",
    )

    # 否定前缀（negation prefixes）：用于对关键词之前的文本做后缀匹配，匹配前会先去掉尾部空格。
    # 英文否定词的规范形式带尾随空格；匹配时会对前缀文本执行 rstrip，
    # 因此 "do not" 既能匹配前缀 "do not " 也能匹配 "do not"。
    _NEGATION_PATTERNS = (
        "not", "don't", "do not", "no", "never", "avoid",  # 英文
        "不要", "不", "别", "勿", "没有",  # 中文
    )

    _NEGATION_CONNECTOR_WORDS = (
        "建议",
        "应",
        "应当",
        "宜",
        "先",
        "再",
        "暂",
        "不必",
        "必须",
        "无需",
    )

    @classmethod
    def infer_direction_expected(cls, operation_advice: Optional[str]) -> str:
        """根据操作建议推断预期方向：up（看多）/ down（看空）/ not_down（不看空）/ flat（观望）。"""
        # 先判看空再判观望：避免"建议减仓并观望"这类复合建议被误判为纯观望
        text = cls._normalize_text(operation_advice)
        if cls._matches_intent(text, cls._BEARISH_KEYWORDS):
            return "down"
        if cls._first_intent_position(text, cls._WAIT_KEYWORDS) is not None:
            wait_pos = cls._first_intent_position(text, cls._WAIT_KEYWORDS)
            bullish_pos = cls._first_intent_position(text, cls._BULLISH_KEYWORDS)
            hold_pos = cls._first_intent_position(text, cls._HOLD_KEYWORDS)
            if (bullish_pos is None or wait_pos < bullish_pos) and (
                hold_pos is None or wait_pos < hold_pos
            ):
                return "flat"
        if cls._matches_intent(text, cls._BULLISH_KEYWORDS):
            return "up"
        if cls._matches_intent(text, cls._HOLD_KEYWORDS):
            return "not_down"
        if cls._matches_intent(text, cls._WAIT_KEYWORDS):
            return "flat"
        return "flat"

    @classmethod
    def infer_position_recommendation(cls, operation_advice: Optional[str]) -> str:
        """根据操作建议推断推荐持仓：long（持仓）/ cash（空仓，因系统仅做多）。

        优先级：看空/观望 → cash；看多/持有 → long；无法识别 → cash。
        """
        text = cls._normalize_text(operation_advice)
        if cls._matches_intent(text, cls._BEARISH_KEYWORDS):
            return "cash"
        wait_pos = cls._first_intent_position(text, cls._WAIT_KEYWORDS)
        if wait_pos is not None:
            bullish_pos = cls._first_intent_position(text, cls._BULLISH_KEYWORDS)
            hold_pos = cls._first_intent_position(text, cls._HOLD_KEYWORDS)
            if (bullish_pos is None or wait_pos < bullish_pos) and (
                hold_pos is None or wait_pos < hold_pos
            ):
                return "cash"
        if cls._matches_intent(text, cls._BULLISH_KEYWORDS) or cls._matches_intent(text, cls._HOLD_KEYWORDS):
            return "long"
        if cls._matches_intent(text, cls._WAIT_KEYWORDS):
            return "cash"
        return "cash"

    @classmethod
    def evaluate_single(
        cls,
        *,
        operation_advice: Optional[str],
        analysis_date: date,
        start_price: float,
        forward_bars: Sequence[DailyBarLike],
        stop_loss: Optional[float],
        take_profit: Optional[float],
        config: EvaluationConfig,
    ) -> Dict[str, Any]:
        """用历史分析建议对照其后的日线 Bar 进行单条回测评估。

        说明：
        - 日线 Bar 无法还原当日盘中的先后顺序。若同一根 Bar 内止损价与止盈价
          同时被触及，则记 first_hit="ambiguous"，并在模拟出场时假设先触发止损，
          以对风险估计保持保守口径。
        """

        if start_price is None or start_price <= 0:
            return {
                "analysis_date": analysis_date,
                "operation_advice": operation_advice,
                "position_recommendation": cls.infer_position_recommendation(operation_advice),
                "direction_expected": cls.infer_direction_expected(operation_advice),
                "eval_status": "error",
            }

        eval_days = int(config.eval_window_days)
        if eval_days <= 0:
            raise ValueError("eval_window_days must be positive")

        if len(forward_bars) < eval_days:
            return {
                "analysis_date": analysis_date,
                "operation_advice": operation_advice,
                "position_recommendation": cls.infer_position_recommendation(operation_advice),
                "direction_expected": cls.infer_direction_expected(operation_advice),
                "eval_status": "insufficient_data",
                "eval_window_days": eval_days,
            }

        window_bars = list(forward_bars[:eval_days])
        end_close = window_bars[-1].close
        highs = [b.high for b in window_bars if b.high is not None]
        lows = [b.low for b in window_bars if b.low is not None]
        max_high = max(highs) if highs else None
        min_low = min(lows) if lows else None

        stock_return_pct: Optional[float]
        if end_close is None:
            stock_return_pct = None
        else:
            stock_return_pct = (end_close - start_price) / start_price * 100

        direction_expected = cls.infer_direction_expected(operation_advice)
        position = cls.infer_position_recommendation(operation_advice)

        outcome, direction_correct = cls._classify_outcome(
            stock_return_pct=stock_return_pct,
            direction_expected=direction_expected,
            neutral_band_pct=config.neutral_band_pct,
        )

        (
            hit_stop_loss,
            hit_take_profit,
            first_hit,
            first_hit_date,
            first_hit_days,
            simulated_exit_price,
            simulated_exit_reason,
        ) = cls._evaluate_targets(
            position=position,
            stop_loss=stop_loss,
            take_profit=take_profit,
            window_bars=window_bars,
            end_close=end_close,
        )

        simulated_entry_price = start_price if position == "long" else None
        simulated_return_pct: Optional[float]
        if position != "long":
            simulated_return_pct = 0.0
        elif simulated_exit_price is None:
            simulated_return_pct = None
        else:
            simulated_return_pct = (simulated_exit_price - start_price) / start_price * 100

        return {
            "analysis_date": analysis_date,
            "eval_window_days": eval_days,
            "engine_version": config.engine_version,
            "eval_status": "completed",
            "operation_advice": operation_advice,
            "position_recommendation": position,
            "start_price": start_price,
            "end_close": end_close,
            "max_high": max_high,
            "min_low": min_low,
            "stock_return_pct": stock_return_pct,
            "direction_expected": direction_expected,
            "direction_correct": direction_correct,
            "outcome": outcome,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "hit_stop_loss": hit_stop_loss,
            "hit_take_profit": hit_take_profit,
            "first_hit": first_hit,
            "first_hit_date": first_hit_date,
            "first_hit_trading_days": first_hit_days,
            "simulated_entry_price": simulated_entry_price,
            "simulated_exit_price": simulated_exit_price,
            "simulated_exit_reason": simulated_exit_reason,
            "simulated_return_pct": simulated_return_pct,
        }

    @classmethod
    def compute_summary(
        cls,
        *,
        results: Iterable[BacktestResultLike],
        scope: str,
        code: Optional[str],
        eval_window_days: int,
        engine_version: str,
    ) -> Dict[str, Any]:
        """将多条 BacktestResult 记录聚合为汇总指标（胜率、方向准确率、触发率等）。"""
        results_list = list(results)

        total = len(results_list)
        completed = [r for r in results_list if (r.eval_status or "") == "completed"]
        insufficient_count = sum(1 for r in results_list if (r.eval_status or "") == "insufficient_data")

        long_count = sum(1 for r in completed if (r.position_recommendation or "") == "long")
        cash_count = sum(1 for r in completed if (r.position_recommendation or "") == "cash")

        win_count = sum(1 for r in completed if (r.outcome or "") == "win")
        loss_count = sum(1 for r in completed if (r.outcome or "") == "loss")
        neutral_count = sum(1 for r in completed if (r.outcome or "") == "neutral")

        direction_denominator = sum(1 for r in completed if r.direction_correct is not None)
        direction_numerator = sum(1 for r in completed if r.direction_correct is True)
        direction_accuracy_pct = (
            round(direction_numerator / direction_denominator * 100, 2) if direction_denominator else None
        )

        win_loss_denominator = win_count + loss_count
        win_rate_pct = round(win_count / win_loss_denominator * 100, 2) if win_loss_denominator else None
        neutral_rate_pct = round(neutral_count / len(completed) * 100, 2) if completed else None

        avg_stock_return_pct = cls._average([r.stock_return_pct for r in completed])
        avg_simulated_return_pct = cls._average([r.simulated_return_pct for r in completed])

        stop_applicable = [
            r
            for r in completed
            if (r.position_recommendation or "") == "long" and r.hit_stop_loss is not None
        ]
        stop_loss_trigger_rate = (
            round(sum(1 for r in stop_applicable if r.hit_stop_loss is True) / len(stop_applicable) * 100, 2)
            if stop_applicable
            else None
        )

        take_profit_applicable = [
            r
            for r in completed
            if (r.position_recommendation or "") == "long" and r.hit_take_profit is not None
        ]
        take_profit_trigger_rate = (
            round(
                sum(1 for r in take_profit_applicable if r.hit_take_profit is True) / len(take_profit_applicable) * 100,
                2,
            )
            if take_profit_applicable
            else None
        )

        any_target_applicable = [
            r
            for r in completed
            if (r.position_recommendation or "") == "long"
            and (r.hit_stop_loss is not None or r.hit_take_profit is not None)
        ]
        ambiguous_rate = (
            round(
                sum(1 for r in any_target_applicable if (r.first_hit or "") == "ambiguous")
                / len(any_target_applicable)
                * 100,
                2,
            )
            if any_target_applicable
            else None
        )
        avg_days_to_first_hit = cls._average(
            [
                float(r.first_hit_trading_days)
                for r in any_target_applicable
                if r.first_hit_trading_days is not None and (r.first_hit or "") in ("stop_loss", "take_profit", "ambiguous")
            ]
        )

        advice_breakdown = cls._compute_advice_breakdown(completed)
        diagnostics = cls._compute_diagnostics(results_list)

        return {
            "scope": scope,
            "code": code,
            "eval_window_days": int(eval_window_days),
            "engine_version": engine_version,
            "total_evaluations": total,
            "completed_count": len(completed),
            "insufficient_count": insufficient_count,
            "long_count": long_count,
            "cash_count": cash_count,
            "win_count": win_count,
            "loss_count": loss_count,
            "neutral_count": neutral_count,
            "direction_accuracy_pct": direction_accuracy_pct,
            "win_rate_pct": win_rate_pct,
            "neutral_rate_pct": neutral_rate_pct,
            "avg_stock_return_pct": avg_stock_return_pct,
            "avg_simulated_return_pct": avg_simulated_return_pct,
            "stop_loss_trigger_rate": stop_loss_trigger_rate,
            "take_profit_trigger_rate": take_profit_trigger_rate,
            "ambiguous_rate": ambiguous_rate,
            "avg_days_to_first_hit": avg_days_to_first_hit,
            "advice_breakdown": advice_breakdown,
            "diagnostics": diagnostics,
        }

    @staticmethod
    def _normalize_text(value: Optional[str]) -> str:
        """关键词匹配前，对自由文本建议做归一化（去空白、转小写）。"""
        return str(value or "").strip().lower()

    @classmethod
    def _matches_intent(cls, text: str, keywords: Sequence[str]) -> bool:
        """判断文本是否表达了某个关键词的意图，并考虑否定词（negation）的影响。

        第一层：精确匹配（覆盖"买入""hold"这类干净标签）。
        第二层：带否定保护的子串匹配。
        约定关键词均为小写，与 _normalize_text 的输出保持一致。
        """
        return cls._first_intent_position(text, keywords) is not None

    @classmethod
    def _first_intent_position(cls, text: str, keywords: Sequence[str]) -> Optional[int]:
        """返回意图关键词最先出现的位置下标，未命中则返回 None。"""
        if not text:
            return None

        best_pos: Optional[int] = None

        for kw in keywords:
            if not kw:
                continue
            if text == kw:
                return 0

            keyword = kw.lower().strip()
            if not keyword:
                continue

            # ASCII 关键词用词边界匹配，避免 "watch" 被误判为命中 "wait" 这类假阳性。
            if bool(re.search(r"[a-z]", keyword)):
                for match in re.finditer(
                    rf"(?<![a-zA-Z0-9_]){re.escape(keyword)}(?![a-zA-Z0-9_])",
                    text,
                ):
                    if not cls._is_negated(text[: match.start()], keyword):
                        pos = match.start()
                        if best_pos is None or pos < best_pos:
                            best_pos = pos
                            break
                    continue

            # 非 ASCII 词条（中文）用子串匹配，这样"建议买入"等自然语言表述也能命中。
            if re.search(r"[\u4e00-\u9fff]", keyword):
                start = 0
                while True:
                    match_idx = text.find(keyword, start)
                    if match_idx < 0:
                        break
                    if not cls._is_negated(text[:match_idx], keyword):
                        if best_pos is None or match_idx < best_pos:
                            best_pos = match_idx
                        break
                    start = match_idx + len(keyword)
                continue

        return best_pos

    @classmethod
    def _is_negated(cls, prefix: str, keyword: str) -> bool:
        """判断候选意图前面的文本是否构成否定（negation）。"""
        stripped = prefix.rstrip()
        target = (keyword or "").lower().strip()
        if not target:
            return False

        if any(stripped.endswith(neg) for neg in cls._NEGATION_PATTERNS):
            return True

        # 限定"否定词 + 动作动词"的紧邻匹配，避免把条件分句里的否定误伤到核心建议意图。
        # 只回看关键词前 12 个字符：既覆盖"不要买入"这类紧邻否定，
        # 又避免长句中远处的否定词（如"虽然不…但是买入"）误伤核心意图
        lookback = stripped[-12:]
        for neg in cls._NEGATION_PATTERNS:
            if not neg:
                continue
            neg_idx = lookback.rfind(neg)
            if neg_idx < 0:
                continue

            suffix_gap = lookback[neg_idx + len(neg):].strip()
            if not suffix_gap:
                return True
            if any(ch in suffix_gap for ch in "，,。；;:!?！？"):
                continue

            if cls._contains_keyword(suffix_gap, target):
                return True

            # 英文短间隔规则：否定词后紧跟 to 等连接词（如 "not to sell"）时视为否定。
            if not any(ch >= "\u4e00" and ch <= "\u9fff" for ch in suffix_gap):
                if len(suffix_gap) <= 6:
                    return True
                continue

            if cls._is_negation_connector_gap(suffix_gap):
                return True

        return False

    @classmethod
    def _contains_keyword(cls, text: str, keyword: str) -> bool:
        """判断 *keyword* 是否存在于文本中（带意图感知的边界处理）。"""
        if not text or not keyword:
            return False
        if bool(re.search(r"[a-z]", keyword)):
            return bool(re.search(rf"(?<![a-zA-Z0-9_]){re.escape(keyword)}(?![a-zA-Z0-9_])", text))
        return keyword in text

    @classmethod
    def _is_negation_connector_gap(cls, gap: str) -> bool:
        """判断一段短的中文否定间隔是否仍构成有效的否定桥接（如"不应买入"）。"""
        compact = re.sub(r"[\s,，。；;:!?！？]", "", gap).strip()
        if not compact:
            return True
        return compact in cls._NEGATION_CONNECTOR_WORDS

    @classmethod
    def _classify_outcome(
        cls,
        *,
        stock_return_pct: Optional[float],
        direction_expected: str,
        neutral_band_pct: float,
    ) -> tuple[Optional[str], Optional[bool]]:
        """将实际涨跌幅与推断出的建议方向对照，判出 win/loss/neutral 及方向是否正确。"""
        if stock_return_pct is None:
            return None, None

        band = abs(float(neutral_band_pct))
        r = float(stock_return_pct)

        if direction_expected == "up":
            if r >= band:
                return "win", True
            if r <= -band:
                return "loss", False
            return "neutral", None

        if direction_expected == "down":
            if r <= -band:
                return "win", True
            if r >= band:
                return "loss", False
            return "neutral", None

        # not_down（持有/震荡）：只要不下跌即视为判断正确，中性带内的小幅上涨也算赢
        if direction_expected == "not_down":
            if r >= 0:
                return "win", True
            if r <= -band:
                return "loss", False
            return "neutral", None

        # flat（观望）：窗口内波动落在中性带内即为"判断正确"，否则视为踏空/误判
        if abs(r) <= band:
            return "win", True
        return "loss", False

    @classmethod
    def _evaluate_targets(
        cls,
        *,
        position: str,
        stop_loss: Optional[float],
        take_profit: Optional[float],
        window_bars: List[DailyBarLike],
        end_close: Optional[float],
    ) -> tuple[
        Optional[bool],
        Optional[bool],
        str,
        Optional[date],
        Optional[int],
        Optional[float],
        str,
    ]:
        """遍历后续日线 Bar，评估止损/止盈的触发情况。

        日线 Bar 提供最高/最低价但无法还原盘中先后顺序。当同一根 Bar 内两档目标
        价格同时被触及，结果记 ``ambiguous``，且模拟出场假设先触发止损，以使风险
        估计保持保守口径。
        """
        if position != "long":
            return (
                None,
                None,
                "not_applicable",
                None,
                None,
                None,
                "cash",
            )

        has_any_target = stop_loss is not None or take_profit is not None
        if not has_any_target:
            return (
                None,
                None,
                "neither",
                None,
                None,
                end_close,
                "window_end",
            )

        hit_sl: Optional[bool] = None if stop_loss is None else False
        hit_tp: Optional[bool] = None if take_profit is None else False
        first_hit = "neither"
        first_hit_date: Optional[date] = None
        first_hit_days: Optional[int] = None
        exit_price: Optional[float] = end_close
        exit_reason = "window_end"

        for idx, bar in enumerate(window_bars, start=1):
            low = bar.low
            high = bar.high
            stop_hit = stop_loss is not None and low is not None and low <= stop_loss
            tp_hit = take_profit is not None and high is not None and high >= take_profit

            if stop_hit:
                hit_sl = True
            if tp_hit:
                hit_tp = True

            if not stop_hit and not tp_hit:
                continue

            first_hit_date = bar.date
            # idx 从 1 开始，故此处即为"自分析日算起的第几个交易日触发"
            first_hit_days = idx

            if stop_hit and tp_hit:
                # 同一根 Bar 内两档都被触及，无法还原先后顺序，保守按止损处理
                first_hit = "ambiguous"
                exit_price = stop_loss
                exit_reason = "ambiguous_stop_loss"
                break

            if stop_hit:
                first_hit = "stop_loss"
                exit_price = stop_loss
                exit_reason = "stop_loss"
                break

            first_hit = "take_profit"
            exit_price = take_profit
            exit_reason = "take_profit"
            break

        return (
            hit_sl,
            hit_tp,
            first_hit,
            first_hit_date,
            first_hit_days,
            exit_price,
            exit_reason,
        )

    @staticmethod
    def _average(values: Iterable[Optional[float]]) -> Optional[float]:
        """返回四舍五入后的平均值，忽略缺失（None）的数值；无有效值时返回 None。"""
        items = [float(v) for v in values if v is not None]
        if not items:
            return None
        return round(sum(items) / len(items), 4)

    @staticmethod
    def _compute_advice_breakdown(results: List[BacktestResultLike]) -> Dict[str, Any]:
        """按原始操作建议文本聚合各建议的胜/负/中性计数及胜率。"""
        breakdown: Dict[str, Dict[str, int]] = {}
        for row in results:
            raw_advice = row.operation_advice
            advice = (raw_advice if isinstance(raw_advice, str) else str(raw_advice or "")).strip() or "(unknown)"
            bucket = breakdown.setdefault(advice, {"total": 0, "win": 0, "loss": 0, "neutral": 0})
            bucket["total"] += 1
            outcome = (row.outcome or "").strip()
            if outcome in ("win", "loss", "neutral"):
                bucket[outcome] += 1

        enriched: Dict[str, Any] = {}
        for advice, bucket in breakdown.items():
            win = bucket["win"]
            loss = bucket["loss"]
            denom = win + loss
            win_rate = round(win / denom * 100, 2) if denom else None
            enriched[advice] = {**bucket, "win_rate_pct": win_rate}
        return enriched

    @staticmethod
    def _compute_diagnostics(results: List[BacktestResultLike]) -> Dict[str, Any]:
        """返回底层状态计数（eval_status、first_hit 分布），供 API/调试面板使用。"""
        status_counts: Dict[str, int] = {}
        first_hit_counts: Dict[str, int] = {}
        for row in results:
            status = (row.eval_status or "").strip() or "(unknown)"
            status_counts[status] = status_counts.get(status, 0) + 1
            first_hit = (row.first_hit or "").strip() or "(none)"
            first_hit_counts[first_hit] = first_hit_counts.get(first_hit, 0) + 1
        return {
            "eval_status": status_counts,
            "first_hit": first_hit_counts,
        }
