# -*- coding: utf-8 -*-
"""DSA 原生的市场热点上下文服务。

本服务刻意不引入 AlphaSift 依赖，而是直接复用 DSA 已有的行业/概念排行榜 provider
来组装首版"主题层"上下文；当更富的热点证据缺失时，返回显式的数据质量标记。

设计要点：
- 并发与超时：通过信号量 + ``Future`` 实现排行榜抓取上限、去重、冷却
- TTL 缓存：按 (market, trade_date, limit) 维度缓存热点结果，区分成功/失败 TTL
- 模式：支持调用方预加载上游抓取结果，跳过内部抓取路径
"""

from __future__ import annotations

import logging
import copy
import threading
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from datetime import date
import time
from typing import Any, Callable, Dict, Hashable, List, Optional, Set, Tuple

from data_provider import DataFetcherManager

from src.schemas.market_structure import (
    MarketStructureDataQuality,
    MarketStructureSource,
    MarketThemeContext,
    MarketThemeItem,
    RankedThemeItem,
    ThemeBreadth,
    ThemeRankSource,
    dump_market_structure_model,
)


logger = logging.getLogger(__name__)

# 排行榜抓取的默认超时、并发、TTL 等参数(可通过构造函数覆盖)
DEFAULT_RANKING_FETCH_TIMEOUT_SECONDS = 3.0  # 单次排行榜接口超时秒数
DEFAULT_RANKING_CACHE_FAILURE_TTL_SECONDS = 30.0  # 失败结果缓存 TTL, 避免反复重试失败调用
DEFAULT_RANKING_CACHE_SUCCESS_TTL_SECONDS = 60.0  # 成功结果缓存 TTL, 平衡时效与请求量
RANKING_FETCH_MAX_WORKERS = 2  # 同时进行的排行榜抓取任务上限(基于 BoundedSemaphore)
RANKING_FETCH_TIMEOUT_RETRY_DELAY_SECONDS = 0.2  # 触发超时空挡后, 冷却基线时长(秒)


class MarketHotspotService:
    """基于 DSA 行业/概念排行榜构建低敏感度的 A 股市场/主题上下文。"""

    # 进程内共享的并发控制与去重资源, 所有实例共用同一组信号量/未来对象。
    _ranking_fetch_slots = threading.BoundedSemaphore(RANKING_FETCH_MAX_WORKERS)
    _ranking_fetch_futures: Dict[Hashable, Future] = {}  # inflight_key -> 进行中的 Future
    _ranking_fetch_detached_futures: Set[Future] = set()  # 已脱离主表的 Future(超时后不再追踪)
    _ranking_fetch_retry_after: Dict[Hashable, Tuple[Future, float]] = {}  # 冷却表: inflight_key -> (Future, 解禁时间)
    _ranking_fetch_futures_lock = threading.Lock()  # 保护上面三张表的互斥锁

    def __init__(
        self,
        fetcher_manager: Optional[DataFetcherManager] = None,
        ranking_fetch_timeout_seconds: Optional[float] = None,
        failure_cache_ttl_seconds: Optional[float] = None,
        success_cache_ttl_seconds: Optional[float] = None,
    ) -> None:
        """初始化数据源、超时阈值与 TTL 缓存表。

        Args:
            fetcher_manager: DSA 数据拉取管理器；缺省按需惰性创建。
            ranking_fetch_timeout_seconds: 排行榜接口单次抓取的超时秒数。
            failure_cache_ttl_seconds: 失败结果缓存 TTL（避坑反复打挂点）。
            success_cache_ttl_seconds: 成功结果缓存 TTL（平衡时效与请求量）。
        """
        self.fetcher_manager = fetcher_manager or DataFetcherManager()
        self._ranking_fetch_timeout_seconds = ranking_fetch_timeout_seconds
        self._failure_cache_ttl_seconds = self._coerce_cache_ttl(
            DEFAULT_RANKING_CACHE_FAILURE_TTL_SECONDS
            if failure_cache_ttl_seconds is None
            else failure_cache_ttl_seconds
        )
        self._success_cache_ttl_seconds = self._coerce_cache_ttl(
            DEFAULT_RANKING_CACHE_SUCCESS_TTL_SECONDS
            if success_cache_ttl_seconds is None
            else success_cache_ttl_seconds
        )
        # 热点缓存 (market, trade_date_text, limit) -> {"payload": ..., "expires_at": ...}
        self._hotspots_cache: Dict[
            Tuple[str, Optional[str], int],
            Dict[str, Any],
        ] = {}
        self._hotspots_cache_lock = threading.Lock()

    def get_hotspots(
        self,
        *,
        market: str,
        trade_date: Any = None,
        limit: int = 5,
        sector_rankings: Any = None,
        concept_rankings: Any = None,
    ) -> Dict[str, Any]:
        """组装热门主题上下文, 返回可序列化的字典。

        支持"调用方预加载"模式: 传入 ``sector_rankings`` / ``concept_rankings`` 时跳过
        内部抓取与缓存写入, 主要用于复用上游已抓取的行业/概念数据。

        Args:
            market: 市场代码（如 ``"cn"``），未识别值会被规整成 ``cn``。
            trade_date: 交易日；任意可格式化对象，缺省为 ``None``。
            limit: 主题条目上限。
            sector_rankings: 预加载的行业排行榜结果，未提供则内部抓取。
            concept_rankings: 预加载的概念排行榜结果，未提供则内部抓取。

        Returns:
            可序列化为 JSON 的字典，结构遵循 :class:`MarketThemeContext` 转储格式。
        """
        # 归一化市场代码, 未识别时默认 cn, 避免下游分支误判
        normalized_market = str(market or "cn").strip().lower() or "cn"
        trade_date_text = self._format_trade_date(trade_date)
        try:
            limit = max(1, int(limit or 5))
        except (TypeError, ValueError):
            limit = 5

        uses_preloaded_rankings = sector_rankings is not None or concept_rankings is not None
        cache_key = (normalized_market, trade_date_text, limit)
        # 仅在未走预加载分支时才使用内部 TTL 缓存, 避免覆盖调用方的输入
        if not uses_preloaded_rankings:
            cached = self._get_cached_hotspots(cache_key)
            if cached is not None:
                return cached

        # 首版只支持 A 股; 非 cn 市场直接返回 not_supported, 节省下游无意义处理
        if normalized_market != "cn":
            context = MarketThemeContext(
                status="not_supported",
                market=normalized_market,
                trade_date=trade_date_text,
                data_quality=MarketStructureDataQuality(
                    status="not_supported",
                    missing_fields=["industry_rankings", "concept_rankings"],
                    sources=[
                        MarketStructureSource(
                            provider="dsa",
                            dataset="sector_rankings",
                            status="not_supported",
                            message="market structure hotspots are only supported for A-share first version",
                        )
                    ],
                ),
            )
            return self._store_cached_hotspots(cache_key, dump_market_structure_model(context))

        errors: List[str] = []
        sources: List[MarketStructureSource] = []
        top_industries, bottom_industries = self._resolve_rankings(
            "get_sector_rankings",
            "sector_rankings",
            limit,
            errors,
            sources,
            preloaded_rankings=sector_rankings,
        )
        top_concepts, bottom_concepts = self._resolve_rankings(
            "get_concept_rankings",
            "concept_rankings",
            limit,
            errors,
            sources,
            preloaded_rankings=concept_rankings,
        )

        leading_industries = self._normalize_ranked_items(top_industries, "industry")
        leading_concepts = self._normalize_ranked_items(top_concepts, "concept")
        lagging_themes = list(
            self._normalize_ranked_items(bottom_industries, "industry")
        ) + list(self._normalize_ranked_items(bottom_concepts, "concept"))
        active_themes = self._build_active_themes(
            list(leading_industries) + list(leading_concepts),
            limit=limit,
        )

        missing_fields: List[str] = []
        if not leading_industries and not bottom_industries:
            missing_fields.append("industry_rankings")
        if not leading_concepts and not bottom_concepts:
            missing_fields.append("concept_rankings")

        has_any_ranking = bool(leading_industries or leading_concepts or lagging_themes)
        has_partial_source = any(
            source.status == "partial"
            for source in sources
            if source.provider == "dsa" and source.dataset in {"sector_rankings", "concept_rankings"}
        )
        if not missing_fields and not errors and not has_partial_source:
            status = "ok"
        elif has_any_ranking:
            status = "partial"
        else:
            status = "unknown"

        context = MarketThemeContext(
            status=status,
            market=normalized_market,
            trade_date=trade_date_text,
            active_themes=active_themes,
            leading_industries=leading_industries,
            leading_concepts=leading_concepts,
            # Keep both ranking families. Each provider result is already bounded
            # by ``limit``; truncating the combined list here can discard every
            # lagging concept whenever the industry list fills the limit, which
            # removes valid evidence from downstream stock-board matching.
            lagging_themes=lagging_themes,
            theme_breadth=ThemeBreadth(
                active_count=len(active_themes),
                leading_industry_count=len(leading_industries),
                leading_concept_count=len(leading_concepts),
                lagging_count=len(lagging_themes),
            ),
            data_quality=MarketStructureDataQuality(
                status=status,
                missing_fields=missing_fields,
                sources=sources,
                errors=errors,
            ),
        )
        payload = dump_market_structure_model(context)
        if uses_preloaded_rankings:
            return payload
        return self._store_cached_hotspots(cache_key, payload)

    def _resolve_rankings(
        self,
        fetch_name: str,
        dataset: str,
        limit: int,
        errors: List[str],
        sources: List[MarketStructureSource],
        *,
        preloaded_rankings: Any = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """取一份排行榜数据：有预加载就用预加载，否则走抓取路径。"""
        preloaded = self._rankings_from_payload(preloaded_rankings, dataset, sources)
        if preloaded is not None:
            return preloaded
        return self._fetch_rankings(fetch_name, dataset, limit, errors, sources)

    @staticmethod
    def _rankings_from_payload(
        rankings: Any,
        dataset: str,
        sources: List[MarketStructureSource],
    ) -> Optional[Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]:
        """把调用方预加载的排行榜（dict 形态）解析成 ``(top, bottom)``。

        形状不规范时写入一条 ``invalid`` 数据源标记，并返回空对。
        返回 ``None`` 表示"调用方没传"，由上层走抓取路径。
        """
        if rankings is None:
            return None
        if not isinstance(rankings, dict):
            sources.append(
                MarketStructureSource(
                    provider="dsa",
                    dataset=dataset,
                    status="invalid",
                    message="preloaded ranking payload is invalid",
                )
            )
            return [], []

        top = rankings.get("top")
        bottom = rankings.get("bottom")
        top_items = list(top) if isinstance(top, list) else []
        bottom_items = list(bottom) if isinstance(bottom, list) else []
        if isinstance(rankings.get("status"), str):
            raw_status = rankings.get("status").strip().lower()
            status = raw_status if raw_status in {"ok", "partial", "not_supported", "unknown"} else "ok"
        else:
            status = "ok"
        if not top_items and not bottom_items:
            status = "empty"
        sources.append(
            MarketStructureSource(
                provider="dsa",
                dataset=dataset,
                status=status,
                message="reused fundamental_context ranking payload",
            )
        )
        return top_items, bottom_items

    def get_hotspot_detail(self, theme_name: str, market: str = "cn") -> Dict[str, Any]:
        """返回更细的热点主题详情；当前首版尚未实现，仅占位返回缺失字段。

        上层据此判断是否需要降级到仅有排行榜数据的上下文。
        """
        normalized_market = str(market or "cn").strip().lower() or "cn"
        # cn 当前仍是 unknown(待真实详情接口), 其它市场直接 not_supported
        status = "unknown" if normalized_market == "cn" else "not_supported"
        return {
            "theme_name": str(theme_name or "").strip(),
            "market": normalized_market,
            "status": status,
            "missing_fields": ["hotspot_route", "hotspot_constituents", "leader_stocks"],
        }

    def get_concept_rankings(
        self,
        limit: int = 5,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """获取概念榜单的 top/bottom，受超时与并发上限保护。"""
        errors: List[str] = []
        sources: List[MarketStructureSource] = []
        return self._resolve_rankings(
            "get_concept_rankings",
            "concept_rankings",
            limit,
            errors,
            sources,
        )

    def _get_cached_hotspots(
        self,
        cache_key: Tuple[str, Optional[str], int],
    ) -> Optional[Dict[str, Any]]:
        """从 TTL 缓存中取结果；命中并有效则深拷贝一份返回避免外部修改。"""
        with self._hotspots_cache_lock:
            cached = self._hotspots_cache.get(cache_key)
            if not isinstance(cached, dict):
                return None

            payload = cached.get("payload")
            if not isinstance(payload, dict):
                return None

            expires_at = cached.get("expires_at")
            # 过期项顺手清理掉, 避免缓存表长期膨胀
            if isinstance(expires_at, (int, float)) and expires_at < time.time():
                self._hotspots_cache.pop(cache_key, None)
                return None

            return copy.deepcopy(payload)

    def _store_cached_hotspots(
        self,
        cache_key: Tuple[str, Optional[str], int],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """按 payload 状态选择 TTL（ok 走成功 TTL，其它走失败 TTL）写入缓存。"""
        if payload.get("status") == "ok":
            success_ttl = self._success_cache_ttl_seconds
            if success_ttl <= 0:
                return copy.deepcopy(payload)
            expires_at = time.time() + success_ttl
        else:
            status_error = payload.get("data_quality", {}).get("errors", [])
            has_missing = bool(payload.get("data_quality", {}).get("missing_fields", []))
            # 任一异常维度存在就当失败结果缓存, 抑制短时间内反复打挂点
            if status_error or has_missing or payload.get("status") != "ok":
                failure_ttl = self._failure_cache_ttl_seconds
                if failure_ttl <= 0:
                    return copy.deepcopy(payload)
                expires_at = time.time() + failure_ttl
            else:
                expires_at = None

        entry: Dict[str, Any] = {
            "payload": copy.deepcopy(payload),
            "expires_at": expires_at,
        }
        with self._hotspots_cache_lock:
            self._hotspots_cache[cache_key] = copy.deepcopy(entry)
        return copy.deepcopy(payload)

    @staticmethod
    def _coerce_cache_ttl(value: Any) -> float:
        """把传入值规整成非负浮点；无效输入退回到失败 TTL 默认值。"""
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return DEFAULT_RANKING_CACHE_FAILURE_TTL_SECONDS

    def _fetch_rankings(
        self,
        fetch_name: str,
        dataset: str,
        limit: int,
        errors: List[str],
        sources: List[MarketStructureSource],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """内部抓取排行榜：超时受控、异常被吞写日志并写入 sources 标记。"""
        fetch_rankings = getattr(self.fetcher_manager, fetch_name, None)
        if not callable(fetch_rankings):
            sources.append(
                MarketStructureSource(
                    provider="dsa",
                    dataset=dataset,
                    status="missing",
                    message=f"{fetch_name} is unavailable",
                )
            )
            return [], []

        try:
            rankings = self._call_with_timeout(
                lambda: fetch_rankings(limit),
                timeout_seconds=self._resolve_ranking_fetch_timeout_seconds(),
                task_name=dataset,
                inflight_key=(
                    type(self.fetcher_manager),
                    id(self.fetcher_manager),
                    fetch_name,
                    limit,
                ),
            )
            if isinstance(rankings, tuple) and len(rankings) == 2:
                top, bottom = rankings
                top_items = list(top) if isinstance(top, list) else []
                bottom_items = list(bottom) if isinstance(bottom, list) else []
                sources.append(
                    MarketStructureSource(
                        provider="dsa",
                        dataset=dataset,
                        status="ok" if top_items or bottom_items else "empty",
                    )
                )
                return top_items, bottom_items
            sources.append(
                MarketStructureSource(
                    provider="dsa",
                    dataset=dataset,
                    status="invalid",
                    message="ranking provider returned an invalid payload",
                )
            )
        except Exception as exc:
            logger.debug("market hotspot ranking fetch failed dataset=%s: %s", dataset, exc)
            errors.append(f"{dataset}: {exc}")
            sources.append(
                MarketStructureSource(
                    provider="dsa",
                    dataset=dataset,
                    status="failed",
                    message=str(exc),
                )
            )
        return [], []

    def _resolve_ranking_fetch_timeout_seconds(self) -> float:
        """从多种来源解析排行榜接口超时阈值，统一做非负化处理。"""
        if self._ranking_fetch_timeout_seconds is not None:
            try:
                return max(0.0, float(self._ranking_fetch_timeout_seconds))
            except (TypeError, ValueError):
                return DEFAULT_RANKING_FETCH_TIMEOUT_SECONDS
        try:
            from src.config import get_config

            # 复用基本面的超时配置作为兜底, 避免给热点服务额外引入新配置项
            value = getattr(
                get_config(),
                "fundamental_fetch_timeout_seconds",
                DEFAULT_RANKING_FETCH_TIMEOUT_SECONDS,
            )
            return max(0.0, float(value))
        except Exception:
            return DEFAULT_RANKING_FETCH_TIMEOUT_SECONDS

    @classmethod
    def _call_with_timeout(
        cls,
        task: Callable[[], Any],
        *,
        timeout_seconds: float,
        task_name: str,
        inflight_key: Optional[Hashable] = None,
    ) -> Any:
        """用 ``Future`` + 信号量实现排行榜接口的并发受控与超时控制。"""
        timeout_value = max(0.0, float(timeout_seconds))
        if timeout_value <= 0:
            # 配置的超时<=0 视为禁用抓取, 立即抛 TimeoutError 让上层走降级路径
            raise TimeoutError(f"{task_name} ranking fetch timeout")

        effective_inflight_key = inflight_key or task_name
        future = cls._get_or_submit_ranking_fetch(
            task,
            inflight_key=effective_inflight_key,
            task_name=task_name,
        )
        try:
            return future.result(timeout=timeout_value)
        except FutureTimeoutError as exc:
            if future.done() and future.exception(timeout=0) is exc:
                # Worker 自身正好抛出了同类型异常, 直接原样抛出避免被错误地标为超时
                raise
            # 等待超时: 把 future 转入冷却表, 防止后续并发立刻再次打挂点
            cls._mark_ranking_fetch_timeout(
                effective_inflight_key,
                future,
                retry_after=time.monotonic()
                + max(timeout_value, RANKING_FETCH_TIMEOUT_RETRY_DELAY_SECONDS),
            )
            raise TimeoutError(
                f"{task_name} ranking fetch timeout after {timeout_value:g}s"
            ) from exc

    @classmethod
    def _get_or_submit_ranking_fetch(
        cls,
        task: Callable[[], Any],
        *,
        inflight_key: Hashable,
        task_name: str,
    ) -> Future:
        """查询或启动一次排行榜抓取: 命中 in-flight 或冷却期直接复用, 否则新开线程执行。"""
        submitted: Future
        worker: threading.Thread
        with cls._ranking_fetch_futures_lock:
            retry_entry = cls._ranking_fetch_retry_after.get(inflight_key)
            now = time.monotonic()
            if retry_entry is not None:
                retry_future, retry_after = retry_entry
                if retry_after > now:
                    raise TimeoutError(
                        f"{task_name} ranking fetch cooling down after previous timeout"
                    )
                cls._ranking_fetch_retry_after.pop(inflight_key, None)
                if cls._ranking_fetch_futures.get(inflight_key) is retry_future:
                    cls._ranking_fetch_futures.pop(inflight_key, None)
                    if retry_future.done() or retry_future.cancelled():
                        cls._ranking_fetch_slots.release()
                    else:
                        cls._ranking_fetch_detached_futures.add(retry_future)

            current = cls._ranking_fetch_futures.get(inflight_key)
            if current is not None:
                if not current.done():
                    return current
                cls._ranking_fetch_futures.pop(inflight_key, None)
                cls._ranking_fetch_slots.release()

            if not cls._ranking_fetch_slots.acquire(blocking=False):
                raise TimeoutError(f"{task_name} ranking fetch in-flight limit reached")

            future: Future = Future()
            cls._ranking_fetch_retry_after.pop(inflight_key, None)
            cls._ranking_fetch_futures[inflight_key] = future
            future.add_done_callback(
                lambda done_future: cls._forget_ranking_fetch(inflight_key, done_future)
            )
            worker = threading.Thread(
                target=cls._run_ranking_fetch,
                args=(future, task),
                daemon=True,
                name=f"market-hotspot-{task_name}",
            )
            submitted = future
        try:
            worker.start()
        except BaseException as exc:
            cls._drop_unstarted_ranking_fetch(inflight_key, submitted)
            submitted.set_exception(exc)
            raise
        return submitted

    @classmethod
    def _forget_ranking_fetch(cls, inflight_key: Hashable, future: Future) -> None:
        """Future 完成时回调: 清理主表/脱落表/冷却表对应条目并归还并发槽位。"""
        with cls._ranking_fetch_futures_lock:
            should_release_slot = False
            if cls._ranking_fetch_futures.get(inflight_key) is future:
                cls._ranking_fetch_futures.pop(inflight_key, None)
                should_release_slot = True
            elif future in cls._ranking_fetch_detached_futures:
                cls._ranking_fetch_detached_futures.remove(future)
                should_release_slot = True

            retry_entry = cls._ranking_fetch_retry_after.get(inflight_key)
            if retry_entry is not None and retry_entry[0] is future:
                cls._ranking_fetch_retry_after.pop(inflight_key, None)

            if should_release_slot:
                cls._ranking_fetch_slots.release()

    @classmethod
    def _mark_ranking_fetch_timeout(
        cls,
        inflight_key: Hashable,
        future: Future,
        *,
        retry_after: float,
    ) -> None:
        """一次抓取超时时, 把对应槽位与冷却表转入"超时后冷却"状态。"""
        with cls._ranking_fetch_futures_lock:
            if cls._ranking_fetch_futures.get(inflight_key) is future:
                cls._ranking_fetch_futures.pop(inflight_key, None)
                if future.done() or future.cancelled():
                    cls._ranking_fetch_retry_after.pop(inflight_key, None)
                    cls._ranking_fetch_slots.release()
                    return
                cls._ranking_fetch_detached_futures.add(future)
                cls._ranking_fetch_retry_after[inflight_key] = (future, retry_after)

    @classmethod
    def _drop_unstarted_ranking_fetch(
        cls,
        inflight_key: Hashable,
        future: Future,
    ) -> None:
        """``worker.start()`` 失败时回滚主表与槽位, 避免泄漏。"""
        with cls._ranking_fetch_futures_lock:
            if cls._ranking_fetch_futures.get(inflight_key) is future:
                cls._ranking_fetch_futures.pop(inflight_key, None)
                cls._ranking_fetch_slots.release()

    @staticmethod
    def _run_ranking_fetch(future: Future, task: Callable[[], Any]) -> None:
        """工作线程入口: 把 ``task()`` 的成功/异常落到 ``Future`` 上, 供调用方 ``result(timeout=...)`` 等待。"""
        if not future.set_running_or_notify_cancel():
            return
        try:
            result = task()
        except BaseException as exc:
            future.set_exception(exc)
        else:
            future.set_result(result)

    def _normalize_ranked_items(
        self,
        items: Any,
        source: ThemeRankSource,
    ) -> List[RankedThemeItem]:
        """把上游 dict-list 规范化成 :class:`RankedThemeItem`, 字段名做多语言兼容。"""
        if not isinstance(items, list):
            return []

        normalized: List[RankedThemeItem] = []
        for index, item in enumerate(items, 1):
            if not isinstance(item, dict):
                continue
            # 名字兼容中英文与多种上游命名, 任意一个命中即可
            name = self._optional_text(
                item.get("name")
                or item.get("板块名称")
                or item.get("概念名称")
                or item.get("行业名称")
            )
            if not name:
                continue
            change_pct = self._safe_float(
                item.get("change_pct")
                if "change_pct" in item
                else item.get("pct_chg")
                if "pct_chg" in item
                else item.get("涨跌幅")
                if "涨跌幅" in item
                else item.get("涨跌幅%")
            )
            normalized.append(
                RankedThemeItem(
                    name=name,
                    code=self._optional_text(item.get("code") or item.get("板块代码")),
                    change_pct=change_pct,
                    rank=self._safe_int(item.get("rank")) or index,
                    source=source,
                    updated_at=self._optional_text(item.get("updated_at")),
                )
            )
        return normalized

    def _build_active_themes(
        self,
        items: List[RankedThemeItem],
        *,
        limit: int,
    ) -> List[MarketThemeItem]:
        """从涨跌幅为正的条目中挑出活跃主题，按涨幅排序后取前 ``limit`` 条。"""
        positive_items = [
            item for item in items if item.change_pct is not None and item.change_pct > 0
        ]
        positive_items.sort(key=lambda item: item.change_pct or 0, reverse=True)

        active: List[MarketThemeItem] = []
        for item in positive_items[:limit]:
            active.append(
                MarketThemeItem(
                    name=item.name,
                    code=item.code,
                    change_pct=item.change_pct,
                    rank=item.rank,
                    source=item.source,
                    updated_at=item.updated_at,
                    phase=self._phase_from_change(item.change_pct),
                    strength_score=self._strength_from_change(item.change_pct),
                    reason="industry/concept ranking gain",
                )
            )
        return active

    @staticmethod
    def _phase_from_change(value: Optional[float]) -> str:
        """根据涨跌幅判定主题阶段（加速 / 升温 / 降温 / 未知）。"""
        if value is None:
            return "unknown"
        # 3% 是经验阈值, 表示主题强度跨入"加速"档
        if value >= 3:
            return "accelerating"
        if value > 0:
            return "warming"
        return "cooling"

    @staticmethod
    def _strength_from_change(value: Optional[float]) -> Optional[int]:
        """由涨跌幅线性映射到 0-100 强度分（50 起步，±1% ⇒ ±8 分）。"""
        if value is None:
            return None
        return max(0, min(100, int(round(50 + value * 8))))

    @staticmethod
    def _format_trade_date(value: Any) -> Optional[str]:
        """把任意日期对象规整成 ISO 字符串或 ``None``。"""
        if value is None:
            return None
        if isinstance(value, date):
            return value.isoformat()
        text = str(value).strip()
        return text or None

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        """健壮地解析浮点值；带百分号后缀会自动剥离。"""
        if value is None:
            return None
        try:
            if isinstance(value, str):
                text = value.strip()
                if not text:
                    return None
                # 上游部分数据源会用 "1.23%" 表示涨跌幅, 兼容剥离
                if text.endswith("%"):
                    text = text[:-1].strip()
                return float(text)
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        """健壮地解析整数，无法解析时返回 ``None``。"""
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_text(value: Any) -> Optional[str]:
        """把任意值转成去除空白的字符串，空串/``None`` 一律返回 ``None``。"""
        if value is None:
            return None
        text = str(value).strip()
        return text or None
