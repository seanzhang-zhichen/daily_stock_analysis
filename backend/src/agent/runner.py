# -*- coding: utf-8 -*-
"""共享执行器 —— 抽取出的 LLM + 工具执行循环。

提供 ``run_agent_loop``，这是此前内联在 ``AgentExecutor._run_loop`` 中的
ReAct 执行循环的唯一权威实现。所有当前与未来的 agent 都应委托给该执行器，
而不是各自重新实现循环。

设计目标：
- 保持与原始 ``_run_loop`` 相同的可观测行为
- 支持可插拔的回调以处理进度、消息历史与结果
- 保持无状态 —— 所有可变状态都放在调用方
"""

from __future__ import annotations

import json
import logging
import re
import time
import threading
import contextvars
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from src.agent.llm_adapter import LLMToolAdapter
from src.agent.tools.execution import TOOL_CANCEL_EVENT
from src.agent.tools.registry import ToolRegistry
from src.storage import persist_llm_usage as _persist_usage

logger = logging.getLogger(__name__)

# 工具名 → 进度消息所用的友好标签
_THINKING_TOOL_LABELS: Dict[str, str] = {
    "get_realtime_quote": "行情获取",
    "get_daily_history": "K线数据获取",
    "analyze_trend": "技术指标分析",
    "get_chip_distribution": "筹码分布分析",
    "search_stock_news": "新闻搜索",
    "search_comprehensive_intel": "综合情报搜索",
    "get_market_indices": "市场概览获取",
    "get_sector_rankings": "行业板块分析",
    "get_analysis_context": "历史分析上下文",
    "get_stock_info": "基本信息获取",
    "analyze_pattern": "K线形态识别",
    "get_volume_analysis": "量能分析",
    "calculate_ma": "均线计算",
    "get_skill_backtest_summary": "技能回测概览",
    "get_strategy_backtest_summary": "策略回测概览",
    "get_stock_backtest_summary": "个股回测数据",
}


# ============================================================
# RunLoopResult —— 一次 run_agent_loop 调用的输出
# ============================================================

@dataclass
class RunLoopResult:
    """:func:`run_agent_loop` 产生的输出。"""

    success: bool = False
    content: str = ""
    tool_calls_log: List[Dict[str, Any]] = field(default_factory=list)
    total_steps: int = 0
    total_tokens: int = 0
    provider: str = ""
    models_used: List[str] = field(default_factory=list)
    error: Optional[str] = None
    # 循环结束时的原始消息列表（调用方可能想持久化）
    messages: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def model(self) -> str:
        """本次运行中使用的去重后的模型名，以逗号分隔。"""
        return ", ".join(dict.fromkeys(m for m in self.models_used if m))


# ============================================================
# 辅助函数
# ============================================================

def serialize_tool_result(result: Any) -> str:
    """将工具结果序列化为 LLM 可消费的 JSON 字符串。"""
    if result is None:
        return json.dumps({"result": None})
    if isinstance(result, str):
        return result
    if isinstance(result, (dict, list)):
        try:
            return json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(result)
    if hasattr(result, "__dict__"):
        try:
            d = {k: v for k, v in result.__dict__.items() if not k.startswith("_")}
            return json.dumps(d, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(result)
    return str(result)


def _normalize_tool_stock_code(value: Any) -> Any:
    """规范化股票代码参数，使等价的港股变体共享同一个缓存键。"""
    if not isinstance(value, str):
        return value

    text = value.strip().upper()
    if not text:
        return text

    if text.endswith(".HK"):
        base = text[:-3]
        if base.isdigit() and 1 <= len(base) <= 5:
            return f"HK{base.zfill(5)}"

    if text.startswith("HK"):
        base = text[2:]
        if base.isdigit() and 1 <= len(base) <= 5:
            return f"HK{base.zfill(5)}"

    if text.isdigit() and len(text) == 5:
        return f"HK{text}"

    try:
        from data_provider.base import canonical_stock_code, normalize_stock_code

        return canonical_stock_code(normalize_stock_code(text))
    except Exception:
        return text


def _build_tool_cache_key(tool_name: str, arguments: Dict[str, Any]) -> Optional[str]:
    """为带有规范化股票代码参数的工具调用构造稳定的缓存键。"""
    if not isinstance(arguments, dict):
        return None

    normalized_args: Dict[str, Any] = {}
    for key, value in arguments.items():
        if key == "stock_code":
            normalized_args[key] = _normalize_tool_stock_code(value)
        else:
            normalized_args[key] = value

    try:
        payload = json.dumps(normalized_args, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return None
    return f"{tool_name}:{payload}"


def _is_non_retriable_tool_result(result: Any) -> bool:
    """当工具结果显式告知 agent 不要重试时返回 True。"""
    return (
        isinstance(result, dict)
        and bool(result.get("error"))
        and result.get("retriable") is False
    )


def parse_dashboard_json(content: str) -> Optional[Dict[str, Any]]:
    """从 agent 文本中提取并解析 Decision Dashboard JSON。

    依次尝试多种策略：
    1. Markdown 代码块（```json ... ```）
    2. 直接 JSON 解析
    3. ``json_repair`` 库
    4. 花括号包裹的子串
    """
    if not content:
        return None

    from json_repair import repair_json

    # 策略 1：Markdown 代码块
    json_blocks = re.findall(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
    if json_blocks:
        for block in json_blocks:
            parsed = _try_parse_json(block)
            if parsed is not None:
                return parsed
            parsed = _try_repair_json(block, repair_json)
            if parsed is not None:
                return parsed

    # 策略 2：直接解析
    parsed = _try_parse_json(content)
    if parsed is not None:
        return parsed

    # 策略 3：对全文使用 json_repair
    parsed = _try_repair_json(content, repair_json)
    if parsed is not None:
        return parsed

    # 策略 4：花括号包裹的子串
    brace_start = content.find("{")
    brace_end = content.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        candidate = content[brace_start : brace_end + 1]
        parsed = _try_parse_json(candidate)
        if parsed is not None:
            return parsed
        parsed = _try_repair_json(candidate, repair_json)
        if parsed is not None:
            return parsed

    logger.warning("Failed to parse dashboard JSON from agent response")
    return None


def try_parse_json(text: str) -> Optional[Dict[str, Any]]:
    """从 LLM 文本中尽力提取 JSON 字典。

    可处理：
    1. 直接 JSON 解析
    2. Markdown 代码围栏（```json ... ```）
    3. 花括号包裹的子串
    4. 对轻微损坏 JSON 使用 ``json_repair`` 兜底

    这是所有 agent 的 ``post_process`` 方法应复用的共享工具，避免重复实现同一逻辑。
    """
    if not text:
        return None

    candidates: List[str] = []
    cleaned = text.strip()
    if cleaned:
        candidates.append(cleaned)

    if cleaned.startswith("```"):
        unfenced = re.sub(r'^```(?:json)?\s*', '', cleaned)
        unfenced = re.sub(r'\s*```$', '', unfenced)
        if unfenced:
            candidates.append(unfenced.strip())

    fenced_blocks = re.findall(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    for block in fenced_blocks:
        block = block.strip()
        if block:
            candidates.append(block)

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        snippet = text[start:end + 1].strip()
        if snippet:
            candidates.append(snippet)

    seen: set[str] = set()
    unique_candidates: List[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            unique_candidates.append(candidate)

    for candidate in unique_candidates:
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            continue

    try:
        from json_repair import repair_json
    except Exception:
        repair_json = None

    if repair_json is not None:
        for candidate in unique_candidates:
            repaired = _try_repair_json(candidate, repair_json)
            if repaired is not None:
                return repaired

    return None


# 保留私有别名，供 parse_dashboard_json 内部使用
_try_parse_json = try_parse_json


def _try_repair_json(text: str, repair_fn: Callable) -> Optional[Dict[str, Any]]:
    """修复损坏的 JSON 文本，修复成功后返回字典。"""
    try:
        repaired = repair_fn(text)
        obj = json.loads(repaired)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _remaining_timeout_seconds(
    start_time: float,
    max_wall_clock_seconds: Optional[float],
) -> Optional[float]:
    """返回剩余墙钟时间预算（秒），未启用时返回 None。"""
    if max_wall_clock_seconds is None or max_wall_clock_seconds <= 0:
        return None
    return max(0.0, float(max_wall_clock_seconds) - (time.time() - start_time))


def _build_timeout_result(
    *,
    start_time: float,
    max_wall_clock_seconds: float,
    step: int,
    tool_calls_log: List[Dict[str, Any]],
    total_tokens: int,
    provider_used: str,
    models_used: List[str],
    messages: List[Dict[str, Any]],
) -> RunLoopResult:
    """当循环耗尽墙钟预算时构造标准的失败结果。"""
    elapsed = time.time() - start_time
    return RunLoopResult(
        success=False,
        content="",
        tool_calls_log=tool_calls_log,
        total_steps=step,
        total_tokens=total_tokens,
        provider=provider_used,
        models_used=models_used,
        error=f"Agent timed out after {elapsed:.2f}s (limit: {max_wall_clock_seconds:.2f}s)",
        messages=messages,
    )


def _build_budget_guard_result(
    *,
    start_time: float,
    step: int,
    tool_calls_log: List[Dict[str, Any]],
    total_tokens: int,
    provider_used: str,
    models_used: List[str],
    messages: List[Dict[str, Any]],
    remaining_timeout_s: float,
    min_step_budget_s: float,
) -> RunLoopResult:
    """当剩余时间不足以再发起一次 LLM 调用时构造失败结果。"""
    elapsed = time.time() - start_time
    return RunLoopResult(
        success=False,
        content="",
        tool_calls_log=tool_calls_log,
        total_steps=step,
        total_tokens=total_tokens,
        provider=provider_used,
        models_used=models_used,
        error=(
            "Agent step skipped due to insufficient budget: "
            f"{remaining_timeout_s:.2f}s remaining, minimum {min_step_budget_s:.1f}s required"
        ),
        messages=messages,
    )


# ============================================================
# 核心循环
# ============================================================

def _run_agent_loop_impl(
    *,
    messages: List[Dict[str, Any]],
    tool_registry: ToolRegistry,
    llm_adapter: LLMToolAdapter,
    max_steps: int = 10,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    thinking_labels: Optional[Dict[str, str]] = None,
    max_wall_clock_seconds: Optional[float] = None,
    tool_call_timeout_seconds: Optional[float] = None,
    emit_stage_events: bool = True,
) -> RunLoopResult:
    """执行 ReAct 的 LLM ↔ 工具循环。

    这是 agent 执行循环的*唯一共享实现*。无论是旧版 ``AgentExecutor``
    还是未来任何多 agent 执行器，都应委托到这里。

    Args:
        messages: 初始消息列表（system + user + 可选历史）。
                  **会原地修改** —— 工具结果会被追加进去。
        tool_registry: 可调用工具的注册表。
        llm_adapter: LLM 后端（处理多提供商回退）。
        max_steps: LLM 往返的最大次数。
        progress_callback: 可选的接收进度字典的回调。
        thinking_labels: tool_name → 友好标签的覆盖映射。
        max_wall_clock_seconds: 循环整体的可选超时预算。
        tool_call_timeout_seconds: 单批并行工具调用的可选超时。

    Returns:
        :class:`RunLoopResult`，包含最终内容、统计信息以及（被修改过的）
        messages 列表。
    """
    labels = thinking_labels or _THINKING_TOOL_LABELS
    tool_decls = tool_registry.to_openai_tools()

    start_time = time.time()
    tool_calls_log: List[Dict[str, Any]] = []
    non_retriable_tool_results: Dict[str, str] = {}
    total_tokens = 0
    provider_used = ""
    models_used: List[str] = []

    # 一次有意义的 LLM 往返所需的最少秒数。若剩余预算为正但低于该阈值，
    # 该步几乎肯定会在调用中途超时，白白浪费一次计费请求。仅从第 2 步起
    # 强制执行，使第一步即使在总预算很小时也总能获得一次机会。
    _MIN_STEP_BUDGET_S = 8.0

    for step in range(max_steps):
        remaining_timeout = _remaining_timeout_seconds(start_time, max_wall_clock_seconds)
        timeout_exhausted = remaining_timeout is not None and remaining_timeout <= 0
        budget_guard_triggered = (
            not timeout_exhausted
            and remaining_timeout is not None
            and step > 0
            and remaining_timeout <= _MIN_STEP_BUDGET_S
        )
        if timeout_exhausted or budget_guard_triggered:
            if budget_guard_triggered:
                logger.warning(
                    "Agent budget too low for step %d (%.1fs remaining, min %.1fs)",
                    step + 1,
                    remaining_timeout,
                    _MIN_STEP_BUDGET_S,
                )
                return _build_budget_guard_result(
                    start_time=start_time,
                    step=step,
                    tool_calls_log=tool_calls_log,
                    total_tokens=total_tokens,
                    provider_used=provider_used,
                    models_used=models_used,
                    messages=messages,
                    remaining_timeout_s=remaining_timeout,
                    min_step_budget_s=_MIN_STEP_BUDGET_S,
                )

            if remaining_timeout <= 0:
                logger.warning("Agent timed out before step %d", step + 1)
            return _build_timeout_result(
                start_time=start_time,
                max_wall_clock_seconds=float(max_wall_clock_seconds),
                step=step,
                tool_calls_log=tool_calls_log,
                total_tokens=total_tokens,
                provider_used=provider_used,
                models_used=models_used,
                messages=messages,
            )

        logger.info("Agent step %d/%d", step + 1, max_steps)

        # --- progress: thinking ---
        if progress_callback:
            if not tool_calls_log:
                thinking_msg = "正在制定分析路径..."
            else:
                last_tool = tool_calls_log[-1].get("tool", "")
                label = labels.get(last_tool, last_tool)
                thinking_msg = f"「{label}」已完成，继续深入分析..."
            progress_callback({"type": "thinking", "step": step + 1, "message": thinking_msg})

        # --- LLM 调用 ---
        response = llm_adapter.call_with_tools(
            messages,
            tool_decls,
            timeout=remaining_timeout,
        )
        provider_used = response.provider
        total_tokens += (response.usage or {}).get("total_tokens", 0)
        m = getattr(response, "model", "") or response.provider
        if m and m != "error":
            models_used.append(m)
        model_for_usage = m or response.provider
        if model_for_usage and model_for_usage != "error" and response.usage:
            _persist_usage(response.usage, model_for_usage, call_type="agent")

        remaining_timeout = _remaining_timeout_seconds(start_time, max_wall_clock_seconds)
        if remaining_timeout is not None and remaining_timeout <= 0:
            logger.warning("Agent timed out after LLM call at step %d", step + 1)
            return _build_timeout_result(
                start_time=start_time,
                max_wall_clock_seconds=float(max_wall_clock_seconds),
                step=step + 1,
                tool_calls_log=tool_calls_log,
                total_tokens=total_tokens,
                provider_used=provider_used,
                models_used=models_used,
                messages=messages,
            )

        if response.tool_calls:
            # ---- tool execution branch ----
            logger.info(
                "Agent requesting %d tool call(s): %s",
                len(response.tool_calls),
                [tc.name for tc in response.tool_calls],
            )

            # 将 assistant 消息（含 tool_calls）追加到历史
            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": response.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": tc.arguments,
                        **({"thought_signature": tc.thought_signature} if tc.thought_signature is not None else {}),
                    }
                    for tc in response.tool_calls
                ],
            }
            if response.reasoning_content is not None:
                assistant_msg["reasoning_content"] = response.reasoning_content
            messages.append(assistant_msg)

            # 执行工具（多于 1 个时并行）
            tool_results = _execute_tools(
                response.tool_calls,
                tool_registry,
                step + 1,
                progress_callback,
                tool_calls_log,
                non_retriable_tool_results,
                tool_call_timeout_seconds=tool_call_timeout_seconds,
                tool_wait_timeout_seconds=remaining_timeout,
            )

            # 追加工具结果，保持原始调用顺序
            tc_order = {tc.id: i for i, tc in enumerate(response.tool_calls)}
            tool_results.sort(key=lambda x: tc_order.get(x["tc"].id, 0))
            for tr in tool_results:
                messages.append(
                    {
                        "role": "tool",
                        "name": tr["tc"].name,
                        "tool_call_id": tr["tc"].id,
                        "content": tr["result_str"],
                    }
                )

            remaining_timeout = _remaining_timeout_seconds(start_time, max_wall_clock_seconds)
            if remaining_timeout is not None and remaining_timeout <= 0:
                logger.warning("Agent timed out after tool execution at step %d", step + 1)
                return _build_timeout_result(
                    start_time=start_time,
                    max_wall_clock_seconds=float(max_wall_clock_seconds),
                    step=step + 1,
                    tool_calls_log=tool_calls_log,
                    total_tokens=total_tokens,
                    provider_used=provider_used,
                    models_used=models_used,
                    messages=messages,
                )

        else:
            # ---- 最终答案分支 ----
            logger.info(
                "Agent completed in %d steps (%.1fs, %d tokens)",
                step + 1,
                time.time() - start_time,
                total_tokens,
            )
            if progress_callback:
                progress_callback({"type": "generating", "step": step + 1, "message": "正在生成最终分析..."})

            final_content = response.content or ""
            is_error = response.provider == "error"

            return RunLoopResult(
                success=not is_error and bool(final_content),
                content=final_content if not is_error else "",
                tool_calls_log=tool_calls_log,
                total_steps=step + 1,
                total_tokens=total_tokens,
                provider=provider_used,
                models_used=models_used,
                error=final_content if is_error else None,
                messages=messages,
            )

    # 超出最大步数
    logger.warning("Agent hit max steps (%d)", max_steps)
    return RunLoopResult(
        success=False,
        content="",
        tool_calls_log=tool_calls_log,
        total_steps=max_steps,
        total_tokens=total_tokens,
        provider=provider_used,
        models_used=models_used,
        error=f"Agent exceeded max steps ({max_steps}). Try increasing AGENT_MAX_STEPS if analysis tasks are complex.",
        messages=messages,
    )


def run_agent_loop(
    *,
    messages: List[Dict[str, Any]],
    tool_registry: ToolRegistry,
    llm_adapter: LLMToolAdapter,
    max_steps: int = 10,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    thinking_labels: Optional[Dict[str, str]] = None,
    max_wall_clock_seconds: Optional[float] = None,
    tool_call_timeout_seconds: Optional[float] = None,
    emit_stage_events: bool = True,
) -> RunLoopResult:
    """运行循环，并为 SSE 消费方暴露一致的生命周期。"""
    callback = progress_callback
    started_at = time.monotonic()
    if emit_stage_events and callback:
        callback({"type": "stage_start", "stage": "agent_loop", "message": "Agent analysis started"})
    result = _run_agent_loop_impl(
        messages=messages,
        tool_registry=tool_registry,
        llm_adapter=llm_adapter,
        max_steps=max_steps,
        progress_callback=callback,
        thinking_labels=thinking_labels,
        max_wall_clock_seconds=max_wall_clock_seconds,
        tool_call_timeout_seconds=tool_call_timeout_seconds,
        emit_stage_events=emit_stage_events,
    )
    if emit_stage_events and callback:
        callback({
            "type": "stage_done",
            "stage": "agent_loop",
            "status": "completed" if result.success else "failed",
            "duration": round(time.monotonic() - started_at, 2),
        })
    return result


# ============================================================
# 内部工具执行
# ============================================================

def _positive_timeout(value: Optional[float]) -> Optional[float]:
    """将禁用、无效及非正数的超时值归一化为 None。"""
    try:
        timeout = float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return None
    return timeout if timeout > 0 else None


def _resolve_tool_timeout(
    tool_call: Any,
    tool_registry: ToolRegistry,
    explicit_timeout: Optional[float],
    wall_clock_budget: Optional[float],
) -> Optional[float]:
    """按显式 > 工具 > 类别的优先级解析超时，并受总预算封顶。"""
    explicit = _positive_timeout(explicit_timeout)
    definition = tool_registry.get(tool_call.name)
    declared = _positive_timeout(getattr(definition, "timeout_seconds", None))
    category = _positive_timeout(
        tool_registry.category_default_timeout(definition.category)
        if definition is not None else None
    )
    selected = explicit or declared or category
    budget = _positive_timeout(wall_clock_budget)
    if selected is None:
        return budget
    return min(selected, budget) if budget is not None else selected


def _tool_timeout_payload(
    tool_name: str,
    timeout_seconds: float,
    non_retriable_tool_results: Optional[Dict[str, str]],
    arguments: Dict[str, Any],
) -> str:
    """构造工具执行超时的载荷，并按需写入不可重试缓存。"""
    payload = json.dumps({
        "error": f"Tool execution timed out after {timeout_seconds:.2f}s",
        "timeout": True,
        "retriable": False,
    })
    if non_retriable_tool_results is not None:
        cache_key = _build_tool_cache_key(tool_name, arguments)
        if cache_key:
            non_retriable_tool_results[cache_key] = payload
    return payload

def _execute_tools(
    tool_calls,
    tool_registry: ToolRegistry,
    step: int,
    progress_callback: Optional[Callable],
    tool_calls_log: List[Dict[str, Any]],
    non_retriable_tool_results: Optional[Dict[str, str]] = None,
    tool_call_timeout_seconds: Optional[float] = None,
    tool_wait_timeout_seconds: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """执行一次或多次工具调用，返回有序的结果字典。

    显式超时优先于工具声明，其次才是类别默认值。
    循环剩余预算始终是不可突破的外层上限。
    """

    def _exec_single(tc_item):
        """执行单次工具调用并返回结果文本及执行元数据。"""
        t0 = time.time()
        cache_key = _build_tool_cache_key(tc_item.name, tc_item.arguments)

        if cache_key and non_retriable_tool_results is not None and cache_key in non_retriable_tool_results:
            dur = round(time.time() - t0, 2)
            logger.info(
                "Tool '%s' skipped via non-retriable cache for arguments=%s",
                tc_item.name,
                tc_item.arguments,
            )
            return tc_item, non_retriable_tool_results[cache_key], False, dur, True

        try:
            res = tool_registry.execute(tc_item.name, **tc_item.arguments)
            res_str = serialize_tool_result(res)
            ok = True
            if cache_key and non_retriable_tool_results is not None and _is_non_retriable_tool_result(res):
                non_retriable_tool_results[cache_key] = res_str
        except Exception as e:
            res_str = json.dumps({"error": str(e)})
            ok = False
            logger.warning("Tool '%s' failed: %s", tc_item.name, e)
        dur = round(time.time() - t0, 2)
        return tc_item, res_str, ok, dur, False

    results: List[Dict[str, Any]] = []

    plan = [
        (tc, _resolve_tool_timeout(tc, tool_registry, tool_call_timeout_seconds, tool_wait_timeout_seconds))
        for tc in tool_calls
    ]
    for tc, _timeout in plan:
        if progress_callback:
            progress_callback({"type": "tool_start", "step": step, "tool": tc.name})

    def _record(tc_item, outcome=None, timeout=None):
        """记录一次工具调用结果：超时/成功分别构造日志条目与返回载荷。"""
        if outcome is None:
            result_str = _tool_timeout_payload(tc_item.name, timeout or 0.0, non_retriable_tool_results, tc_item.arguments)
            success, dur, cached = False, round(timeout or 0.0, 2), False
        else:
            _, result_str, success, dur, cached = outcome
        if progress_callback:
            progress_callback({"type": "tool_done", "step": step, "tool": tc_item.name, "success": success, "duration": dur})
        entry = {"step": step, "tool": tc_item.name, "arguments": tc_item.arguments,
                 "success": success, "duration": dur, "result_length": len(result_str), "cached": cached}
        if outcome is None:
            entry["timeout"] = True
        tool_calls_log.append(entry)
        results.append({"tc": tc_item, "result_str": result_str})

    if len(plan) == 1 and plan[0][1] is None:
        _record(plan[0][0], _exec_single(plan[0][0]))
        return results

    pool = ThreadPoolExecutor(max_workers=min(len(plan), 5))
    timeout_triggered = False
    try:
        futures = {}
        deadline_of = {}
        cancel_of = {}
        for tc, timeout in plan:
            holder = [None]
            cancel_event = threading.Event()
            def _run(tc_item=tc, timeout_value=timeout, holder_ref=holder, event=cancel_event):
                """在线程池中执行单个工具调用，挂载取消事件并登记超时截止时间。"""
                # 将取消事件挂到 contextvar，让工具内部能感知取消请求
                token = TOOL_CANCEL_EVENT.set(event)
                if timeout_value is not None:
                    holder_ref[0] = time.monotonic() + timeout_value
                try:
                    return _exec_single(tc_item)
                finally:
                    TOOL_CANCEL_EVENT.reset(token)
            future = pool.submit(contextvars.copy_context().run, _run)
            futures[future] = (tc, timeout)
            deadline_of[future] = holder
            cancel_of[future] = cancel_event
        batch_deadline = time.monotonic() + tool_wait_timeout_seconds if _positive_timeout(tool_wait_timeout_seconds) else None
        pending = set(futures)
        while pending:
            # 取所有未完成任务与整批截止时间中最早的一个作为本次等待上限
            deadlines = [deadline_of[f][0] for f in pending if deadline_of[f][0] is not None]
            if batch_deadline is not None:
                deadlines.append(batch_deadline)
            wait_timeout = max(0.0, min(deadlines) - time.monotonic()) if deadlines else 0.01
            done, _ = wait(pending, timeout=wait_timeout, return_when=FIRST_COMPLETED)
            for future in done:
                pending.discard(future)
                _record(futures[future][0], future.result())
            now = time.monotonic()
            # 超时的 future 直接取消并记录超时结果，避免阻塞后续步骤
            for future in list(pending):
                deadline = deadline_of[future][0]
                if (deadline is not None and now >= deadline) or (batch_deadline is not None and now >= batch_deadline):
                    pending.discard(future)
                    future.cancel()
                    cancel_of[future].set()
                    timeout_triggered = True
                    _record(futures[future][0], timeout=futures[future][1] or tool_wait_timeout_seconds)
    finally:
        # 超时触发时不等待剩余任务，尽快关闭线程池
        pool.shutdown(wait=not timeout_triggered, cancel_futures=True)

    return results
