# -*- coding: utf-8 -*-
# Derived from AlphaSift revision 9f522747caafd3c0b1ddb7e14d5cf44c8580b6cf.
# Licensed under Apache-2.0 and modified for daily_stock_analysis.
"""筛选/抓取场景下的通用字符串与数值归一化工具。

提供一组 ``safe_*`` 容错解析函数，处理来自不同数据源（AKShare/Eastmoney/Tushare/富途
等）的脏数据：空字符串、NaN、占位符（``-``/``--``）、带百分号/逗号的数字、
``123456.0`` 这种带小数点的代码等场景。

主要用途：
- 选股/筛选管道把任意来源的代码、价格、布尔值统一规整成下游可用的标量
- LLM 排序结果 JSON 在入库/筛选前做兜底清理，避免单条脏数据击穿整批
"""

from __future__ import annotations

import math
import re

# 常见的"空文本"拼写：来自 pandas/PySpark/上游 API 的 NaN/None/空串统一视为空
_NULL_TEXT_VALUES = {"", "nan", "none", "<na>", "na", "null"}

# 美股代码：ASCII 字母/数字 + 可选 . / - 分隔符，至少要含一个字母（纯数字留给 A 股代码）
# 例子：AAPL、BRK-B、BRK.B
_TICKER_RE = re.compile(r"^(?=.*[A-Za-z])[A-Za-z0-9][A-Za-z0-9.\-]{0,19}$")


def safe_text(value: object, *, max_len: int | None = None) -> str:
    """返回清洗后的字符串；常见"空值"拼写归一为空串，可选最大长度截断。"""
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    if text.lower() in _NULL_TEXT_VALUES:
        return ""
    if max_len is not None:
        return text[:max_len]
    return text


def normalize_code(value: object, *, width: int = 6, allow_ticker: bool = False) -> str:
    """把股票代码归一化为标准 A 股 6 位码，或在显式开启时透传美股 ticker。

    A 股识别始终优先，逻辑不变；当 ``allow_ticker=True`` 且文本不含 A 股代码但看起来像
    美股代码（AAPL、BRK-B）时，按原样大写透传，避免被强制归一化为空串。

    注意：仅在结构化字段（快照行、LLM 排序 JSON、已存储 picks）上开启
    ``allow_ticker``；自由文本挖掘路径必须保持严格，让垃圾 token 落到空串。
    """
    text = safe_text(value, max_len=80)
    if not text:
        return ""
    # pandas/CSV 把 ``600001`` 存为 ``600001.0`` 时去掉小数点
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if text.isdigit():
        return text.zfill(width)[-width:]
    # 文本中嵌入的 6 位 A 股代码（如 "sh600001"）抽出后再做零填充
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    if match:
        return match.group(1)
    if allow_ticker and _TICKER_RE.match(text):
        return text.upper()
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(width)[-width:] if digits else ""


def safe_float(value: object, default: float | None = None) -> float | None:
    """从松散字符串/数值中安全解析 float；解析失败或为 NaN 时返回 ``default``。"""
    text = safe_text(value)
    if not text or text in {"-", "--"}:
        return default
    try:
        # 去掉百分号/千位分隔符后解析，例如 ``1,234.5%`` → 1234.5
        parsed = float(text.replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return default
    if math.isnan(parsed):
        return default
    return parsed


def safe_int(value: object, default: int | None = None) -> int | None:
    """在 :func:`safe_float` 基础上转为 int；解析失败返回 ``default``。"""
    parsed = safe_float(value)
    if parsed is None:
        return default
    return int(parsed)


def safe_bool(value: object) -> bool | None:
    """把 1/true/yes/on 等字符串解析为 bool；输入为空时返回 ``None``。"""
    text = safe_text(value)
    if not text:
        return None
    if isinstance(value, bool):
        return value
    return text.lower() in {"1", "true", "yes", "on"}


def bounded_float(value: object, *, low: float, high: float) -> float | None:
    """解析 float 后夹紧到 ``[low, high]``；解析失败返回 ``None``。"""
    parsed = safe_float(value)
    if parsed is None:
        return None
    return max(low, min(parsed, high))


def safe_string_list(value: object, *, max_len: int = 80) -> list[str]:
    """把 list-like 字段清理成去空字符串列表（每项 ``safe_text`` 后过滤空值）。"""
    if not isinstance(value, list):
        return []
    return [
        text
        for text in (safe_text(item, max_len=max_len) for item in value)
        if text
    ]


