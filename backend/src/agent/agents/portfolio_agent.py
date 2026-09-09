# -*- coding: utf-8 -*-
"""组合层面分析 Agent：把逐股意见聚合为组合级评估。

与单只股票分析不同，本 Agent 关注的是"一组持仓作为一个组合"的风险与配置：

- **仓位建议**：等权基准 + 基于置信度 / 波动率的加权。
- **相关性 & 板块集中度**：发现"看似分散实则相关"的隐性风险。
- **组合级风险指标**：beta、回撤、板块暴露。
- **跨市场联动**：A 股 ↔ 港股 ↔ 美股之间的溢出效应。

输入是普通编排流水线产出的逐股意见 ``ctx.data["stock_opinions"]``，
输出是把这些意见叠加组合视角后的二次结论。

典型用法::

    from src.agent.agents.portfolio_agent import PortfolioAgent
    agent = PortfolioAgent(model=model, registry=registry)
    result = agent.run(ctx)
"""

from __future__ import annotations

import logging
from typing import Optional

from src.agent.agents.base_agent import BaseAgent
from src.agent.protocols import AgentContext, AgentOpinion
from src.agent.runner import try_parse_json

logger = logging.getLogger(__name__)


class PortfolioAgent(BaseAgent):
    """组合层面分析的 Agent。

    该 Agent 在逐股分析完成后运行。它从 ``ctx.data["stock_opinions"]``
    （stock_code → opinion 的字典）读取逐股意见，并产出组合级评估，
    包括：仓位建议、板块集中度警告、相关性风险、跨市场联动、
    组合风险评分（1-10）以及再平衡建议。
    """

    # Agent 在注册表中的英文唯一标识
    agent_name = "portfolio"
    # 简短描述，用于日志 / 调试面板展示
    description = "Portfolio-level risk and allocation analysis"

    # 该 Agent 允许调用的工具白名单
    tool_names = [
        "get_realtime_quote",
        "get_stock_info",
    ]

    # ------------------------------------------------------------------
    # 提示词构造
    # ------------------------------------------------------------------

    def system_prompt(self, ctx: AgentContext) -> str:
        """构造组合级配置与风险提示词（system prompt）。"""
        return (
            "You are a professional **portfolio analyst** specializing in "
            "multi-asset allocation for A-share, HK, and US equity portfolios.\n\n"
            "## Your task\n"
            "Given individual stock analysis opinions, produce a **Portfolio Assessment** "
            "that covers:\n"
            "1. **Position Sizing** — suggested weight per stock (equal-weight baseline, "
            "adjusted by conviction and volatility).\n"
            "2. **Sector Concentration** — warn if > 40% in one sector.\n"
            "3. **Correlation Risk** — flag highly correlated pairs.\n"
            "4. **Cross-Market Linkage** — note HK/US spill-over effects on A-shares.\n"
            "5. **Portfolio Risk Score** — 1-10 scale.\n"
            "6. **Rebalance Suggestions** — trim/add recommendations.\n\n"
            "## Output format\n"
            "Return a single JSON object:\n"
            "```json\n"
            "{\n"
            '  "portfolio_risk_score": 6,\n'
            '  "total_stocks": 5,\n'
            '  "positions": [\n'
            '    {"code": "600519", "suggested_weight": 0.25, "signal": "buy", "note": "..."},\n'
            "    ...\n"
            "  ],\n"
            '  "sector_warnings": ["Consumer sector > 40%"],\n'
            '  "correlation_warnings": ["600519 & 000858 high correlation"],\n'
            '  "cross_market_notes": ["US tariff risk may impact export-heavy positions"],\n'
            '  "rebalance_suggestions": ["Trim 000858, add defensive sector exposure"],\n'
            '  "summary": "Portfolio is moderately concentrated ..."\n'
            "}\n"
            "```\n"
        )

    def build_user_message(self, ctx: AgentContext) -> str:
        """汇总逐股意见与风险标记，构建发送给 LLM 的 user message。"""
        # 从上下文收集逐股意见与股票列表
        stock_opinions = ctx.data.get("stock_opinions", {})
        stock_list = ctx.data.get("stock_list", [])

        parts = [f"Analyze the following portfolio of {len(stock_list) or len(stock_opinions)} stocks:\n"]

        # 优先展示带 opinion 的股票；缺失时退化为纯代码列表
        if stock_opinions:
            for code, opinion in stock_opinions.items():
                if isinstance(opinion, AgentOpinion):
                    parts.append(
                        f"- **{code}**: signal={opinion.signal}, "
                        f"confidence={opinion.confidence:.0%}, "
                        f"summary={opinion.reasoning[:200]}"
                    )
                elif isinstance(opinion, dict):
                    parts.append(
                        f"- **{code}**: signal={opinion.get('signal', 'unknown')}, "
                        f"confidence={opinion.get('confidence', 'N/A')}, "
                        f"summary={str(opinion.get('summary', ''))[:200]}"
                    )
        elif stock_list:
            for code in stock_list:
                parts.append(f"- {code}")

        # 如有来自逐股阶段的风险标记则一并附上
        if ctx.risk_flags:
            parts.append("\n### Risk Flags from Individual Analysis:")
            for flag in ctx.risk_flags:
                parts.append(f"- ⚠️ {flag}")

        if ctx.query:
            parts.append(f"\nUser request: {ctx.query}")

        return "\n".join(parts)

    def post_process(self, ctx: AgentContext, raw_response: str) -> Optional[AgentOpinion]:
        """解析 LLM 返回的 JSON 组合评估结果，并把风险评分映射成 buy/hold/sell 信号。

        Args:
            ctx: 当前调用上下文，结果将写入 ``ctx.data["portfolio_assessment"]``。
            raw_response: LLM 返回的原始字符串。

        Returns:
            Optional[AgentOpinion]: 标准化的 AgentOpinion；
                JSON 解析失败时返回保守的 hold 低置信度意见。
        """
        data = try_parse_json(raw_response)
        if data is None:
            logger.debug("[PortfolioAgent] post_process: failed to parse JSON")
            # 解析失败时给出兜底意见，避免上游链路崩溃
            return AgentOpinion(
                agent_name="portfolio",
                signal="hold",
                confidence=0.3,
                reasoning=raw_response[:500],
                raw_data={"raw": raw_response[:1000]},
            )

        # 把完整的组合评估结果存入上下文，便于下游展示 / 二阶段推理
        ctx.data["portfolio_assessment"] = data

        # 将 1-10 的风险评分粗粒度映射到三档信号
        risk_score = data.get("portfolio_risk_score", 5)
        signal = "hold"
        if risk_score <= 3:
            signal = "buy"
        elif risk_score >= 7:
            signal = "sell"

        return AgentOpinion(
            agent_name="portfolio",
            signal=signal,
            confidence=0.6,
            reasoning=data.get("summary", raw_response[:300]),
            raw_data=data,
        )
