# -*- coding: utf-8 -*-
"""
===================================
Bot Webhook 处理器
===================================

处理各平台的 Webhook 回调，分发到命令处理器。
"""

import asyncio
import json
import logging
import threading
from typing import Dict, Optional, TYPE_CHECKING

from bot.models import WebhookResponse
from bot.dispatcher import get_dispatcher
from bot.platforms import ALL_PLATFORMS

if TYPE_CHECKING:
    from bot.platforms.base import BotPlatform  # noqa: F401

logger = logging.getLogger(__name__)

# 平台实例缓存：键为平台名称，值为对应的 BotPlatform 适配器实例。
# 使用全局缓存避免重复创建平台适配器对象，提升性能。
_platform_instances: Dict[str, 'BotPlatform'] = {}


def get_platform(platform_name: str) -> Optional['BotPlatform']:
    """
    获取指定平台的适配器实例。

    该函数使用单例缓存模式：如果缓存中已存在对应平台的实例，则直接返回；
    否则根据平台名称从 ALL_PLATFORMS 中查找对应的类，实例化后存入缓存。

    Args:
        platform_name: 平台名称（如 'feishu'、'dingtalk'、'wecom'、'telegram'）。

    Returns:
        对应平台的适配器实例（BotPlatform 子类实例）；
        如果平台名称未知，则返回 None。
    """
    if platform_name not in _platform_instances:
        # 从全局平台注册表中查找对应平台类
        platform_class = ALL_PLATFORMS.get(platform_name)
        if platform_class:
            # 实例化并缓存该平台适配器
            _platform_instances[platform_name] = platform_class()
        else:
            # 未知平台，记录警告日志
            logger.warning(f"[BotHandler] 未知平台: {platform_name}")
            return None

    return _platform_instances[platform_name]


def handle_webhook(
    platform_name: str,
    headers: Dict[str, str],
    body: bytes,
    query_params: Optional[Dict[str, list]] = None
) -> WebhookResponse:
    """
    处理 Webhook 请求的同步入口函数。

    这是所有平台 Webhook 的统一入口。处理流程如下：
    1. 检查机器人功能是否启用；
    2. 获取对应平台的适配器实例；
    3. 解析请求体中的 JSON 数据；
    4. 调用平台适配器处理 Webhook，获取消息对象和即时响应；
    5. 根据返回结果决定是直接返回响应、延迟处理，还是分发到命令处理器。

    Args:
        platform_name: 平台名称（如 'feishu'、'dingtalk'、'wecom'、'telegram'）。
        headers: HTTP 请求头字典，包含平台相关的签名、时间戳等信息。
        body: 请求体的原始字节数据。
        query_params: URL 查询参数字典（可选），用于某些平台的验证场景。

    Returns:
        WebhookResponse 响应对象，包含响应状态码和响应体。
    """
    logger.info(f"[BotHandler] 收到 {platform_name} Webhook 请求")

    # 动态导入配置，检查机器人功能是否被全局禁用
    from src.config import get_config
    config = get_config()

    # 如果配置中 bot_enabled 为 False，则直接返回成功响应，不处理任何消息
    if not getattr(config, 'bot_enabled', True):
        logger.info("[BotHandler] 机器人功能未启用")
        return WebhookResponse.success()

    # 获取平台适配器实例
    platform = get_platform(platform_name)
    if not platform:
        # 未知平台，返回 400 错误
        return WebhookResponse.error(f"Unknown platform: {platform_name}", 400)

    # 解析 JSON 数据：将请求体字节解码为 UTF-8 字符串，再解析为 Python 字典
    try:
        data = json.loads(body.decode('utf-8')) if body else {}
    except json.JSONDecodeError as e:
        # JSON 格式非法，返回 400 错误
        logger.error(f"[BotHandler] JSON 解析失败: {e}")
        return WebhookResponse.error("Invalid JSON", 400)

    # 记录请求数据摘要（最多 500 字符，避免日志过大）
    logger.debug(f"[BotHandler] 请求数据: {json.dumps(data, ensure_ascii=False)[:500]}")

    # 调用平台适配器处理 Webhook，返回解析后的消息对象和即时响应
    message, immediate_response = platform.handle_webhook(headers, body, data)

    # 场景一：平台返回了即时响应（如验证请求、错误响应），且没有需要进一步处理的消息
    if immediate_response and not message:
        logger.info("[BotHandler] 返回验证响应")
        return immediate_response

    # 场景二：延迟响应（如 Discord type 5 交互）：
    # 立即返回 ACK 响应，然后在后台线程中异步处理命令并发送后续补充消息。
    if immediate_response and message:
        logger.info("[BotHandler] 返回延迟 ACK，后台处理命令")

        def _deferred_dispatch() -> None:
            """
            在后台线程中处理已确认的 Webhook 并发送后续补充消息。

            该函数在独立的守护线程中运行，用于处理需要延迟响应的场景。
            它会获取命令分发器，将消息分发到对应的命令处理器，
            如果命令处理器返回了文本响应，则通过平台适配器发送后续补充消息。
            """
            try:
                dispatcher = get_dispatcher()
                response = dispatcher.dispatch(message)
                if response.text:
                    # 发送后续补充消息（如 Discord 的 follow-up message）
                    platform.send_followup(response, message)
            except Exception as exc:
                logger.error("[BotHandler] 延迟命令处理失败: %s", exc)

        # 启动守护线程在后台处理命令，不阻塞当前请求
        threading.Thread(target=_deferred_dispatch, daemon=True).start()
        return immediate_response

    # 场景三：没有解析到消息（如心跳包、无关事件），返回空成功响应
    if not message:
        logger.debug("[BotHandler] 无需处理的消息")
        return WebhookResponse.success()

    # 场景四：正常消息，记录日志并分发到命令处理器
    logger.info(f"[BotHandler] 解析到消息: user={message.user_name}, content={message.content[:50]}")

    # 获取命令分发器，将消息分发到对应的命令处理器
    dispatcher = get_dispatcher()
    response = dispatcher.dispatch(message)

    # 如果命令处理器返回了文本响应，通过平台适配器格式化为平台特定的响应格式
    if response.text:
        webhook_response = platform.format_response(response, message)
        return webhook_response

    return WebhookResponse.success()


async def handle_webhook_async(
    platform_name: str,
    headers: Dict[str, str],
    body: bytes,
    query_params: Optional[Dict[str, list]] = None
) -> WebhookResponse:
    """
    处理 Webhook 请求的异步入口函数。

    这是 handle_webhook 的异步版本，处理流程与同步版本一致，
    但在异步上下文（如 FastAPI 端点）中调用时更合适，可避免阻塞事件循环。

    主要区别在于延迟处理时使用 asyncio 而非 threading，
    并且命令分发使用 dispatch_async 异步方法。

    Args:
        platform_name: 平台名称（如 'feishu'、'dingtalk'、'wecom'、'telegram'）。
        headers: HTTP 请求头字典，包含平台相关的签名、时间戳等信息。
        body: 请求体的原始字节数据。
        query_params: URL 查询参数字典（可选），用于某些平台的验证场景。

    Returns:
        WebhookResponse 响应对象，包含响应状态码和响应体。
    """
    logger.info(f"[BotHandler] 收到 {platform_name} Webhook 请求 (async)")

    # 动态导入配置，检查机器人功能是否被全局禁用
    from src.config import get_config
    config = get_config()

    # 如果配置中 bot_enabled 为 False，则直接返回成功响应，不处理任何消息
    if not getattr(config, 'bot_enabled', True):
        logger.info("[BotHandler] 机器人功能未启用")
        return WebhookResponse.success()

    # 获取平台适配器实例
    platform = get_platform(platform_name)
    if not platform:
        # 未知平台，返回 400 错误
        return WebhookResponse.error(f"Unknown platform: {platform_name}", 400)

    # 解析 JSON 数据：将请求体字节解码为 UTF-8 字符串，再解析为 Python 字典
    try:
        data = json.loads(body.decode('utf-8')) if body else {}
    except json.JSONDecodeError as e:
        # JSON 格式非法，返回 400 错误
        logger.error(f"[BotHandler] JSON 解析失败: {e}")
        return WebhookResponse.error("Invalid JSON", 400)

    # 记录请求数据摘要（最多 500 字符，避免日志过大）
    logger.debug(f"[BotHandler] 请求数据: {json.dumps(data, ensure_ascii=False)[:500]}")

    # 调用平台适配器处理 Webhook，返回解析后的消息对象和即时响应
    message, immediate_response = platform.handle_webhook(headers, body, data)

    # 场景一：平台返回了即时响应（如验证请求、错误响应），且没有需要进一步处理的消息
    if immediate_response and not message:
        logger.info("[BotHandler] 返回验证响应")
        return immediate_response

    # 场景二：延迟响应（如 Discord type 5 交互）：
    # 立即返回 ACK 响应，然后在后台异步处理命令并发送后续补充消息。
    if immediate_response and message:
        logger.info("[BotHandler] 返回延迟 ACK，后台处理命令 (async)")

        async def _deferred_dispatch() -> None:
            """
            在事件循环中异步处理已确认的 Webhook 并发送后续补充消息。

            该协程在后台运行，用于处理需要延迟响应的异步场景。
            它会获取命令分发器，将消息异步分发到对应的命令处理器，
            如果命令处理器返回了文本响应，则通过线程池发送后续补充消息。
            """
            try:
                dispatcher = get_dispatcher()
                response = await dispatcher.dispatch_async(message)
                if response.text:
                    # 使用 asyncio.to_thread 在线程池中执行同步的 send_followup 方法
                    # 避免阻塞事件循环
                    await asyncio.to_thread(platform.send_followup, response, message)
            except Exception as exc:
                logger.error("[BotHandler] 延迟命令处理失败: %s", exc)

        # 将延迟处理协程提交到事件循环，不阻塞当前请求
        asyncio.ensure_future(_deferred_dispatch())
        return immediate_response

    # 场景三：没有解析到消息（如心跳包、无关事件），返回空成功响应
    if not message:
        logger.debug("[BotHandler] 无需处理的消息")
        return WebhookResponse.success()

    # 场景四：正常消息，记录日志并分发到命令处理器
    logger.info(f"[BotHandler] 解析到消息: user={message.user_name}, content={message.content[:50]}")

    # 获取命令分发器，异步分发消息到对应的命令处理器
    dispatcher = get_dispatcher()
    response = await dispatcher.dispatch_async(message)

    # 如果命令处理器返回了文本响应，通过平台适配器格式化为平台特定的响应格式
    if response.text:
        webhook_response = platform.format_response(response, message)
        return webhook_response

    return WebhookResponse.success()


def handle_feishu_webhook(headers: Dict[str, str], body: bytes) -> WebhookResponse:
    """
    处理飞书（Feishu/Lark）平台的 Webhook 请求。

    飞书是字节跳动旗下的企业协作平台，支持机器人通过 Webhook 接收消息事件。
    该函数是 handle_webhook 针对飞书平台的便捷封装。

    Args:
        headers: HTTP 请求头字典，包含飞书平台的签名、时间戳等验证信息。
        body: 请求体的原始字节数据。

    Returns:
        WebhookResponse 响应对象。
    """
    return handle_webhook('feishu', headers, body)


def handle_dingtalk_webhook(headers: Dict[str, str], body: bytes) -> WebhookResponse:
    """
    处理钉钉（DingTalk）平台的 Webhook 请求。

    钉钉是阿里巴巴旗下的企业协作平台，支持机器人通过 Webhook 接收消息事件。
    该函数是 handle_webhook 针对钉钉平台的便捷封装。

    Args:
        headers: HTTP 请求头字典，包含钉钉平台的签名、时间戳等验证信息。
        body: 请求体的原始字节数据。

    Returns:
        WebhookResponse 响应对象。
    """
    return handle_webhook('dingtalk', headers, body)


def handle_wecom_webhook(headers: Dict[str, str], body: bytes) -> WebhookResponse:
    """
    处理企业微信（WeCom/WeChat Work）平台的 Webhook 请求。

    企业微信是腾讯旗下的企业协作平台，支持机器人通过 Webhook 接收消息事件。
    该函数是 handle_webhook 针对企业微信平台的便捷封装。

    Args:
        headers: HTTP 请求头字典，包含企业微信平台的签名、时间戳等验证信息。
        body: 请求体的原始字节数据。

    Returns:
        WebhookResponse 响应对象。
    """
    return handle_webhook('wecom', headers, body)


def handle_telegram_webhook(headers: Dict[str, str], body: bytes) -> WebhookResponse:
    """
    处理 Telegram 平台的 Webhook 请求。

    Telegram 是一款跨平台的即时通讯应用，支持通过 Bot API 的 Webhook 接收消息更新。
    该函数是 handle_webhook 针对 Telegram 平台的便捷封装。

    Args:
        headers: HTTP 请求头字典，包含 Telegram 平台的验证信息。
        body: 请求体的原始字节数据。

    Returns:
        WebhookResponse 响应对象。
    """
    return handle_webhook('telegram', headers, body)
