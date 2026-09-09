"""钉钉群机器人消息投递（带签名 + 字节安全 markdown 分片）。

按钉钉加签规则在 webhook URL 上拼装 `timestamp` 与 `sign` 查询参数，
把超长 markdown 内容按 UTF-8 字节安全切分为多个消息块逐条发送，
被调度与通知服务统一拉起使用。

主要能力：
- `_signed_url`：在 webhook 基础上按钉钉官方规则计算 HMAC-SHA256 加签 URL
- `send_to_dingtalk`：标题清洗、内容隐藏元数据剥离、按字节上限分片、按序推送并降级处理
"""

import base64
import hashlib
import hmac
import logging
import time
from typing import Optional
from urllib.parse import quote_plus

import requests

from src.config import Config
from src.formatters import chunk_markdown_preserving_blocks, strip_hidden_markdown_metadata, utf8_len

logger = logging.getLogger(__name__)
# 钉钉单条 markdown 消息体上限的保守阈值，留出余量避免触发接口拒绝
_MAX_BODY_BYTES = 19000


class DingtalkSender:
    """钉钉群机器人发送器。

    Attributes:
        webhook_url: 钉钉群机器人 webhook URL，未配置时整发送过程会直接返回 False。
        secret: 钉钉机器人加签密钥；为空时退化为无签名 URL。
    """

    def __init__(self, config: Config):
        """初始化发送器，从 config 中读取 webhook 与 secret。"""
        self.webhook_url = getattr(config, "dingtalk_webhook_url", None)
        self.secret = getattr(config, "dingtalk_secret", None)

    def _signed_url(self) -> Optional[str]:
        """按钉钉官方规则构造带签名的 webhook URL。

        Returns:
            - 未配置 webhook 时返回 None，调用方应据此跳过本次发送。
            - 未配置 secret 时直接返回原 URL（部分老群机器人未启用加签）。
            - 否则按 timestamp + secret 拼接签名字符串，做 SHA256 + base64 后拼到 URL 上。
        """
        if not self.webhook_url:
            return None
        if not self.secret:
            return self.webhook_url
        timestamp = str(round(time.time() * 1000))
        signature = hmac.new(
            self.secret.encode("utf-8"),
            f"{timestamp}\n{self.secret}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
        # URL 已有查询串时用 & 拼接，否则用 ?
        joiner = "&" if "?" in self.webhook_url else "?"
        return f"{self.webhook_url}{joiner}timestamp={timestamp}&sign={quote_plus(base64.b64encode(signature))}"

    def send_to_dingtalk(self, content: str, title: str = "", timeout_seconds: int = 10) -> bool:
        """把 markdown 内容按字节上限分片后逐条发到钉钉群。

        Args:
            content: 待发送的 markdown 原文。
            title: 消息标题；超出 100 字会被截断。
            timeout_seconds: 单条 HTTP 请求超时时间。

        Returns:
            True 表示所有分片均被服务端接受；任意分片失败返回 False。
        """
        url = self._signed_url()
        if not url:
            return False
        # 钉钉 markdown title 字段建议控制在 100 字内，超长会被接口拒收
        safe_title = (title or "A股分析报告")[:100]
        # 先剥离 LLM 偶发输出的隐藏 markdown 元数据，避免钉钉把它渲染为 0 宽控制符
        sanitized_content = strip_hidden_markdown_metadata(content).strip()
        chunks = chunk_markdown_preserving_blocks(
            sanitized_content,
            _MAX_BODY_BYTES,
            len_fn=utf8_len,
            add_page_marker=True,
        )
        for index, chunk in enumerate(chunks, start=1):
            display_title = f"{safe_title} ({index}/{len(chunks)})" if len(chunks) > 1 else safe_title
            text = f"### {safe_title}\n\n{chunk}" if index == 1 and safe_title else chunk
            try:
                response = requests.post(url, json={"msgtype": "markdown", "markdown": {"title": display_title, "text": text}}, timeout=timeout_seconds)
                response.raise_for_status()
                # 钉钉返回体中 errcode != 0 即业务失败，需与 HTTP 错误分开处理
                if response.json().get("errcode") != 0:
                    logger.error("DingTalk chunk %d rejected", index)
                    return False
            except (requests.RequestException, ValueError) as exc:
                logger.error("DingTalk chunk %d failed: %s", index, exc)
                return False
            # 分片间短暂 sleep 避开钉钉机器人 QPS 限制（实测 20 msg/s）
            if index < len(chunks):
                time.sleep(0.5)
        return True
