# -*- coding: utf-8 -*-
"""导入与 API 输入路径共用的股票代码解析工具函数。"""

from __future__ import annotations

import re
from typing import Optional

from data_provider.base import is_bse_code


# Known exchange prefixes (case-insensitive) and the digit lengths they accept.
# e.g. SH600519 -> 600519, HK00700 -> 00700
_PREFIX_DIGIT_LENS: dict = {
    "SH": (6,),
    "SZ": (6,),
    "SS": (6,),
    "BJ": (6,),
    "HK": (1, 2, 3, 4, 5),
}

_SUFFIX_DIGIT_LENS: dict = {
    ".SH": (6,),
    ".SZ": (6,),
    ".SS": (6,),
    ".BJ": (6,),
    ".HK": (1, 2, 3, 4, 5),
}


def _valid_exchange_code(exchange: str, base: str, digit_lens: tuple[int, ...]) -> bool:
    """校验交易所专属的数字长度以及北交所（Beijing exchange）代码形态。"""
    if not (base.isdigit() and len(base) in digit_lens):
        return False
    if exchange == "BJ":
        return is_bse_code(base)
    return True


def _strip_exchange_prefix(text: str) -> Optional[str]:
    """剥离前导交易所前缀（SH/SZ/HK 等）并返回纯数字代码，失败返回 None。"""
    for prefix, digit_lens in _PREFIX_DIGIT_LENS.items():
        if text.startswith(prefix):
            base = text[len(prefix):]
            if _valid_exchange_code(prefix, base, digit_lens):
                return base.zfill(5) if prefix == "HK" else base
    return None


def _strip_exchange_suffix(text: str) -> Optional[str]:
    """剥离交易所后缀（.SH/.SZ/.SS/.HK）并返回归一化的纯数字代码，失败返回 None。"""
    for suffix, digit_lens in _SUFFIX_DIGIT_LENS.items():
        if text.endswith(suffix):
            base = text[: -len(suffix)].strip()
            exchange = suffix.lstrip(".")
            if _valid_exchange_code(exchange, base, digit_lens):
                return base.zfill(5) if suffix == ".HK" else base
    return None


def is_code_like(value: str) -> bool:
    """判断字符串是否像股票代码（5-6 位数字、1-5 位字母，或带前缀/后缀代码）。"""
    text = value.strip().upper()
    if not text:
        return False
    if text.isdigit() and len(text) in (5, 6):
        return True
    if _strip_exchange_suffix(text) is not None:
        return True
    if re.match(r"^[A-Z]{1,5}(?:\.(?:US|[A-Z]))?$", text):
        return True
    # Support exchange-prefixed codes: SH600519, SZ000001, BJ920493, HK00700
    if _strip_exchange_prefix(text) is not None:
        return True
    return False


def normalize_code(raw: str) -> Optional[str]:
    """归一化并校验单个股票代码。

    支持：
    - 纯数字代码：600519、00700
    - 后缀格式：600519.SH、600519.SZ、920493.BJ、00700.HK
    - 前缀格式：SH600519、SZ000001、BJ920493、HK00700（大小写不敏感）
    - 美股代码：AAPL、TSLA
    """
    text = raw.strip().upper()
    if not text:
        return None
    if text.isdigit() and len(text) in (5, 6):
        return text
    if re.match(r"^[A-Z]{1,5}(?:\.(?:US|[A-Z]))?$", text):
        return text
    stripped_suffix = _strip_exchange_suffix(text)
    if stripped_suffix is not None:
        return stripped_suffix
    # Support exchange-prefixed codes: SH600519 -> 600519, BJ920493 -> 920493
    stripped = _strip_exchange_prefix(text)
    if stripped is not None:
        return stripped
    return None
