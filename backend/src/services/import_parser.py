# -*- coding: utf-8 -*-
"""统一导入解析管道。

把 CSV / Excel / 剪贴板文本解析成 (code, name, confidence) 形式的股票条目。
"""

from __future__ import annotations

import io
import logging
import re
from typing import List, Optional, Tuple

import pandas as pd

from src.services.name_to_code_resolver import resolve_name_to_code
from src.services.stock_code_utils import is_code_like, normalize_code

logger = logging.getLogger(__name__)

# Column name mappings (case-insensitive)
_CODE_ALIASES = frozenset({"code", "股票代码", "代码", "stock_code", "symbol"})
_NAME_ALIASES = frozenset({"name", "股票名称", "名称", "stock_name"})

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB
MAX_TEXT_BYTES = 100 * 1024  # 100KB


def _should_use_single_column_fast_path(lines: List[str]) -> bool:
    """
    判断纯文本输入是否应走"单列快速路径"。

    护栏：若某行形如"代码 + 名称"以空白分隔，则不使用单列模式，
    否则代码/名称对被粘进同一格会损害解析质量。
    """
    if not lines:
        return False

    # If explicit separators exist, this is not single-column input.
    if any(re.search(r"[\t,;]", ln) for ln in lines):
        return False

    for ln in lines:
        parts = ln.split()
        if len(parts) >= 2 and is_code_like(parts[0]):
            # Example: "600519 贵州茅台" / "HK00700 腾讯控股"
            # First token is code-like and tail contains non-code token(s).
            if any(not is_code_like(p) for p in parts[1:]):
                return False

    return True


def _detect_column_indices(df: pd.DataFrame) -> Tuple[Optional[int], Optional[int]]:
    """根据 DataFrame 的列名返回 (code_col_idx, name_col_idx)。"""
    code_idx, name_idx = None, None
    cols = [str(c).strip().lower() for c in df.columns]
    for i, c in enumerate(cols):
        if c in _CODE_ALIASES or c in {a.lower() for a in _CODE_ALIASES}:
            code_idx = i
        if c in _NAME_ALIASES or c in {a.lower() for a in _NAME_ALIASES}:
            name_idx = i
    return code_idx, name_idx


def _parse_dataframe(df: pd.DataFrame) -> List[Tuple[Optional[str], Optional[str], str]]:
    """把 DataFrame 解析成 (code, name, confidence) 条目列表。

    code 在名称反查失败时会是 None，由调用方决定是否丢弃该行。
    """
    result: List[Tuple[Optional[str], Optional[str], str]] = []
    code_idx, name_idx = _detect_column_indices(df)
    has_header = code_idx is not None or name_idx is not None

    for _, row in df.iterrows():
        # 有表头时按列下标取值，否则按"第 0 列代码、第 1 列名称"取值
        code_val = None
        name_val = None
        if has_header:
            if code_idx is not None and code_idx < len(row):
                v = row.iloc[code_idx]
                code_val = str(v).strip() if pd.notna(v) else None
            if name_idx is not None and name_idx < len(row):
                v = row.iloc[name_idx]
                name_val = str(v).strip() if pd.notna(v) else None
        else:
            # No header: col 0 = code, col 1 = name
            if len(row) >= 1:
                v = row.iloc[0]
                code_val = str(v).strip() if pd.notna(v) else None
            if len(row) >= 2:
                v = row.iloc[1]
                name_val = str(v).strip() if pd.notna(v) else None

        # Skip empty rows
        if not code_val and not name_val:
            continue

        # If "name" value looks like code, use as code
        if not code_val and name_val and is_code_like(name_val):
            code_val = name_val
            name_val = None

        code = None
        if code_val:
            code = normalize_code(code_val)
            # If code_val is not a valid code, treat as name only when name_val is empty
            # (do not overwrite valid name with dirty code_val, e.g. INVALID,贵州茅台)
            if not code and not is_code_like(code_val):
                if name_val:
                    code = resolve_name_to_code(name_val)
                    # Keep name_val; do not overwrite with code_val
                else:
                    code = resolve_name_to_code(code_val)
                    name_val = code_val
        if not code and name_val:
            code = resolve_name_to_code(name_val)
            if not code:
                logger.debug(f"[ImportParser] 名称解析失败: {name_val}")

        result.append((code, name_val if name_val else None, "medium"))
    return result


def parse_import_from_bytes(data: bytes, filename: Optional[str] = None) -> List[Tuple[Optional[str], Optional[str], str]]:
    """
    解析文件字节（CSV/Excel/纯文本）为导入条目。

    Args:
        data: 文件内容字节。
        filename: 可选文件名，用于格式探测（如 "a.csv"、"b.xlsx"）。

    Returns:
        (code, name, confidence) 条目列表；解析失败的条目 code 可能为 None。

    Raises:
        ValueError: 解析失败或不支持的格式。
    """
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"文件超过 {MAX_FILE_BYTES // (1024 * 1024)}MB 限制")

    ext = ""
    if filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    logger.debug(f"[ImportParser] 开始解析文件: filename={filename or '-'}, ext={ext or '-'}, bytes={len(data)}")

    # xlsx 本质是 zip 容器，用魔数判断比只看扩展名更可靠
    looks_like_zip = len(data) >= 4 and data[:4] == b"PK\x03\x04"

    # Excel: .xlsx (or zip magic)
    if ext == ".xlsx" or looks_like_zip:
        try:
            # 用 header=None 读入，避免工作表无表头时第一行数据被当成列名；
            # 表头判定方式与 CSV 路径保持一致
            df = pd.read_excel(io.BytesIO(data), sheet_name=0, engine="openpyxl", header=None, dtype=str)
            if df is None or df.empty:
                return []
            df = df.fillna("")
            first_row = [str(x).strip().lower() for x in df.iloc[0].tolist()]
            if any(c in _CODE_ALIASES or c in _NAME_ALIASES for c in first_row):
                df.columns = df.iloc[0]
                df = df.iloc[1:].reset_index(drop=True)
            return _parse_dataframe(df)
        except Exception as e:
            # 字节特征强烈表明是 xlsx 容器时，视为真正的 Excel 解析失败并直接报错
            if looks_like_zip:
                hint = (
                    "请确认：(1) 文件为 .xlsx 格式；(2) 工作表不为空；(3) 文件未损坏。"
                    "若为 .xls 格式，请另存为 .xlsx 后重试。"
                )
                raise ValueError(f"Excel 解析失败: {e}。{hint}") from e
            # For extension-only mismatch (e.g. csv named .xlsx), fallback to text parsing.
            logger.warning(f"扩展名为 .xlsx 但未解析为 Excel，将回退文本解析: {e}")

    # .xls not supported
    if ext == ".xls":
        raise ValueError("仅支持 .xlsx 格式，请将 .xls 另存为 .xlsx 后重试")

    # CSV / text
    for encoding in ("utf-8", "gbk"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("无法识别文件编码，请使用 UTF-8 或 GBK")

    # Single-column (one value per line): bypass pandas to avoid sep=None inference issues
    # e.g. "00700\n600519" or "code\n00700" - pandas with sep=None can produce wrong results
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if _should_use_single_column_fast_path(lines):
        rows = [[ln] for ln in lines]
        df = pd.DataFrame(rows)
        first_row = [str(x).strip().lower() for x in df.iloc[0].tolist()]
        if any(c in _CODE_ALIASES or c in _NAME_ALIASES for c in first_row):
            df.columns = df.iloc[0]
            df = df.iloc[1:].reset_index(drop=True)
        return _parse_dataframe(df)

    # 类 CSV 交给 pandas；dtype=str 是为了保住前导零（如 00700）
    try:
        df = pd.read_csv(io.StringIO(text), sep=None, engine="python", header=None, dtype=str)
        if df is not None and not df.empty:
            df = df.fillna("")
            first_row = [str(x).strip().lower() for x in df.iloc[0].tolist()]
            if any(c in _CODE_ALIASES or c in _NAME_ALIASES for c in first_row):
                df.columns = df.iloc[0]
                df = df.iloc[1:].reset_index(drop=True)
            return _parse_dataframe(df)
    except pd.errors.ParserError as e:
        raise ValueError(
            f"CSV 解析失败：请检查分隔符是否一致、列数是否匹配。"
            f"常见原因：引号未闭合、某行列数与其他行不一致。原始错误: {e}"
        ) from e
    except Exception:
        pass

    # Fallback: plain text, split by comma/tab/space
    lines = text.strip().splitlines()
    rows = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"[\t,;\s]+", line)
        if parts:
            rows.append(parts)
    if not rows:
        return []
    df = pd.DataFrame(rows)
    return _parse_dataframe(df)


def parse_import_from_text(text: str) -> List[Tuple[Optional[str], Optional[str], str]]:
    """解析剪贴板/纯文本（每行一个代码或代码+名称）为 ``(code, name, confidence)`` 列表。

    Args:
        text: 待解析的纯文本。

    Returns:
        ``(code, name, confidence)`` 三元组列表。
    """
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError(f"文本超过 {MAX_TEXT_BYTES // 1024}KB 限制")

    logger.debug(f"[ImportParser] 开始解析粘贴文本: bytes={len(text.encode('utf-8'))}")
    data = text.encode("utf-8")
    return parse_import_from_bytes(data, filename="paste.txt")
