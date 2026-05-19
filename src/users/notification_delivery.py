# -*- coding: utf-8 -*-
"""每日推送通知投递层 (Phase 3 收尾)。

把 ``run_per_user_scheduled_analysis`` 里的「报告 → 推送」逻辑从 ``main.py``
抽离, 便于:

- HTML 邮件模板 + 一键退订链接的统一渲染。
- 多类型 Webhook (飞书 / 企业微信 / Discord / Telegram / 通用 JSON) 的实际发送。

设计要点:

- 邮件:
  - ``body_text`` 仍是 Markdown 报告全文 (保持纯文本可读, 兼容老客户端);
  - ``body_html`` 通过 ``markdown2`` 渲染为 HTML, 失败时回退 ``<pre>`` 包裹;
  - HTML 底部固定附 *免责声明* + *一键退订* 链接, 链接 token 由
    :mod:`src.users.unsubscribe` 签发。
- Webhook:
  - 每个类型独立函数, 出错只 log 不抛, 失败不影响其它用户;
  - 内容按渠道做长度截断 (WeCom 4096 字节, Discord 2000 字符, Telegram 4096 字符);
  - 仅当 ``plan.can_webhook=True`` 且 ``webhook_url`` 已配置时才会真正发送,
    具体由调用方传 ``can_webhook`` 控制 (避免免费档绕过套餐限制)。
"""

from __future__ import annotations

import html as html_lib
import logging
from dataclasses import dataclass
from typing import Optional

import requests

from src.users.email import EmailBackend, EmailMessageDTO, get_email_backend
from src.users.notification_prefs import NotificationPrefs
from src.users.unsubscribe import (
    ACTION_DAILY,
    build_unsubscribe_url,
)

logger = logging.getLogger(__name__)

# 各 Webhook 渠道的安全长度上限。
_WECOM_MAX_BYTES = 4000  # 官方 4096, 留一点余量给 markdown 包裹字段
_DISCORD_MAX_CHARS = 1900
_TELEGRAM_MAX_CHARS = 4000
_FEISHU_MAX_CHARS = 18000  # 飞书消息体在 20KB 字节左右, 字符级粗略截断
_GENERIC_MAX_CHARS = 18000

_WEBHOOK_TIMEOUT_SECONDS = 15


# ---------------------------------------------------------------------------
# HTML email
# ---------------------------------------------------------------------------


def _render_markdown_to_html(markdown_text: str) -> str:
    """Markdown -> HTML, 失败时回退 ``<pre>`` 包裹纯文本。"""
    try:
        import markdown2  # type: ignore

        rendered = markdown2.markdown(
            markdown_text,
            extras=["tables", "fenced-code-blocks", "break-on-newline"],
        )
        return str(rendered)
    except Exception:
        logger.debug("markdown2 渲染失败, 回退到 <pre> 纯文本", exc_info=True)
        safe = html_lib.escape(markdown_text or "")
        return f"<pre style=\"white-space:pre-wrap;font-family:inherit;\">{safe}</pre>"


def _build_html_body(
    *,
    report_markdown: str,
    unsubscribe_url: str,
    user_email: str,
) -> str:
    rendered = _render_markdown_to_html(report_markdown or "")
    safe_email = html_lib.escape(user_email or "")
    safe_unsubscribe = html_lib.escape(unsubscribe_url or "")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>DSA 每日分析报告</title>
</head>
<body style="margin:0;padding:0;background:#f5f6f8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,'Microsoft YaHei',sans-serif;color:#1f2937;">
  <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background:#f5f6f8;padding:24px 0;">
    <tr>
      <td align="center">
        <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width:680px;background:#ffffff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.04);">
          <tr>
            <td style="padding:24px 32px 8px 32px;border-bottom:1px solid #eef0f3;">
              <div style="font-size:13px;color:#6b7280;">DSA · 每日股票智能分析</div>
              <div style="font-size:20px;font-weight:600;color:#111827;margin-top:4px;">您的自选股分析报告</div>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 32px 24px 32px;font-size:14px;line-height:1.6;">
              {rendered}
            </td>
          </tr>
          <tr>
            <td style="padding:16px 32px 24px 32px;border-top:1px solid #eef0f3;font-size:12px;color:#6b7280;line-height:1.6;">
              <div>本邮件由 DSA 自动发送至 <strong>{safe_email}</strong>，请勿直接回复。</div>
              <div style="margin-top:6px;">本服务基于 AI 模型生成观点，不构成投资建议。投资有风险，入市需谨慎。</div>
              <div style="margin-top:10px;">
                如不希望继续接收每日分析邮件，可
                <a href="{safe_unsubscribe}" style="color:#2563eb;text-decoration:underline;">一键退订</a>。
              </div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def _build_text_body(
    *,
    report_markdown: str,
    unsubscribe_url: str,
) -> str:
    """text 版仍是原始报告 + 末尾退订提示, 兼容纯文本客户端。"""
    footer = (
        "\n\n---\n"
        "本邮件由 DSA 自动发送，请勿直接回复。\n"
        "本服务基于 AI 模型生成观点，不构成投资建议。投资有风险，入市需谨慎。\n"
        f"一键退订: {unsubscribe_url}\n"
    )
    return f"{report_markdown or ''}{footer}"


@dataclass(frozen=True)
class DailyEmailContext:
    """构建每日推送邮件所需的最小上下文。"""

    user_id: int
    user_email: str
    subject: str
    report_markdown: str


def build_daily_email_message(ctx: DailyEmailContext) -> EmailMessageDTO:
    """根据上下文构建一封含 HTML + 退订链接的邮件。"""
    unsubscribe_url = build_unsubscribe_url(
        user_id=ctx.user_id, action=ACTION_DAILY
    )
    text_body = _build_text_body(
        report_markdown=ctx.report_markdown,
        unsubscribe_url=unsubscribe_url,
    )
    html_body = _build_html_body(
        report_markdown=ctx.report_markdown,
        unsubscribe_url=unsubscribe_url,
        user_email=ctx.user_email,
    )
    return EmailMessageDTO(
        to=ctx.user_email,
        subject=ctx.subject,
        body_text=text_body,
        body_html=html_body,
    )


def send_daily_email(
    ctx: DailyEmailContext,
    *,
    backend: Optional[EmailBackend] = None,
) -> bool:
    """发送每日推送邮件; 异常吞掉只 log, 返回是否成功。"""
    if not ctx.user_email:
        logger.info("[daily-email] 用户 %d 无邮箱, 跳过", ctx.user_id)
        return False
    backend = backend or get_email_backend()
    message = build_daily_email_message(ctx)
    try:
        backend.send(message)
    except Exception:
        logger.exception("[daily-email] 用户 %d 邮件发送失败", ctx.user_id)
        return False
    return True


# ---------------------------------------------------------------------------
# Webhook delivery
# ---------------------------------------------------------------------------


def _post_json(url: str, payload: dict, *, label: str) -> bool:
    try:
        resp = requests.post(
            url,
            json=payload,
            timeout=_WEBHOOK_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("[webhook] %s 请求异常: %s", label, exc)
        return False

    if 200 <= resp.status_code < 300:
        logger.info("[webhook] %s 推送成功 (status=%s)", label, resp.status_code)
        return True

    body_preview = (resp.text or "")[:200]
    logger.warning(
        "[webhook] %s 推送失败 status=%s body=%s",
        label,
        resp.status_code,
        body_preview,
    )
    return False


def _truncate_text(content: str, max_chars: int) -> str:
    if not content:
        return ""
    if len(content) <= max_chars:
        return content
    return content[: max_chars - 20] + "\n…（内容已截断）"


def _truncate_bytes(content: str, max_bytes: int) -> str:
    if not content:
        return ""
    encoded = content.encode("utf-8")
    if len(encoded) <= max_bytes:
        return content
    # 按字节截断后再回退到一个合法的 utf-8 边界
    truncated = encoded[: max_bytes - 40]
    text = truncated.decode("utf-8", errors="ignore")
    return text + "\n…（内容已截断）"


def _send_feishu_webhook(url: str, content: str, *, title: str) -> bool:
    """飞书自定义机器人 - 交互卡片 (lark_md)。

    不依赖 keyword / signature, 假定用户在创建机器人时未启用安全设置或
    通过 ``title`` 已包含关键词; 失败时回退普通 text 消息。
    """
    safe_content = _truncate_bytes(content, _FEISHU_MAX_CHARS)
    card_payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title or "DSA 每日分析报告"}
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": safe_content},
                }
            ],
        },
    }
    if _post_json(url, card_payload, label="feishu(card)"):
        return True
    # 卡片失败时回退到文本 (例如 lark_md 不可用)
    text_payload = {"msg_type": "text", "content": {"text": safe_content}}
    return _post_json(url, text_payload, label="feishu(text)")


def _send_wecom_webhook(url: str, content: str, *, title: str) -> bool:
    """企业微信群机器人 - markdown 格式。"""
    body = f"### {title}\n\n{content}" if title else content
    safe_body = _truncate_bytes(body, _WECOM_MAX_BYTES)
    payload = {
        "msgtype": "markdown",
        "markdown": {"content": safe_body},
    }
    return _post_json(url, payload, label="wecom")


def _send_discord_webhook(url: str, content: str, *, title: str) -> bool:
    """Discord webhook - 直接走 content 字段, 不使用 embed (避免格式失真)。"""
    full = f"**{title}**\n{content}" if title else content
    safe_body = _truncate_text(full, _DISCORD_MAX_CHARS)
    payload = {"content": safe_body}
    return _post_json(url, payload, label="discord")


def _send_telegram_webhook(url: str, content: str, *, title: str) -> bool:
    """Telegram - URL 形如 ``https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<ID>``;

    POST body 携带 ``text`` 字段。用户也可以贴一个自建中转 URL,
    我们仅保证 POST 一个统一 schema。
    """
    full = f"*{title}*\n\n{content}" if title else content
    safe_body = _truncate_text(full, _TELEGRAM_MAX_CHARS)
    payload = {
        "text": safe_body,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    return _post_json(url, payload, label="telegram")


def _send_generic_webhook(url: str, content: str, *, title: str) -> bool:
    """通用 JSON webhook - 不假设接收方 schema, 一律 POST ``{title, content}``。"""
    safe_body = _truncate_text(content, _GENERIC_MAX_CHARS)
    payload = {
        "title": title or "DSA 每日分析报告",
        "content": safe_body,
    }
    return _post_json(url, payload, label="generic")


_WEBHOOK_DISPATCH = {
    "feishu": _send_feishu_webhook,
    "wecom": _send_wecom_webhook,
    "discord": _send_discord_webhook,
    "telegram": _send_telegram_webhook,
    "generic": _send_generic_webhook,
}


def dispatch_user_webhook(
    prefs: NotificationPrefs,
    *,
    can_webhook: bool,
    content: str,
    title: str = "DSA 每日分析报告",
) -> bool:
    """根据用户偏好把内容推到对应渠道。

    返回 ``True`` 表示实际发起了一次成功的 POST; ``False`` 表示跳过 / 失败。
    """
    if not can_webhook:
        return False
    url = (prefs.webhook_url or "").strip()
    webhook_type = (prefs.webhook_type or "").strip().lower()
    if not url or not webhook_type:
        return False

    handler = _WEBHOOK_DISPATCH.get(webhook_type)
    if handler is None:
        logger.warning("[webhook] 未知 webhook_type=%s, 跳过", webhook_type)
        return False

    try:
        return handler(url, content, title=title)
    except Exception:
        logger.exception("[webhook] %s 投递异常", webhook_type)
        return False


__all__ = [
    "DailyEmailContext",
    "build_daily_email_message",
    "send_daily_email",
    "dispatch_user_webhook",
]
