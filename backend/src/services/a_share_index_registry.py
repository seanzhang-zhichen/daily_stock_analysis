"""
常用 A 股指数的显式注册表。

本模块维护一份预定义的 A 股指数列表，提供代码、名称、别名与规范化 ID 的映射。
主要用途：
- 在分析、报告和 Agent 交互中识别用户提到的指数
- 将用户输入的代码/名称/别名统一映射到标准化的指数对象
- 支持前端展示、数据查询和报告生成时的指数元数据
"""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata
from typing import Optional


@dataclass(frozen=True)
class AShareIndex:
    """一条 A 股指数注册项：代码、名称、别名与规范化 ID。

    Attributes:
        code: 指数原始代码，如 "000001"（不带交易所前缀）
        name: 指数的官方中文名称，如 "上证指数"
        aliases: 指数的别名/简称元组，如 ("沪指", "上证综指")
        canonical_id: 规范化的指数 ID，自动由代码前缀生成，如 "sh000001"
    """

    code: str
    name: str
    aliases: tuple[str, ...] = ()
    canonical_id: str = ""

    def __post_init__(self) -> None:
        """构造后自动补齐缺失的 canonical_id。

        根据 code 的前缀自动判断所属交易所：
        - 以 "93" 开头 → CSI（中证指数）
        - 以 "3" 开头 → SZ（深圳）
        - 其他 → SH（上海）
        然后通过 object.__setattr__ 设置 frozen dataclass 的属性。
        """
        if self.canonical_id:
            return
        prefix = "csi" if self.code.startswith("93") else "sz" if self.code.startswith(("3",)) else "sh"
        object.__setattr__(self, "canonical_id", f"{prefix}{self.code}")

    @property
    def exchange(self) -> str:
        """根据代码前缀推断所属交易所（CSI / SZ / SH）。

        判断逻辑：
        - 以 "93" 开头或 canonical_id 以 "csi" 开头 → CSI（中证指数）
        - 以 "3" 开头或 canonical_id 以 "sz" 开头 → SZ（深圳）
        - 其他 → SH（上海）

        Returns:
            交易所标识字符串，大写。
        """
        canonical = self.canonical_id.casefold()
        if canonical.startswith("csi") or self.code.startswith("93"):
            return "CSI"
        if canonical.startswith("sz") or self.code.startswith(("3",)):
            return "SZ"
        return "SH"

    @property
    def bare_code(self) -> str:
        """返回不带任何前缀的纯代码。

        例如 canonical_id 为 "sh000001" 时返回 "000001"。
        与 code 属性一致，提供语义化的属性名。

        Returns:
            纯数字代码字符串。
        """
        return self.code

    @property
    def display_name(self) -> str:
        """返回用于展示的名称。

        通常直接使用官方名称，供前端展示、报告生成等场景使用。

        Returns:
            指数的官方中文名称。
        """
        return self.name


_ENTRIES = (
    AShareIndex("000001", "上证指数", ("沪指", "上证综指")),
    AShareIndex("000009", "上证380", ()),
    AShareIndex("000010", "上证180", ()),
    AShareIndex("000015", "红利指数", ()),
    AShareIndex("399001", "深证成指", ("深成指",)),
    AShareIndex("399005", "中小100", ()),
    AShareIndex("399006", "创业板指", ("创业板指数",)),
    AShareIndex("399303", "国证2000", ()),
    AShareIndex("399324", "深证红利", ()),
    AShareIndex("399330", "深证100", ()),
    AShareIndex("399296", "创成长", ()),
    AShareIndex("399967", "中证军工", ()),
    AShareIndex("399975", "证券公司", ("证券公司指数",)),
    AShareIndex("399986", "中证银行", ()),
    AShareIndex("399989", "中证医疗", ()),
    AShareIndex("399997", "中证白酒", ()),
    AShareIndex("000300", "沪深300", ("沪深三百",)),
    AShareIndex("000905", "中证500", ("中证五百",)),
    AShareIndex("000852", "中证1000", ("中证一千",)),
    AShareIndex("000906", "中证800", ()),
    AShareIndex("000016", "上证50", ("上证五十",)),
    AShareIndex("000688", "科创50", ("科创板50",)),
    AShareIndex("000922", "中证红利", ()),
    AShareIndex("000941", "新能源", ("中证新能源",)),
    AShareIndex("000510", "中证A500", ("中证A500指数",)),
    AShareIndex("980092", "自由现金流", ()),
    AShareIndex("930955", "红利低波100", ("红利低波", "csi930955", "930955.CSI")),
    AShareIndex("932365", "中证现金流", ("csi932365", "932365.CSI")),
    AShareIndex("931052", "国信价值", ("csi931052", "931052.CSI")),
    AShareIndex("931446", "中证红利低波", ("csi931446", "931446.CSI")),
    AShareIndex("931643", "科创创业50", ("csi931643", "931643.CSI")),
    AShareIndex("932366", "300现金流", ("csi932366", "932366.CSI")),
)


def _normalize(value: str) -> str:
    """把指数代码/名称归一化：NFKC 全半角折叠 + 去空白 + 小写。

    处理流程：
    1. 使用 unicodedata.normalize("NFKC", ...) 将全角字符转为半角
    2. 去除所有空白字符（包括空格、制表符、换行等）
    3. 转为小写，确保大小写不敏感匹配

    Args:
        value: 原始输入字符串，可能包含全角字符、空格或大小写混合。

    Returns:
        规范化后的字符串，全半角统一、无空白、全小写。
    """
    return "".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


_BY_CODE = {entry.code: entry for entry in _ENTRIES}
_BY_NAME = {
    _normalize(label): entry
    for entry in _ENTRIES
    for label in (entry.name, *entry.aliases)
}


def get_a_share_index(code_or_name: str) -> Optional[AShareIndex]:
    """解析一个显式的 A 股指数代码或已注册的展示名称。

    支持多种输入格式：
    - 纯数字代码，如 "000001"
    - 带交易所前缀的代码，如 "sh000001"、"sz399001"
    - 指数名称，如 "上证指数"
    - 指数别名，如 "沪指"、"上证综指"
    - 带点号后缀的代码，如 "000001.SH"、"930955.CSI"

    匹配优先级：
    1. 先按纯代码（_BY_CODE）精确匹配
    2. 再按规范化后的名称/别名（_BY_NAME）匹配

    Args:
        code_or_name: 用户输入的指数代码或名称。

    Returns:
        匹配的 AShareIndex 对象；未匹配到时返回 None。
    """
    value = _normalize(code_or_name)
    if value.startswith(("sh", "sz")):
        value = value[2:]
    if value.startswith("csi") and value[3:].isdigit():
        value = value[3:]
    if value.endswith((".sh", ".sz")):
        value = value[:-3]
    elif value.endswith(".csi"):
        value = value[:-4]
    return _BY_CODE.get(value) or _BY_NAME.get(value)


def is_a_share_index_code(code: str) -> bool:
    """判断给定代码/名称是否命中已注册的 A 股指数。

    是对 get_a_share_index 的便捷封装，用于快速判断输入是否有效。

    Args:
        code: 待判断的指数代码或名称。

    Returns:
        若能在注册表中找到对应指数则返回 True，否则返回 False。
    """
    return get_a_share_index(code) is not None


def list_a_share_indices() -> tuple[AShareIndex, ...]:
    """返回全部已注册的 A 股指数。

    返回的元组包含所有预定义的 AShareIndex 对象，
    可用于批量展示、遍历或导出。

    Returns:
        包含所有已注册 AShareIndex 对象的元组。
    """
    return _ENTRIES


__all__ = ["AShareIndex", "get_a_share_index", "is_a_share_index_code", "list_a_share_indices"]
