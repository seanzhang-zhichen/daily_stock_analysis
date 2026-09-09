# -*- coding: utf-8 -*-
"""通知配置诊断(只读)。

诊断模块面向 CLI / API 展示, **绝不打印任何密钥值**。它只检查:

- 各个渠道的最小必填 key 与高级 key 是否齐备
- 成对配置(例如 BOT_TOKEN + CHAT_ID)是否同时存在
- ntfy / Gotify 等 URL 格式是否合法
- P3 路由配置、P4 降噪配置、P6 ntfy/Gotify 渠道
- 上下文型渠道(钉钉会话 / 飞书会话)等运行时信息

调用方: ``notification:diagnose`` CLI、API 端点; 仅做读取和结果汇总, 不发起
任何实际通知发送。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional, Sequence, Tuple

from src.config import Config
from src.notification import ChannelDetector, NotificationChannel, NotificationService
from src.notification_noise import (
    NOTIFICATION_SEVERITIES,
    P4_NOISE_ENV_KEYS,
    is_supported_notification_severity,
    parse_notification_quiet_hours,
    validate_notification_timezone,
)
from src.notification_routing import (
    NOTIFICATION_ROUTE_CONFIGS,
    ROUTABLE_NOTIFICATION_CHANNELS,
    split_notification_route_channels,
)
from src.notification_sender.gotify_sender import resolve_gotify_message_endpoint
from src.notification_sender.ntfy_sender import resolve_ntfy_endpoint

# key 的重要性等级：minimal 是启用渠道必须；advanced 是可选优化项
KeyTier = Literal["minimal", "advanced"]
# 诊断问题的严重程度：error 必须修复，warning 提示配置可能失效，info 仅说明
IssueSeverity = Literal["error", "warning", "info"]
# 渠道分类：configured 来自静态环境变量，fallback 是兜底枚举，context 仅运行时可见
ChannelKind = Literal["configured", "fallback", "context"]


@dataclass(frozen=True)
class NotificationKeySpec:
    """单个通知相关环境变量的元信息，供诊断页/CLI 表格展示。"""

    key: str
    tier: KeyTier
    description: str
    channel: str


@dataclass(frozen=True)
class NotificationChannelSpec:
    """单个通知渠道的基线元信息（最小 key、可选高级 key、备注等）。"""

    channel: str
    display_name: str
    kind: ChannelKind
    minimal_keys: Tuple[str, ...]
    alternative_minimal_keys: Tuple[Tuple[str, ...], ...] = ()
    advanced_keys: Tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class NotificationDiagnosticIssue:
    """单条诊断结果，含严重等级、错误码、描述与关联环境变量。"""

    severity: IssueSeverity
    code: str
    message: str
    key: Optional[str] = None


@dataclass(frozen=True)
class NotificationDiagnosticResult:
    """结构化的通知诊断结果，包含已配置渠道与三类问题列表。"""

    configured_channels: Tuple[str, ...]
    errors: Tuple[NotificationDiagnosticIssue, ...]
    warnings: Tuple[NotificationDiagnosticIssue, ...]
    info: Tuple[NotificationDiagnosticIssue, ...]

    @property
    def ok(self) -> bool:
        """返回是否不存在阻塞性问题；用于 CLI 退出码判定。"""
        return not self.errors


# 全部渠道的基线规格；新增渠道时同时把 minimal/advanced key 写到这里
CHANNEL_SPECS: Tuple[NotificationChannelSpec, ...] = (
    NotificationChannelSpec(
        channel=NotificationChannel.WECHAT.value,
        display_name=ChannelDetector.get_channel_name(NotificationChannel.WECHAT),
        kind="configured",
        minimal_keys=("WECHAT_WEBHOOK_URL",),
        advanced_keys=("WECHAT_MSG_TYPE",),
    ),
    NotificationChannelSpec(
        channel=NotificationChannel.FEISHU.value,
        display_name=ChannelDetector.get_channel_name(NotificationChannel.FEISHU),
        kind="configured",
        minimal_keys=("FEISHU_WEBHOOK_URL",),
        advanced_keys=("FEISHU_WEBHOOK_SECRET", "FEISHU_WEBHOOK_KEYWORD"),
    ),
    NotificationChannelSpec(
        channel=NotificationChannel.TELEGRAM.value,
        display_name=ChannelDetector.get_channel_name(NotificationChannel.TELEGRAM),
        kind="configured",
        minimal_keys=("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
        advanced_keys=("TELEGRAM_MESSAGE_THREAD_ID",),
    ),
    NotificationChannelSpec(
        channel=NotificationChannel.EMAIL.value,
        display_name=ChannelDetector.get_channel_name(NotificationChannel.EMAIL),
        kind="configured",
        minimal_keys=("EMAIL_SENDER", "EMAIL_PASSWORD"),
        advanced_keys=("EMAIL_RECEIVERS", "EMAIL_SENDER_NAME"),
    ),
    NotificationChannelSpec(
        channel=NotificationChannel.PUSHOVER.value,
        display_name=ChannelDetector.get_channel_name(NotificationChannel.PUSHOVER),
        kind="configured",
        minimal_keys=("PUSHOVER_USER_KEY", "PUSHOVER_API_TOKEN"),
    ),
    NotificationChannelSpec(
        channel=NotificationChannel.NTFY.value,
        display_name=ChannelDetector.get_channel_name(NotificationChannel.NTFY),
        kind="configured",
        minimal_keys=("NTFY_URL",),
        advanced_keys=("NTFY_TOKEN", "WEBHOOK_VERIFY_SSL"),
        note="NTFY_URL must include the topic path, e.g. https://ntfy.sh/my-topic.",
    ),
    NotificationChannelSpec(
        channel=NotificationChannel.GOTIFY.value,
        display_name=ChannelDetector.get_channel_name(NotificationChannel.GOTIFY),
        kind="configured",
        minimal_keys=("GOTIFY_URL", "GOTIFY_TOKEN"),
        advanced_keys=("WEBHOOK_VERIFY_SSL",),
        note="GOTIFY_URL is the server base URL; the sender appends /message.",
    ),
    NotificationChannelSpec(
        channel=NotificationChannel.PUSHPLUS.value,
        display_name=ChannelDetector.get_channel_name(NotificationChannel.PUSHPLUS),
        kind="configured",
        minimal_keys=("PUSHPLUS_TOKEN",),
        advanced_keys=("PUSHPLUS_TOPIC",),
    ),
    NotificationChannelSpec(
        channel=NotificationChannel.SERVERCHAN3.value,
        display_name=ChannelDetector.get_channel_name(NotificationChannel.SERVERCHAN3),
        kind="configured",
        minimal_keys=("SERVERCHAN3_SENDKEY",),
    ),
    NotificationChannelSpec(
        channel=NotificationChannel.CUSTOM.value,
        display_name=ChannelDetector.get_channel_name(NotificationChannel.CUSTOM),
        kind="configured",
        minimal_keys=("CUSTOM_WEBHOOK_URLS",),
        advanced_keys=("CUSTOM_WEBHOOK_BEARER_TOKEN", "CUSTOM_WEBHOOK_BODY_TEMPLATE", "WEBHOOK_VERIFY_SSL"),
    ),
    NotificationChannelSpec(
        channel=NotificationChannel.DISCORD.value,
        display_name=ChannelDetector.get_channel_name(NotificationChannel.DISCORD),
        kind="configured",
        minimal_keys=("DISCORD_WEBHOOK_URL",),
        alternative_minimal_keys=(("DISCORD_BOT_TOKEN", "DISCORD_MAIN_CHANNEL_ID"),),
        advanced_keys=("DISCORD_INTERACTIONS_PUBLIC_KEY",),
        note="Webhook URL or bot token + channel ID can enable Discord.",
    ),
    NotificationChannelSpec(
        channel=NotificationChannel.SLACK.value,
        display_name=ChannelDetector.get_channel_name(NotificationChannel.SLACK),
        kind="configured",
        minimal_keys=("SLACK_WEBHOOK_URL",),
        alternative_minimal_keys=(("SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID"),),
        note="Webhook URL or bot token + channel ID can enable Slack.",
    ),
    NotificationChannelSpec(
        channel=NotificationChannel.ASTRBOT.value,
        display_name=ChannelDetector.get_channel_name(NotificationChannel.ASTRBOT),
        kind="configured",
        minimal_keys=("ASTRBOT_URL",),
        advanced_keys=("ASTRBOT_TOKEN", "WEBHOOK_VERIFY_SSL"),
    ),
    NotificationChannelSpec(
        channel=NotificationChannel.DINGTALK.value,
        display_name=ChannelDetector.get_channel_name(NotificationChannel.DINGTALK),
        kind="configured",
        minimal_keys=("DINGTALK_WEBHOOK_URL",),
        advanced_keys=("DINGTALK_SECRET",),
    ),
    NotificationChannelSpec(
        channel=NotificationChannel.UNKNOWN.value,
        display_name=ChannelDetector.get_channel_name(NotificationChannel.UNKNOWN),
        kind="fallback",
        minimal_keys=(),
        note="Fallback enum value only; it is not configured from static environment keys.",
    ),
    NotificationChannelSpec(
        channel="dingtalk_context",
        display_name="钉钉会话",
        kind="context",
        minimal_keys=(),
        note="Runtime-only reply channel extracted from source message context.",
    ),
    NotificationChannelSpec(
        channel="feishu_context",
        display_name="飞书会话",
        kind="context",
        minimal_keys=(),
        note="Runtime-only reply channel extracted from source message context.",
    ),
)

# 展开后的"渠道 → key"明细表，供前端渠道诊断页与 CLI 全量扫描
KEY_SPECS: Tuple[NotificationKeySpec, ...] = tuple(
    NotificationKeySpec(key=key, tier="minimal", description="Required to enable the channel.", channel=spec.channel)
    for spec in CHANNEL_SPECS
    for key in (
        spec.minimal_keys
        + tuple(key for key_group in spec.alternative_minimal_keys for key in key_group)
    )
) + tuple(
    NotificationKeySpec(key=key, tier="advanced", description="Optional channel behavior or security setting.", channel=spec.channel)
    for spec in CHANNEL_SPECS
    for key in spec.advanced_keys
) + tuple(
    NotificationKeySpec(
        key=route["env_key"],
        tier="advanced",
        description=route["description"],
        channel="routing",
    )
    for route in NOTIFICATION_ROUTE_CONFIGS.values()
) + tuple(
    NotificationKeySpec(
        key=key,
        tier="advanced",
        description="Optional notification noise-control setting.",
        channel="noise",
    )
    for key in P4_NOISE_ENV_KEYS
)

# P0 基础设置类环境变量
P0_ACTIONS_ENV_KEYS: Tuple[str, ...] = (
    "CUSTOM_WEBHOOK_BODY_TEMPLATE",
    "WEBHOOK_VERIFY_SSL",
    "FEISHU_WEBHOOK_SECRET",
    "FEISHU_WEBHOOK_KEYWORD",
    "PUSHPLUS_TOPIC",
)

# P3 路由类环境变量（按 severity/route_type 路由到不同渠道）
P3_ROUTE_ENV_KEYS: Tuple[str, ...] = tuple(
    route["env_key"] for route in NOTIFICATION_ROUTE_CONFIGS.values()
)

# P4 降噪类环境变量（与 P4_NOISE_ENV_KEYS 同源，避免重复维护）
P4_NOISE_ACTIONS_ENV_KEYS: Tuple[str, ...] = P4_NOISE_ENV_KEYS

# P6 渠道类环境变量：当前主要覆盖 ntfy 与 Gotify
P6_CHANNEL_ACTIONS_ENV_KEYS: Tuple[str, ...] = (
    "NTFY_URL",
    "NTFY_TOKEN",
    "GOTIFY_URL",
    "GOTIFY_TOKEN",
)


def _value(config: Config, attr: str):
    """读取 config 上某一属性，缺省返回 ``None``（屏蔽 ``AttributeError``）。"""
    return getattr(config, attr, None)


def _has(config: Config, attr: str) -> bool:
    """判断 config 上某属性是否"有值"：容器要看是否非空，标量要看是否为非空白字符串。"""
    value = _value(config, attr)
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return value is not None and str(value).strip() != ""


def _issue(
    severity: IssueSeverity,
    code: str,
    message: str,
    key: Optional[str] = None,
) -> NotificationDiagnosticIssue:
    """构造一条结构化的诊断问题。"""
    return NotificationDiagnosticIssue(severity=severity, code=code, message=message, key=key)


def _require_pair(
    config: Config,
    *,
    left_attr: str,
    right_attr: str,
    left_key: str,
    right_key: str,
    channel_name: str,
    errors: List[NotificationDiagnosticIssue],
    warnings: Optional[List[NotificationDiagnosticIssue]] = None,
    severity: IssueSeverity = "error",
) -> None:
    """校验成对出现的通知配置（如 BOT_TOKEN + CHAT_ID）；单边存在时记 warning/error。"""
    left = _has(config, left_attr)
    right = _has(config, right_attr)
    target = errors if severity == "error" else warnings
    if target is None:
        # 调用方传了 warning 但没传 warnings 容器时回落到 errors，避免漏报
        target = errors
    if left and not right:
        target.append(
            _issue(
                severity,
                "partial_channel_config",
                f"{channel_name} 已配置 {left_key}，但缺少 {right_key}，该渠道不会启用。",
                key=right_key,
            )
        )
    if right and not left:
        target.append(
            _issue(
                severity,
                "partial_channel_config",
                f"{channel_name} 已配置 {right_key}，但缺少 {left_key}，该渠道不会启用。",
                key=left_key,
            )
        )


def run_notification_diagnostics(config: Config) -> NotificationDiagnosticResult:
    """对通知配置执行只读诊断，汇总 errors / warnings / info 三类问题。"""

    configured = tuple(channel.value for channel in NotificationService.detect_configured_channels(config))
    errors: List[NotificationDiagnosticIssue] = []
    warnings: List[NotificationDiagnosticIssue] = []
    info: List[NotificationDiagnosticIssue] = [
        _issue(
            "info",
            "context_channels_runtime_only",
            "钉钉会话和飞书会话属于运行时消息上下文渠道，无法仅靠静态 .env 完整判断。",
        ),
        _issue(
            "info",
            "phase_scope",
            "通知诊断会检查渠道基线、只读诊断、Web 测试、P3 路由配置、P4 降噪配置和 P6 ntfy/Gotify 渠道。",
        ),
    ]

    if not configured:
        # 没有任何渠道启用 → 通知功能不可用，必须报 error
        errors.append(
            _issue(
                "error",
                "no_channels_configured",
                "0 个通知渠道已配置；如需发送通知，请至少配置一个渠道的 minimal key。",
            )
        )

    if _has(config, "ntfy_url"):
        ntfy_server_url, ntfy_topic = resolve_ntfy_endpoint(getattr(config, "ntfy_url", None))
        if not ntfy_server_url or not ntfy_topic:
            errors.append(
                _issue(
                    "error",
                    "invalid_ntfy_url",
                    "NTFY_URL 必须包含 topic path，例如 https://ntfy.sh/my-topic。",
                    key="NTFY_URL",
                )
            )

    if _has(config, "gotify_url"):
        gotify_endpoint = resolve_gotify_message_endpoint(getattr(config, "gotify_url", None))
        if not gotify_endpoint:
            errors.append(
                _issue(
                    "error",
                    "invalid_gotify_url",
                    "GOTIFY_URL 必须是 Gotify server base URL，不包含 /message，例如 https://gotify.example。",
                    key="GOTIFY_URL",
                )
            )

    _require_pair(
        config,
        left_attr="telegram_bot_token",
        right_attr="telegram_chat_id",
        left_key="TELEGRAM_BOT_TOKEN",
        right_key="TELEGRAM_CHAT_ID",
        channel_name="Telegram",
        errors=errors,
    )
    _require_pair(
        config,
        left_attr="email_sender",
        right_attr="email_password",
        left_key="EMAIL_SENDER",
        right_key="EMAIL_PASSWORD",
        channel_name="邮件",
        errors=errors,
    )
    _require_pair(
        config,
        left_attr="pushover_user_key",
        right_attr="pushover_api_token",
        left_key="PUSHOVER_USER_KEY",
        right_key="PUSHOVER_API_TOKEN",
        channel_name="Pushover",
        errors=errors,
    )
    _require_pair(
        config,
        left_attr="gotify_url",
        right_attr="gotify_token",
        left_key="GOTIFY_URL",
        right_key="GOTIFY_TOKEN",
        channel_name="Gotify",
        errors=errors,
    )
    # Discord/Slack：若同时配置了 Webhook 渠道则 Bot 缺一边只是 warning，否则视为 error
    _require_pair(
        config,
        left_attr="discord_bot_token",
        right_attr="discord_main_channel_id",
        left_key="DISCORD_BOT_TOKEN",
        right_key="DISCORD_MAIN_CHANNEL_ID",
        channel_name="Discord Bot",
        errors=errors,
        warnings=warnings,
        severity="warning" if _has(config, "discord_webhook_url") else "error",
    )
    _require_pair(
        config,
        left_attr="slack_bot_token",
        right_attr="slack_channel_id",
        left_key="SLACK_BOT_TOKEN",
        right_key="SLACK_CHANNEL_ID",
        channel_name="Slack Bot",
        errors=errors,
        warnings=warnings,
        severity="warning" if _has(config, "slack_webhook_url") else "error",
    )

    # 高级 key 已配置但最小 key 缺失：渠道不会启用，给 warning
    if (_has(config, "feishu_webhook_secret") or _has(config, "feishu_webhook_keyword")) and not _has(config, "feishu_webhook_url"):
        warnings.append(
            _issue(
                "warning",
                "advanced_without_minimal",
                "已配置飞书 Webhook 高级安全项，但缺少 FEISHU_WEBHOOK_URL，飞书 Webhook 渠道不会启用。",
                key="FEISHU_WEBHOOK_URL",
            )
        )
    if _has(config, "pushplus_topic") and not _has(config, "pushplus_token"):
        warnings.append(
            _issue(
                "warning",
                "advanced_without_minimal",
                "已配置 PUSHPLUS_TOPIC，但缺少 PUSHPLUS_TOKEN，PushPlus 渠道不会启用。",
                key="PUSHPLUS_TOKEN",
            )
        )
    if _has(config, "ntfy_token") and not _has(config, "ntfy_url"):
        warnings.append(
            _issue(
                "warning",
                "advanced_without_minimal",
                "已配置 NTFY_TOKEN，但缺少 NTFY_URL，ntfy 渠道不会启用。",
                key="NTFY_URL",
            )
        )
    if (
        _has(config, "custom_webhook_bearer_token")
        or _has(config, "custom_webhook_body_template")
    ) and not _has(config, "custom_webhook_urls"):
        warnings.append(
            _issue(
                "warning",
                "advanced_without_minimal",
                "已配置自定义 Webhook 高级项，但缺少 CUSTOM_WEBHOOK_URLS，自定义 Webhook 渠道不会启用。",
                key="CUSTOM_WEBHOOK_URLS",
            )
        )
    if _has(config, "astrbot_token") and not _has(config, "astrbot_url"):
        warnings.append(
            _issue(
                "warning",
                "advanced_without_minimal",
                "已配置 ASTRBOT_TOKEN，但缺少 ASTRBOT_URL，AstrBot 渠道不会启用。",
                key="ASTRBOT_URL",
            )
        )

    configured_set = set(configured)
    for route_type, route_config in NOTIFICATION_ROUTE_CONFIGS.items():
        route_channels = getattr(config, route_config["config_attr"], []) or []
        if not route_channels:
            continue

        valid_channels, invalid_channels = split_notification_route_channels(route_channels)
        if invalid_channels:
            errors.append(
                _issue(
                    "error",
                    "invalid_route_channel",
                    (
                        f"{route_config['env_key']} 包含未知通知渠道: {', '.join(invalid_channels)}；"
                        f"允许值: {', '.join(ROUTABLE_NOTIFICATION_CHANNELS)}。"
                    ),
                    key=route_config["env_key"],
                )
            )

        # 路由指向尚未启用的渠道时给 warning：路由不会报错但实际收不到消息
        disabled_channels = [channel for channel in valid_channels if channel not in configured_set]
        if disabled_channels:
            warnings.append(
                _issue(
                    "warning",
                    "route_channel_not_configured",
                    (
                        f"{route_config['env_key']} 路由 {route_type} 指向未启用渠道: "
                        f"{', '.join(disabled_channels)}；这些渠道不会收到该类型通知。"
                    ),
                    key=route_config["env_key"],
                )
            )

    if getattr(config, "notification_quiet_hours", ""):
        try:
            parse_notification_quiet_hours(config.notification_quiet_hours)
        except ValueError as exc:
            errors.append(
                _issue(
                    "error",
                    "invalid_quiet_hours",
                    f"NOTIFICATION_QUIET_HOURS 配置无效: {exc}",
                    key="NOTIFICATION_QUIET_HOURS",
                )
            )

    if getattr(config, "notification_timezone", ""):
        try:
            validate_notification_timezone(config.notification_timezone)
        except ValueError as exc:
            errors.append(
                _issue(
                    "error",
                    "invalid_notification_timezone",
                    f"NOTIFICATION_TIMEZONE 配置无效: {exc}",
                    key="NOTIFICATION_TIMEZONE",
                )
            )

    min_severity = getattr(config, "notification_min_severity", "") or ""
    if min_severity and not is_supported_notification_severity(min_severity):
        errors.append(
            _issue(
                "error",
                "invalid_notification_min_severity",
                (
                    "NOTIFICATION_MIN_SEVERITY 配置无效；"
                    f"允许值: {', '.join(NOTIFICATION_SEVERITIES)}。"
                ),
                key="NOTIFICATION_MIN_SEVERITY",
            )
        )

    if getattr(config, "notification_daily_digest_enabled", False):
        # 每日摘要目前是预留配置，不做任何实际行为，单独提醒
        warnings.append(
            _issue(
                "warning",
                "reserved_daily_digest",
                (
                    "NOTIFICATION_DAILY_DIGEST_ENABLED 当前为预留配置；"
                    "P4 不会发送每日摘要或持久化摘要内容。"
                ),
                key="NOTIFICATION_DAILY_DIGEST_ENABLED",
            )
        )

    return NotificationDiagnosticResult(
        configured_channels=configured,
        errors=tuple(errors),
        warnings=tuple(warnings),
        info=tuple(info),
    )


def _format_issues(title: str, issues: Sequence[NotificationDiagnosticIssue]) -> List[str]:
    """把同类问题格式化成给人读的 CLI 多行文本。"""
    if not issues:
        return [f"{title}: 无"]
    lines = [f"{title}:"]
    for item in issues:
        key_suffix = f" [{item.key}]" if item.key else ""
        lines.append(f"- {item.code}{key_suffix}: {item.message}")
    return lines


def format_notification_diagnostics(result: NotificationDiagnosticResult) -> str:
    """把诊断结果格式化成 CLI 文本输出，全程不展示任何密钥值。"""

    lines = [
        "通知配置诊断",
        f"已配置渠道: {len(result.configured_channels)} 个",
    ]
    if result.configured_channels:
        channel_names = [
            ChannelDetector.get_channel_name(NotificationChannel(channel))
            for channel in result.configured_channels
        ]
        lines.append("渠道列表: " + ", ".join(channel_names))
    else:
        lines.append("渠道列表: (无)")

    lines.append("")
    lines.extend(_format_issues("Errors", result.errors))
    lines.append("")
    lines.extend(_format_issues("Warnings", result.warnings))
    lines.append("")
    lines.extend(_format_issues("Info", result.info))
    return "\n".join(lines)
