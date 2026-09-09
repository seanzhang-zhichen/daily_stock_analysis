# -*- coding: utf-8 -*-
# Derived from AlphaSift revision 9f522747caafd3c0b1ddb7e14d5cf44c8580b6cf.
# Licensed under Apache-2.0 and modified for daily_stock_analysis.
"""选股（screening）模块的配置加载与归一化。

负责从 .env / 环境变量加载运行配置、解析数据源优先级与 LLM 渠道，
并提供 Hermes 历史保留名识别与若干 profile 配置项。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from src.config import (
    is_supported_llm_channel_api_surface_value,
    find_incompatible_llm_channel_models,
    find_llm_channel_surface_conflicts,
    normalize_llm_channel_api_surface,
    normalize_llm_channel_model,
    resolve_llm_channel_protocol,
)
try:
    # Hermes 是历史专属前缀, 单独识别以避免与普通渠道名冲突
    from src.llm.hermes import is_reserved_hermes_name
except ImportError:  # Older DSA branches do not ship the Hermes channel helper.
    def is_reserved_hermes_name(value: str) -> bool:
        """回退实现：直接以小写前缀判断渠道名是否 Hermes 保留名。"""
        # 回退实现: 直接以小写前缀判断, 与历史行为兼容
        return str(value or "").strip().lower().startswith("hermes")

# 向上溯源到 backend 项目根: 本文件在 .../backend/src/services/screening/config.py
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PACKAGE_DIR = Path(__file__).resolve().parent
# L3 / post-analysis 默认采用 scorecard 本地打分器; DSA 是其中一个可选后端
DEFAULT_POST_ANALYZERS = ["scorecard"]
DEFAULT_LLM_MODEL = "gemini/gemini-2.5-flash"
# 无 tushare 时, 默认按这个顺序拉快照数据源
DEFAULT_SNAPSHOT_SOURCE_PRIORITY = ["sina", "efinance", "akshare_em", "em_datacenter"]
# 配置了 tushare token 时, 把 tushare 提到最前面
TUSHARE_FIRST_SOURCE_PRIORITY = ["tushare", "sina", "efinance", "akshare_em", "em_datacenter"]
# .env 缓存: key=Path, value=(mtime_ns+size, {key: parsed_value})
_ENV_FILE_CACHE: dict[Path, tuple[tuple[int, int], dict[str, str]]] = {}
# 记录被本模块应用过的 env 值, 便于在测试/卸载时回退
_APPLIED_ENV_FILE_VALUES: dict[str, str] = {}


def _load_env_file() -> None:
    """从候选位置加载 .env, 合并去重后写入 ``os.environ``。"""
    # 优先级: SCREENING_ENV_FILE(S) > cwd > 项目根
    candidates = [
        *_env_file_candidates_from_env(),
        Path.cwd() / ".env",
        _PROJECT_ROOT / ".env",
    ]
    seen: set[Path] = set()
    file_values: dict[str, str] = {}
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        for key, value in _read_env_file_values(path).items():
            # setdefault 保证先到先得, 后续文件不再覆盖
            file_values.setdefault(key, value)
    _apply_env_file_values(file_values)


def _read_env_file_values(path: Path) -> dict[str, str]:
    """解析单个 .env 文件, mtime+size 命中缓存时直接返回历史结果。"""
    resolved = path.resolve()
    stat = path.stat()
    signature = (stat.st_mtime_ns, stat.st_size)
    cached = _ENV_FILE_CACHE.get(resolved)
    if cached is not None and cached[0] == signature:
        return dict(cached[1])

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        # 跳过空行 / 注释 / 语法不全的 ``=`` 行
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        cleaned = value.strip().strip("'\"")
        if cleaned == "":
            continue
        values.setdefault(key.strip(), cleaned)
    _ENV_FILE_CACHE[resolved] = (signature, dict(values))
    return values


def _apply_env_file_values(file_values: dict[str, str]) -> None:
    """把 .env 解析结果复制进 ``os.environ``, 处理"撤回"已被写入的环境变量。"""
    # 先清理: 把曾经由我们注入的 key 在被新值覆盖或空值时撤回
    for key, old_value in list(_APPLIED_ENV_FILE_VALUES.items()):
        if os.environ.get(key) == old_value and file_values.get(key) != old_value:
            os.environ.pop(key, None)
        if os.environ.get(key) != old_value:
            _APPLIED_ENV_FILE_VALUES.pop(key, None)

    for key, value in file_values.items():
        # 仅当进程环境还没有显式设置时才注入, 优先尊重 shell 传入
        if key not in os.environ:
            os.environ[key] = value
            _APPLIED_ENV_FILE_VALUES[key] = value


def _env_file_candidates_from_env() -> list[Path]:
    """从 SCREENING_ENV_FILE / SCREENING_ENV_FILES 中读取手动指定的 .env 路径。"""
    raw_values = [
        os.getenv("SCREENING_ENV_FILE", ""),
        os.getenv("SCREENING_ENV_FILES", ""),
    ]
    paths: list[Path] = []
    for raw in raw_values:
        # 同时支持 posix/nt 风格分隔符, 统一按逗号切分
        for item in raw.replace(os.pathsep, ",").split(","):
            value = item.strip()
            if value:
                paths.append(Path(value))
    return paths


def _parse_bool_env(name: str, default: bool) -> bool:
    """从环境读取布尔值, 支持 1/true/yes/on; 不存在时返回 default。"""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_csv_env(name: str, default: list[str] | None = None) -> list[str]:
    """从环境读取 CSV, 关闭类字面值(off/none/false)被视作空列表。"""
    value = os.getenv(name)
    if value is None:
        return list(default or [])
    if value.strip().lower() in {"", "0", "false", "none", "off"}:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_optional_path_env(name: str) -> Path | None:
    """读环境里的可选路径, 空字符串归一化为 None。"""
    value = os.getenv(name, "").strip()
    return Path(value) if value else None


def _has_tushare_token() -> bool:
    """检测是否配置了 tushare token(支持新旧两个 key 名)。"""
    return bool(
        os.getenv("TUSHARE_TOKEN", "").strip()
        or os.getenv("TUSHARE_API_TOKEN", "").strip()
    )


def _resolve_snapshot_source_priority() -> list[str]:
    """按是否配置 tushare 来选择快照数据源优先级。"""
    explicit = os.getenv("SNAPSHOT_SOURCE_PRIORITY")
    if explicit is not None:
        return [s.strip() for s in explicit.split(",") if s.strip()]
    if _has_tushare_token():
        return list(TUSHARE_FIRST_SOURCE_PRIORITY)
    return list(DEFAULT_SNAPSHOT_SOURCE_PRIORITY)


def _resolve_fallback_snapshot_path(data_dir: Path) -> Path | None:
    """解析"最后一帧可用快照"的本地缓存路径; 关闭型配置返回 None。"""
    for name in ("SCREENING_FALLBACK_SNAPSHOT_PATH", "FALLBACK_SNAPSHOT_PATH"):
        raw = os.getenv(name)
        if raw is None:
            continue
        value = raw.strip()
        # 显式关闭时禁止 fallback
        if value.lower() in {"", "0", "false", "none", "off"}:
            return None
        return Path(value)
    return data_dir / "snapshot.last_good.json"


def _default_strategies_dir() -> Path:
    """默认把策略目录指向 screening 包内的 ``strategies``。"""
    return _PACKAGE_DIR / "strategies"


def _resolve_strategies_dir() -> Path:
    """从 STRATEGIES_DIR 解析策略目录, 未配置时用内置目录。"""
    return _parse_optional_path_env("STRATEGIES_DIR") or _default_strategies_dir()


@dataclass
class Config:
    """运行时配置, 从环境变量加载。"""

    # LLM
    llm_api_key: str = ""
    llm_model: str = DEFAULT_LLM_MODEL
    llm_base_url: str = ""
    llm_config_path: Path | None = None
    llm_fallback_models: list[str] = field(default_factory=list)
    llm_channels: list[dict[str, object]] = field(default_factory=list)
    llm_context: str = ""
    llm_candidate_context_enabled: bool = False
    llm_candidate_context_max_candidates: int = 8
    llm_candidate_context_providers: list[str] = field(default_factory=lambda: ["news", "fund_flow", "announcement", "quote"])
    llm_candidate_context_news_limit: int = 3
    llm_candidate_context_announcement_limit: int = 3
    llm_candidate_context_cache_enabled: bool = True
    llm_candidate_context_cache_ttl_hours: int = 24
    llm_temperature: float = 0.2
    llm_json_mode: bool = True
    llm_silent: bool = True
    llm_rank_weight: float = 0.40
    llm_candidate_multiplier: int = 6
    llm_max_candidates: int = 30
    llm_max_retries: int = 1
    llm_min_coverage: float = 0.60
    llm_context_max_chars: int = 4000
    llm_timeout_sec: float = 60.0
    llm_max_tokens: int = 2048

    # Snapshot data source priority
    snapshot_source_priority: list[str] = field(
        default_factory=lambda: list(DEFAULT_SNAPSHOT_SOURCE_PRIORITY)
    )
    fallback_snapshot_path: Path | None = (
        _PROJECT_ROOT / "data" / "snapshot.last_good.json"
    )
    snapshot_cache_ttl_seconds: float = 300.0

    # Strategy directory
    strategies_dir: Path = field(default_factory=_default_strategies_dir)

    # Optional deterministic industry/concept enrichment.
    industry_map_files: list[Path] = field(default_factory=list)
    industry_provider: str = "none"
    industry_provider_max_boards: int = 80
    industry_provider_cache_dir: Path | None = (
        _PROJECT_ROOT / "data" / "industry_provider_cache"
    )
    industry_provider_cache_ttl_hours: int = 24

    # Optional: DSA API for L3 deep analysis
    dsa_api_url: str = ""
    dsa_report_type: str = "detailed"
    dsa_max_picks: int = 3
    dsa_timeout_sec: float = 120.0
    dsa_force_refresh: bool = False
    dsa_notify: bool = False

    # L3/post-ranking analyzers. scorecard is the default local scorer; DSA is
    # one optional backend, not the pipeline's default or only final stage.
    post_analyzers: list[str] = field(default_factory=lambda: list(DEFAULT_POST_ANALYZERS))
    post_analysis_max_picks: int = 3
    post_analyzer_url: str = ""
    post_analyzer_timeout_sec: float = 120.0

    # Optional daily K-line enrichment after snapshot hard filters.
    daily_enrich_enabled: bool = False
    daily_enrich_max_candidates: int = 100
    daily_lookback_days: int = 120
    daily_source: str = "auto"
    daily_fetch_retries: int = 2
    daily_fetch_max_workers: int = 1
    daily_history_cache_dir: Path | None = None
    daily_history_cache_ttl_hours: int = 24

    # Independent risk layer.
    risk_enabled: bool = True
    risk_max_penalty: float = 12.0
    risk_veto_high: bool = False

    # Portfolio diversity layer driven by LLM sector/theme risk buckets.
    portfolio_diversity_enabled: bool = True
    portfolio_max_same_llm_sector: int = 1
    portfolio_concentration_penalty: float = 4.0

    # Evaluation overlay.
    evaluation_cost_bps: float = 0.0
    evaluation_follow_through_pct: float = 3.0
    evaluation_failed_breakout_pct: float = -3.0
    evaluation_price_path_enabled: bool = False
    evaluation_price_path_lookback_days: int = 90

    # Data directory
    data_dir: Path = _PROJECT_ROOT / "data"

    # Optional guardrail for last-good snapshot fallback freshness.
    snapshot_fallback_max_age_hours: float | None = None

    def has_llm_config(self) -> bool:
        """判断是否至少配置了其中一种 LiteLLM 通道(API key / Ollama / 配置文件 / 多渠道)。"""
        return any([
            bool(self.llm_api_key),
            # Ollama 本地部署时即使没有 API key 也能用
            bool(self.llm_base_url and self.llm_model.startswith("ollama/")),
            bool(self.llm_config_path),
            bool(self.llm_channels),
            self.llm_model.startswith("ollama/"),
        ])

    @classmethod
    def from_env(cls) -> "Config":
        """加载 .env + 系统环境, 构造完整的 Config 实例。"""
        _load_env_file()
        channels = _parse_llm_channels_env()
        llm_model = _resolve_llm_model(channels)
        data_dir = Path(os.getenv("SCREENING_DATA_DIR", str(_PROJECT_ROOT / "data")))
        fallback_snapshot_path = _resolve_fallback_snapshot_path(data_dir)
        # 历史 / 当前命名兼容: SCREENING_ 前缀优先, 否则沿用通用环境名
        daily_history_cache_dir = (
            _parse_optional_path_env("SCREENING_DAILY_HISTORY_CACHE_DIR")
            or _parse_optional_path_env("DAILY_HISTORY_CACHE_DIR")
            or data_dir / "daily_history"
        )
        industry_provider_cache_dir = (
            _parse_optional_path_env("SCREENING_INDUSTRY_PROVIDER_CACHE_DIR")
            or _parse_optional_path_env("INDUSTRY_PROVIDER_CACHE_DIR")
            or data_dir / "industry_provider_cache"
        )
        return cls(
            llm_api_key=_resolve_llm_api_key(llm_model),
            llm_model=llm_model,
            llm_base_url=_resolve_llm_base_url(llm_model),
            llm_config_path=_parse_optional_path_env("LITELLM_CONFIG"),
            llm_fallback_models=_parse_csv_env("LITELLM_FALLBACK_MODELS", []),
            llm_channels=channels,
            llm_context=os.getenv("LLM_CONTEXT", ""),
            llm_candidate_context_enabled=_parse_bool_env("LLM_CANDIDATE_CONTEXT_ENABLED", False),
            llm_candidate_context_max_candidates=max(
                1,
                int(os.getenv("LLM_CANDIDATE_CONTEXT_MAX_CANDIDATES", "8")),
            ),
            llm_candidate_context_providers=_parse_csv_env(
                "LLM_CANDIDATE_CONTEXT_PROVIDERS",
                ["news", "fund_flow", "announcement", "quote"],
            ),
            llm_candidate_context_news_limit=max(1, int(os.getenv("LLM_CANDIDATE_CONTEXT_NEWS_LIMIT", "3"))),
            llm_candidate_context_announcement_limit=max(
                1,
                int(os.getenv("LLM_CANDIDATE_CONTEXT_ANNOUNCEMENT_LIMIT", "3")),
            ),
            llm_candidate_context_cache_enabled=_parse_bool_env("LLM_CANDIDATE_CONTEXT_CACHE_ENABLED", True),
            llm_candidate_context_cache_ttl_hours=max(
                0,
                int(os.getenv("LLM_CANDIDATE_CONTEXT_CACHE_TTL_HOURS", "24")),
            ),
            llm_temperature=_parse_float_env("LLM_TEMPERATURE", 0.2),
            llm_json_mode=_parse_bool_env("LLM_JSON_MODE", True),
            llm_silent=_parse_bool_env("LLM_SILENT", True),
            llm_rank_weight=_parse_float_env("LLM_RANK_WEIGHT", 0.40),
            llm_candidate_multiplier=max(1, int(os.getenv("LLM_CANDIDATE_MULTIPLIER", "6"))),
            llm_max_candidates=max(1, int(os.getenv("LLM_MAX_CANDIDATES", "30"))),
            llm_max_retries=max(0, int(os.getenv("LLM_MAX_RETRIES", "1"))),
            llm_min_coverage=_parse_float_env("LLM_MIN_COVERAGE", 0.60),
            llm_context_max_chars=max(500, int(os.getenv("LLM_CONTEXT_MAX_CHARS", "4000"))),
            llm_timeout_sec=max(1.0, _parse_float_env("LLM_TIMEOUT_SEC", 60.0)),
            llm_max_tokens=max(1, int(os.getenv("LLM_MAX_TOKENS", "2048"))),
            snapshot_source_priority=_resolve_snapshot_source_priority(),
            fallback_snapshot_path=fallback_snapshot_path,
            snapshot_cache_ttl_seconds=max(
                0.0,
                _parse_float_env("SCREENING_SNAPSHOT_CACHE_TTL_SEC", 300.0),
            ),
            snapshot_fallback_max_age_hours=_parse_optional_float_env(
                "SNAPSHOT_FALLBACK_MAX_AGE_HOURS"
            ),
            strategies_dir=_resolve_strategies_dir(),
            industry_map_files=[
                Path(item)
                for item in _parse_csv_env("INDUSTRY_MAP_FILES", [])
            ],
            industry_provider=os.getenv("INDUSTRY_PROVIDER", "none"),
            industry_provider_max_boards=max(1, int(os.getenv("INDUSTRY_PROVIDER_MAX_BOARDS", "80"))),
            industry_provider_cache_dir=industry_provider_cache_dir,
            industry_provider_cache_ttl_hours=max(
                0,
                int(
                    os.getenv(
                        "SCREENING_INDUSTRY_PROVIDER_CACHE_TTL_HOURS",
                        os.getenv("INDUSTRY_PROVIDER_CACHE_TTL_HOURS", "24"),
                    )
                ),
            ),
            dsa_api_url=os.getenv("DSA_API_URL", ""),
            dsa_report_type=os.getenv("DSA_REPORT_TYPE", "detailed"),
            dsa_max_picks=max(1, int(os.getenv("DSA_MAX_PICKS", "3"))),
            dsa_timeout_sec=float(os.getenv("DSA_TIMEOUT_SEC", "120")),
            dsa_force_refresh=_parse_bool_env("DSA_FORCE_REFRESH", False),
            dsa_notify=_parse_bool_env("DSA_NOTIFY", False),
            post_analyzers=_parse_csv_env("POST_ANALYZERS", DEFAULT_POST_ANALYZERS),
            post_analysis_max_picks=max(
                1,
                int(os.getenv("POST_ANALYSIS_MAX_PICKS", os.getenv("DSA_MAX_PICKS", "3"))),
            ),
            post_analyzer_url=os.getenv("POST_ANALYZER_URL", ""),
            post_analyzer_timeout_sec=float(os.getenv("POST_ANALYZER_TIMEOUT_SEC", "120")),
            daily_enrich_enabled=_parse_bool_env("DAILY_ENRICH_ENABLED", False),
            daily_enrich_max_candidates=max(1, int(os.getenv("DAILY_ENRICH_MAX_CANDIDATES", "100"))),
            daily_lookback_days=max(30, int(os.getenv("DAILY_LOOKBACK_DAYS", "120"))),
            daily_source=os.getenv("DAILY_SOURCE", "auto"),
            daily_fetch_retries=max(0, int(os.getenv("DAILY_FETCH_RETRIES", "2"))),
            daily_fetch_max_workers=max(1, int(os.getenv("DAILY_FETCH_MAX_WORKERS", "1"))),
            daily_history_cache_dir=daily_history_cache_dir,
            daily_history_cache_ttl_hours=max(
                0,
                int(
                    os.getenv(
                        "SCREENING_DAILY_HISTORY_CACHE_TTL_HOURS",
                        os.getenv("DAILY_HISTORY_CACHE_TTL_HOURS", "24"),
                    )
                ),
            ),
            risk_enabled=_parse_bool_env("RISK_ENABLED", True),
            risk_max_penalty=_parse_float_env("RISK_MAX_PENALTY", 12.0),
            risk_veto_high=_parse_bool_env("RISK_VETO_HIGH", False),
            portfolio_diversity_enabled=_parse_bool_env("PORTFOLIO_DIVERSITY_ENABLED", True),
            portfolio_max_same_llm_sector=max(
                1,
                int(os.getenv("PORTFOLIO_MAX_SAME_LLM_SECTOR", "1")),
            ),
            portfolio_concentration_penalty=_parse_float_env("PORTFOLIO_CONCENTRATION_PENALTY", 4.0),
            evaluation_cost_bps=_parse_float_env("EVALUATION_COST_BPS", 0.0),
            evaluation_follow_through_pct=_parse_float_env("EVALUATION_FOLLOW_THROUGH_PCT", 3.0),
            evaluation_failed_breakout_pct=_parse_float_env("EVALUATION_FAILED_BREAKOUT_PCT", -3.0),
            evaluation_price_path_enabled=_parse_bool_env("EVALUATION_PRICE_PATH_ENABLED", False),
            evaluation_price_path_lookback_days=max(
                30,
                int(os.getenv("EVALUATION_PRICE_PATH_LOOKBACK_DAYS", "90")),
            ),
            data_dir=data_dir,
        )


def _parse_float_env(name: str, default: float) -> float:
    """从环境读取浮点值, 缺失或为空时返回 default。"""
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return float(value)


def _parse_optional_float_env(name: str) -> float | None:
    """从环境读取可选浮点值, 缺失或关闭型配置返回 None。"""
    value = os.getenv(name)
    if value is None:
        return None
    cleaned = value.strip()
    if cleaned.lower() in {"", "none", "off", "false"}:
        return None
    return float(cleaned)


def _parse_llm_channels_env() -> list[dict[str, object]]:
    """从 ``LLM_CHANNELS`` 及 ``LLM_<NAME>_*`` 系列环境变量构造多渠道配置列表。"""
    channels = []
    for raw_name in _parse_csv_env("LLM_CHANNELS", []):
        name = raw_name.strip()
        if not name:
            continue
        key = name.upper()
        enabled = _parse_bool_env(f"LLM_{key}_ENABLED", True)
        base_url = os.getenv(f"LLM_{key}_BASE_URL", "").strip()
        protocol = os.getenv(f"LLM_{key}_PROTOCOL", "").strip().lower()
        api_surface_raw = os.getenv(f"LLM_{key}_API_SURFACE", "")
        api_surface = normalize_llm_channel_api_surface(api_surface_raw)
        api_keys = (
            _parse_csv_env(f"LLM_{key}_API_KEYS", [])
            or _parse_csv_env(f"LLM_{key}_API_KEY", [])
        )
        models = _parse_csv_env(f"LLM_{key}_MODELS", [])
        resolved_protocol = resolve_llm_channel_protocol(
            protocol,
            base_url=base_url,
            models=models,
            channel_name=name,
        )
        if not is_supported_llm_channel_api_surface_value(api_surface_raw):
            continue
        effective_protocol = resolved_protocol or "openai"
        if api_surface == "responses" and effective_protocol != "openai":
            continue
        if is_reserved_hermes_name(name) and api_surface == "responses":
            continue
        if find_incompatible_llm_channel_models(models, effective_protocol, api_surface, base_url):
            continue
        normalized_models = [
            normalize_llm_channel_model(model, effective_protocol, base_url)
            for model in models
        ]
        channels.append({
            "name": name.lower(),
            "protocol": effective_protocol,
            "api_surface": api_surface,
            "base_url": base_url,
            "api_keys": api_keys,
            "models": normalized_models,
            "enabled": enabled,
        })
    enabled_channels = [channel for channel in channels if channel["enabled"]]
    surface_conflicts = set(find_llm_channel_surface_conflicts(enabled_channels))
    if not surface_conflicts:
        return enabled_channels
    return [
        channel
        for channel in enabled_channels
        if not set(channel.get("models", [])).intersection(surface_conflicts)
    ]


def _resolve_llm_model(channels: list[dict[str, object]]) -> str:
    """按显式环境变量 -> 渠道默认 -> Ollama / DeepSeek / Gemini / OpenAI / AIHubMix 的顺序挑选模型。"""
    explicit = (
        os.getenv("LITELLM_MODEL")
        or os.getenv("LLM_MODEL")
        or os.getenv("AGENT_LITELLM_MODEL")
        or ""
    ).strip()
    if explicit:
        return _normalize_litellm_model(explicit)

    for channel in channels:
        models = channel.get("models", [])
        if isinstance(models, list) and models:
            return _normalize_litellm_model(str(models[0]), str(channel.get("protocol", "openai")))

    if os.getenv("OLLAMA_API_BASE"):
        ollama_model = os.getenv("OLLAMA_MODEL", "").strip()
        return f"ollama/{ollama_model}" if ollama_model else DEFAULT_LLM_MODEL
    if os.getenv("DEEPSEEK_API_KEY"):
        return "deepseek/deepseek-chat"
    if os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEYS"):
        return _normalize_litellm_model(os.getenv("GEMINI_MODEL", DEFAULT_LLM_MODEL), "gemini")
    if os.getenv("OPENAI_API_KEY"):
        return _normalize_litellm_model(os.getenv("OPENAI_MODEL", "gpt-4o-mini"), "openai")
    if os.getenv("AIHUBMIX_KEY"):
        return _normalize_litellm_model(os.getenv("OPENAI_MODEL", "gpt-4o-mini"), "openai")
    return DEFAULT_LLM_MODEL


def _normalize_litellm_model(model: str, protocol: str = "openai") -> str:
    """把模型名补全为 LiteLLM 要求的 ``provider/model`` 形式, 已带前缀则原样返回。"""
    model = model.strip()
    if "/" in model:
        return model
    if protocol == "ollama":
        return f"ollama/{model}"
    if protocol == "gemini":
        return f"gemini/{model}"
    if protocol == "deepseek":
        return f"deepseek/{model}"
    return f"openai/{model}"


def _resolve_llm_api_key(model: str) -> str:
    """按模型前缀从对应环境变量挑选 API key, ``LLM_API_KEY`` 显式配置优先。"""
    explicit = os.getenv("LLM_API_KEY", "").strip()
    if explicit:
        return explicit
    if model.startswith("gemini/"):
        keys = _parse_csv_env("GEMINI_API_KEYS", [])
        return keys[0] if keys else os.getenv("GEMINI_API_KEY", "")
    if model.startswith("deepseek/"):
        return os.getenv("DEEPSEEK_API_KEY", "")
    if model.startswith("anthropic/"):
        return os.getenv("ANTHROPIC_API_KEY", "")
    if os.getenv("AIHUBMIX_KEY"):
        return os.getenv("AIHUBMIX_KEY", "")
    if model.startswith("openai/"):
        return os.getenv("OPENAI_API_KEY", "")
    return os.getenv("OPENAI_API_KEY", "")


def _resolve_llm_base_url(model: str) -> str:
    """按模型前缀与已配置密钥返回对应的 base_url, ``LLM_BASE_URL`` 显式配置优先。"""
    explicit = os.getenv("LLM_BASE_URL", "").strip()
    if explicit:
        return explicit
    if model.startswith("ollama/"):
        return os.getenv("OLLAMA_API_BASE", "")
    if os.getenv("AIHUBMIX_KEY"):
        return os.getenv("AIHUBMIX_BASE_URL", "https://api.aihubmix.com/v1")
    if model.startswith("openai/"):
        return os.getenv("OPENAI_BASE_URL", "")
    return os.getenv("OPENAI_BASE_URL", "")

