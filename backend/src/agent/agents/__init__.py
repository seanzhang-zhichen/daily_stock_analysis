# -*- coding: utf-8 -*-
"""
多 Agent 流水线中的各类专用 Agent。

每个 Agent 类都继承自 :class:`BaseAgent`，实现专注的分析范围
（技术面、情报、决策、风险）。
"""

# 导入所有 Agent 类，供外部统一引用
from src.agent.agents.base_agent import BaseAgent  # 抽象基类，定义所有 Agent 的通用接口
from src.agent.agents.technical_agent import TechnicalAgent  # 技术面分析 Agent
from src.agent.agents.intel_agent import IntelAgent  # 情报/情绪分析 Agent
from src.agent.agents.decision_agent import DecisionAgent  # 决策 Agent
from src.agent.agents.risk_agent import RiskAgent  # 风险筛查 Agent
from src.agent.agents.portfolio_agent import PortfolioAgent  # 组合层面分析 Agent

# 公开接口列表：定义本模块对外暴露的所有 Agent 类
__all__ = [
    "BaseAgent",
    "TechnicalAgent",
    "IntelAgent",
    "DecisionAgent",
    "RiskAgent",
    "PortfolioAgent",
]
