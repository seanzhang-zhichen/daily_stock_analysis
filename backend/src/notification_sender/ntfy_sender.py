# -*- coding: utf-8 -*-
"""ntfy 通知渠道发送器。

通过 ntfy 的 JSON 发布接口（POST）推送 Markdown 文本通知，
被统一通知派发器根据用户配置拉起使用。

主要能力：
- `resolve_ntfy_endpoint`：把 `NTFY_URL` 拆分为服务器根地址与 topic 两部分
- `NtfySender`：带鉴权 token / TLS 校验 / 状态码分级处理的发送封装
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Tuple
from urllib.parse import unquote, urlparse, urlunparse

import requests

from src.config import Config


logger = logging.getLogger(__name__)


def resolve_ntfy_endpoint(ntfy_url: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """将 `NTFY_URL` 按最后一段路径拆分为服务器根地址与 topic。

    支持形如 `https://ntfy.sh/my-topic` 的完整 endpoint；空值、缺 scheme/host 或没有 topic 路径段时返回 `(None, None)`。

    Args:
        ntfy_url: 配置中的 ntfy endpoint 字符串。

    Returns:
        (server_url, topic) 二元组；任一无效则返回 `(None, None)`。
    """
    raw_url = (ntfy_url or "").strip().rstrip("/")
    if not raw_url:
        return None, None

    parsed = urlparse(raw_url)
    # 仅允许 http/https，缺失 scheme 或 host 视为非法配置
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None, None

    path_segments = [segment for segment in parsed.path.split("/") if segment]
    if not path_segments:
        return None, None

    # 解码 percent-encoded topic 后再次判空，避免主题是纯空白或编码符号
    topic = unquote(path_segments[-1]).strip()
    if not topic:
        return None, None

    root_path = "/".join(path_segments[:-1])
    server_url = urlunparse(
        parsed._replace(
            path=f"/{root_path}" if root_path else "",
            params="",
            query="",
            fragment="",
        )
    ).rstrip("/")

    return server_url, topic


class NtfySender:
    """通过 ntfy JSON publish API 推送 Markdown 通知。"""

    def __init__(self, config: Config):
        """缓存 ntfy endpoint、可选 token 与 TLS 校验开关。

        Args:
            config: 全局配置对象，提供 `ntfy_url` / `ntfy_token` / `webhook_verify_ssl` 等字段。
        """
        self._ntfy_url = getattr(config, "ntfy_url", None)
        self._ntfy_token = getattr(config, "ntfy_token", None)
        self._webhook_verify_ssl = getattr(config, "webhook_verify_ssl", True)

    def _is_ntfy_configured(self) -> bool:
        """返回是否已配置 ntfy topic endpoint。"""
        return bool(self._ntfy_url)

    def _resolve_ntfy_endpoint(self) -> Tuple[Optional[str], Optional[str]]:
        """将当前配置的 ntfy URL 拆分为服务器与 topic 两部分。"""
        return resolve_ntfy_endpoint(self._ntfy_url)

    def send_to_ntfy(
        self,
        content: str,
        title: Optional[str] = None,
        *,
        timeout_seconds: Optional[float] = None,
    ) -> bool:
        """以 JSON 主体向 ntfy 发布一条 UTF-8 通知。

        Args:
            content: 通知正文（Markdown）。
            title: 可选标题；未传入时按当日日期生成默认标题。
            timeout_seconds: HTTP 请求超时秒数；None 时使用 10 秒。

        Returns:
            True 表示服务端返回 2xx 状态码；其余情况返回 False 并记录对应级别日志。
        """
        if not self._is_ntfy_configured():
            logger.warning("ntfy URL 未配置，跳过推送")
            return False

        server_url, topic = self._resolve_ntfy_endpoint()
        if not server_url or not topic:
            logger.error("NTFY_URL 必须是包含 topic path 的完整 endpoint，例如 https://ntfy.sh/my-topic")
            return False

        if title is None:
            # 未显式传入标题时，使用当天日期生成默认标题，便于用户在客户端按日期归档
            date_str = datetime.now().strftime("%Y-%m-%d")
            title = f"📈 股票分析报告 - {date_str}"

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "daily_stock_analysis",
        }
        token = (self._ntfy_token or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        payload = {
            "topic": topic,
            "title": title,
            "message": content,
            "markdown": True,
        }

        try:
            response = requests.post(
                server_url,
                json=payload,
                headers=headers,
                timeout=timeout_seconds or 10,
                verify=self._webhook_verify_ssl,
            )
            if 200 <= response.status_code < 300:
                logger.info("ntfy 消息发送成功")
                return True

            logger.error("ntfy 请求失败: HTTP %s", response.status_code)
            logger.debug("ntfy 响应内容: %s", response.text)
            return False
        except requests.exceptions.Timeout:
            logger.error("发送 ntfy 消息失败: 请求超时")
            return False
        except requests.exceptions.RequestException as exc:
            logger.error("发送 ntfy 消息失败: 网络请求异常")
            logger.debug("ntfy 请求异常类型: %s", type(exc).__name__)
            return False
        except Exception as exc:
            logger.error("发送 ntfy 消息失败: 未知异常")
            logger.debug("ntfy 未知异常类型: %s", type(exc).__name__)
            return False
