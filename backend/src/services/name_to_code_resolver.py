# -*- coding: utf-8 -*-
"""
===================================
股票名称到代码解析引擎
===================================

将股票名称解析为代码：本地映射 + 拼音匹配 + AkShare 兜底 + 模糊匹配。
"""

from __future__ import annotations

import difflib
import logging
import time
import unicodedata
from typing import Dict, Optional, Set, Tuple

from src.data.stock_mapping import STOCK_NAME_MAP
from src.services.stock_code_utils import is_code_like, normalize_code

logger = logging.getLogger(__name__)

# AkShare 结果缓存：``(拉取时间戳, 名称 -> 代码 字典)``
_akshare_cache: Optional[tuple[float, Dict[str, str]]] = None
_AKSHARE_CACHE_TTL = 1800  # 30 分钟


def _contains_cjk(text: str) -> bool:
    """判断文本是否包含 CJK 字符，用于过滤拉丁噪声输入。"""
    return any("\u3400" <= ch <= "\u9fff" for ch in text)


def _normalize_stock_name(name: str) -> str:
    """归一化股票名称（NFKC + 去空白），保证用户输入与 provider 输入可稳定比对。"""
    return "".join(unicodedata.normalize("NFKC", str(name or "")).split())


def _is_code_like(s: str) -> bool:
    """``is_code_like`` 的兼容包装，便于旧调用代码继续工作。"""
    return is_code_like(s)


def _normalize_code(raw: str) -> Optional[str]:
    """``normalize_code`` 的兼容包装，便于旧调用代码继续工作。"""
    return normalize_code(raw)


def _build_reverse_map_no_duplicates(
    code_to_name: Dict[str, str],
) -> Dict[str, str]:
    """构造名称 -> 代码的反向映射。

    若一个名称对应多个代码（即歧义），则忽略以避免误解析。
    """
    name_to_codes: Dict[str, Set[str]] = {}
    for code, name in code_to_name.items():
        if not name or not code:
            continue
        name = name.strip()
        if name not in name_to_codes:
            name_to_codes[name] = set()
        name_to_codes[name].add(code)
    # 只保留名称对应的代码唯一的条目
    return {name: next(iter(codes)) for name, codes in name_to_codes.items() if len(codes) == 1}


def _build_local_name_indexes(code_to_name: Dict[str, str]) -> Tuple[Dict[str, str], Set[str]]:
    """构建本地缓存的两类查找结构。

    - 唯一名称 -> 代码：直接用于精确匹配。
    - 歧义名称集合：命中时直接 fail-fast，避免误猜。
    """
    name_to_codes: Dict[str, Set[str]] = {}
    for code, name in code_to_name.items():
        if not name or not code:
            continue
        normalized_name = _normalize_stock_name(name)
        if not normalized_name:
            continue
        name_to_codes.setdefault(normalized_name, set()).add(code)

    unique_names = {
        name: next(iter(codes))
        for name, codes in name_to_codes.items()
        if len(codes) == 1
    }
    ambiguous_names = {
        name
        for name, codes in name_to_codes.items()
        if len(codes) > 1
    }
    return unique_names, ambiguous_names


_LOCAL_REVERSE_MAP, _LOCAL_AMBIGUOUS_NAMES = _build_local_name_indexes(STOCK_NAME_MAP)


def _get_akshare_name_to_code() -> Optional[Dict[str, str]]:
    """带 30 分钟 TTL 缓存地从 AkShare 拉取 A 股名称 -> 代码映射。

    AkShare 调用可能失败或超时，因此单独捕获异常并降级为 None，
    上层可继续走本地/模糊匹配兜底。
    """
    global _akshare_cache
    now = time.time()
    if _akshare_cache is not None and (now - _akshare_cache[0]) < _AKSHARE_CACHE_TTL:
        return _akshare_cache[1]
    try:
        import akshare as ak

        df = ak.stock_info_a_code_name()
        if df is None or df.empty:
            return None
        code_to_name = {}
        for _, row in df.iterrows():
            code = row.get("code")
            name = row.get("name")
            if code is None or name is None:
                continue
            code_str = str(code).strip()
            # 去掉 .SH/.SZ/.SS 等交易所后缀，统一为纯数字代码
            if "." in code_str:
                base, suffix = code_str.rsplit(".", 1)
                if suffix.upper() in ("SH", "SZ", "SS") and base.isdigit():
                    code_str = base
            code_to_name[code_str] = _normalize_stock_name(str(name))
        result = _build_reverse_map_no_duplicates(code_to_name)
        _akshare_cache = (now, result)
        logger.info(f"[NameResolver] AkShare cache loaded: {len(result)} name->code mappings")
        return result
    except Exception as e:
        logger.warning(f"[NameResolver] AkShare fallback failed: {e}")
        return None


def _is_single_char_typo(input_name: str, candidate_name: str) -> bool:
    """判断两个等长名称是否只差一个字符位置，用于单字误写的兜底匹配。"""
    if not input_name or not candidate_name:
        return False
    if len(input_name) != len(candidate_name):
        return False
    # 仅在足够长的名称上启用 typo 兜底，避免短名上误判
    if len(input_name) < 3:
        return False
    diff = sum(1 for a, b in zip(input_name, candidate_name) if a != b)
    return diff == 1


def resolve_name_to_code(name: str) -> Optional[str]:
    """把股票名称解析为代码。

    策略顺序：
    1. 若输入形似代码（5-6 位数字或 1-5 位字母），直接规范化返回；
    2. 本地 ``STOCK_NAME_MAP`` 反向查找（跳过歧义名称）；
    3. 拼音精确匹配本地名称；
    4. AkShare 在线兜底（A 股）；
    5. 子串/模糊匹配（difflib）；
    6. 全部失败返回 None。

    Args:
        name: 股票名称或代码字符串。

    Returns:
        解析到的股票代码；歧义或全部失败返回 None。
    """
    if not name or not isinstance(name, str):
        return None
    s = _normalize_stock_name(name)
    if not s:
        return None

    # 在股票映射前优先识别 A 股指数代码，避免 000001 这类代码与个股冲突
    try:
        from src.services.a_share_index_registry import get_a_share_index
        index = get_a_share_index(s)
        if index is not None:
            return index.code
    except Exception:
        pass

    # 1. 输入形似代码
    if _is_code_like(s):
        return _normalize_code(s)

    # 2. 本地反向映射（去重后唯一）
    local_reverse = _LOCAL_REVERSE_MAP
    if s in local_reverse:
        return local_reverse[s]
    if s in _LOCAL_AMBIGUOUS_NAMES:
        logger.debug(f"[NameResolver] 命中本地歧义名称，快速返回 None: {s}")
        return None

    # 3. 拼音精确匹配
    try:
        from pypinyin import lazy_pinyin

        input_pinyin = "".join(lazy_pinyin(s)).lower()
        for local_name, code in local_reverse.items():
            local_pinyin = "".join(lazy_pinyin(local_name)).lower()
            if input_pinyin == local_pinyin:
                return code
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"[NameResolver] Pinyin match failed: {e}")

    # 对非 CJK 的乱码输入直接返回，避免昂贵的 AkShare/模糊匹配路径
    if not _contains_cjk(s):
        logger.debug(f"[NameResolver] Skip CJK-only fallbacks for non-CJK input: {s}")
        return None

    # 4. AkShare 兜底
    akshare_map = _get_akshare_name_to_code()
    if akshare_map and s in akshare_map:
        logger.debug(f"[NameResolver] 命中 AkShare 映射: {s} -> {akshare_map[s]}")
        return akshare_map[s]

    # 5. 模糊匹配（本地优先，AkShare 补全）
    all_name_to_code = dict(local_reverse)
    if akshare_map:
        all_name_to_code.update(akshare_map)
    # 中文名称的子串兜底（如只输入"茅台"也能命中"贵州茅台"）
    if sum(1 for ch in s if "\u3400" <= ch <= "\u9fff") >= 2:
        substring_matches = [name for name in all_name_to_code if s in name]
        if len(substring_matches) == 1:
            return all_name_to_code[substring_matches[0]]
    # 极短输入（<=2 字）跳过模糊匹配，避免在 5000+ 股票池中误匹配类似"中国"
    # 这种过于宽泛的关键词；同时长字符串用 0.8 cutoff 进一步降噪
    if len(s) > 2:
        names = list(all_name_to_code.keys())
        matches = difflib.get_close_matches(s, names, n=1, cutoff=0.8)
        if matches:
            logger.debug(f"[NameResolver] 命中模糊匹配: input={s}, matched={matches[0]}")
            return all_name_to_code[matches[0]]

        # 对中长名称再做一次"单字误写"兜底：保留 0.8 cutoff 的严苛默认，
        # 仅在确实是单字误写时放行（如"贵州茅苔" -> "贵州茅台"）。
        typo_matches = difflib.get_close_matches(s, names, n=1, cutoff=0.7)
        if typo_matches and _is_single_char_typo(s, typo_matches[0]):
            logger.debug(f"[NameResolver] 命中单字误写兜底: input={s}, matched={typo_matches[0]}")
            return all_name_to_code[typo_matches[0]]

    logger.debug(f"[NameResolver] 解析失败: {s}")
    return None
