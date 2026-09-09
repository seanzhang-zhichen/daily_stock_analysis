# -*- coding: utf-8 -*-
"""``/history`` 命令实现：查看 / 清除当前用户的 Agent 对话历史。

用户隔离：每个用户只能看到 ``session_id`` 以自身 ``{platform}_{user_id}``
前缀开头的会话。命令支持三种调用形态：

- ``/history`` 或 ``/history <n>``：列出最近 n 条会话（n <= 50，默认 10）；
- ``/history <session_id>``：查看某个会话的最近 20 条消息；
- ``/history clear``：清除当前用户的"主聊天会话"。

注意：传入的 ``session_id`` 必须以当前用户前缀开头，否则视为越权访问。
"""

import logging
from typing import List, Optional

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse, ChatType

logger = logging.getLogger(__name__)


def _user_prefix(message: BotMessage) -> str:
    """生成当前用户的 session-id 规范化前缀。

    Session ID 形如 ``{platform}_{user_id}:{scope}``，冒号分隔符可以避免
    user_id 之间的前缀冲突（例如 '123' 与 '1234'）。

    Returns:
        str: 形如 ``feishu_u123:`` 的前缀字符串。
    """
    return f"{message.platform}_{message.user_id}:"


def _legacy_chat_session_id(message: BotMessage) -> str:
    """老版本（无冒号作用域）下的聊天 session id，用于兼容历史数据。"""
    return f"{message.platform}_{message.user_id}"


def _current_chat_session_id(message: BotMessage) -> str:
    """返回当前激活作用域的聊天 session id。

    - 私聊：``{platform}_{user_id}:chat``；
    - 群聊：``{platform}_{user_id}:{chat_id}:chat``，以 ``chat_id`` 区分不同群。
    """
    prefix = _user_prefix(message)
    # 群聊需要把 chat_id 嵌入作用域，避免不同群之间串台
    if message.chat_type == ChatType.GROUP and message.chat_id:
        return f"{prefix}{message.chat_id}:chat"
    return f"{prefix}chat"


class HistoryCommand(BotCommand):
    """``/history`` 命令：查看与清除当前用户的 Agent 对话历史。

    通过 ``session_id`` 前缀强制用户隔离，越权访问会被拒绝。
    """

    @property
    def name(self) -> str:
        """返回调度器使用的主要命令名（英文）。"""
        return "history"

    @property
    def aliases(self) -> List[str]:
        """返回历史命令的本地化别名，便于中文机器人直接识别。"""
        return ["历史", "会话"]

    @property
    def description(self) -> str:
        """返回帮助列表中展示的简短描述。"""
        return "查看 Agent 对话历史"

    @property
    def usage(self) -> str:
        """返回帮助中展示的参数格式说明。"""
        return "/history [session_id | clear]"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        """分发并执行历史命令。

        Args:
            message: 来自平台的入站消息。
            args: 命令行参数列表；首项决定行为（``clear`` / 数字 / 字符串）。

        Returns:
            BotResponse: 文本或 Markdown 响应，错误时携带 ``⚠️`` 前缀。
        """
        try:
            from src.storage import get_db
            db = get_db()
        except Exception as e:
            logger.error(f"History: storage unavailable: {e}")
            return BotResponse.text_response("⚠️ 存储模块不可用，无法查询对话历史。")

        # 提前计算三个关键的 session 标识，避免分支里重复推导
        prefix = _user_prefix(message)
        legacy_chat_session_id = _legacy_chat_session_id(message)
        current_chat_session_id = _current_chat_session_id(message)

        # 1) /history clear — 清除当前用户的聊天 session
        if args and args[0].lower() in ("clear", "清除"):
            try:
                deleted = db.delete_conversation_session(current_chat_session_id)
                # 如果当前 session 恰好是"无 chat_id"的老格式，再兜底删一次旧 session
                if current_chat_session_id == f"{prefix}chat":
                    deleted += db.delete_conversation_session(legacy_chat_session_id)
                return BotResponse.text_response(
                    f"✅ 已清除当前会话 ({deleted} 条消息)"
                )
            except Exception as e:
                logger.error(f"History clear failed: {e}")
                return BotResponse.text_response(f"⚠️ 清除失败: {str(e)}")

        # 2) /history <session_id> — 查看指定 session 的最近消息
        # 仅当 session 归属当前用户时才允许查看，防止越权读他人对话。
        if args and not args[0].isdigit():
            session_id = args[0]
            # 必须以当前用户前缀开头，或者是历史遗留的旧 session_id
            if not (session_id.startswith(prefix) or session_id == legacy_chat_session_id):
                return BotResponse.text_response("⚠️ 你只能查看自己的会话记录。")
            try:
                messages_list = db.get_conversation_messages(session_id, limit=20)
                if not messages_list:
                    return BotResponse.text_response(f"📭 会话 `{session_id}` 无消息记录")

                lines = [f"💬 **会话详情**: `{session_id}`", ""]
                for msg in messages_list:
                    # 用户 / Agent 用不同图标区分
                    role_icon = "👤" if msg["role"] == "user" else "🤖"
                    # 长内容截断展示，避免单条消息撑爆消息卡片
                    content_preview = msg["content"][:200]
                    if len(msg["content"]) > 200:
                        content_preview += "..."
                    # 时间戳裁剪到分钟精度即可
                    time_str = msg.get("created_at", "")[:16] if msg.get("created_at") else ""
                    lines.append(f"{role_icon} {time_str}")
                    lines.append(f"  {content_preview}")
                    lines.append("")

                return BotResponse.markdown_response("\n".join(lines))
            except Exception as e:
                logger.error(f"History detail failed: {e}")
                return BotResponse.text_response(f"⚠️ 获取会话详情失败: {str(e)}")

        # 3) /history [count] — 列出当前用户最近的若干 session
        limit = 10
        # 入参是数字时按数字截取，最多 50 条，避免一次拉太多
        if args and args[0].isdigit():
            limit = min(int(args[0]), 50)

        try:
            sessions = db.get_chat_sessions(
                limit=limit,
                session_prefix=prefix,
                extra_session_ids=[legacy_chat_session_id],
            )
            if not sessions:
                return BotResponse.text_response("📭 暂无对话历史记录")

            lines = ["📋 **最近对话会话**", ""]
            for i, sess in enumerate(sessions, 1):
                title = sess.get("title", "新对话")
                msg_count = sess.get("message_count", 0)
                # 时间字段安全访问，缺失时退化为空串
                last_active = sess.get("last_active", "")[:16] if sess.get("last_active") else ""
                sid = sess["session_id"]
                lines.append(f"**{i}.** {title}")
                lines.append(f"   💬 {msg_count} 条消息 | 🕐 {last_active}")
                lines.append(f"   ID: `{sid}`")
                lines.append("")

            lines.append(f"💡 使用 `/history <session_id>` 查看具体会话内容")
            return BotResponse.markdown_response("\n".join(lines))

        except Exception as e:
            logger.error(f"History list failed: {e}")
            return BotResponse.text_response(f"⚠️ 获取会话列表失败: {str(e)}")
