# -*- coding: utf-8 -*-
"""PushPlus 发送提醒服务。

封装对 PushPlus（国内第三方推送通道）的 HTTP 调用，支持 Markdown 内容推送、
长内容自动分批、超长截断保护。

职责：
1. 通过 PushPlus ``/send`` API 推送 Markdown 消息；
2. 单条超过 ``pushplus_max_bytes`` 时自动分批发送；
3. 内部对网络异常和 PushPlus 业务错误统一降级为 ``False`` 返回值。
"""
import logging
import time
from typing import Optional
from datetime import datetime
import requests

from src.config import Config
from src.formatters import chunk_content_by_max_bytes


logger = logging.getLogger(__name__)


class PushplusSender:
    """通过 PushPlus 通道发送 Markdown 通知的封装类。

    仅依赖 ``Config`` 中的 ``pushplus_token`` / ``pushplus_topic`` /
    ``pushplus_max_bytes`` 三个字段；缺失 token 时所有发送动作都将直接跳过。
    """
    
    def __init__(self, config: Config):
        """
        初始化 PushPlus 配置。

        Args:
            config: 全局配置对象；从中读取 ``pushplus_token`` / ``pushplus_topic`` /
                ``pushplus_max_bytes``，缺失字段使用 ``None`` / ``20000`` 兜底。
        """
        # 用户令牌，用于认证 PushPlus 账户
        self._pushplus_token = getattr(config, 'pushplus_token', None)
        # 群发主题（可选）；设置后会推送给该主题所有订阅者
        self._pushplus_topic = getattr(config, 'pushplus_topic', None)
        # 单次推送允许的最大字节数，超出会分批发送
        self._pushplus_max_bytes = getattr(config, 'pushplus_max_bytes', 20000)
        
    def send_to_pushplus(
        self,
        content: str,
        title: Optional[str] = None,
        *,
        timeout_seconds: Optional[float] = None,
    ) -> bool:
        """
        推送 Markdown 消息到 PushPlus。

        PushPlus API 格式：
        POST http://www.pushplus.plus/send
        {
            "token": "用户令牌",
            "title": "消息标题",
            "content": "消息内容",
            "template": "html/txt/json/markdown"
        }

        PushPlus 特点：
        - 国内推送服务，免费额度充足
        - 支持微信公众号推送
        - 支持多种消息格式

        Args:
            content: 消息内容（Markdown 格式）。
            title: 消息标题，可选；不传则使用默认的"股票分析报告 - 日期"。
            timeout_seconds: 单次 HTTP 请求超时时间，None 表示使用默认 10 秒。

        Returns:
            是否发送成功（True/False）。任何异常都被吞掉并返回 False。
        """
        # 没配 token 就直接跳过，避免把异常抛到上层调用栈
        if not self._pushplus_token:
            logger.warning("PushPlus Token 未配置，跳过推送")
            return False

        api_url = "http://www.pushplus.plus/send"

        # 默认标题带上当日日期，便于用户在客户端区分多日报告
        if title is None:
            date_str = datetime.now().strftime('%Y-%m-%d')
            title = f"📈 股票分析报告 - {date_str}"

        try:
            # PushPlus 按字节数（UTF-8）计算体积，先做预判避免请求被拒
            content_bytes = len(content.encode('utf-8'))
            if content_bytes > self._pushplus_max_bytes:
                logger.info(
                    "PushPlus 消息内容超长(%s字节/%s字符)，将分批发送",
                    content_bytes,
                    len(content),
                )
                return self._send_pushplus_chunked(
                    api_url,
                    content,
                    title,
                    self._pushplus_max_bytes,
                )

            return self._send_pushplus_message(api_url, content, title, timeout_seconds=timeout_seconds)
        except Exception as e:
            # 任何异常一律降级为失败，避免阻塞上游通知链路
            logger.error(f"发送 PushPlus 消息失败: {e}")
            return False

    def _send_pushplus_message(
        self,
        api_url: str,
        content: str,
        title: str,
        *,
        timeout_seconds: Optional[float] = None,
    ) -> bool:
        """发送单条 PushPlus Markdown 消息负载。

        Args:
            api_url: PushPlus API URL。
            content: 消息正文。
            title: 消息标题。
            timeout_seconds: HTTP 请求超时时间，None 时默认 10 秒。

        Returns:
            bool: 推送成功返回 True；HTTP 非 200 或业务 code 非 200 时返回 False。
        """
        payload = {
            "token": self._pushplus_token,
            "title": title,
            "content": content,
            "template": "markdown",
        }

        # 配置了主题时附带 topic 字段，启用群发推送
        if self._pushplus_topic:
            payload["topic"] = self._pushplus_topic

        response = requests.post(api_url, json=payload, timeout=timeout_seconds or 10)

        if response.status_code == 200:
            result = response.json()
            # PushPlus 业务码：200 表示推送成功
            if result.get('code') == 200:
                logger.info("PushPlus 消息发送成功")
                return True

            error_msg = result.get('msg', '未知错误')
            logger.error(f"PushPlus 返回错误: {error_msg}")
            return False

        logger.error(f"PushPlus 请求失败: HTTP {response.status_code}")
        return False

    def _send_pushplus_chunked(self, api_url: str, content: str, title: str, max_bytes: int) -> bool:
        """分批发送长 PushPlus 消息，给 JSON payload 预留空间。

        为了避免单批字节数刚好卡在上限附近触发服务端拒绝，
        这里把每批的字符预算设为 ``max(1000, max_bytes - 1500)``，预留 ~1.5KB
        给 JSON 包装字段。

        Args:
            api_url: PushPlus API URL。
            content: 原始完整 Markdown 内容。
            title: 基础标题；多批时会附加 ``(i/n)`` 后缀。
            max_bytes: 单批最大字节数限制。

        Returns:
            bool: 所有分批全部成功才返回 True，否则 False。
        """
        # 预留 1500 字节给 token / title / topic / template 等 JSON 字段
        budget = max(1000, max_bytes - 1500)
        chunks = chunk_content_by_max_bytes(content, budget, add_page_marker=True)
        total_chunks = len(chunks)
        success_count = 0

        logger.info(f"PushPlus 分批发送：共 {total_chunks} 批")

        for i, chunk in enumerate(chunks):
            # 多批时在标题里追加 (i/n)，单批保持原标题不变
            chunk_title = f"{title} ({i+1}/{total_chunks})" if total_chunks > 1 else title
            if self._send_pushplus_message(api_url, chunk, chunk_title):
                success_count += 1
                logger.info(f"PushPlus 第 {i+1}/{total_chunks} 批发送成功")
            else:
                logger.error(f"PushPlus 第 {i+1}/{total_chunks} 批发送失败")

            # 分批之间 sleep 1 秒，避免触发 PushPlus 频率限制
            if i < total_chunks - 1:
                time.sleep(1)

        return success_count == total_chunks
