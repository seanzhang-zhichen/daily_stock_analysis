# -*- coding: utf-8 -*-
"""情报 Agent：负责抓取并结构化最新新闻 / 公告 / 资金流证据。

工作职责：

- 搜索目标股票的最新新闻与公司公告；
- 运行综合情报搜索（含市场分析、风险扫描、业绩展望）；
- 检测风险事件（减持、业绩预亏、监管处罚、解禁、PE 异常等）；
- 解读 A 股主力资金净流入 / 净流出；
- 输出结构化 JSON 情绪意见，供下游 Agent（如 RiskAgent）复用。

注意：LLM prompt 字符串字面量不可修改——它是直接喂给模型的合同。
"""

from __future__ import annotations

import logging
from typing import Optional

from src.agent.agents.base_agent import BaseAgent
from src.agent.protocols import AgentContext, AgentOpinion
from src.agent.runner import try_parse_json

logger = logging.getLogger(__name__)


class IntelAgent(BaseAgent):
    """情报 / 情绪 Agent：把外部事件转化为可被决策 Agent 消费的结构化信号。

    关键行为：
    - 调用工具获取新闻与资金流；
    - 在 ``ctx.data["intel_opinion"]`` 缓存结构化结果，供下游复用；
    - 将 ``risk_alerts`` 注入 ``ctx.risk_flags``，让风险 Agent 看见。
    """

    # Agent 在注册表中的英文唯一标识
    agent_name = "intel"
    # 限制最多 4 次工具调用，避免在搜索阶段耗尽 token / 时间
    max_steps = 4
    # 允许调用的工具白名单：新闻 / 综合情报 / 个股信息 / 资金流
    tool_names = [
        "search_stock_news",
        "search_comprehensive_intel",
        "get_stock_info",
        "get_capital_flow",
    ]

    def system_prompt(self, ctx: AgentContext) -> str:
        """构造情报收集 system prompt 与 JSON 输出约定。

        Returns:
            str: 多行字符串，定义工作流、风险优先级、资金流解读、JSON schema。
        """
        return """\
You are an **Intelligence & Sentiment Agent** specialising in A-shares, \
HK, and US equities.

Your task: gather the latest news, announcements, and risk signals for \
the given stock, then produce a structured JSON opinion.

## Workflow
1. Search latest stock news (earnings, announcements, insider activity)
2. Run comprehensive intel search — this covers latest news, company \
announcements (公司公告), market analysis, risk checks, and earnings outlook
3. For A-share stocks, call get_capital_flow to obtain main-force (主力) \
capital inflow/outflow data and include it in your analysis
4. Classify positive catalysts and risk alerts
5. Assess overall sentiment

## Risk Detection Priorities
- Insider / major shareholder sell-downs (减持)
- Earnings warnings or pre-loss announcements (业绩预亏)
- Regulatory penalties or investigations
- Industry-wide policy headwinds
- Large lock-up expirations (解禁)
- PE valuation anomalies
- Sustained main-force capital outflow (主力持续净流出)

## Capital Flow Interpretation (A-shares only)
- main_net_inflow > 0: bullish signal (主力净流入)
- main_net_inflow < 0: bearish signal (主力净流出)
- inflow_5d / inflow_10d: medium-term accumulation or distribution trend

## Output Format
Return **only** a JSON object:
{
  "signal": "strong_buy|buy|hold|sell|strong_sell",
  "confidence": 0.0-1.0,
  "reasoning": "2-3 sentence summary of news/sentiment/capital-flow findings",
  "risk_alerts": ["list", "of", "detected", "risks"],
  "positive_catalysts": ["list", "of", "catalysts"],
  "sentiment_label": "very_positive|positive|neutral|negative|very_negative",
  "capital_flow_signal": "inflow|outflow|neutral|not_available",
  "key_news": [
    {"title": "...", "impact": "positive|negative|neutral"}
  ]
}
"""

    def build_user_message(self, ctx: AgentContext) -> str:
        """构造 user message：要求 LLM 取新闻 + 资金流证据并输出 JSON。

        Args:
            ctx: 当前调用上下文，至少需要 ``stock_code``；有 ``stock_name`` 时一并附上。

        Returns:
            str: 多行 Markdown 字符串，包含分步指引。
        """
        # 第一行明确"分析对象"，股票名仅在已知时附加
        parts = [f"Gather intelligence and assess sentiment for stock **{ctx.stock_code}**"]
        if ctx.stock_name:
            parts[0] += f" ({ctx.stock_name})"
        # 步骤提示：先取综合情报，再补资金流，最后输出 JSON 意见
        parts.append(
            "Steps:\n"
            "1. Call search_comprehensive_intel to get latest news, company announcements "
            "(公司公告), risk events, and earnings outlook.\n"
            "2. Call get_capital_flow to obtain main-force (主力) capital flow data "
            "(A-share only; skip for HK/US).\n"
            "3. Output the JSON opinion including capital_flow_signal."
        )
        return "\n".join(parts)

    def post_process(self, ctx: AgentContext, raw_text: str) -> Optional[AgentOpinion]:
        """解析 LLM 返回的 JSON 情绪意见，注入上下文风险标志。

        Args:
            ctx: 当前调用上下文，会写入 ``ctx.data["intel_opinion"]`` 与风险标志。
            raw_text: LLM 原始返回字符串。

        Returns:
            Optional[AgentOpinion]: 标准化意见；JSON 解析失败时返回 ``None``。
        """
        parsed = try_parse_json(raw_text)
        if parsed is None:
            logger.warning("[IntelAgent] failed to parse opinion JSON")
            return None

        # 缓存解析后的情报，供下游 Agent（尤其是 RiskAgent）复用，
        # 避免对同一批证据重复搜索。
        ctx.set_data("intel_opinion", parsed)

        # 把风险警报传递到上下文：每个非空字符串都作为一条风险标记
        for alert in parsed.get("risk_alerts", []):
            if isinstance(alert, str) and alert:
                ctx.add_risk_flag(category="intel", description=alert)

        return AgentOpinion(
            agent_name=self.agent_name,
            signal=parsed.get("signal", "hold"),
            confidence=float(parsed.get("confidence", 0.5)),
            reasoning=parsed.get("reasoning", ""),
            raw_data=parsed,
        )

