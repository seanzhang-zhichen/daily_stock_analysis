# -*- coding: utf-8 -*-
"""
===================================
飞书 Stream 模式适配器
===================================

使用飞书官方 lark-oapi SDK 的 WebSocket 长连接模式接入机器人，
无需公网 IP 和 Webhook 配置。

优势：
- 不需要公网 IP 或域名
- 不需要配置 Webhook URL
- 通过 WebSocket 长连接接收消息
- 更简单的接入方式
- 内置自动重连和心跳保活

依赖：
uv sync --locked

飞书长连接文档：
https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/server-side-sdk/python--sdk/handle-events
"""

import json
import logging
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional, Callable
import time

logger = logging.getLogger(__name__)

# 尝试导入飞书 SDK
try:
    import lark_oapi as lark
    from lark_oapi import ws
    from lark_oapi.api.im.v1 import (
        P2ImMessageReceiveV1,
        ReplyMessageRequest,
        ReplyMessageRequestBody,
        CreateMessageRequest,
        CreateMessageRequestBody,
    )

    FEISHU_SDK_AVAILABLE = True
except ImportError:
    FEISHU_SDK_AVAILABLE = False
    logger.warning("[Feishu Stream] lark-oapi SDK 未安装，Stream 模式不可用")
    logger.warning("[Feishu Stream] 请运行: uv sync --locked")

from bot.models import BotMessage, BotResponse, ChatType
from src.formatters import format_feishu_markdown, chunk_content_by_max_bytes
from src.config import get_config


class FeishuReplyClient:
    """
    飞书消息回复客户端

    使用飞书 API 发送回复消息。
    """

    def __init__(self, app_id: str, app_secret: str):
        """
        Args:
            app_id: 飞书应用 ID
            app_secret: 飞书应用密钥
        """
        if not FEISHU_SDK_AVAILABLE:
            raise ImportError("lark-oapi SDK 未安装")

        self._client = lark.Client.builder() \
            .app_id(app_id) \
            .app_secret(app_secret) \
            .log_level(lark.LogLevel.WARNING) \
            .build()

        # 获取配置的最大字节数
        config = get_config()
        self._max_bytes = getattr(config, 'feishu_max_bytes', 20000)

    def _send_interactive_card(self, content: str, message_id: Optional[str] = None,
                               chat_id: Optional[str] = None,
                               receive_id_type: str = "chat_id",
                               at_user: bool = False, user_id: Optional[str] = None) -> bool:
        """
        发送交互卡片消息（支持 Markdown 渲染）

        飞书交互卡片是一种富文本消息格式，支持 Markdown 渲染、
        按钮、图片等丰富的交互元素。相比纯文本消息，交互卡片的
        展示效果更好，适合发送分析报告等结构化内容。

        发送方式：
        - 回复消息：通过 ``message_id`` 指定原消息，在原消息下方回复
        - 主动发送：通过 ``chat_id`` 指定目标会话，主动发送消息

        Args:
            content: Markdown 格式的内容
            message_id: 原消息 ID（回复时使用）
            chat_id: 会话 ID（主动发送时使用）
            receive_id_type: 接收者 ID 类型
            at_user: 是否 @用户
            user_id: 用户 open_id（at_user=True 时需要）

        Returns:
            是否发送成功
        """
        try:
            # 如果需要 @用户，在内容前添加 @ 标记
            final_content = content
            if at_user and user_id:
                final_content = f"<at user_id=\"{user_id}\"></at> {content}"

            # 构建交互卡片 payload
            card_data = {
                "config": {"wide_screen_mode": True},
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": final_content
                        }
                    }
                ]
            }

            content_json = json.dumps(card_data)

            if message_id:
                # 回复消息
                request = ReplyMessageRequest.builder() \
                    .message_id(message_id) \
                    .request_body(
                    ReplyMessageRequestBody.builder()
                    .content(content_json)
                    .msg_type("interactive")
                    .build()
                ) \
                    .build()
                response = self._client.im.v1.message.reply(request)
            else:
                # 主动发送消息
                request = CreateMessageRequest.builder() \
                    .receive_id_type(receive_id_type) \
                    .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .content(content_json)
                    .msg_type("interactive")
                    .build()
                ) \
                    .build()
                response = self._client.im.v1.message.create(request)

            if not response.success():
                logger.error(
                    f"[Feishu Stream] 发送交互卡片失败: code={response.code}, "
                    f"msg={response.msg}, log_id={response.get_log_id()}"
                )
                return False

            logger.debug("[Feishu Stream] 发送交互卡片成功")
            return True

        except Exception as e:
            logger.error(f"[Feishu Stream] 发送交互卡片异常: {e}")
            return False

    def reply_text(self, message_id: str, text: str, at_user: bool = False,
                   user_id: Optional[str] = None) -> bool:
        """
        回复文本消息（支持交互卡片和分段发送）

        将文本内容转换为飞书 Markdown 格式后发送。如果内容超过
        配置的最大字节数限制，会自动分段发送，确保每条消息
        都在平台限制范围内。

        发送流程：
        1. 将文本转换为飞书 Markdown 格式
        2. 检查内容长度是否超过限制
        3. 超长时自动分段，逐条发送
        4. 正常长度时直接发送交互卡片

        Args:
            message_id: 原消息 ID
            text: 回复文本
            at_user: 是否 @用户
            user_id: 用户 open_id（at_user=True 时需要）

        Returns:
            是否发送成功
        """
        # 将文本转换为飞书 Markdown 格式
        formatted_text = format_feishu_markdown(text)

        # 检查是否需要分段发送
        content_bytes = len(formatted_text.encode('utf-8'))
        if content_bytes > self._max_bytes:
            logger.info(
                f"[Feishu Stream] 回复消息内容超长({content_bytes}字节)，将分批发送"
            )
            return self._send_to_chat_chunked(
                formatted_text,
                lambda chunk: self._send_interactive_card(
                    chunk,
                    message_id=message_id,
                    at_user=at_user,
                    user_id=user_id,
                ),
            )

        # 单条消息，使用交互卡片
        return self._send_interactive_card(
            formatted_text, message_id=message_id, at_user=at_user, user_id=user_id
        )

    def send_to_chat(self, chat_id: str, text: str,
                     receive_id_type: str = "chat_id") -> bool:
        """
        发送消息到指定会话（支持交互卡片和分段发送）

        主动向指定会话发送消息，支持自动分段。与 ``reply_text`` 的区别：
        - ``reply_text``: 回复某条消息（需要 message_id）
        - ``send_to_chat``: 主动发送到指定会话（需要 chat_id）

        适用场景：
        - 定时任务发送通知
        - 系统事件推送
        - 主动提醒用户

        Args:
            chat_id: 会话 ID
            text: 消息文本
            receive_id_type: 接收者 ID 类型，默认 chat_id

        Returns:
            是否发送成功
        """
        # 将文本转换为飞书 Markdown 格式
        formatted_text = format_feishu_markdown(text)

        # 检查是否需要分段发送
        content_bytes = len(formatted_text.encode('utf-8'))
        if content_bytes > self._max_bytes:
            logger.info(
                f"[Feishu Stream] 发送消息内容超长({content_bytes}字节)，将分批发送"
            )
            return self._send_to_chat_chunked(
                formatted_text,
                lambda chunk: self._send_interactive_card(
                    chunk,
                    chat_id=chat_id,
                    receive_id_type=receive_id_type,
                ),
            )

        # 单条消息，使用交互卡片
        return self._send_interactive_card(formatted_text, chat_id=chat_id, receive_id_type=receive_id_type)

    def _send_to_chat_chunked(self, content: str, send_func: Callable[[str], bool]) -> bool:
        """
        分批发送消息（支持交互卡片和分段发送）

        当消息内容超过平台限制时，将内容分割成多个小块，
        逐块发送。每发送完一块后等待 1 秒，避免触发频率限制。

        分块策略：
        - 使用 ``chunk_content_by_max_bytes`` 按字节数分割
        - 每块添加页码标记，方便用户识别顺序
        - 发送失败时记录错误日志，继续发送下一块

        Args:
            content: 消息文本
            send_func: 发送单个分片的函数，返回是否发送成功

        Returns:
            是否全部发送成功（所有分片都发送成功才返回 True）
        """
        chunks = chunk_content_by_max_bytes(content, self._max_bytes, add_page_marker=True)
        success_count = 0
        for i, chunk in enumerate(chunks):
            if send_func(chunk):
                success_count += 1
            else:
                logger.error(f"[Feishu Stream] 发送消息失败: {chunk}")
            if i < len(chunks) - 1:
                time.sleep(1)
        return success_count == len(chunks)


class FeishuStreamHandler:
    """
    飞书 Stream 模式消息处理器

    将 SDK 的事件转换为统一的 BotMessage 格式，
    并调用命令分发器处理。
    """

    def __init__(
            self,
            on_message: Callable[[BotMessage], BotResponse],
            reply_client: FeishuReplyClient
    ):
        """
        Args:
            on_message: 消息处理回调函数，接收 BotMessage 返回 BotResponse
            reply_client: 飞书回复客户端
        """
        self._on_message = on_message
        self._reply_client = reply_client
        self._logger = logger
        # Different conversations can run in parallel, but one conversation
        # must stay FIFO so multi-turn chat and replies do not get reordered.
        self._executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="feishu-msg")
        self._pending_messages: dict[str, deque[BotMessage]] = {}
        self._active_conversations: set[str] = set()
        self._queue_lock = threading.Lock()
        self._shutdown = False

    def _conversation_key(self, bot_message: BotMessage) -> str:
        """返回用于"同一会话 FIFO 顺序处理"的会话键。

        会话键的生成规则：
        - 私聊：使用 chat_id 或 user_id 或 message_id 作为键
        - 群聊：使用 "chat_id:user_id" 组合键，确保同一用户在群聊中的消息按顺序处理

        这样设计的目的是：不同会话可以并行处理，但同一会话内的消息
        必须按 FIFO 顺序处理，避免多轮对话和回复出现乱序。

        Args:
            bot_message: 消息对象

        Returns:
            会话键字符串
        """
        if bot_message.chat_type == ChatType.PRIVATE:
            return bot_message.chat_id or bot_message.user_id or bot_message.message_id

        chat_id = bot_message.chat_id or "unknown-chat"
        user_id = bot_message.user_id or "unknown-user"
        return f"{chat_id}:{user_id}"

    def _enqueue_message(self, bot_message: BotMessage) -> None:
        """将一条消息入队; 若其所属会话当前空闲, 启动一个工作协程处理。

        消息队列的工作流程：
        1. 检查 handler 是否已关闭，已关闭则丢弃消息
        2. 计算会话键，确定消息属于哪个会话
        3. 将消息加入对应会话的队列
        4. 如果该会话当前没有活跃的工作线程，启动一个新的工作线程
        5. 工作线程会按 FIFO 顺序处理该会话的所有消息

        线程安全：
        - 使用 ``_queue_lock`` 保护队列和活跃会话集合的并发访问
        - 在锁外提交线程池任务，避免在持有锁时进行 I/O 操作

        Args:
            bot_message: 消息对象
        """
        if self._shutdown:
            self._logger.debug("[Feishu Stream] Handler already stopped, dropping message")
            return

        conversation_key = self._conversation_key(bot_message)
        should_start_worker = False

        with self._queue_lock:
            self._pending_messages.setdefault(conversation_key, deque()).append(bot_message)
            if conversation_key not in self._active_conversations:
                self._active_conversations.add(conversation_key)
                should_start_worker = True

        if should_start_worker:
            try:
                self._executor.submit(self._drain_conversation, conversation_key)
            except RuntimeError as exc:
                with self._queue_lock:
                    self._active_conversations.discard(conversation_key)
                    self._pending_messages.pop(conversation_key, None)
                self._logger.error("[Feishu Stream] 无法启动消息处理线程: %s", exc)

    def _drain_conversation(self, conversation_key: str) -> None:
        """按 FIFO 顺序排空单个会话的消息队列, 队列空后退出。

        工作线程的主循环：
        1. 获取该会话的消息队列
        2. 如果队列为空，从活跃会话集合中移除该会话，退出循环
        3. 从队列头部取出一条消息
        4. 调用 ``_process_message`` 处理消息
        5. 重复步骤 1，直到队列为空

        注意：此方法在 ThreadPoolExecutor 的工作线程中运行，
        需要处理所有异常，避免工作线程异常退出。

        Args:
            conversation_key: 会话键
        """
        while True:
            with self._queue_lock:
                queue = self._pending_messages.get(conversation_key)
                if not queue:
                    self._pending_messages.pop(conversation_key, None)
                    self._active_conversations.discard(conversation_key)
                    return
                bot_message = queue.popleft()

            self._process_message(bot_message)

    def _process_message(self, bot_message: BotMessage) -> None:
        """在 SDK 回调线程之外执行命令处理, 避免阻塞 SDK 的事件循环。

        处理流程：
        1. 调用 ``_on_message`` 回调函数（即命令分发器）处理消息
        2. 如果返回了响应且包含文本内容，通过回复客户端发送回复
        3. 发送回复时，根据响应的 ``at_user`` 设置决定是否 @用户

        异常处理：
        - 命令处理异常会被捕获并记录，不会导致工作线程退出
        - 回复发送异常也会被捕获，避免影响后续消息处理

        Args:
            bot_message: 消息对象
        """
        try:
            response = self._on_message(bot_message)

            if response and response.text:
                self._reply_client.reply_text(
                    message_id=bot_message.message_id,
                    text=response.text,
                    at_user=response.at_user,
                    user_id=bot_message.user_id if response.at_user else None,
                )
        except Exception as e:
            self._logger.error(f"[Feishu Stream] 异步处理消息失败: {e}")
            self._logger.exception(e)

    @staticmethod
    def _truncate_log_content(text: str, max_len: int = 200) -> str:
        """截断日志内容

        将多行文本压缩为单行，并截断到指定长度，
        避免日志内容过长影响可读性。

        Args:
            text: 原始文本
            max_len: 最大长度，默认 200

        Returns:
            截断后的文本
        """
        cleaned = text.replace("\n", " ").strip()
        if len(cleaned) > max_len:
            return f"{cleaned[:max_len]}..."
        return cleaned

    def _log_incoming_message(self, message: BotMessage) -> None:
        """记录收到的消息日志

        将消息的关键信息记录到日志中，便于问题排查和监控。
        消息内容会被截断到 200 个字符以内，避免日志过长。

        记录的信息：
        - msg_id: 消息 ID
        - user_id: 用户 ID
        - chat_id: 会话 ID
        - chat_type: 会话类型
        - content: 消息内容摘要（已截断）

        Args:
            message: 消息对象
        """
        content = message.raw_content or message.content or ""
        summary = self._truncate_log_content(content)
        self._logger.info(
            "[Feishu Stream] Incoming message: msg_id=%s user_id=%s "
            "chat_id=%s chat_type=%s content=%s",
            message.message_id,
            message.user_id,
            message.chat_id,
            getattr(message.chat_type, "value", message.chat_type),
            summary,
        )

    def handle_message(self, event: 'P2ImMessageReceiveV1') -> None:
        """
        处理接收到的消息事件

        Args:
            event: 飞书消息接收事件
        """
        try:
            # 解析消息
            bot_message = self._parse_event_message(event)

            if bot_message is None:
                return

            self._log_incoming_message(bot_message)

            self._enqueue_message(bot_message)

        except Exception as e:
            self._logger.error(f"[Feishu Stream] 处理消息失败: {e}")
            self._logger.exception(e)

    def _parse_event_message(self, event: 'P2ImMessageReceiveV1') -> Optional[BotMessage]:
        """
        解析飞书事件消息为统一格式

        Args:
            event: P2ImMessageReceiveV1 事件对象
        """
        try:
            event_data = event.event
            if event_data is None:
                return None

            message_data = event_data.message
            sender_data = event_data.sender

            if message_data is None:
                return None

            # 只处理文本消息
            message_type = message_data.message_type or ""
            if message_type != "text":
                self._logger.debug(f"[Feishu Stream] 忽略非文本消息: {message_type}")
                return None

            # 解析消息内容
            content_str = message_data.content or "{}"
            try:
                content_json = json.loads(content_str)
                raw_content = content_json.get("text", "")
            except json.JSONDecodeError:
                raw_content = content_str

            # 提取命令（去除 @机器人）
            content = self._extract_command(raw_content, message_data.mentions)
            mentioned = "@" in raw_content or bool(message_data.mentions)

            # 获取发送者信息
            user_id = ""
            if sender_data and sender_data.sender_id:
                user_id = sender_data.sender_id.open_id or sender_data.sender_id.user_id or ""

            # 获取会话类型
            chat_type_str = message_data.chat_type or ""
            if chat_type_str == "group":
                chat_type = ChatType.GROUP
            elif chat_type_str == "p2p":
                chat_type = ChatType.PRIVATE
            else:
                chat_type = ChatType.UNKNOWN

            # 创建时间
            create_time = message_data.create_time
            try:
                if create_time:
                    timestamp = datetime.fromtimestamp(int(create_time) / 1000)
                else:
                    timestamp = datetime.now()
            except (ValueError, TypeError):
                timestamp = datetime.now()

            # 构建原始数据
            raw_data = {
                "header": {
                    "event_id": event.header.event_id if event.header else "",
                    "event_type": event.header.event_type if event.header else "",
                    "create_time": event.header.create_time if event.header else "",
                    "token": event.header.token if event.header else "",
                    "app_id": event.header.app_id if event.header else "",
                },
                "event": {
                    "message_id": message_data.message_id,
                    "chat_id": message_data.chat_id,
                    "chat_type": message_data.chat_type,
                    "content": message_data.content,
                }
            }

            return BotMessage(
                platform="feishu",
                message_id=message_data.message_id or "",
                user_id=user_id,
                user_name=user_id,  # 飞书不直接返回用户名
                chat_id=message_data.chat_id or "",
                chat_type=chat_type,
                content=content,
                raw_content=raw_content,
                mentioned=mentioned,
                mentions=[m.key or "" for m in (message_data.mentions or [])],
                timestamp=timestamp,
                raw_data=raw_data,
            )

        except Exception as e:
            self._logger.error(f"[Feishu Stream] 解析消息失败: {e}")
            return None

    def _extract_command(self, text: str, mentions: list) -> str:
        """
        提取命令内容（去除 @机器人）

        飞书的 @用户 格式是：@_user_1, @_user_2 等
        需要将这些 @标记 从消息内容中移除，提取出纯命令文本。

        清理策略（按优先级）：
        1. 通过 mentions 列表精确移除 @标记（最准确）
        2. 正则兜底，移除飞书 @用户 格式（@_user_N）
        3. 清理多余空格

        Args:
            text: 原始消息文本
            mentions: @提及列表

        Returns:
            清理后的命令文本
        """
        import re

        # 方式1: 通过 mentions 列表移除（精确匹配）
        for mention in (mentions or []):
            key = getattr(mention, 'key', '') or ''
            if key:
                text = text.replace(key, '')

        # 方式2: 正则兜底，移除飞书 @用户 格式（@_user_N）
        # 当 mentions 为空或未正确传递时生效
        text = re.sub(r'@_user_\d+\s*', '', text)

        # 清理多余空格
        return ' '.join(text.split())

    def shutdown(self, wait: bool = False) -> None:
        """停止接收新消息并关闭工作线程池。

        安全关闭流程：
        1. 设置 ``_shutdown`` 标志，新消息将被丢弃
        2. 清空待处理消息队列和活跃会话集合
        3. 关闭线程池（可选等待正在执行的任务完成）

        注意：此方法不会等待正在处理的消息完成，
        如果需要等待，请将 ``wait`` 参数设为 True。

        Args:
            wait: 是否等待线程池中的任务完成
        """
        self._shutdown = True
        with self._queue_lock:
            self._pending_messages.clear()
            self._active_conversations.clear()
        self._executor.shutdown(wait=wait)


class FeishuStreamClient:
    """
    飞书 Stream 模式客户端

    封装 lark-oapi SDK 的 WebSocket 客户端，提供简单的启动接口。

    使用方式：
        client = FeishuStreamClient()
        client.start()  # 阻塞运行

        # 或者在后台运行
        client.start_background()
    """

    def __init__(
            self,
            app_id: Optional[str] = None,
            app_secret: Optional[str] = None
    ):
        """
        Args:
            app_id: 应用 ID（不传则从配置读取）
            app_secret: 应用密钥（不传则从配置读取）
        """
        if not FEISHU_SDK_AVAILABLE:
            raise ImportError(
                "lark-oapi SDK 未安装。\n"
                "请运行: uv sync --locked"
            )

        from src.config import get_config
        config = get_config()

        self._app_id = app_id or getattr(config, 'feishu_app_id', None)
        self._app_secret = app_secret or getattr(config, 'feishu_app_secret', None)

        if not self._app_id or not self._app_secret:
            raise ValueError(
                "飞书 Stream 模式需要配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET"
            )

        self._ws_client: Optional[ws.Client] = None
        self._reply_client: Optional[FeishuReplyClient] = None
        self._message_handler: Optional[FeishuStreamHandler] = None
        self._background_thread: Optional[threading.Thread] = None
        self._running = False

    def _create_message_handler(self) -> Callable[[BotMessage], BotResponse]:
        """创建消息处理函数

        返回一个闭包函数，该函数接收 BotMessage 对象，
        通过全局命令分发器处理消息，并返回 BotResponse。

        闭包设计：
        - 内部引用全局的 ``get_dispatcher()`` 函数
        - 每次调用时获取最新的分发器实例
        - 支持热更新（分发器实例可以被替换）

        Returns:
            消息处理函数
        """
        def handle_message(message: BotMessage) -> BotResponse:
            """通过同步 bot 分发器处理一条飞书 Stream 消息。"""
            from bot.dispatcher import get_dispatcher
            dispatcher = get_dispatcher()
            return dispatcher.dispatch(message)

        return handle_message

    def _create_event_handler(self) -> 'lark.EventDispatcherHandler':
        """创建事件分发处理器

        构建飞书 SDK 的事件处理器，包括：
        1. 创建回复客户端（用于发送消息）
        2. 创建消息处理器（处理接收到的消息）
        3. 注册事件回调（P2ImMessageReceiveV1）

        加密和验证：
        - encrypt_key: 消息加密密钥（可选，长连接模式下不是必需的）
        - verification_token: 验证令牌（可选，长连接模式下不是必需的）
        - 两者都可以从配置中读取，未配置时使用空字符串

        Returns:
            飞书事件处理器实例
        """
        # 创建回复客户端
        self._reply_client = FeishuReplyClient(self._app_id, self._app_secret)

        # 创建消息处理器
        handler = FeishuStreamHandler(
            self._create_message_handler(),
            self._reply_client
        )
        self._message_handler = handler

        # 创建并注册事件处理器
        # 注意：encrypt_key 和 verification_token 在长连接模式下不是必需的
        # 但 SDK 要求传入（可以为空字符串）
        from src.config import get_config
        config = get_config()

        encrypt_key = getattr(config, 'feishu_encrypt_key', '') or ''
        verification_token = getattr(config, 'feishu_verification_token', '') or ''

        event_handler = lark.EventDispatcherHandler.builder(
            encrypt_key=encrypt_key,
            verification_token=verification_token,
            level=lark.LogLevel.WARNING
        ).register_p2_im_message_receive_v1(
            handler.handle_message
        ).build()

        return event_handler

    def start(self) -> None:
        """
        启动 Stream 客户端（阻塞）

        此方法会阻塞当前线程，直到客户端停止。
        启动流程：
        1. 创建事件处理器（包含消息处理和回复客户端）
        2. 创建 WebSocket 客户端（配置自动重连和日志级别）
        3. 启动 WebSocket 连接（阻塞，直到连接断开）

        异常处理：
        - 连接断开时会自动重连（如果 auto_reconnect=True）
        - 需要在其他线程中调用 stop() 来停止客户端
        """
        logger.info("[Feishu Stream] 正在启动...")

        # 创建事件处理器
        event_handler = self._create_event_handler()

        # 创建 WebSocket 客户端
        self._ws_client = ws.Client(
            app_id=self._app_id,
            app_secret=self._app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.WARNING,
            auto_reconnect=True
        )

        self._running = True
        logger.info("[Feishu Stream] 客户端已启动，等待消息...")

        # 启动（阻塞）
        self._ws_client.start()

    def start_background(self) -> None:
        """
        在后台线程启动 Stream 客户端（非阻塞）

        适用于与其他服务（如 WebUI）同时运行的场景。
        启动流程：
        1. 检查是否已有后台线程在运行
        2. 创建新的后台线程
        3. 在线程中调用 ``_run_in_background`` 方法

        注意：
        - 此方法不会阻塞当前线程
        - 后台线程是守护线程（daemon=True），主线程退出时会自动终止
        - 需要调用 ``stop()`` 方法来停止后台线程
        """
        if self._background_thread and self._background_thread.is_alive():
            logger.warning("[Feishu Stream] 客户端已在运行")
            return

        self._running = True
        self._background_thread = threading.Thread(
            target=self._run_in_background,
            daemon=True,
            name="FeishuStreamClient"
        )
        self._background_thread.start()
        logger.info("[Feishu Stream] 后台客户端已启动")

    def _run_in_background(self) -> None:
        """后台运行（处理异常和重连）

        后台线程的主循环：
        1. 调用 ``start()`` 启动客户端（阻塞，直到连接断开）
        2. 如果发生异常，记录错误日志
        3. 如果客户端仍在运行（不是被 stop() 停止的），等待 5 秒后重连
        4. 重复步骤 1

        重连策略：
        - 连接异常时自动重连
        - 每次重连前等待 5 秒，避免频繁重连
        - 被 stop() 停止后不再重连
        """
        import time

        while self._running:
            try:
                self.start()
            except Exception as e:
                logger.error(f"[Feishu Stream] 运行异常: {e}")
                if self._running:
                    logger.info("[Feishu Stream] 5 秒后重连...")
                    time.sleep(5)

    def stop(self) -> None:
        """停止客户端

        安全停止流程：
        1. 设置 ``_running`` 标志为 False，通知后台线程退出
        2. 如果消息处理器存在，调用其 ``shutdown`` 方法关闭线程池
        3. 记录停止日志

        注意：
        - 此方法不会立即断开 WebSocket 连接
        - WebSocket 连接会在下次心跳或消息收发时检测到 _running=False 而断开
        - 如果需要立即断开，需要直接关闭 WebSocket 客户端
        """
        self._running = False
        if self._message_handler is not None:
            self._message_handler.shutdown(wait=False)
        logger.info("[Feishu Stream] 客户端已停止")

    @property
    def is_running(self) -> bool:
        """是否正在运行

        通过检查 ``_running`` 标志来判断客户端是否处于运行状态。
        注意：此属性只反映本地状态，不反映 WebSocket 连接的实际状态。
        即使 ``is_running`` 为 True，WebSocket 连接也可能已断开
        （正在等待重连）。

        Returns:
            是否正在运行
        """
        return self._running


# 全局客户端实例
_stream_client: Optional[FeishuStreamClient] = None


def get_feishu_stream_client() -> Optional[FeishuStreamClient]:
    """获取全局 Stream 客户端实例

    使用单例模式管理全局客户端实例，避免重复创建。
    如果 lark-oapi SDK 未安装，返回 None。

    异常处理：
    - ImportError: SDK 未安装
    - ValueError: 配置缺失（app_id 或 app_secret 未配置）

    Returns:
        全局客户端实例，或 None（SDK 未安装或配置错误）
    """
    global _stream_client

    if _stream_client is None and FEISHU_SDK_AVAILABLE:
        try:
            _stream_client = FeishuStreamClient()
        except (ImportError, ValueError) as e:
            logger.warning(f"[Feishu Stream] 无法创建客户端: {e}")
            return None

    return _stream_client


def start_feishu_stream_background() -> bool:
    """
    在后台启动飞书 Stream 客户端

    便捷函数，自动获取或创建全局客户端实例，
    并在后台线程中启动。

    使用场景：
    - 应用启动时自动启动飞书 Stream 客户端
    - 与其他服务（如 WebUI）同时启动

    Returns:
        是否成功启动。成功返回 True，失败（SDK 未安装、配置错误等）返回 False。
    """
    client = get_feishu_stream_client()
    if client:
        client.start_background()
        return True
    return False
