# -*- coding: utf-8 -*-
"""技术面 Agent：调用行情与指标工具，产出结构化的趋势 / 形态意见。

主要职责：

- 拉取实时行情与日 K 线历史数据；
- 调用趋势分析、均线、量能、形态识别等工具；
- 汇总技术面证据，输出 JSON 形态的意见（信号 + 置信度 + 关键位等）。

注意：LLM prompt 字符串字面量、JSON 输出 schema 都是与下游协议耦合的合同，
绝不能改动；本注释仅做说明。
"""

from __future__ import annotations

import logging
from typing import Optional

from src.agent.agents.base_agent import BaseAgent
from src.agent.protocols import AgentContext, AgentOpinion
from src.agent.runner import try_parse_json

logger = logging.getLogger(__name__)


class TechnicalAgent(BaseAgent):
    """技术面 Agent：调用行情与指标工具，输出结构化趋势意见。"""

    # Agent 注册表中的英文唯一标识
    agent_name = "technical"
    # 最多 6 步工具调用：拉行情 / 算指标 / 形态识别都吃 token，要控量
    max_steps = 6
    # 该 Agent 可调用的工具白名单
    tool_names = [
        "get_realtime_quote",
        "get_daily_history",
        "analyze_trend",
        "calculate_ma",
        "get_volume_analysis",
        "analyze_pattern",
        "get_chip_distribution",
        "get_analysis_context",
    ]

    def system_prompt(self, ctx: AgentContext) -> str:
        """构造技术分析 system prompt，可附带技能指引。

        Args:
            ctx: 当前调用上下文（实际未使用，保留以对齐父类签名）。

        Returns:
            str: 拼接好 baseline / skills 的完整 prompt。
        """
        # 注入"激活的交易技能"片段，由 Skill 机制按需追加
        skills = ""
        if self.skill_instructions:
            skills = f"\n## Active Trading Skills\n\n{self.skill_instructions}\n"
        # 注入技术面策略基线（如风险偏好、止盈止损默认参数）
        baseline = ""
        if self.technical_skill_policy:
            baseline = f"\n{self.technical_skill_policy}\n"

        return f"""\
You are a **Technical Analysis Agent** specialising in Chinese A-shares, \
Hong Kong stocks, and US equities.

Your task: perform a thorough technical analysis of the given stock and \
output a structured JSON opinion.

## Workflow (execute stages in order)
1. Fetch realtime quote + daily history (if not already provided)
2. Run trend analysis (MA alignment, MACD, RSI)
3. Analyse volume and chip distribution
4. Identify chart patterns

{baseline}
{skills}
## Output Format
Return **only** a JSON object (no markdown fences):
{{
  "signal": "strong_buy|buy|hold|sell|strong_sell",
  "confidence": 0.0-1.0,
  "reasoning": "2-3 sentence summary",
  "key_levels": {{
    "support": <float>,
    "resistance": <float>,
    "stop_loss": <float>
  }},
  "trend_score": 0-100,
  "ma_alignment": "bullish|neutral|bearish",
  "volume_status": "heavy|normal|light",
  "pattern": "<detected pattern or none>"
}}
"""

    def build_user_message(self, ctx: AgentContext) -> str:
        """构造 user message：让 LLM 主动补齐缺失行情后输出 JSON 意见。

        Args:
            ctx: 当前调用上下文。

        Returns:
            str: 多行文本，包含股票标识 + 操作指引。
        """
        # 第一行明确分析对象；股票名仅在已知时附加
        parts = [f"Perform technical analysis on stock **{ctx.stock_code}**"]
        if ctx.stock_name:
            parts[0] += f" ({ctx.stock_name})"
        parts.append("Use your tools to fetch any missing data, then output the JSON opinion.")
        return "\n".join(parts)

    def post_process(self, ctx: AgentContext, raw_text: str) -> Optional[AgentOpinion]:
        """解析 LLM 返回的 JSON 技术分析意见。

        Args:
            ctx: 当前调用上下文（本函数不写入）。
            raw_text: LLM 原始返回字符串。

        Returns:
            Optional[AgentOpinion]: 标准化意见，附带 ``key_levels``；
            JSON 解析失败时返回 ``None``。
        """
        parsed = try_parse_json(raw_text)
        if parsed is None:
            logger.warning("[TechnicalAgent] failed to parse opinion JSON")
            return None

        # 只把数字型价位保留为 float，其他字段（如 string 描述）丢弃，
        # 防止 LLM 偶尔给出非数字导致下游渲染崩溃
        key_levels = {
            k: float(v) for k, v in parsed.get("key_levels", {}).items()
            if isinstance(v, (int, float))
        }

        return AgentOpinion(
            agent_name=self.agent_name,
            signal=parsed.get("signal", "hold"),
            confidence=float(parsed.get("confidence", 0.5)),
            reasoning=parsed.get("reasoning", ""),
            key_levels=key_levels,
            raw_data=parsed,
        )
