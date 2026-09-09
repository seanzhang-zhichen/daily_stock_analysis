# -*- coding: utf-8 -*-
# Derived from AlphaSift revision 9f522747caafd3c0b1ddb7e14d5cf44c8580b6cf.
# Licensed under Apache-2.0 and modified for daily_stock_analysis.
"""LLM 软排序使用的上下文装配与降级。

负责把人工上下文、上下文文件、事件画像、候选身份、候选外部线索、候选抓取
线索、市场快照、候选池快照、候选因子概貌等异构材料拼成对 LLM 友好的有限
长度文本, 并在超出 ``max_chars`` 时按优先级/权重降级裁剪。

下游: ``src.services.screening.*`` 中的 LLM 调用方(尤其是 `screening/scorer.py`
里的 scorer 在使用 LLM 加权评分时), 以及 ``src.agents`` 内基于 LLM 软排序
的入口。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import pandas as pd

from src.services.screening.normalize import normalize_code as _normalize_code

_CONTEXT_TRIM_MARKER = "[context_trimmed]"
_CANDIDATE_CONTEXT_COLUMNS = {
    "news": "新闻",
    "announcement": "公告",
    "announcements": "公告",
    "fund_flow": "资金流",
    "fundflow": "资金流",
    "quote": "行情估值",
    "summary": "摘要",
    "context": "上下文",
    "context_summary": "压缩摘要",
    "text": "文本",
    "risk": "风险",
    "catalyst": "催化",
    "source_count": "来源数",
    "source_confidence": "来源置信度",
    "source_weight_score": "来源权重分",
    "event_tags": "事件标签",
    "announcement_categories": "公告类别",
    "negative_event_flags": "负面风险",
}


@dataclass(frozen=True)
class _ContextSection:
    """LLM 上下文的一个候选分段：文本内容 + 排序/裁剪元数据。"""

    text: str
    kind: str
    priority: int
    min_chars: int
    weight: int
    line_aware: bool = False


def build_llm_context(
    *,
    base_context: str = "",
    context_files: list[str | Path] | None = None,
    candidate_context_files: list[str | Path] | None = None,
    candidate_context_rows: list[dict[str, object]] | None = None,
    snapshot_df: pd.DataFrame | None = None,
    candidate_df: pd.DataFrame | None = None,
    event_profile: dict[str, object] | None = None,
    max_chars: int = 4000,
    degradation: list[str] | None = None,
) -> str:
    """组装 LLM 软排序所需的有界上下文文本。"""
    sections: list[_ContextSection] = []
    if base_context.strip():
        sections.append(_section(
            "【人工上下文】\n" + base_context.strip(),
            kind="market_context",
            priority=5,
            min_chars=80,
            weight=1,
            line_aware=True,
        ))

    file_context = _read_context_files(context_files or [])
    if file_context:
        sections.append(_section(
            "【上下文文件】\n" + file_context,
            kind="market_files",
            priority=5,
            min_chars=80,
            weight=1,
            line_aware=True,
        ))

    event_profile_context = summarize_event_profile(event_profile)
    if event_profile_context:
        sections.append(_section(
            event_profile_context,
            kind="event_profile",
            priority=2,
            min_chars=160,
            weight=2,
            line_aware=True,
        ))

    candidate_identity = summarize_candidate_identity(candidate_df)
    if candidate_identity:
        sections.append(_section(
            candidate_identity,
            kind="candidate_identity",
            priority=0,
            min_chars=360,
            weight=4,
            line_aware=True,
        ))

    candidate_external_context = _read_candidate_context_files(
        candidate_context_files or [],
        candidate_df,
    )
    if candidate_external_context:
        sections.append(_section(
            "【候选外部线索】\n" + candidate_external_context,
            kind="candidate_external_context",
            priority=1,
            min_chars=320,
            weight=4,
            line_aware=True,
        ))

    collected_candidate_context = _format_candidate_context_rows(
        candidate_context_rows or [],
        candidate_df,
    )
    if collected_candidate_context:
        sections.append(_section(
            "【候选抓取线索】\n" + collected_candidate_context,
            kind="candidate_collected_context",
            priority=1,
            min_chars=360,
            weight=4,
            line_aware=True,
        ))

    snapshot_context = summarize_snapshot_context(snapshot_df, title="全市场快照")
    if snapshot_context:
        sections.append(_section(
            snapshot_context,
            kind="market_snapshot",
            priority=4,
            min_chars=160,
            weight=2,
            line_aware=True,
        ))

    candidate_context = summarize_snapshot_context(candidate_df, title="候选池快照")
    if candidate_context:
        sections.append(_section(
            candidate_context,
            kind="candidate_snapshot",
            priority=2,
            min_chars=180,
            weight=2,
            line_aware=True,
        ))

    candidate_profile = summarize_candidate_profile(candidate_df)
    if candidate_profile:
        sections.append(_section(
            candidate_profile,
            kind="candidate_profile",
            priority=2,
            min_chars=240,
            weight=3,
            line_aware=True,
        ))

    return _join_bounded_context_sections(sections, max_chars=max_chars, degradation=degradation)


def summarize_snapshot_context(df: pd.DataFrame | None, *, title: str) -> str:
    """根据快照 DataFrame 总结市场宽度、活跃度与涨跌极值。"""
    if df is None or df.empty:
        return ""

    lines = [f"【{title}】", f"样本数: {len(df)}"]
    if "change_pct" in df.columns:
        change = pd.to_numeric(df["change_pct"], errors="coerce").dropna()
        if not change.empty:
            positive_ratio = (change > 0).mean() * 100
            lines.append(
                "涨跌分布: "
                f"上涨占比 {positive_ratio:.1f}%, "
                f"中位涨跌幅 {change.median():.2f}%, "
                f"平均涨跌幅 {change.mean():.2f}%"
            )
            lines.append("涨跌极值: " + _format_extremes(df, change, ascending=False))
    if "amount" in df.columns:
        amount = pd.to_numeric(df["amount"], errors="coerce").dropna()
        if not amount.empty:
            lines.append(f"成交额中位数: {amount.median():.0f}")
    if "volume_ratio" in df.columns:
        volume_ratio = pd.to_numeric(df["volume_ratio"], errors="coerce").dropna()
        if not volume_ratio.empty:
            hot_ratio = (volume_ratio >= 2).mean() * 100
            lines.append(f"量比>=2占比: {hot_ratio:.1f}%")
    return "\n".join(lines)


def summarize_candidate_profile(df: pd.DataFrame | None) -> str:
    """汇总候选池的因子分布、行业/概念结构与板块热度。"""
    if df is None or df.empty:
        return ""

    lines = ["【候选池结构】"]
    factor_cols = {
        "价值": "factor_value_score",
        "流动性": "factor_liquidity_score",
        "动量": "factor_momentum_score",
        "反转": "factor_reversal_score",
        "活跃度": "factor_activity_score",
        "稳定性": "factor_stability_score",
        "市值容量": "factor_size_score",
        "主题热度": "factor_theme_heat_score",
    }
    available = {label: col for label, col in factor_cols.items() if col in df.columns}
    if available:
        averages = []
        for label, col in available.items():
            series = pd.to_numeric(df[col], errors="coerce").dropna()
            if not series.empty:
                averages.append(f"{label}{series.mean():.1f}")
        if averages:
            lines.append("因子均值: " + "，".join(averages))

        # 每条因子记录 Top1(代码+名称+分数), 帮助 LLM 理解"谁的哪个轴领先"
        leaders = []
        columns = [col for col in ("code", "name") if col in df.columns]
        for label, col in available.items():
            series = pd.to_numeric(df[col], errors="coerce")
            if series.dropna().empty or not columns:
                continue
            idx = series.idxmax()
            row = df.loc[idx]
            leaders.append(f"{label}:{row.get('code', '')}{row.get('name', '')}({series.loc[idx]:.1f})")
        if leaders:
            # 只取前 8 条, 避免上下文爆炸
            lines.append("因子领先: " + "，".join(leaders[:8]))

    if "screen_score" in df.columns:
        screen_score = pd.to_numeric(df["screen_score"], errors="coerce").dropna()
        if not screen_score.empty:
            lines.append(
                f"主评分分布: 最高{screen_score.max():.1f}，"
                f"中位{screen_score.median():.1f}，最低{screen_score.min():.1f}"
            )

    industry_summary = _summarize_label_distribution(df, "industry")
    if industry_summary:
        lines.append("行业分布: " + industry_summary)
    concept_summary = _summarize_label_distribution(df, "concepts")
    if concept_summary:
        lines.append("概念线索: " + concept_summary)
    heat_summary = _summarize_board_heat(df)
    if heat_summary:
        lines.append("板块/主题热度: " + heat_summary)

    return "\n".join(lines) if len(lines) > 1 else ""


def summarize_candidate_identity(df: pd.DataFrame | None, *, limit: int = 30) -> str:
    """把前 N 只候选身份与关键排名字段整理成表格化的文本。"""
    if df is None or df.empty or "code" not in df.columns:
        return ""

    lines = ["【候选身份】"]
    for _, row in df.head(max(int(limit), 1)).iterrows():
        code = _normalize_code(row.get("code", row.get("代码", "")))
        if not code:
            continue
        name = _safe_context_value(
            row.get("name") or row.get("名称") or row.get("股票名称"),
            max_len=40,
        )
        fields = []
        score = _safe_context_value(row.get("screen_score"), max_len=24)
        if score:
            fields.append(f"screen_score={score}")
        industry = _safe_context_value(row.get("industry") or row.get("行业"), max_len=40)
        if industry:
            fields.append(f"industry={industry}")
        concepts = _safe_context_value(row.get("concepts") or row.get("概念"), max_len=80)
        if concepts:
            fields.append(f"concepts={concepts}")
        heat = _safe_context_value(row.get("board_heat_score"), max_len=24)
        if heat:
            fields.append(f"board_heat_score={heat}")
        suffix = ": " + ", ".join(fields) if fields else ""
        lines.append(f"- {code} {name}{suffix}")
    # 超额部分显式提示, 让 LLM 知道还有更多候选
    if len(df) > limit:
        lines.append(f"...[candidate_identity_omitted:{len(df) - limit}]")
    return "\n".join(lines) if len(lines) > 1 else ""


def summarize_event_profile(event_profile: dict[str, object] | None) -> str:
    """把策略级事件偏好整理成 LLM 易读的摘要。"""
    if not event_profile:
        return ""
    lines = ["【策略事件偏好】"]
    field_labels = {
        "preferred_event_tags": "偏好事件标签",
        "avoided_event_tags": "规避事件标签",
        "preferred_announcement_categories": "偏好公告类别",
        "avoided_announcement_categories": "规避公告类别",
        "notes": "事件备注",
    }
    for field, label in field_labels.items():
        value = _format_profile_value(event_profile.get(field))
        if value:
            lines.append(f"{label}: {value}")
    source_weights = event_profile.get("source_weights")
    if isinstance(source_weights, dict) and source_weights:
        items = []
        for source, weight in source_weights.items():
            text = _safe_context_value(source, max_len=40)
            if not text:
                continue
            try:
                items.append(f"{text}={float(weight):.2f}")
            except (TypeError, ValueError):
                continue
        if items:
            lines.append("来源权重: " + "，".join(items))
    return "\n".join(lines) if len(lines) > 1 else ""


def _read_context_files(paths: list[str | Path]) -> str:
    """读取并拼接用户传入的若干上下文文件, 找不到时直接抛错。"""
    chunks: list[str] = []
    for path_like in paths:
        path = Path(path_like)
        if not path.is_file():
            raise FileNotFoundError(f"Context file not found: {path}")
        text = path.read_text(encoding="utf-8").strip()
        if text:
            chunks.append(f"# {path.name}\n{text}")
    return "\n\n".join(chunks)


def _read_candidate_context_files(
    paths: list[str | Path],
    candidate_df: pd.DataFrame | None,
) -> str:
    """从若干文件中加载候选外部线索, 仅保留命中候选池的股票。"""
    if not paths or candidate_df is None or candidate_df.empty or "code" not in candidate_df.columns:
        return ""

    candidate_names, candidate_order = _candidate_maps(candidate_df)
    candidate_codes = set(candidate_names)
    chunks: list[tuple[int, int, str]] = []
    row_position = 0
    for path_like in paths:
        path = Path(path_like)
        if not path.is_file():
            raise FileNotFoundError(f"Candidate context file not found: {path}")
        rows = _load_candidate_context_rows(path)
        for row in rows:
            code = _normalize_code(row.get("code", row.get("代码", "")))
            item = _format_candidate_context_row(row, candidate_codes, candidate_names)
            if item:
                # 排序键: 候选序号优先, 原文件行号次之, 保持候选身份稳定
                chunks.append((candidate_order.get(code, len(candidate_order)), row_position, item))
            row_position += 1
    return "\n".join(item for _, _, item in sorted(chunks))


def _format_candidate_context_rows(
    rows: list[dict[str, object]],
    candidate_df: pd.DataFrame | None,
) -> str:
    """把内存里收集到的候选外部线索行格式化为可拼接文本。"""
    if not rows or candidate_df is None or candidate_df.empty or "code" not in candidate_df.columns:
        return ""
    candidate_names, candidate_order = _candidate_maps(candidate_df)
    candidate_codes = set(candidate_names)
    chunks = []
    for idx, row in enumerate(rows):
        code = _normalize_code(row.get("code", row.get("代码", "")))
        item = _format_candidate_context_row(row, candidate_codes, candidate_names)
        if item:
            chunks.append((candidate_order.get(code, len(candidate_order)), idx, item))
    return "\n".join(item for _, _, item in sorted(chunks))


def _candidate_maps(candidate_df: pd.DataFrame) -> tuple[dict[str, str], dict[str, int]]:
    """从候选池构建 ``{code: name}`` 与 ``{code: 出现次序}`` 两张索引。"""
    candidate_names: dict[str, str] = {}
    candidate_order: dict[str, int] = {}
    for idx, (_, row) in enumerate(candidate_df.iterrows()):
        code = _normalize_code(row.get("code", row.get("代码", "")))
        if not code:
            continue
        candidate_names[code] = str(row.get("name", row.get("名称", "")) or "")
        # 多只相同代码取首次出现的位置, 用于输出顺序的稳定性
        candidate_order.setdefault(code, idx)
    return candidate_names, candidate_order


def _format_candidate_context_row(
    row: dict[str, object],
    candidate_codes: set[str],
    candidate_names: dict[str, str],
) -> str:
    """把单行候选上下文裁剪到候选池范围, 并按字段标签拼接。"""
    code = _normalize_code(row.get("code", row.get("代码", "")))
    if code not in candidate_codes:
        return ""
    fields = []
    for column, label in _CANDIDATE_CONTEXT_COLUMNS.items():
        value = _safe_context_value(row.get(column))
        if value:
            fields.append(f"{label}:{value}")
    if not fields:
        return ""
    name = _safe_context_value(row.get("name") or row.get("名称")) or candidate_names.get(code, "")
    return f"- {code} {name}: " + "；".join(fields)


def _load_candidate_context_rows(path: Path) -> list[dict[str, object]]:
    """从 csv / jsonl / json 三种格式加载候选上下文记录。"""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, dtype=str).fillna("").to_dict(orient="records")
    if suffix == ".jsonl":
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                item = json.loads(line)
                if isinstance(item, dict):
                    rows.append(item)
        return rows
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            items = data.get("items") or data.get("data")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
            rows = []
            for code, value in data.items():
                # 兼容 ``{"000001": {"news": "..."}}`` 这种"按代码为键"的紧凑格式
                if isinstance(value, dict):
                    rows.append({"code": code, **value})
                elif isinstance(value, str):
                    rows.append({"code": code, "text": value})
            return rows
    raise ValueError(f"Unsupported candidate context file format: {path}")


def _safe_context_value(value: object, *, max_len: int = 280) -> str:
    """把任意输入归一化为上下文可拼接的字符串, 自动屏蔽 NaN/None 噪声。"""
    if value is None:
        return ""
    if isinstance(value, list):
        text = ",".join(str(item).strip() for item in value if str(item).strip())
    else:
        text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return ""
    return text[:max_len]


def _format_profile_value(value: object) -> str:
    """把 profile 字段值(可能是 list)格式化为一段中文逗号串。"""
    if isinstance(value, list):
        return "，".join(
            item
            for item in (_safe_context_value(raw, max_len=80) for raw in value)
            if item
        )
    return _safe_context_value(value, max_len=280)


def _section(
    text: str,
    *,
    kind: str,
    priority: int,
    min_chars: int,
    weight: int,
    line_aware: bool = False,
) -> _ContextSection:
    """构造一个上下文分片, 自动 strip 文本与限制 min_chars/weight 范围。"""
    return _ContextSection(
        text=text.strip(),
        kind=kind,
        priority=priority,
        min_chars=max(int(min_chars), 0),
        # 权重至少为 1, 避免全部为 0 时分配退化为无意义
        weight=max(int(weight), 1),
        line_aware=line_aware,
    )


def _join_bounded_context_sections(
    sections: list[_ContextSection],
    *,
    max_chars: int,
    degradation: list[str] | None,
) -> str:
    """在 ``max_chars`` 预算内拼接各分片, 超限时按优先级/权重降级裁剪。"""
    sections = [section for section in sections if section.text.strip()]
    combined = "\n\n".join(section.text for section in sections).strip()
    if not combined:
        return ""
    if len(combined) <= max_chars:
        return combined

    marker_line = f"【上下文降级】{_CONTEXT_TRIM_MARKER} low-priority context trimmed."
    budget = max(int(max_chars) - len(marker_line) - 2, 0)
    if budget <= 0:
        return marker_line[:max_chars]

    allocations = _allocate_section_budgets(sections, budget)
    chunks: list[str] = []
    trimmed_kinds: list[str] = []
    for section, limit in zip(sections, allocations, strict=False):
        trimmed = _trim_section(section, limit)
        if trimmed:
            chunks.append(trimmed)
        # 记录触发降级的分片类型, 用于日志/前端提示
        if len(trimmed) < len(section.text):
            trimmed_kinds.append(section.kind)

    result = "\n\n".join(chunks).strip()
    if trimmed_kinds:
        trimmed_labels = ",".join(dict.fromkeys(trimmed_kinds))
        marker_line = (
            f"【上下文降级】{_CONTEXT_TRIM_MARKER} "
            f"trimmed={trimmed_labels}"
        )
        result = _append_marker_within_limit(result, marker_line, max_chars=max_chars)
        if degradation is not None:
            degradation.append(f"LLM context truncated: trimmed={trimmed_labels}")
    return result[:max_chars]


def _allocate_section_budgets(sections: list[_ContextSection], budget: int) -> list[int]:
    """按优先级下限 + 权重分配各分片的字符预算。"""
    # 段间用一个 "\n\n" 连接, 预留分隔预算
    separator_budget = max(len(sections) - 1, 0) * 2
    body_budget = max(budget - separator_budget, 0)
    minimums = [min(len(section.text), section.min_chars) for section in sections]
    minimum_total = sum(minimums)
    # 总下限已经超出预算, 进入"按优先级逐段压下限"的保底模式
    if minimum_total > body_budget:
        return _priority_floor_allocations(sections, body_budget)

    allocations = list(minimums)
    remaining = body_budget - minimum_total
    while remaining > 0:
        # 只对"还没分配到原文长度"的分片做加权扩张
        expandable = [
            idx
            for idx, section in enumerate(sections)
            if allocations[idx] < len(section.text)
        ]
        if not expandable:
            break
        total_weight = sum(sections[idx].weight for idx in expandable)
        progressed = False
        for idx in expandable:
            section = sections[idx]
            extra = len(section.text) - allocations[idx]
            # 按权重比例分配剩余预算, 至少保证 1 字符, 避免死循环
            share = max(1, int(remaining * section.weight / max(total_weight, 1)))
            take = min(extra, share, remaining)
            if take <= 0:
                continue
            allocations[idx] += take
            remaining -= take
            progressed = True
            if remaining <= 0:
                break
        if not progressed:
            break
    return allocations


def _priority_floor_allocations(sections: list[_ContextSection], budget: int) -> list[int]:
    """当总下限超出预算时, 按优先级从高到低尽量满足各段下限。"""
    allocations = [0] * len(sections)
    remaining = max(int(budget), 0)
    # 优先级数字越小越重要(同一优先级按列表顺序)
    for idx in sorted(range(len(sections)), key=lambda item: sections[item].priority):
        if remaining <= 0:
            break
        section = sections[idx]
        # 下限至少保留 80 字符, 防止"几乎不可读"的输出
        floor = min(len(section.text), max(section.min_chars, 80))
        take = min(floor, remaining)
        allocations[idx] = take
        remaining -= take
    return allocations


def _trim_section(section: _ContextSection, limit: int) -> str:
    """按预算裁剪单段; line_aware 时按行裁, 否则直接按字符裁。"""
    if limit <= 0:
        return ""
    text = section.text
    if len(text) <= limit:
        return text

    marker = f"\n...{_CONTEXT_TRIM_MARKER}:{section.kind}"
    if limit <= len(marker) + 8:
        return text[:limit].rstrip()
    content_limit = limit - len(marker)
    if not section.line_aware:
        return text[:content_limit].rstrip() + marker

    # 行级裁剪: 一旦加上下一行将超出预算, 就停在当前行, 并附上降级标记
    kept: list[str] = []
    for line in text.splitlines():
        candidate = "\n".join([*kept, line]).rstrip() + marker
        if len(candidate) > limit:
            prefix = "\n".join(kept).rstrip()
            separator = "\n" if prefix else ""
            remaining = limit - len(prefix) - len(separator) - len(marker)
            if remaining > 8:
                kept.append(line[:remaining].rstrip())
            break
        kept.append(line)
    if not kept:
        return text[:content_limit].rstrip() + marker
    return "\n".join(kept).rstrip() + marker


def _append_marker_within_limit(text: str, marker: str, *, max_chars: int) -> str:
    """在不超 ``max_chars`` 的前提下把降级标记追加到文本末尾。"""
    if not text:
        return marker[:max_chars]
    candidate = f"{text}\n{marker}"
    if len(candidate) <= max_chars:
        return candidate
    keep = max(max_chars - len(marker) - 1, 0)
    if keep <= 0:
        return marker[:max_chars]
    return text[:keep].rstrip() + "\n" + marker


def _format_extremes(df: pd.DataFrame, change: pd.Series, *, ascending: bool) -> str:
    """挑出涨跌最高/最低的若干行, 拼接为 ``代码名称(幅度%)`` 形式的字符串。"""
    columns = [col for col in ("code", "name", "change_pct") if col in df.columns]
    if not columns:
        return ""
    top = df.loc[change.sort_values(ascending=ascending).head(3).index, columns]
    items = []
    for _, row in top.iterrows():
        code = str(row.get("code", ""))
        name = str(row.get("name", ""))
        pct = row.get("change_pct", 0)
        items.append(f"{code}{name}({float(pct):.2f}%)")
    return ", ".join(items)


def _summarize_label_distribution(df: pd.DataFrame, column: str) -> str:
    """统计指定列(industry/concepts)中各标签出现次数, 取 Top6 拼成中文串。"""
    if column not in df.columns:
        return ""
    labels: list[str] = []
    for raw in df[column].dropna().astype(str):
        for item in raw.replace("，", ",").replace("、", ",").split(","):
            label = item.strip()
            if label and label.lower() not in {"nan", "none", "<na>"}:
                labels.append(label)
    if not labels:
        return ""
    counts = pd.Series(labels).value_counts().head(6)
    return "，".join(f"{label}{count}" for label, count in counts.items())


def _summarize_board_heat(df: pd.DataFrame, *, limit: int = 5) -> str:
    """把板块热度字段汇总为前 N 行结构化文本, 包含 trend/persist/cooling 等子分。"""
    if "board_heat_score" not in df.columns:
        return ""
    values = pd.to_numeric(df["board_heat_score"], errors="coerce")
    if values.dropna().empty:
        return ""
    items = []
    for idx in values.sort_values(ascending=False).dropna().head(limit).index:
        row = df.loc[idx]
        code = str(row.get("code", "") or "")
        name = str(row.get("name", "") or "")
        label = str(row.get("board_heat_summary", "") or row.get("industry", "") or "")
        trend = _safe_context_value(row.get("board_heat_trend_score"), max_len=20)
        persistence = _safe_context_value(row.get("board_heat_persistence_score"), max_len=20)
        cooling = _safe_context_value(row.get("board_heat_cooling_score"), max_len=20)
        state = _safe_context_value(row.get("board_heat_state"), max_len=20)
        trend_text = f",trend={trend}" if trend else ""
        persistence_text = f",persist={persistence}" if persistence else ""
        cooling_text = f",cooling={cooling}" if cooling else ""
        state_text = f",state={state}" if state else ""
        items.append(
            f"{code}{name}:{float(values.loc[idx]):.1f}"
            f"{trend_text}{persistence_text}{cooling_text}{state_text}({label[:60]})"
        )
    return "，".join(items)


