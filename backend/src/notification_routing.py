# -*- coding: utf-8 -*-
"""通知路由配置辅助函数。

本模块刻意仅使用普通字符串，避免在此处导入 ``NotificationChannel``，
否则会与会话运行时的通知服务形成循环依赖。
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

ROUTABLE_NOTIFICATION_CHANNELS: Tuple[str, ...] = (
    "wechat",
    "feishu",
    "telegram",
    "email",
    "pushover",
    "ntfy",
    "gotify",
    "pushplus",
    "serverchan3",
    "custom",
    "discord",
    "slack",
    "astrbot",
)
ROUTABLE_NOTIFICATION_CHANNEL_SET = frozenset(ROUTABLE_NOTIFICATION_CHANNELS)

NOTIFICATION_ROUTE_CONFIGS: Dict[str, Dict[str, str]] = {
    "report": {
        "env_key": "NOTIFICATION_REPORT_CHANNELS",
        "config_attr": "notification_report_channels",
        "description": "Routes stock, daily, and market-review report notifications.",
    },
    "alert": {
        "env_key": "NOTIFICATION_ALERT_CHANNELS",
        "config_attr": "notification_alert_channels",
        "description": "Routes event-driven alert notifications.",
    },
    "system_error": {
        "env_key": "NOTIFICATION_SYSTEM_ERROR_CHANNELS",
        "config_attr": "notification_system_error_channels",
        "description": "Routes future system error notifications.",
    },
}


def parse_notification_route_channels(raw_value: object) -> List[str]:
    """解析以逗号分隔的路由渠道字符串，且不丢弃非法 token。

    该函数对输入做宽松解析：非法或无法识别的渠道不会被丢弃，
    而是原样保留，交由上层逻辑决定是否过滤。
    """
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        items: Iterable[object] = raw_value.split(",")
    elif isinstance(raw_value, (list, tuple, set)):
        items = raw_value
    else:
        items = [raw_value]

    channels: List[str] = []
    for item in items:
        token = str(item).strip().lower()
        if token:
            channels.append(token)
    return channels


def split_notification_route_channels(channels: Iterable[object]) -> Tuple[List[str], List[str]]:
    """返回去重后的合法与非法路由渠道，并保持输入顺序。

    通过两个集合分别记录已出现的合法/非法渠道，确保结果中每个
    渠道只出现一次，且相对输入顺序不变。
    """
    valid: List[str] = []
    invalid: List[str] = []
    seen_valid = set()
    seen_invalid = set()

    for channel in parse_notification_route_channels(channels):
        if channel in ROUTABLE_NOTIFICATION_CHANNEL_SET:
            if channel not in seen_valid:
                valid.append(channel)
                seen_valid.add(channel)
        elif channel not in seen_invalid:
            invalid.append(channel)
            seen_invalid.add(channel)
    return valid, invalid


def get_notification_route_config(route_type: Optional[str]) -> Optional[Dict[str, str]]:
    """返回规范化后路由类型对应的路由元数据；未知路由返回 None。

    输入会先去除首尾空白并转为小写后再查表，因此大小写与多余
    空格不影响匹配结果；路由类型为空时直接返回 None。
    """
    if route_type is None:
        return None
    return NOTIFICATION_ROUTE_CONFIGS.get(str(route_type).strip().lower())
