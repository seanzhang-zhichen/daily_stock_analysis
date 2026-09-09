# -*- coding: utf-8 -*-
"""ResearchAgent —— 面向深度分析的深度研究智能体。

负责：
- 将复杂的研究问题拆解为若干子问题
- 迭代式搜索与信息收集
- 对发现进行交叉验证
- 产出结构化的研究报告

由 ``/research`` 命令或 API 异步任务接口触发，
面向长耗时分析场景设计（token 预算上限为 ``AGENT_DEEP_RESEARCH_BUDGET``）。
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from src.agent.llm_adapter import LLMToolAdapter
from src.agent.runner import RunLoopResult, run_agent_loop
from src.agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# 深度研究的默认 token 预算
_DEFAULT_TOKEN_BUDGET = 30000
_DEFAULT_MAX_SUB_QUESTIONS = 8
_DEFAULT_SUB_QUESTION_MAX_STEPS = 6
_DEFAULT_STEP_LLM_TIMEOUT_SECONDS = 600


class ResearchAgent:
    """多轮深度研究智能体。

    与固定步数的标准智能体循环不同，ResearchAgent：
    1. 将问题拆解为子问题（规划阶段）
    2. 针对每个子问题进行专门检索
    3. 将发现综合成完整的研究报告
    4. 依据可配置的 token 预算追踪累计用量
    """

    agent_name = "research"
    tool_names = [
        "search_stock_news",
        "search_comprehensive_intel",
        "get_stock_info",
        "get_realtime_quote",
        "get_daily_history",
        "get_sector_rankings",
        "get_market_indices",
    ]

    def __init__(
        self,
        tool_registry: ToolRegistry,
        llm_adapter: LLMToolAdapter,
        token_budget: int = _DEFAULT_TOKEN_BUDGET,
        max_sub_questions: int = _DEFAULT_MAX_SUB_QUESTIONS,
        sub_question_max_steps: int = _DEFAULT_SUB_QUESTION_MAX_STEPS,
    ):
        """保存依赖，并将研究规划/执行的上限参数约束到合法范围。"""
        self.tool_registry = tool_registry
        self.llm_adapter = llm_adapter
        self.token_budget = token_budget
        self.max_sub_questions = max(1, int(max_sub_questions))
        self.sub_question_max_steps = max(1, int(sub_question_max_steps))

    def research(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        timeout_seconds: Optional[float] = None,
    ) -> ResearchResult:
        """执行一次深度研究任务。

        Args:
            query: 研究问题或主题。
            context: 可选上下文（stock_code、stock_name 等）。
            progress_callback: 可选的进度回调。
            timeout_seconds: 可选的整个研究任务的总时间预算。

        Returns:
            一个包含报告与元数据的 :class:`ResearchResult` 实例。
        """
        started_at = time.monotonic()
        tokens_used = 0
        all_findings: List[Dict[str, Any]] = []
        questions: List[str] = [query]

        # 阶段一：拆解问题
        if self._is_timed_out(started_at, timeout_seconds):
            return self._build_timeout_result(
                query=query,
                questions=questions,
                findings_count=0,
                total_tokens=tokens_used,
                duration_s=round(time.monotonic() - started_at, 2),
                timeout_seconds=timeout_seconds,
            )
        if progress_callback:
            progress_callback({"type": "research_phase", "phase": "decompose", "message": "Decomposing research query..."})

        sub_questions = self._decompose_query(
            query,
            context,
            timeout_seconds=self._remaining_timeout_seconds(started_at, timeout_seconds),
        )
        tokens_used += sub_questions.get("tokens", 0)

        questions = sub_questions.get("questions", [query])[:self.max_sub_questions]
        if sub_questions.get("timed_out"):
            return self._build_timeout_result(
                query=query,
                questions=questions,
                findings_count=0,
                total_tokens=tokens_used,
                duration_s=round(time.monotonic() - started_at, 2),
                timeout_seconds=timeout_seconds,
            )
        logger.info("[ResearchAgent] decomposed into %d sub-questions", len(questions))

        # 阶段二：逐个研究子问题
        for i, question in enumerate(questions):
            if self._is_timed_out(started_at, timeout_seconds):
                return self._build_timeout_result(
                    query=query,
                    questions=questions,
                    findings_count=len(all_findings),
                    total_tokens=tokens_used,
                    duration_s=round(time.monotonic() - started_at, 2),
                    timeout_seconds=timeout_seconds,
                )
            if tokens_used >= self.token_budget:
                logger.warning("[ResearchAgent] token budget exceeded (%d/%d), stopping", tokens_used, self.token_budget)
                break

            if progress_callback:
                progress_callback({
                    "type": "research_phase",
                    "phase": "search",
                    "message": f"Researching ({i + 1}/{len(questions)}): {question[:60]}...",
                    "progress": (i + 1) / len(questions),
                })

            finding = self._research_sub_question(
                question,
                context,
                tokens_used,
                timeout_seconds=self._remaining_timeout_seconds(started_at, timeout_seconds),
            )
            tokens_used += finding.get("tokens", 0)
            if finding.get("timed_out"):
                return self._build_timeout_result(
                    query=query,
                    questions=questions,
                    findings_count=len(all_findings),
                    total_tokens=tokens_used,
                    duration_s=round(time.monotonic() - started_at, 2),
                    timeout_seconds=timeout_seconds,
                )
            all_findings.append(finding)

        # 阶段三：综合成报告
        if self._is_timed_out(started_at, timeout_seconds):
            return self._build_timeout_result(
                query=query,
                questions=questions,
                findings_count=len(all_findings),
                total_tokens=tokens_used,
                duration_s=round(time.monotonic() - started_at, 2),
                timeout_seconds=timeout_seconds,
            )
        if progress_callback:
            progress_callback({"type": "research_phase", "phase": "synthesize", "message": "Synthesising research report..."})

        report = (
            self._synthesise_report(
                query,
                all_findings,
                context,
                timeout_seconds=self._remaining_timeout_seconds(started_at, timeout_seconds),
            )
            if all_findings
            else {"content": "No findings gathered.", "tokens": 0}
        )
        tokens_used += report.get("tokens", 0)
        if report.get("timed_out"):
            return self._build_timeout_result(
                query=query,
                questions=questions,
                findings_count=len(all_findings),
                total_tokens=tokens_used,
                duration_s=round(time.monotonic() - started_at, 2),
                timeout_seconds=timeout_seconds,
            )

        duration = round(time.monotonic() - started_at, 2)

        return ResearchResult(
            success=not report.get("error"),
            report=report.get("content", ""),
            sub_questions=questions,
            findings_count=len(all_findings),
            total_tokens=tokens_used,
            duration_s=duration,
            error=report.get("error"),
        )

    @staticmethod
    def _remaining_timeout_seconds(started_at: float, timeout_seconds: Optional[float]) -> Optional[float]:
        """返回整个研究任务剩余的总时间预算。"""
        if timeout_seconds is None:
            return None
        return max(0.0, float(timeout_seconds) - (time.monotonic() - started_at))

    @staticmethod
    def _is_timed_out(started_at: float, timeout_seconds: Optional[float]) -> bool:
        """判断整个研究任务的截止时间是否已超。"""
        remaining = ResearchAgent._remaining_timeout_seconds(started_at, timeout_seconds)
        return remaining is not None and remaining <= 0

    @staticmethod
    def _resolve_step_timeout(default_timeout: int, timeout_seconds: Optional[float]) -> Optional[int]:
        """将单个阶段的超时收敛到整个研究任务剩余预算之内。"""
        if timeout_seconds is None:
            return default_timeout
        if timeout_seconds <= 0:
            return None
        return max(1, math.ceil(min(float(default_timeout), float(timeout_seconds))))

    @staticmethod
    def _looks_like_timeout_error(error: Any) -> bool:
        """尽力识别来自下层的超时类失败。"""
        message = str(error or "").lower()
        return (
            "timed out" in message
            or "timeout" in message
            or "insufficient budget" in message
            or "budget too low" in message
        )

    @staticmethod
    def _build_timeout_result(
        *,
        query: str,
        questions: List[str],
        findings_count: int,
        total_tokens: int,
        duration_s: float,
        timeout_seconds: Optional[float],
    ) -> ResearchResult:
        """构造结构化的超时结果，避免遗留未完成的工作。"""
        timeout_label = f"{timeout_seconds}s" if timeout_seconds is not None else "the configured limit"
        logger.warning("[ResearchAgent] timed out after %s for query: %s", timeout_label, query[:120])
        return ResearchResult(
            success=False,
            report="",
            sub_questions=questions,
            findings_count=findings_count,
            total_tokens=total_tokens,
            duration_s=duration_s,
            error=f"Deep research timed out after {timeout_label}",
            timed_out=True,
        )

    def _call_text_completion(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> Dict[str, Any]:
        """通过共享 adapter 执行一次纯文本 LLM 补全。"""
        response = self.llm_adapter.call_text(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        if response.provider == "error":
            raise RuntimeError(response.content or "LLM completion failed")
        return {
            "content": (response.content or "").strip(),
            "tokens": response.usage.get("total_tokens", 0),
        }

    def _decompose_query(
        self,
        query: str,
        context: Optional[Dict[str, Any]],
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """使用 LLM 将研究问题拆解为若干子问题。"""
        started_at = time.monotonic()
        stock_hint = ""
        if context and context.get("stock_code"):
            stock_hint = f"\nStock context: {context['stock_code']} ({context.get('stock_name', '')})"

        system = f"""\
You are a research planning assistant. Given a research query, decompose it \
into as many specific, searchable sub-questions as needed, up to \
{self.max_sub_questions}. Stop early when the coverage is sufficient; do not \
add filler questions.

Return a JSON object:
{{"questions": ["question 1", "question 2", ...]}}
"""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Research query: {query}{stock_hint}"},
        ]

        try:
            step_timeout = self._resolve_step_timeout(_DEFAULT_STEP_LLM_TIMEOUT_SECONDS, timeout_seconds)
            if step_timeout is None:
                return {"questions": [query], "tokens": 0, "timed_out": True}
            completion = self._call_text_completion(
                messages,
                temperature=0.3,
                max_tokens=400,
                timeout=step_timeout,
            )
            raw = completion["content"]
            tokens = completion["tokens"]

            # 解析 JSON（兼容 ```json 代码块包裹的情况）
            if raw.startswith("```"):
                raw = re.sub(r'^```(?:json)?\s*', '', raw)
                raw = re.sub(r'\s*```$', '', raw)
            parsed = json.loads(raw)
            raw_questions = parsed.get("questions", [query])
            questions = [
                str(item).strip()
                for item in raw_questions
                if str(item).strip()
            ]
            return {"questions": questions or [query], "tokens": tokens}
        except Exception as exc:
            logger.warning("[ResearchAgent] decompose failed: %s", exc)
            if timeout_seconds is not None and self._looks_like_timeout_error(exc):
                elapsed = time.monotonic() - started_at
                if elapsed >= float(timeout_seconds):
                    return {"questions": [query], "tokens": 0, "timed_out": True, "error": str(exc)}
                return {"questions": [query], "tokens": 0, "error": str(exc)}
            return {"questions": [query], "tokens": 0}

    def _research_sub_question(
        self,
        question: str,
        context: Optional[Dict[str, Any]],
        current_tokens: int,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """使用智能体循环研究单个子问题。"""
        started_at = time.monotonic()
        if timeout_seconds is not None and timeout_seconds <= 0:
            return {
                "question": question,
                "content": "",
                "tokens": 0,
                "success": False,
                "timed_out": True,
                "error": "Deep research timed out before sub-question execution",
            }
        remaining_budget = self.token_budget - current_tokens

        system = f"""\
You are a research agent investigating a specific question.
Use your tools to search for relevant information, then summarise \
your findings in 2-4 paragraphs.  Be factual and cite sources.
Token budget remaining: ~{remaining_budget}
"""
        stock_context = ""
        if context and context.get("stock_code"):
            stock_context = f" (related to stock {context['stock_code']})"

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Research question: {question}{stock_context}"},
        ]

        try:
            registry = self._filtered_registry()
            result: RunLoopResult = run_agent_loop(
                messages=messages,
                tool_registry=registry,
                llm_adapter=self.llm_adapter,
                max_steps=self.sub_question_max_steps,
                max_wall_clock_seconds=timeout_seconds,
                tool_call_timeout_seconds=timeout_seconds,
            )
            if not result.success and self._looks_like_timeout_error(result.error):
                error_text = str(result.error or "").lower()
                elapsed = time.monotonic() - started_at
                is_overall_timeout = timeout_seconds is not None and elapsed >= float(timeout_seconds)
                is_loop_budget_timeout = (
                    "agent timed out" in error_text
                    or "insufficient budget" in error_text
                    or "budget too low" in error_text
                )
                if is_overall_timeout or is_loop_budget_timeout:
                    return {
                        "question": question,
                        "content": "",
                        "tokens": result.total_tokens,
                        "success": False,
                        "timed_out": True,
                        "error": result.error,
                    }
            return {
                "question": question,
                "content": result.content,
                "tokens": result.total_tokens,
                "success": result.success,
            }
        except Exception as exc:
            logger.warning("[ResearchAgent] sub-question failed: %s", exc)
            if timeout_seconds is not None and self._looks_like_timeout_error(exc):
                elapsed = time.monotonic() - started_at
                if elapsed >= float(timeout_seconds):
                    return {
                        "question": question,
                        "content": "",
                        "tokens": 0,
                        "success": False,
                        "timed_out": True,
                        "error": str(exc),
                    }
            return {"question": question, "content": "", "tokens": 0, "success": False, "error": str(exc)}

    def _synthesise_report(
        self,
        original_query: str,
        findings: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]],
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """将所有发现综合成连贯的研究报告。"""
        started_at = time.monotonic()
        findings_text = "\n\n".join(
            f"### Sub-question: {f['question']}\n{f.get('content', 'No data')}"
            for f in findings if f.get("content")
        )

        system = """\
You are a senior research analyst. Synthesise the following research \
findings into a comprehensive, well-structured report.

## Report Structure
1. **Executive Summary** (2-3 sentences)
2. **Key Findings** (bullet points)
3. **Detailed Analysis** (sections per topic)
4. **Risk Factors** (if applicable)
5. **Conclusion & Recommendations**

Use Markdown formatting.  Be concise but thorough.
Output only the final report body. Do not include follow-up offers, task lists,
template suggestions, process explanations, or meta statements such as
"I can next", "if you want", "would you like", "here is a template",
"如果你愿意", "我可以下一步", or "我可以帮你整理成模板".
"""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Original query: {original_query}\n\n## Research Findings\n\n{findings_text}"},
        ]

        try:
            step_timeout = self._resolve_step_timeout(_DEFAULT_STEP_LLM_TIMEOUT_SECONDS, timeout_seconds)
            if step_timeout is None:
                return {"content": "", "tokens": 0, "timed_out": True}
            completion = self._call_text_completion(
                messages,
                temperature=0.3,
                max_tokens=2000,
                timeout=step_timeout,
            )
            content = completion["content"]
            tokens = completion["tokens"]
            return {"content": content, "tokens": tokens}
        except Exception as exc:
            logger.warning("[ResearchAgent] synthesis failed: %s", exc)
            if timeout_seconds is not None and self._looks_like_timeout_error(exc):
                elapsed = time.monotonic() - started_at
                if elapsed >= float(timeout_seconds):
                    return {"content": "", "tokens": 0, "timed_out": True, "error": str(exc)}
            return {"content": findings_text, "tokens": 0, "error": str(exc)}

    def _filtered_registry(self) -> ToolRegistry:
        """返回仅包含研究相关工具的工具注册表。

        复用 :meth:`BaseAgent._filtered_registry` 的过滤逻辑。
        """
        from src.agent.agents.base_agent import BaseAgent
        # 借用共享实现；该方法会遵循 self.tool_names / self.tool_registry。
        return BaseAgent._filtered_registry(self)


@dataclass
class ResearchResult:
    """深度研究任务的输出结果。"""

    success: bool = False
    report: str = ""
    sub_questions: List[str] = field(default_factory=list)
    findings_count: int = 0
    total_tokens: int = 0
    duration_s: float = 0.0
    error: Optional[str] = None
    timed_out: bool = False
