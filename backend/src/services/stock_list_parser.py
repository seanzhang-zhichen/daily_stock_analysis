"""Parse user supplied analysis targets while preserving A-share indices."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import unicodedata
from typing import Iterable, Optional

from .a_share_index_registry import AShareIndex, get_a_share_index, list_a_share_indices


class ParseStatus:
    STOCK = "stock"
    INDEX = "index"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class AnalysisTarget:
    asset_type: str
    canonical_id: str
    display_code: str
    exchange: Optional[str] = None
    matched_index: Optional[AShareIndex] = None
    status: str = ParseStatus.STOCK
    raw_input: str = ""
    unsupported_reason: Optional[str] = None
    normalized_prefix: Optional[str] = None
    normalized_code: Optional[str] = None


@dataclass(frozen=True)
class IndexEntry:
    """Manifest-compatible index entry accepted by :class:`IndexRegistry`."""

    bare_code: str
    exchange: str
    canonical_id: str
    display_name: str
    aliases: tuple[str, ...] = ()

    @property
    def code(self) -> str:
        return self.bare_code

    @property
    def name(self) -> str:
        return self.display_name


class IndexRegistry:
    """Lookup registry for explicit index identities and aliases."""

    def __init__(self, entries: Iterable[object] = ()) -> None:
        self._entries = tuple(entries)
        self._by_key: dict[str, AShareIndex] = {}
        self._by_bare: dict[str, AShareIndex] = {}
        for entry in self._entries:
            code = str(getattr(entry, "code", getattr(entry, "bare_code", "")))
            canonical = str(getattr(entry, "canonical_id", ""))
            aliases = tuple(getattr(entry, "aliases", ()) or ())
            for key in (code, canonical, *aliases):
                normalized = _normalize_key(key)
                if not normalized:
                    continue
                existing = self._by_key.get(normalized)
                existing_code = str(getattr(existing, "code", getattr(existing, "bare_code", ""))) if existing is not None else ""
                if existing is not None and existing_code != code:
                    raise ValueError(f"index key maps to multiple entries: {key}")
                self._by_key[normalized] = entry
            self._by_bare.setdefault(code, entry)

    def __iter__(self):
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def find_by_explicit_key(self, key: str) -> Optional[AShareIndex]:
        return self._by_key.get(_normalize_key(key))

    def find_by_prefixed_code(self, prefix: str, bare_code: str) -> Optional[AShareIndex]:
        prefix = str(prefix or "").casefold()
        if prefix not in {"sh", "sz"}:
            return None
        direct = self.find_by_explicit_key(f"{prefix}{bare_code}")
        if direct is not None:
            return direct
        entry = self._by_bare.get(str(bare_code))
        return entry if entry is not None and str(getattr(entry, "exchange", "")).upper() == prefix.upper() else None

    def find_by_bare_code(self, bare_code: str) -> Optional[AShareIndex]:
        return self._by_bare.get(str(bare_code or ""))

    def find_by_bare_conflict(self, bare_code: str) -> Optional[AShareIndex]:
        return self.find_by_bare_code(bare_code)


def _normalize_key(value: str) -> str:
    text = "".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()
    if text.endswith((".sh", ".sz", ".ss")):
        base, suffix = text.rsplit(".", 1)
        if base.isdigit():
            text = f"{suffix}{base}"
    elif text.endswith(".csi"):
        base = text[:-4]
        if base.isdigit():
            text = "csi" + base
    return text


def _entry_from_row(row: object) -> Optional[AShareIndex]:
    if not isinstance(row, list) or len(row) < 8 or str(row[7]).casefold() != "index":
        return None
    canonical = str(row[0] or "").strip().casefold()
    name = str(row[2] or row[1] or "").strip()
    if not canonical or not name or not canonical.startswith(("sh", "sz", "csi")):
        return None
    bare = canonical[3:] if canonical.startswith("csi") else canonical[2:]
    aliases = tuple(str(item).strip() for item in (row[5] if isinstance(row[5], list) else ()) if str(item).strip())
    display = str(row[1] or "").strip()
    if display and display.casefold() != canonical and display not in aliases:
        aliases += (display,)
    exchange = "CSI" if canonical.startswith("csi") else canonical[:2].upper()
    return IndexEntry(bare, exchange, canonical, name, aliases)


def default_index_registry() -> IndexRegistry:
    """Load index rows from the generated resource, with stable fallback."""
    entries: list[AShareIndex] = []
    try:
        path = Path(__file__).resolve().parents[1] / "data" / "resources" / "stocks.index.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = [item for item in (_entry_from_row(row) for row in payload) if item is not None]
    except (OSError, TypeError, ValueError):
        entries = []
    return IndexRegistry(entries or list(list_a_share_indices()))


_EXPLICIT_RE = re.compile(
    r"^(?P<prefix>sh|sz|csi)(?P<code>\d{6})$"
    r"|^(?P<suffix>\d{1,6})\.(?P<exchange>sh|sz|ss|bj|hk|csi)$",
    re.I,
)
_MARKET_PREFIX_RE = re.compile(r"^(?P<prefix>bj|hk|us)(?P<code>[A-Za-z0-9.]+)$", re.I)


def _clean(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", str(value or "")).split())


def _classify_bare(text: str) -> tuple[str, str]:
    if text.isdigit():
        if len(text) == 6:
            # Beijing Stock Exchange and related legacy instruments must be
            # checked before the generic Shanghai 9xxxxx branch.
            if text.startswith(("92", "43", "83", "87", "88", "81", "82", "889")):
                return "BJ", ParseStatus.STOCK
            if text.startswith(("6", "900", "5", "9")):
                return "SH", ParseStatus.STOCK
            if text.startswith(("0", "2", "3", "1", "8")):
                return "SZ", ParseStatus.STOCK
            if text.startswith("4"):
                return "BJ", ParseStatus.STOCK
        if len(text) in (4, 5):
            return "HK", ParseStatus.STOCK
        return "UNKNOWN", ParseStatus.UNSUPPORTED
    if re.fullmatch(r"[A-Za-z]{1,5}(?:\.[A-Za-z]{1,3})?", text):
        return "US", ParseStatus.STOCK
    return "UNKNOWN", ParseStatus.UNSUPPORTED


def _make_index_target(raw: str, entry: object, exchange: str, prefix: str) -> AnalysisTarget:
    code = str(getattr(entry, "code", getattr(entry, "bare_code", "")))
    canonical = getattr(entry, "canonical_id", "") or (f"csi{code}" if exchange == "CSI" else f"{prefix}{code}")
    name = str(getattr(entry, "name", getattr(entry, "display_name", canonical)))
    return AnalysisTarget(ParseStatus.INDEX, canonical, name, exchange, entry, ParseStatus.INDEX, raw_input=raw, normalized_prefix=prefix, normalized_code=code)


def parse_analysis_target(value: str, registry: Optional[IndexRegistry] = None) -> AnalysisTarget:
    raw = _clean(value)
    if not raw:
        return AnalysisTarget(ParseStatus.UNSUPPORTED, "", "", "UNKNOWN", None, ParseStatus.UNSUPPORTED, raw_input=raw, unsupported_reason="empty input")
    if registry is None:
        registry = default_index_registry()

    explicit = _EXPLICIT_RE.fullmatch(raw)
    if explicit:
        prefix = (explicit.group("prefix") or explicit.group("exchange") or "").casefold()
        bare = explicit.group("code") or explicit.group("suffix") or ""
        exchange = "CSI" if prefix == "csi" else "SS" if prefix == "ss" else prefix.upper()
        entry = registry.find_by_explicit_key(raw) or registry.find_by_prefixed_code(prefix, bare)
        if entry is not None:
            return _make_index_target(raw, entry, getattr(entry, "exchange", None) or exchange, prefix)
        if prefix == "csi":
            return AnalysisTarget(ParseStatus.UNSUPPORTED, raw.casefold(), raw, "CSI", None, ParseStatus.UNSUPPORTED, raw_input=raw, unsupported_reason="unregistered CSI index")
        return AnalysisTarget(ParseStatus.STOCK, f"{prefix}{bare}", raw, exchange, None, ParseStatus.STOCK, raw_input=raw, normalized_prefix=prefix, normalized_code=bare)

    market_prefixed = _MARKET_PREFIX_RE.fullmatch(raw)
    if market_prefixed:
        prefix = market_prefixed.group("prefix").casefold()
        bare = market_prefixed.group("code")
        if prefix in {"bj", "hk"} and not bare.isdigit():
            return AnalysisTarget(ParseStatus.UNSUPPORTED, raw.casefold(), raw, prefix.upper(), None, ParseStatus.UNSUPPORTED, raw_input=raw, unsupported_reason="invalid exchange code")
        if prefix == "hk":
            if not 1 <= len(bare) <= 5:
                return AnalysisTarget(ParseStatus.UNSUPPORTED, raw.casefold(), raw, "HK", None, ParseStatus.UNSUPPORTED, raw_input=raw, unsupported_reason="invalid HK code")
            bare = bare.zfill(5)
        elif prefix == "bj" and len(bare) != 6:
            return AnalysisTarget(ParseStatus.UNSUPPORTED, raw.casefold(), raw, "BJ", None, ParseStatus.UNSUPPORTED, raw_input=raw, unsupported_reason="invalid BJ code")
        elif prefix == "us":
            if not re.fullmatch(r"[A-Za-z]{1,5}(?:\.[A-Za-z]{1,3})?", bare):
                return AnalysisTarget(ParseStatus.UNSUPPORTED, raw.casefold(), raw, "US", None, ParseStatus.UNSUPPORTED, raw_input=raw, unsupported_reason="invalid US ticker")
            bare = bare.upper()
        return AnalysisTarget(ParseStatus.STOCK, f"{prefix}{bare}" if prefix != "us" else bare, raw, prefix.upper(), None, ParseStatus.STOCK, raw_input=raw, normalized_prefix=prefix, normalized_code=bare)

    entry = registry.find_by_explicit_key(raw) or get_a_share_index(raw)
    if entry is not None and not raw.isdigit():
        code = str(getattr(entry, "code", getattr(entry, "bare_code", "")))
        exchange = getattr(entry, "exchange", None) or ("CSI" if code.startswith("93") else "SH" if code.startswith(("0", "5", "6", "9")) else "SZ")
        return _make_index_target(raw, entry, exchange, "csi" if exchange == "CSI" else exchange.casefold())

    exchange, asset_type = _classify_bare(raw)
    if asset_type == ParseStatus.UNSUPPORTED:
        return AnalysisTarget(ParseStatus.UNSUPPORTED, raw.casefold(), raw, exchange, None, ParseStatus.UNSUPPORTED, raw_input=raw, unsupported_reason="unrecognized code shape")
    matched = registry.find_by_bare_conflict(raw) if raw.isdigit() else None
    canonical = raw if raw.isdigit() else raw.upper()
    if exchange == "HK" and raw.isdigit():
        canonical = f"hk{raw.zfill(5)}"
    return AnalysisTarget(ParseStatus.STOCK, canonical, raw, exchange, matched, ParseStatus.STOCK, raw_input=raw, normalized_code=raw)


def split_stock_list(value: str) -> list[str]:
    return [item for item in re.split(r"[\s,;，、；]+", value or "") if item]


def serialize_stock_list(value: str) -> str:
    return ",".join(split_stock_list(value))


def parse_stock_list(value: str, registry: Optional[IndexRegistry] = None) -> list[AnalysisTarget]:
    return [parse_analysis_target(item, registry=registry) for item in split_stock_list(value)]


__all__ = ["AnalysisTarget", "IndexEntry", "IndexRegistry", "ParseStatus", "default_index_registry", "parse_analysis_target", "parse_stock_list", "serialize_stock_list", "split_stock_list"]
