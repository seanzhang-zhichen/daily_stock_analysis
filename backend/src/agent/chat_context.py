"""会话上下文的可见历史构建与可选的滚动摘要压缩。

为 Agent 的多轮对话提供"受控可见"的历史窗口，避免每次调用都把整段会话
塞给 LLM 导致上下文爆炸；并支持在历史超出 token 阈值时调用 LLM 生成
压缩摘要，以"摘要 + 最近若干轮原文"的组合延续上下文。

主要能力：
- 估算文本 token 数（按"每 3 字符 ≈ 1 token"的近似规则）
- 从存储层读取可见消息并裁剪到最近 20 条
- 根据配置项启用滚动摘要：摘要前缀 + 受保护尾部 + 待压缩候选段
- 压缩失败时安全降级回退到原文可见列表
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)
# 注入到摘要前的固定提示前缀，标明该条 user 消息是由系统生成的"历史摘要"而非用户真实输入。
SUMMARY_PREFIX = "[系统生成的历史对话摘要，仅供延续本会话]\n"
# 触发摘要时的 system prompt：明确要求 LLM 只总结、不补充新事实，并保留关键股票/风险/未解决项。
SUMMARY_PROMPT = """你是股票问答系统的会话压缩器。只总结提供的既有对话，不补充新事实或投资建议。
保留股票标的、用户偏好、关键判断、操作条件、风险、数据时效和未解决问题。
使用简洁中文 Markdown 输出。"""


def estimate_tokens(text: str) -> int:
    """按"每 3 个字符约 1 token"的粗略规则估算 ``text`` 占用的 token 数。"""
    return max(0, (len(text or "") + 2) // 3)


def build_chat_history(session_id: str, llm_adapter: Any, config: Any) -> List[Dict[str, str]]:
    """基于存储与配置，构造喂给 LLM 的可见历史消息列表。

    流程：
    1. 从 DB 取出所有可见消息；
    2. 若未启用压缩，最多保留尾部 20 条并原样返回；
    3. 若启用压缩：以"摘要前缀 + 受保护尾部 + 待压缩候选段"拼接；
       当总 token 超出阈值时调用 LLM 生成新摘要并写回 DB。

    Args:
        session_id: 会话 ID。
        llm_adapter: 文本生成接口，用于在需要时生成新的滚动摘要。
        config: 全局配置对象，读取压缩相关开关与阈值。

    Returns:
        ``[{"role": ..., "content": ...}, ...]`` 形式的可见消息列表。
    """
    db = __import__("src.storage", fromlist=["get_db"]).get_db()
    rows = db.get_visible_conversation_messages(session_id)
    if not rows:
        return []
    # 未启用压缩时直接走"最后 20 条"快路径，避免任何 LLM 调用与额外开销。
    enabled = bool(getattr(config, "agent_context_compression_enabled", False))
    if not enabled:
        return [{"role": r["role"], "content": r["content"]} for r in rows[-20:]]

    trigger = int(getattr(config, "agent_context_compression_trigger_tokens", 12000))
    protected_turns = max(0, int(getattr(config, "agent_context_protected_turns", 4)))
    summary_tokens = int(getattr(config, "agent_context_compression_summary_tokens", 1500))
    previous = db.get_conversation_summary(session_id) or {}
    previous_text = str(previous.get("summary") or "")
    # 已覆盖到的消息 ID 上限：小于等于该 ID 的旧消息不需要再被压缩。
    covered_id = int(previous.get("covered_message_id") or 0)

    # 找到倒数第 protected_turns 条 user 消息的索引，作为"受保护尾部"的起点。
    tail_start = len(rows)
    user_count = 0
    for i in range(len(rows) - 1, -1, -1):
        if rows[i]["role"] == "user":
            user_count += 1
            if user_count >= protected_turns:
                tail_start = i
                break
    # 若未启用受保护尾部，则 candidate 段可包含全部历史。
    tail = rows[tail_start:] if protected_turns else []
    # candidate：已覆盖 ID 之后、但不在受保护尾部内的中间消息，是本轮可能要压缩的部分。
    candidates = [r for r in rows if int(r["id"]) > covered_id and int(r["id"]) not in {int(x["id"]) for x in tail}]
    visible = ([{"role": "user", "content": SUMMARY_PREFIX + previous_text}] if previous_text else []) + candidates + tail
    token_count = estimate_tokens("\n".join(str(x["content"]) for x in visible))
    # 总量未超阈值或没有可压缩候选，直接返回拼接结果，不再触发 LLM。
    if token_count <= trigger or not candidates:
        return [{"role": x["role"], "content": x["content"]} for x in visible]

    # 拼接待压缩源：优先保留旧摘要作为上下文，再拼接本轮 candidate。
    source = ("已有滚动摘要：\n" + previous_text + "\n\n" if previous_text else "") + "\n\n".join(f'{x["role"]}:\n{x["content"]}' for x in candidates)
    try:
        response = llm_adapter.call_text(
            [{"role": "system", "content": SUMMARY_PROMPT}, {"role": "user", "content": source}],
            temperature=0, max_tokens=summary_tokens, timeout=20,
        )
        summary = str(getattr(response, "content", "") or "").strip()
    except Exception as exc:
        # 摘要生成失败不得影响聊天可用性：记录后降级返回原文可见列表。
        logger.warning("Conversation summary failed: %s", exc)
        summary = ""
    if not summary:
        return [{"role": x["role"], "content": x["content"]} for x in visible]
    # 持久化新摘要与覆盖位置，便于后续轮次继续累计压缩。
    db.upsert_conversation_summary(
        session_id=session_id, summary=summary,
        covered_message_id=max(int(x["id"]) for x in candidates),
        source_message_count=len(candidates), estimated_tokens=estimate_tokens(summary),
    )
    return [{"role": "user", "content": SUMMARY_PREFIX + summary}] + [
        {"role": x["role"], "content": x["content"]} for x in tail
    ]


__all__ = ["build_chat_history", "estimate_tokens"]
