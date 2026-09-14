# -*- coding: utf-8 -*-
"""
===================================
机器人消息模型
===================================

定义统一的消息和响应模型，屏蔽各平台差异。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional, List


class ChatType(str, Enum):
    """
    会话类型枚举

    用于标识消息来自群聊还是私聊，不同会话类型的处理逻辑可能不同
    （例如群聊中需要处理 @机器人的逻辑）。
    """
    GROUP = "group"      # 群聊：多人参与的会话
    PRIVATE = "private"  # 私聊：一对一的会话
    UNKNOWN = "unknown"  # 未知：未能识别会话类型时的兜底值


class Platform(str, Enum):
    """
    平台类型枚举

    标识消息来源的平台，用于路由到对应的平台适配器，
    以及会话隔离（不同平台的相同 user_id 不会冲突）。
    """
    FEISHU = "feishu"        # 飞书（字节跳动旗下企业协作平台）
    DINGTALK = "dingtalk"    # 钉钉（阿里巴巴旗下企业协作平台）
    WECOM = "wecom"          # 企业微信（腾讯旗下企业协作平台）
    TELEGRAM = "telegram"    # Telegram（海外即时通讯平台）
    UNKNOWN = "unknown"      # 未知平台，用于兜底处理


@dataclass
class BotMessage:
    """
    统一的机器人消息模型

    将各平台的消息格式统一为此模型，便于命令处理器处理。

    Attributes:
        platform: 平台标识
        message_id: 消息 ID（平台原始 ID）
        user_id: 发送者 ID
        user_name: 发送者名称
        chat_id: 会话 ID（群聊 ID 或私聊 ID）
        chat_type: 会话类型
        content: 消息文本内容（已去除 @机器人 部分）
        raw_content: 原始消息内容
        mentioned: 是否 @了机器人
        mentions: @的用户列表
        timestamp: 消息时间戳
        raw_data: 原始请求数据（平台特定，用于调试）
    """
    platform: str
    message_id: str
    user_id: str
    user_name: str
    chat_id: str
    chat_type: ChatType
    content: str
    raw_content: str = ""
    mentioned: bool = False
    mentions: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)
    raw_data: Dict[str, Any] = field(default_factory=dict)

    def get_command_and_args(self, prefix: str = "/") -> tuple:
        """
        解析命令和参数

n        从消息内容中提取命令名称和参数列表。
        支持两种匹配方式：
        1. 标准命令格式：以 prefix 开头，如 "/analyze 600519"
        2. 中文命令格式：无前缀，直接以中文关键词开头，如 "分析 600519"

        Args:
            prefix: 命令前缀，默认 "/"

        Returns:
            (command, args) 元组，如 ("analyze", ["600519"])
            如果不是命令，返回 (None, [])
        """
        text = self.content.strip()

        # 检查是否以命令前缀开头（标准命令格式）
        if not text.startswith(prefix):
            # 尝试匹配中文命令（无前缀）
            chinese_commands = {
                '分析': 'analyze',
                '大盘': 'market',
                '批量': 'batch',
                '帮助': 'help',
                '状态': 'status',
            }
            for cn_cmd, en_cmd in chinese_commands.items():
                if text.startswith(cn_cmd):
                    args = text[len(cn_cmd):].strip().split()
                    return en_cmd, args
            return None, []

        # 去除前缀，提取命令和参数
        text = text[len(prefix):]

        # 分割命令和参数
        parts = text.split()
        if not parts:
            return None, []

        command = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []

        return command, args

    def is_command(self, prefix: str = "/") -> bool:
        """检查消息是否是命令"""
        cmd, _ = self.get_command_and_args(prefix)
        return cmd is not None


@dataclass
class BotResponse:
    """
    统一的机器人响应模型

    命令处理器返回此模型，由平台适配器转换为平台特定格式。
    各平台（钉钉、飞书、Discord 等）的响应格式不同，
    通过此统一模型屏蔽平台差异。

    Attributes:
        text: 回复文本内容
        markdown: 是否为 Markdown 格式（部分平台支持富文本）
        at_user: 是否 @发送者（群聊场景下常用）
        reply_to_message: 是否回复原消息（部分平台支持消息引用）
        extra: 额外数据（平台特定，如 Discord 的 embeds、飞书的卡片等）
    """
    text: str
    markdown: bool = False
    at_user: bool = True
    reply_to_message: bool = True
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def text_response(cls, text: str, at_user: bool = True) -> 'BotResponse':
        """
        创建纯文本响应

        适用于不需要富文本格式的场景，如简单的文本回复。
        """
        return cls(text=text, markdown=False, at_user=at_user)

    @classmethod
    def markdown_response(cls, text: str, at_user: bool = True) -> 'BotResponse':
        """
        创建 Markdown 响应

        适用于需要富文本格式的场景，如包含标题、列表、表格的分析报告。
        注意：并非所有平台都支持 Markdown，平台适配器会做降级处理。
        """
        return cls(text=text, markdown=True, at_user=at_user)

    @classmethod
    def error_response(cls, message: str) -> 'BotResponse':
        """
        创建错误响应

        统一错误消息的格式，自动添加错误前缀图标。
        """
        return cls(text=f"❌ 错误：{message}", markdown=False, at_user=True)


@dataclass
class WebhookResponse:
    """
    Webhook 响应模型

    平台适配器返回此模型，包含 HTTP 响应内容。
    各平台（钉钉、飞书、Discord 等）的 Webhook 验证和响应格式不同，
    通过此统一模型封装 HTTP 层面的响应。

    Attributes:
        status_code: HTTP 状态码（如 200 成功、400 错误、401 未授权、403 禁止访问）
        body: 响应体（字典，将被 JSON 序列化后返回给平台）
        headers: 额外的响应头（如 Content-Type、签名验证头等）
    """
    status_code: int = 200
    body: Dict[str, Any] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def success(cls, body: Optional[Dict] = None) -> 'WebhookResponse':
        """
        创建成功响应

        适用于大多数正常处理场景，返回 200 状态码。
        """
        return cls(status_code=200, body=body or {})

    @classmethod
    def challenge(cls, challenge: str) -> 'WebhookResponse':
        """
        创建验证响应（用于平台 URL 验证）

        部分平台（如飞书）在配置 Webhook 时会发送验证请求，
        需要返回包含 challenge 字段的响应以完成验证。
        """
        return cls(status_code=200, body={"challenge": challenge})

    @classmethod
    def error(cls, message: str, status_code: int = 400) -> 'WebhookResponse':
        """
        创建错误响应

        适用于请求处理失败、签名验证失败、参数错误等场景。

        Args:
            message: 错误描述信息
            status_code: HTTP 错误状态码，默认 400（Bad Request）
        """
        return cls(status_code=status_code, body={"error": message})
