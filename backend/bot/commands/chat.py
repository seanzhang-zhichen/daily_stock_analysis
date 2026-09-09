# -*- coding: utf-8 -*-
"""对话命令处理器：与 Agent 进行自由形式的对话。

被机器人命令分发器（`bot/dispatcher.py`）按 ``/chat`` 命令触发，
依赖 ``src.agent.factory.build_agent_executor`` 执行对话。
仅当配置项 ``AGENT_MODE=true`` 时才会真正调用 Agent。
"""

import logging
from typing import List, Optional

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse, ChatType
from src.config import get_config

logger = logging.getLogger(__name__)


def _scoped_chat_session_id(message: BotMessage) -> str:
    """返回当前对话作用域对应的聊天会话 ID。

    群聊按 ``platform_user:chat_id:chat`` 隔离，避免不同群共享同一历史；
    私聊退化为 ``platform_user:chat``。
    """
    base_session_id = f"{message.platform}_{message.user_id}"
    if message.chat_type == ChatType.GROUP and message.chat_id:
        return f"{base_session_id}:{message.chat_id}:chat"
    return f"{base_session_id}:chat"


def _resolve_chat_session_id(message: BotMessage) -> str:
    """解析最终使用的聊天会话 ID，优先沿用旧版私聊会话以保持历史兼容。

    群聊必须按房间隔离（保持新规则）；私聊若旧会话（``platform_user``）
    已存在而新作用域会话不存在，则沿用旧 ID，以避免历史会话失效。
    """
    legacy_session_id = f"{message.platform}_{message.user_id}"
    session_id = _scoped_chat_session_id(message)

    # 群聊按房间隔离，避免不同群的对话历史互相污染
    if message.chat_type == ChatType.GROUP and message.chat_id:
        return session_id

    try:
        from src.storage import get_db

        # 仅当旧会话存在且新会话尚未建立时回退，兼容尚未迁移的老用户
        db = get_db()
        legacy_exists = db.conversation_session_exists(legacy_session_id)
        current_exists = db.conversation_session_exists(session_id)
        if legacy_exists and not current_exists:
            return legacy_session_id
    except Exception as exc:
        # 历史探测失败不应阻塞新会话使用，仅记录到 debug 日志
        logger.debug("Chat session compatibility check failed: %s", exc)

    return session_id

class ChatCommand(BotCommand):
    """``/chat`` 命令处理器，提供与 AI Agent 的自由对话能力。

    用法示例：``/chat 帮我分析一下茅台最近的走势``。
    必须开启 Agent 模式才会真正调用 Agent 执行。
    """

    @property
    def name(self) -> str:
        """返回调度器使用的主要命令名。"""
        return "chat"

    @property
    def description(self) -> str:
        """返回帮助列表中展示的简短描述。"""
        return "与 AI 助手进行自由对话 (需开启 Agent 模式)"

    @property
    def usage(self) -> str:
        """返回帮助中展示的参数格式。"""
        return "/chat <问题>"

    @property
    def aliases(self) -> list[str]:
        """返回自由对话命令的短别名列表。"""
        return ["c", "问"]

    def validate_args(self, args: List[str]) -> Optional[str]:
        """校验：必须至少提供一个问题参数。"""
        if not args:
            return "请提供要询问的问题。"
        return None

    def execute(self, message: BotMessage, args: list[str]) -> BotResponse:
        """执行自由对话命令：构造会话上下文后调用 Agent ``chat``。"""
        config = get_config()

        # Agent 模式未开启时直接返回提示，绝不调用 LLM，避免浪费额度
        if not config.agent_mode:
            return BotResponse.text_response(
                "⚠️ Agent 模式未开启，无法使用对话功能。\n请在配置中设置 `AGENT_MODE=true`。"
            )

        if not args:
            return BotResponse.text_response(
                "⚠️ 请提供要询问的问题。\n用法: `/chat <问题>`\n示例: `/chat 帮我分析一下茅台最近的走势`"
            )

        user_message = " ".join(args)
        session_id = _resolve_chat_session_id(message)

        try:
            from src.agent.factory import build_agent_executor
            executor = build_agent_executor(config)
            result = executor.chat(message=user_message, session_id=session_id)

            if result.success:
                return BotResponse.text_response(result.content)
            else:
                return BotResponse.text_response(f"⚠️ 对话失败: {result.error}")

        except Exception as e:
            # 对话命令的整体兜底：任何 Agent 内部异常都不应让机器人进程崩溃
            logger.error(f"Chat command failed: {e}")
            logger.exception("Chat error details:")
            return BotResponse.text_response(f"⚠️ 对话执行出错: {str(e)}")
