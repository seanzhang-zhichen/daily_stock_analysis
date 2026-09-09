# -*- coding: utf-8 -*-
"""
AgentMemory —— 供 Agent 自我学习使用的持久化结构化记忆。

主要能力：
1. **分析记忆（analysis memory）** —— 存储带结果的历史分析，让 Agent 能
   从自身过往预测记录中学习。
2. **置信度校准（confidence calibration）** —— 依据历史准确率调整 Agent
   置信度（仅在样本量足够后才启用）。
3. **技能表现追踪（skill performance tracking）** —— 统计各技能的胜率与
   信号准确率，用于自动加权。

存储复用现有的 SQLAlchemy 数据库层（``AnalysisHistory`` + ``BacktestResult``
两张表），不再引入新的存储。

.. note::
   记忆能力由 ``AGENT_MEMORY_ENABLED=true`` 开关控制。
   关闭时，所有方法返回中性/默认值。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 校准生效前所需的最小样本数
_MIN_CALIBRATION_SAMPLES = 30
# 近期准确率计算的滚动窗口大小
_ROLLING_WINDOW = 50


@dataclass
class CalibrationResult:
    """Agent 或技能的置信度校准数据。"""
    agent_name: str = ""
    total_samples: int = 0
    historical_accuracy: float = 0.5  # 历史准确率，范围 0.0–1.0
    direction_accuracy: float = 0.5
    avg_confidence: float = 0.5
    calibrated: bool = False  # 样本量是否达到阈值
    calibration_factor: float = 1.0  # 原始置信度乘以该系数得到校准值


@dataclass
class AnalysisMemoryEntry:
    """注入上下文用的历史分析记忆条目。"""
    stock_code: str = ""
    date: str = ""
    signal: str = ""
    sentiment_score: int = 50
    price_at_analysis: float = 0.0
    outcome_5d: Optional[float] = None  # 5 个交易日后的涨跌幅
    outcome_20d: Optional[float] = None  # 20 个交易日后的涨跌幅
    was_correct: Optional[bool] = None


class AgentMemory:
    """用于 Agent 自我改进的结构化记忆系统。

    用法示例::

        memory = AgentMemory()
        # 获取历史分析用于注入上下文
        past = memory.get_stock_history("600519", limit=5)
        # 校准置信度
        cal = memory.get_calibration("technical", stock_code="600519")
    """

    def __init__(self, enabled: bool = False, min_samples: int = _MIN_CALIBRATION_SAMPLES):
        """以特性开关与校准样本阈值创建记忆访问实例。"""
        self.enabled = enabled
        self.min_samples = min_samples

    @classmethod
    def from_config(cls) -> "AgentMemory":
        """根据当前配置创建 AgentMemory 实例。

        配置读取失败时回退为关闭状态，保证记忆功能不影响主流程。
        """
        try:
            from src.config import get_config
            config = get_config()
            enabled = getattr(config, "agent_memory_enabled", False)
            return cls(enabled=enabled)
        except Exception:
            return cls(enabled=False)

    # -----------------------------------------------------------------
    # 分析历史检索
    # -----------------------------------------------------------------

    def get_stock_history(
        self,
        stock_code: str,
        limit: int = 5,
    ) -> List[AnalysisMemoryEntry]:
        """获取某只股票近期的分析结果。

        返回结构化条目，可注入 Agent 上下文中，用于从过往预测中学习。
        """
        if not self.enabled:
            return []

        try:
            from src.storage import get_db
            db = get_db()
            records = db.get_analysis_history(code=stock_code, limit=limit)
            entries = []
            for r in records:
                raw_result: Dict[str, Any] = {}
                # raw_result 可能是 JSON 字符串，也可能是已解析的字典，统一解析为字典
                if isinstance(getattr(r, "raw_result", None), str) and r.raw_result:
                    try:
                        parsed = json.loads(r.raw_result)
                        if isinstance(parsed, dict):
                            raw_result = parsed
                    except (TypeError, ValueError):
                        raw_result = {}
                elif isinstance(getattr(r, "raw_result", None), dict):
                    raw_result = dict(r.raw_result)

                # 信号字段存在多种历史命名，按优先级回退取值
                signal = raw_result.get("decision_type") or getattr(r, "operation_advice", "") or "hold"
                price_at_analysis = raw_result.get("current_price")
                if price_at_analysis is None:
                    price_at_analysis = 0.0

                entries.append(AnalysisMemoryEntry(
                    stock_code=stock_code,
                    date=(r.created_at.date().isoformat() if getattr(r, "created_at", None) else ""),
                    signal=signal,
                    sentiment_score=getattr(r, "sentiment_score", 50) or 50,
                    price_at_analysis=float(price_at_analysis or 0.0),
                    was_correct=None,
                ))
            return entries
        except Exception as exc:
            logger.debug("[AgentMemory] get_stock_history failed: %s", exc)
            return []

    # -----------------------------------------------------------------
    # 置信度校准
    # -----------------------------------------------------------------

    def get_calibration(
        self,
        agent_name: str,
        stock_code: Optional[str] = None,
        skill_id: Optional[str] = None,
        strategy_id: Optional[str] = None,
    ) -> CalibrationResult:
        """计算某个 Agent 或技能的置信度校准值。

        当 ``AGENT_MEMORY_ENABLED=false`` 或样本量不足时，
        返回中性校准结果（factor = 1.0）。
        """
        result = CalibrationResult(agent_name=agent_name)

        if not self.enabled:
            return result

        try:
            resolved_skill_id = skill_id or strategy_id
            stats = self._get_accuracy_stats(agent_name, stock_code, resolved_skill_id)
            result.total_samples = stats.get("total", 0)
            result.historical_accuracy = stats.get("accuracy", 0.5)
            result.direction_accuracy = stats.get("direction_accuracy", 0.5)
            result.avg_confidence = stats.get("avg_confidence", 0.5)

            if result.total_samples >= self.min_samples:
                result.calibrated = True
                # 校准思路：把置信度向历史准确率靠拢
                # 过度自信时系数 < 1，信心不足时系数 > 1
                if result.avg_confidence > 0:
                    result.calibration_factor = min(
                        1.5,
                        max(0.5, result.historical_accuracy / result.avg_confidence),
                    )
                else:
                    result.calibration_factor = 1.0
            else:
                result.calibrated = False
                result.calibration_factor = 1.0

        except Exception as exc:
            logger.debug("[AgentMemory] calibration failed for %s: %s", agent_name, exc)

        return result

    def calibrate_confidence(self, agent_name: str, raw_confidence: float, stock_code: Optional[str] = None) -> float:
        """对原始置信度应用校准。

        返回调整后的置信度，并夹在 [0.0, 1.0] 区间内。
        """
        cal = self.get_calibration(agent_name, stock_code=stock_code)
        if not cal.calibrated:
            return raw_confidence
        adjusted = raw_confidence * cal.calibration_factor
        return max(0.0, min(1.0, adjusted))

    # -----------------------------------------------------------------
    # 技能表现
    # -----------------------------------------------------------------

    def get_skill_performance(self, skill_id: str) -> Dict[str, Any]:
        """获取某个技能的表现指标。

        供 :class:`SkillAggregator` 计算权重使用。
        """
        if not self.enabled:
            return {"available": False}

        try:
            from src.services.backtest_service import BacktestService
            service = BacktestService()
            summary = service.get_skill_summary(skill_id)
            if summary:
                return {
                    "available": True,
                    "win_rate": summary.get("win_rate", 0.5),
                    "total_evaluations": summary.get("total_evaluations", 0),
                    "avg_return": summary.get("avg_return", 0.0),
                    "direction_accuracy": summary.get("direction_accuracy", 0.5),
                    "sufficient_samples": summary.get("total_evaluations", 0) >= self.min_samples,
                }
            return {"available": False}
        except Exception:
            return {"available": False}

    def get_strategy_performance(self, strategy_id: str) -> Dict[str, Any]:
        """为旧的基于策略的调用方提供的兼容包装。"""
        return self.get_skill_performance(strategy_id)

    # -----------------------------------------------------------------
    # 自动加权
    # -----------------------------------------------------------------

    def compute_skill_weights(
        self,
        skill_ids: List[str],
        use_backtest: bool = True,
    ) -> Dict[str, float]:
        """为一组技能计算归一化权重。

        历史表现越好的技能权重越高；样本量不足的技能取中性权重（1.0）。

        Returns:
            字典：skill_id → 权重（归一化后均值约等于 1.0）
        """
        if not self.enabled or not use_backtest:
            return {sid: 1.0 for sid in skill_ids}

        raw_weights: Dict[str, float] = {}
        for sid in skill_ids:
            perf = self.get_skill_performance(sid)
            if perf.get("sufficient_samples"):
                # 权重 = 0.5 + 胜率，范围 0.5 到 1.5
                raw_weights[sid] = 0.5 + perf.get("win_rate", 0.5)
            else:
                raw_weights[sid] = 1.0

        # 归一化，使均值 = 1.0
        if raw_weights:
            mean_w = sum(raw_weights.values()) / len(raw_weights)
            if mean_w > 0:
                return {sid: w / mean_w for sid, w in raw_weights.items()}

        return {sid: 1.0 for sid in skill_ids}

    def compute_strategy_weights(
        self,
        strategy_ids: List[str],
        use_backtest: bool = True,
    ) -> Dict[str, float]:
        """为旧的基于策略的调用方提供的兼容包装。"""
        return self.compute_skill_weights(strategy_ids, use_backtest=use_backtest)

    # -----------------------------------------------------------------
    # 内部实现
    # -----------------------------------------------------------------

    def _get_accuracy_stats(
        self,
        agent_name: str,
        stock_code: Optional[str],
        skill_id: Optional[str],
    ) -> Dict[str, Any]:
        """从回测历史中聚合准确率统计。"""
        try:
            from src.services.backtest_service import BacktestService
            service = BacktestService()

            if skill_id:
                summary = service.get_skill_summary(skill_id)
            elif stock_code:
                summary = service.get_stock_summary(stock_code)
            else:
                # 无技能与股票维度时退化为全局汇总
                summary = service.get_global_summary() if hasattr(service, "get_global_summary") else None

            if summary:
                return {
                    "total": summary.get("total_evaluations", 0),
                    "accuracy": summary.get("win_rate", 0.5),
                    "direction_accuracy": summary.get("direction_accuracy", 0.5),
                    "avg_confidence": 0.6,  # 历史数据近似值
                }
        except Exception:
            pass
        return {"total": 0, "accuracy": 0.5, "direction_accuracy": 0.5, "avg_confidence": 0.5}
