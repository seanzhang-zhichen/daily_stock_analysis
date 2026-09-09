# -*- coding: utf-8 -*-
"""深度研究命令处理器：基于 ``ResearchAgent`` 调度长程调研任务。

被机器人命令分发器（`bot/dispatcher.py`）按 ``/research`` 触发，
支持两种用法：传入股票代码做个股深度研究，或传入自由文本做主题研究。
"""

import logging
import re
import time
from typing import List, Optional

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse
from src.config import get_config

logger = logging.getLogger(__name__)

# 股票代码识别：A股 6 位数字 / 港股 HK+5 位数字 / 美股 1-5 字母（可选交易所后缀）
_RESEARCH_STOCK_CODE_RE = re.compile(
    r"^\d{6}$|^HK\d{5}$|^[A-Z]{1,5}(?:\.[A-Z]{1,2})?$"
)


class ResearchCommand(BotCommand):
    """``/research`` 命令处理器：调用深度研究 Agent 完成长程调研。

    用法示例：
        ``/research 600519``                对贵州茅台做深度研究。
        ``/research 600519 近期业绩风险``   对指定股票做有侧重问题的研究。
        ``/research 新能源板块前景分析``   对主题做自由文本研究。
    """

    @property
    def name(self) -> str:
        """返回调度器使用的主要命令名。"""
        return "research"

    @property
    def aliases(self) -> List[str]:
        """返回深度研究命令的中文与英文别名。"""
        return ["深研", "deepsearch"]

    @property
    def description(self) -> str:
        """返回帮助列表中展示的简短描述。"""
        return "Deep research on a stock or market topic"

    @property
    def usage(self) -> str:
        """返回帮助中展示的参数格式。"""
        return "/research <stock_code|topic> [specific question]"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        """执行深度研究：解析首个参数是否为股票代码，再分派给 Agent。"""
        if not args:
            return BotResponse.text_response(
                f"Usage: {self.usage}\n"
                "Example: /research 600519 近期有哪些风险\n"
                "Example: /research 新能源板块前景分析"
            )

        config = get_config()

        # 未开启 Agent 模式时直接返回提示，避免无效调用
        if not config.agent_mode:
            return BotResponse.text_response(
                "⚠️ Agent 模式未开启，无法使用深度研究功能。\n请在配置中设置 `AGENT_MODE=true`。"
            )

        # 解析参数：首个参数若匹配股票代码则抽出，剩余为问题文本
        query_parts = list(args)
        stock_code: Optional[str] = None

        # 中文逗号统一为英文逗号后再做匹配，避免输入习惯差异导致识别失败
        first = query_parts[0].upper().replace("，", ",")
        if _RESEARCH_STOCK_CODE_RE.match(first):
            stock_code = first
            query_parts = query_parts[1:]

        # 构造研究问题：缺省时给 Agent 一份通用的个股研究 prompt
        if query_parts:
            question = " ".join(query_parts)
        elif stock_code:
            question = f"Comprehensive deep research on stock {stock_code}: fundamentals, technicals, news sentiment, and risk factors"
        else:
            question = " ".join(args)

        if stock_code:
            question = f"[Stock: {stock_code}] {question}"

        # 调用深度研究 Agent
        try:
            from src.agent.research import ResearchAgent
            from src.agent.factory import get_tool_registry
            from src.agent.llm_adapter import LLMToolAdapter

            registry = get_tool_registry()
            llm_adapter = LLMToolAdapter(config)
            # 深度研究相关阈值均支持通过配置覆盖，使用 getattr 防御配置缺失
            budget = getattr(config, "agent_deep_research_budget", 30000)
            max_sub_questions = getattr(config, "agent_deep_research_max_sub_questions", 8)
            sub_question_steps = getattr(config, "agent_deep_research_sub_question_steps", 6)

            agent = ResearchAgent(
                tool_registry=registry,
                llm_adapter=llm_adapter,
                token_budget=budget,
                max_sub_questions=max_sub_questions,
                sub_question_max_steps=sub_question_steps,
            )

            research_timeout = getattr(config, "agent_deep_research_timeout", 600)
            logger.info("[ResearchCommand] Starting deep research (timeout=%ds): %s", research_timeout, question[:100])
            t0 = time.time()
            result = agent.research(
                question,
                {"stock_code": stock_code, "stock_name": ""} if stock_code else None,
                timeout_seconds=research_timeout,
            )
            # 优先采用 Agent 自报的耗时，缺失时回退到本地墙钟时间
            duration = result.duration_s or round(time.time() - t0, 1)

            if getattr(result, "timed_out", False):
                logger.warning("[ResearchCommand] Deep research timed out after %ss", duration)
                return BotResponse.text_response(
                    f"⏳ 深度研究超时（{duration}s / {research_timeout}s），请稍后重试或缩小研究范围。"
                )

            if result.success:
                # 组装富文本响应头，包含子问题数、来源数、耗时、token 消耗
                header = f"🔬 **Deep Research Report**\n"
                if stock_code:
                    header += f"Stock: {stock_code}\n"
                header += f"Sub-questions: {len(result.sub_questions)} | Sources: {result.findings_count}\n"
                header += f"Time: {duration}s | Tokens: {result.total_tokens:,}\n"
                header += "─" * 40 + "\n\n"

                report = header + result.report

                # 机器人消息有长度上限，超长时截断并提示完整报告可通过 API 获取
                max_len = 4000
                if len(report) > max_len:
                    report = report[:max_len] + "\n\n... (report truncated, full report available via API)"

                return BotResponse.markdown_response(report)
            else:
                return BotResponse.text_response(
                    f"⚠️ Research did not complete successfully.\n"
                    f"Partial results: {result.findings_count} findings collected.\n"
                    f"Time: {duration}s"
                )

        except Exception as exc:
            # 任意异常都不应让机器人进程崩溃，统一降级为错误文本
            logger.error("[ResearchCommand] Error: %s", exc, exc_info=True)
            return BotResponse.text_response(f"❌ Research failed: {exc}")
