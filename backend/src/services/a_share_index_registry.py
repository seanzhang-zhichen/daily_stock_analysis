"""Explicit registry for commonly analysed A-share indices."""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata
from typing import Optional


@dataclass(frozen=True)
class AShareIndex:
    code: str
    name: str
    aliases: tuple[str, ...] = ()
    canonical_id: str = ""

    def __post_init__(self) -> None:
        if self.canonical_id:
            return
        prefix = "csi" if self.code.startswith("93") else "sz" if self.code.startswith(("3",)) else "sh"
        object.__setattr__(self, "canonical_id", f"{prefix}{self.code}")

    @property
    def exchange(self) -> str:
        canonical = self.canonical_id.casefold()
        if canonical.startswith("csi") or self.code.startswith("93"):
            return "CSI"
        if canonical.startswith("sz") or self.code.startswith(("3",)):
            return "SZ"
        return "SH"

    @property
    def bare_code(self) -> str:
        return self.code

    @property
    def display_name(self) -> str:
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
    return "".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


_BY_CODE = {entry.code: entry for entry in _ENTRIES}
_BY_NAME = {
    _normalize(label): entry
    for entry in _ENTRIES
    for label in (entry.name, *entry.aliases)
}


def get_a_share_index(code_or_name: str) -> Optional[AShareIndex]:
    """Resolve an explicit A-share index code or registered display name."""
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
    return get_a_share_index(code) is not None


def list_a_share_indices() -> tuple[AShareIndex, ...]:
    return _ENTRIES


__all__ = ["AShareIndex", "get_a_share_index", "is_a_share_index_code", "list_a_share_indices"]
