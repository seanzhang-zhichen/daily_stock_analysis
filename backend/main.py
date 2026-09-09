# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 主调度程序
===================================

职责：
1. 协调各模块完成股票分析流程
2. 实现低并发的线程池调度
3. 全局异常处理，确保单股失败不影响整体
4. 提供命令行入口

使用方式：
    uv run --locked python backend/main.py              # 正常运行
    uv run --locked python backend/main.py --debug      # 调试模式
    uv run --locked python backend/main.py --dry-run    # 仅获取数据不分析

交易理念（已融入分析）：
- 严进策略：不追高，乖离率 > 5% 不买入
- 趋势交易：只做 MA5>MA10>MA20 多头排列
- 效率优先：关注筹码集中度好的股票
- 买点偏好：缩量回踩 MA5/MA10 支撑
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from dotenv import dotenv_values
from src.config import setup_env

_INITIAL_PROCESS_ENV = dict(os.environ)
setup_env()

# 代理配置 - 通过 USE_PROXY 环境变量控制，默认关闭
# GitHub Actions 环境自动跳过代理配置
if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
    # 本地开发环境，启用代理（可在 .env 中配置 PROXY_HOST 和 PROXY_PORT）
    proxy_host = os.getenv("PROXY_HOST", "127.0.0.1")
    proxy_port = os.getenv("PROXY_PORT", "10809")
    proxy_url = f"http://{proxy_host}:{proxy_port}"
    os.environ["http_proxy"] = proxy_url
    os.environ["https_proxy"] = proxy_url

import argparse
import logging
import sys
import time
import uuid
from datetime import date, datetime, timezone, timedelta

from data_provider.base import canonical_stock_code
from src.config import get_config, Config
from src.logging_config import setup_logging


logger = logging.getLogger(__name__)


def _resolve_portfolio_stock_codes(args: argparse.Namespace) -> Optional[List[str]]:
    """根据 ``--portfolio`` 参数加载券商持仓股票列表，替换默认自选股列表。

    Args:
        args: 解析后的命令行参数。

    Returns:
        股票代码列表；未传参数时返回 ``None`` 表示不替换。

    Raises:
        ValueError: 当 ``--portfolio`` 取了不支持的枚举值。
    """
    portfolio = str(getattr(args, "portfolio", "") or "").strip().lower()
    if not portfolio:
        return None
    if portfolio != "futu":
        raise ValueError(f"unsupported portfolio: {portfolio}")
    from src.brokers.futu.portfolio import load_futu_stock_codes

    codes = [canonical_stock_code(code) for code in load_futu_stock_codes()]
    codes = [code for code in codes if code]
    logger.info("portfolio=futu replaced stocks/STOCK_LIST with %d holdings", len(codes))
    return codes


_RUNTIME_ENV_FILE_KEYS = set()


def _get_active_env_path() -> Path:
    """返回当前激活的 ``.env`` 路径：优先 ``ENV_FILE`` 环境变量，否则用项目根目录下的 ``.env``。"""
    env_file = os.getenv("ENV_FILE")
    if env_file:
        return Path(env_file)
    return Path(__file__).resolve().parent.parent / ".env"


def _read_active_env_values() -> Optional[Dict[str, str]]:
    """读取当前 ``.env`` 中的键值集合。

    仅在解析/读取发生异常时返回 ``None``，文件不存在时返回空字典。
    """
    env_path = _get_active_env_path()
    if not env_path.exists():
        return {}

    try:
        values = dotenv_values(env_path)
    except Exception as exc:  # pragma: no cover - defensive branch
        logger.warning("读取配置文件 %s 失败，继续沿用当前环境变量: %s", env_path, exc)
        return None

    return {
        str(key): "" if value is None else str(value)
        for key, value in values.items()
        if key is not None
    }


_ACTIVE_ENV_FILE_VALUES = _read_active_env_values() or {}
# 记录首次初始化时进程环境中尚未出现的 key，用于后续判断「是 .env 引入的」键
_RUNTIME_ENV_FILE_KEYS = {
    key for key in _ACTIVE_ENV_FILE_VALUES
    if key not in _INITIAL_PROCESS_ENV
}

# setup_env() 已在模块顶层执行，此处只是标记以支持幂等调用
_env_bootstrapped = True


def _bootstrap_environment() -> None:
    """加载 ``.env`` 并应用可选的本地代理设置。

    设计为幂等：可被延迟导入路径（API / Bot 等消费者）安全重复调用。
    """
    global _env_bootstrapped
    if _env_bootstrapped:
        return

    from src.config import setup_env

    setup_env()

    if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
        proxy_host = os.getenv("PROXY_HOST", "127.0.0.1")
        proxy_port = os.getenv("PROXY_PORT", "10809")
        proxy_url = f"http://{proxy_host}:{proxy_port}"
        os.environ["http_proxy"] = proxy_url
        os.environ["https_proxy"] = proxy_url

    _env_bootstrapped = True


def _setup_bootstrap_logging(debug: bool = False) -> None:
    """在读取 config 之前先把日志桥接到 stderr，便于早期失败也能落盘。

    文件日志处理器会延迟到 ``config.log_dir`` 已知时再添加
    （由后续 ``setup_logging()`` 完成），避免健康运行硬编码输出目录。
    """
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    # 防止重复添加 stderr handler 导致同一行日志输出多份
    if not any(
        isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stderr
        for h in root.handlers
    ):
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(level)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        root.addHandler(handler)


def _setup_runtime_logging(log_dir: str, debug: bool = False) -> bool:
    """切换到配置好的文件日志，文件 IO 失败时降级为仅控制台输出。"""
    try:
        setup_logging(log_prefix="stock_analysis", debug=debug, log_dir=log_dir)
        return True
    except OSError as exc:
        logger.warning(
            "文件日志初始化失败，已降级为控制台日志输出；日志目录 %r 当前不可写或不可创建: %s。"
            "官方 Docker 镜像启动入口会自动修复默认挂载目录权限；若仍失败，"
            "请检查是否使用了 --user、只读挂载、rootless Docker 或 NFS 等限制写入的环境。",
            log_dir,
            exc,
        )
        return False


def _get_stock_analysis_pipeline():
    """为外部消费者懒加载 ``StockAnalysisPipeline``。

    同时确保环境/代理引导已执行，使从不调用 ``main()`` 的 API / Bot
    消费者也能应用 ``USE_PROXY``。
    """
    _bootstrap_environment()
    from src.core.pipeline import StockAnalysisPipeline as _Pipeline

    return _Pipeline


class _LazyPipelineDescriptor:
    """描述器：首次属性访问时延迟解析 ``StockAnalysisPipeline``，之后缓存结果。"""

    _resolved = None

    def __set_name__(self, owner, name):
        """记住所导出属性的名称，满足描述器协议完整性。"""
        self._name = name

    def __get__(self, obj, objtype=None):
        """首次访问时解析并缓存 ``StockAnalysisPipeline``，后续直接返回缓存。"""
        if self._resolved is None:
            self._resolved = _get_stock_analysis_pipeline()
        return self._resolved


class _ModuleExports:
    """延迟导出模块级兼容性符号的容器。"""

    StockAnalysisPipeline = _LazyPipelineDescriptor()


_exports = _ModuleExports()


def __getattr__(name: str):
    """为历史模块属性提供延迟兼容访问（仅 ``StockAnalysisPipeline``）。"""
    if name == "StockAnalysisPipeline":
        return _exports.StockAnalysisPipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _reload_env_file_values_preserving_overrides() -> None:
    """刷新 ``.env`` 管理的环境变量，同时保留进程环境变量的覆盖。"""
    global _RUNTIME_ENV_FILE_KEYS

    latest_values = _read_active_env_values()
    if latest_values is None:
        return

    # 仅把「启动后进程环境里没有」的 key 视作由 .env 拥有，避免覆盖外部注入
    managed_keys = {
        key for key in latest_values
        if key not in _INITIAL_PROCESS_ENV
    }

    for key in _RUNTIME_ENV_FILE_KEYS - managed_keys:
        os.environ.pop(key, None)

    for key in managed_keys:
        os.environ[key] = latest_values[key]

    _RUNTIME_ENV_FILE_KEYS = managed_keys


def parse_arguments() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='A股自选股智能分析系统',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
  uv run --locked python backend/main.py                    # 正常运行
  uv run --locked python backend/main.py --debug            # 调试模式
  uv run --locked python backend/main.py --dry-run          # 仅获取数据，不进行 AI 分析
  uv run --locked python backend/main.py --stocks 600519,000001  # 指定分析特定股票
  uv run --locked python backend/main.py --no-notify        # 不发送推送通知
  uv run --locked python backend/main.py --check-notify     # 检查通知配置，不发送通知
  uv run --locked python backend/main.py --single-notify    # 启用单股推送模式（每分析完一只立即推送）
  uv run --locked python backend/main.py --schedule         # 启用定时任务模式
  uv run --locked python backend/main.py --market-review    # 仅运行大盘复盘
        '''
    )

    parser.add_argument(
        '--debug',
        action='store_true',
        help='启用调试模式，输出详细日志'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='仅获取数据，不进行 AI 分析'
    )

    parser.add_argument(
        '--stocks',
        type=str,
        help='指定要分析的股票代码，逗号分隔（覆盖配置文件）'
    )

    parser.add_argument(
        '--portfolio',
        type=str.lower,
        choices=('futu',),
        default=None,
        help='Use holdings from a supported broker portfolio (currently futu)',
    )

    parser.add_argument(
        '--no-notify',
        action='store_true',
        help='不发送推送通知'
    )

    parser.add_argument(
        '--check-notify',
        action='store_true',
        help='只读检查通知渠道配置，不发送通知'
    )

    parser.add_argument(
        '--single-notify',
        action='store_true',
        help='启用单股推送模式：每分析完一只股票立即推送，而不是汇总推送'
    )

    parser.add_argument(
        '--workers',
        type=int,
        default=None,
        help='并发线程数（默认使用配置值）'
    )

    parser.add_argument(
        '--schedule',
        action='store_true',
        help='启用定时任务模式，每日定时执行'
    )

    parser.add_argument(
        '--no-run-immediately',
        action='store_true',
        help='定时任务启动时不立即执行一次'
    )

    parser.add_argument(
        '--market-review',
        action='store_true',
        help='仅运行大盘复盘分析'
    )

    parser.add_argument(
        '--no-market-review',
        action='store_true',
        help='跳过大盘复盘分析'
    )

    parser.add_argument(
        '--force-run',
        action='store_true',
        help='跳过交易日检查，强制执行全量分析（Issue #373）'
    )

    parser.add_argument(
        '--sync-daily-quotes',
        action='store_true',
        help='仅同步全量日线行情（目前支持 A 股 cn）'
    )

    parser.add_argument(
        '--market',
        type=str,
        default=None,
        help='全量日线行情同步市场，逗号分隔；默认 cn'
    )

    parser.add_argument(
        '--sync-date',
        type=str,
        default=None,
        help='全量日线行情同步目标交易日，格式 YYYY-MM-DD'
    )

    parser.add_argument(
        '--sync-limit',
        type=int,
        default=None,
        help='全量日线行情同步最大股票数，主要用于调试'
    )

    parser.add_argument(
        '--webui',
        action='store_true',
        help='启动 Web 管理界面'
    )

    parser.add_argument(
        '--webui-only',
        action='store_true',
        help='仅启动 Web 服务，不执行自动分析'
    )

    parser.add_argument(
        '--serve',
        action='store_true',
        help='启动 FastAPI 后端服务（同时执行分析任务）'
    )

    parser.add_argument(
        '--serve-only',
        action='store_true',
        help='仅启动 FastAPI 后端服务，不自动执行分析'
    )

    parser.add_argument(
        '--port',
        type=int,
        default=8000,
        help='FastAPI 服务端口（默认 8000）'
    )

    parser.add_argument(
        '--host',
        type=str,
        default='0.0.0.0',
        help='FastAPI 服务监听地址（默认 0.0.0.0）'
    )

    parser.add_argument(
        '--no-context-snapshot',
        action='store_true',
        help='不保存分析上下文快照'
    )

    # === Backtest ===
    parser.add_argument(
        '--backtest',
        action='store_true',
        help='运行回测（对历史分析结果进行评估）'
    )

    parser.add_argument(
        '--backtest-code',
        type=str,
        default=None,
        help='仅回测指定股票代码'
    )

    parser.add_argument(
        '--backtest-days',
        type=int,
        default=None,
        help='回测评估窗口（交易日数，默认使用配置）'
    )

    parser.add_argument(
        '--backtest-force',
        action='store_true',
        help='强制回测（即使已有回测结果也重新计算）'
    )

    return parser.parse_args()


def _compute_trading_day_filter(
    config: Config,
    args: argparse.Namespace,
    stock_codes: List[str],
) -> Tuple[List[str], Optional[str], bool]:
    """计算交易日过滤后的股票列表，以及本次大盘复盘要使用的有效市场区域（Issue #373）。

    Returns:
        ``(filtered_codes, effective_region, should_skip_all)``
        - ``effective_region`` 为 ``None`` 表示使用配置默认市场（未启用交易日检查）。
        - ``effective_region`` 为 ``""`` 表示相关市场都休市，跳过大盘复盘。
        - ``should_skip_all``：在没有可分析股票且大盘复盘也跳过时整轮跳过。
    """
    force_run = getattr(args, 'force_run', False)
    # 强制执行或未启用交易日检查时直接放行，避免影响常规本地开发
    if force_run or not getattr(config, 'trading_day_check_enabled', True):
        return (stock_codes, None, False)

    from src.core.trading_calendar import (
        get_market_for_stock,
        get_open_markets_today,
        compute_effective_region,
    )

    open_markets = get_open_markets_today()
    # 仅保留当前正在开市的股票所属市场；未识别市场（None）默认放行
    filtered_codes = []
    for code in stock_codes:
        mkt = get_market_for_stock(code)
        if mkt in open_markets or mkt is None:
            filtered_codes.append(code)

    if config.market_review_enabled and not getattr(args, 'no_market_review', False):
        effective_region = compute_effective_region(
            getattr(config, 'market_review_region', 'cn') or 'cn', open_markets
        )
    else:
        effective_region = None

    should_skip_all = (not filtered_codes) and (effective_region or '') == ''
    return (filtered_codes, effective_region, should_skip_all)


def _run_market_review_with_shared_lock(
    config: Config,
    run_market_review_func: Callable[..., Optional[str]],
    **kwargs: Any,
) -> Optional[str]:
    """在跨入口的大盘复盘共享执行锁下运行 ``run_market_review_func``。

    同一时刻只允许一个调用真正执行大盘复盘，其他入口会直接放弃以避免
    「WebUI + CLI 定时任务」并发触发导致的重复推送。
    """
    from src.core.market_review_lock import (
        release_market_review_lock,
        try_acquire_market_review_lock,
    )

    lock_token = try_acquire_market_review_lock(config)
    if lock_token is None:
        logger.warning("大盘复盘正在执行中，跳过本次大盘复盘")
        return None

    try:
        return run_market_review_func(**kwargs)
    finally:
        # 始终释放锁，防止其它调用永远拿不到锁
        release_market_review_lock(lock_token)


def _parse_sync_target_date(raw_value: Optional[str]) -> Optional[date]:
    """解析 ``--sync-date`` 参数，格式必须为 ``YYYY-MM-DD``。"""
    value = (raw_value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"--sync-date must use YYYY-MM-DD format: {raw_value!r}") from exc


def run_daily_quote_sync(config: Config, args: argparse.Namespace):
    """全市场日线行情同步入口：执行一次并返回统计信息。"""
    from src.services.daily_quote_sync_service import DailyQuoteSyncService

    raw_markets = getattr(args, "market", None) or ",".join(
        getattr(config, "daily_quote_sync_markets", ["cn"]) or ["cn"]
    )
    markets = [m.strip() for m in raw_markets.split(",") if m.strip()]
    return DailyQuoteSyncService(config=config).run(
        markets=markets,
        target_date=_parse_sync_target_date(getattr(args, "sync_date", None)),
        max_workers=getattr(args, "workers", None),
        limit=getattr(args, "sync_limit", None),
        force_run=getattr(args, "force_run", False),
    )


def run_per_user_scheduled_analysis(config: Config, args: argparse.Namespace) -> None:
    """按用户分桶执行定时分析并发送个人推送（Phase 3）。

    仅处理开启「每日推送」且自选股列表非空的用户。
    单用户失败通过日志记录后继续执行其他用户，不影响整体调度。
    """
    from src.core.pipeline import StockAnalysisPipeline
    from src.notification import NotificationService
    from src.storage import get_db as _get_storage_db
    from src.users.config import is_user_mode_enabled
    from src.users.email import get_email_backend
    from src.users.notification_delivery import (
        DailyEmailContext,
        dispatch_user_webhook,
        send_daily_email,
    )
    from src.users.notification_prefs import get_prefs, get_users_with_daily_push
    from src.users.plans import resolve_user_plan
    from src.users.repository import get_user_by_id
    from src.users.watchlist import list_stocks

    if not is_user_mode_enabled():
        return

    db = _get_storage_db()
    with db.session_scope() as session:
        user_ids = get_users_with_daily_push(session)

    if not user_ids:
        logger.info("[per-user 调度] 无开启每日推送的用户，跳过")
        return

    logger.info("[per-user 调度] 开始按用户分析，共 %d 个用户", len(user_ids))

    email_backend = get_email_backend()
    report_type = getattr(config, "report_type", "simple")
    for user_id in user_ids:
        try:
            with db.session_scope() as session:
                user = get_user_by_id(session, user_id)
                # 不活跃账号一律跳过，避免对禁用/未通过审核的账号推送
                if user is None or getattr(user, "status", "active") != "active":
                    continue
                watchlist = list_stocks(session, user_id=user_id)
                prefs = get_prefs(session, user_id=user_id)
                plan = resolve_user_plan(session, user)
                user_email = user.email

            if not plan.is_pro:
                # 非 Pro 用户没有每日自动分析权益，仅在日志中说明
                logger.info("[per-user 调度] 用户 %d 当前非 Pro，跳过每日自动分析", user_id)
                continue

            if not watchlist:
                logger.info("[per-user 调度] 用户 %d 自选股为空，跳过", user_id)
                continue

            stock_codes_user = [item.stock_code for item in watchlist]
            filtered_codes, _, should_skip = _compute_trading_day_filter(
                config, args, stock_codes_user
            )
            if should_skip or not filtered_codes:
                logger.info("[per-user 调度] 用户 %d 今日无可分析股票，跳过", user_id)
                continue

            logger.info(
                "[per-user 调度] 用户 %d 开始分析 %d 只股票: %s",
                user_id,
                len(filtered_codes),
                filtered_codes,
            )

            pipeline = StockAnalysisPipeline(
                config=config,
                max_workers=args.workers,
                query_id=uuid.uuid4().hex,
                query_source="scheduled",
                user_id=user_id,
            )
            results = pipeline.run(
                stock_codes=filtered_codes,
                dry_run=getattr(args, "dry_run", False),
                send_notification=False,
            )

            if not results:
                logger.info("[per-user 调度] 用户 %d 分析结果为空，跳过推送", user_id)
                continue

            notifier = NotificationService()
            report_content = notifier.generate_aggregate_report(results, report_type)
            today_str = datetime.now(tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
            subject = f"[DSA] 您的自选股分析报告 {today_str}"

            # 邮件推送 (HTML + 一键退订)
            if plan.is_pro and prefs.email_enabled and user_email:
                ok = send_daily_email(
                    DailyEmailContext(
                        user_id=user_id,
                        user_email=user_email,
                        subject=subject,
                        report_markdown=report_content,
                    ),
                    backend=email_backend,
                )
                if ok:
                    logger.info(
                        "[per-user 调度] 用户 %d 报告已发送至 %s",
                        user_id,
                        user_email,
                    )
            else:
                logger.info(
                    "[per-user 调度] 用户 %d 非 Pro、邮件推送已关闭或无邮箱，跳过邮件",
                    user_id,
                )

            # Webhook 推送 (仅 Pro / 已配置 webhook 的用户)
            if plan.can_webhook and prefs.webhook_url and prefs.webhook_type:
                dispatch_user_webhook(
                    prefs,
                    can_webhook=plan.can_webhook,
                    content=report_content,
                    title=subject,
                )

        except Exception:
            # 单用户失败不应拖垮整轮调度，统一记录日志后继续下一个用户
            logger.exception("[per-user 调度] 用户 %d 分析失败，已跳过", user_id)

    logger.info("[per-user 调度] 全部用户处理完毕")


def run_plan_lifecycle_task(config: Config, args: argparse.Namespace) -> None:
    """每日调度入口：到期前 7/3/1 天发送续费提醒，过期当日自动降级到 free 档（Phase 2 + Phase 4 收尾）。

    单用户失败通过日志记录后继续, 不影响其它用户。``--dry-run`` 时只扫描不写库。
    """
    try:
        from src.users.config import is_user_mode_enabled
        from src.users.plan_lifecycle import run_plan_lifecycle_check
    except Exception:
        logger.exception("[plan-lifecycle] 模块加载失败, 跳过本轮检查")
        return

    if not is_user_mode_enabled():
        return

    dry_run = bool(getattr(args, "dry_run", False))
    try:
        summary = run_plan_lifecycle_check(dry_run=dry_run)
    except Exception:
        logger.exception("[plan-lifecycle] 执行失败")
        return

    logger.info(
        "[plan-lifecycle] reminders_sent=%d reminders_skipped=%d downgraded=%d downgrade_skipped=%d",
        summary.reminders_sent,
        summary.reminders_skipped,
        summary.downgraded,
        summary.downgrade_skipped,
    )


def run_account_lifecycle_task(config: Config, args: argparse.Namespace) -> None:
    """每日调度入口：处理账号注销冷静期到期与个人数据物理清除（Phase 6 PIPL）。

    - 软删冷静期已满（7 天）的账号 (status: active -> deleted)。
    - 物理清除软删超过保留期（30 天）的个人数据（保留订单/发票）。
    ``--dry-run`` 时只扫描不写库。
    """
    try:
        from src.users.config import is_user_mode_enabled
        from src.users.deletion import execute_pending_deletions, cleanup_deleted_users
        from src.storage import get_db
    except Exception:
        logger.exception("[account-lifecycle] 模块加载失败, 跳过本轮检查")
        return

    if not is_user_mode_enabled():
        return

    dry_run = bool(getattr(args, "dry_run", False))
    db = next(get_db())
    try:
        soft_deleted = execute_pending_deletions(db) if not dry_run else 0
        purged = cleanup_deleted_users(db, dry_run=dry_run)
        logger.info(
            "[account-lifecycle] soft_deleted=%d purged=%d dry_run=%s",
            soft_deleted, purged, dry_run,
        )
    except Exception:
        logger.exception("[account-lifecycle] 执行失败")
    finally:
        db.close()


def run_full_analysis(
    config: Config,
    args: argparse.Namespace,
    stock_codes: Optional[List[str]] = None
):
    """执行完整的分析流程（个股 + 大盘复盘）。

    用于手动运行和兼容全局分析入口；每日定时任务只处理用户自选股。
    """
    # Broker 加载属于 CLI 合约边界：配置错误和 OpenD 异常必须冒泡到 main()
    # 以让一次性命令以非零状态退出，而不是被吞掉静默返回。
    portfolio_codes = _resolve_portfolio_stock_codes(args)
    portfolio_is_empty = portfolio_codes == []

    # 把 pipeline 导入放在宽泛 try/except 之外，使模块加载失败（如导入异常）
    # 直接抛给调用方而不是被静默吞掉。
    from src.core.market_review import run_market_review
    from src.core.pipeline import StockAnalysisPipeline

    try:
        if portfolio_codes is not None:
            stock_codes = portfolio_codes

        # 当全局分析入口未显式给出股票列表时，热加载最新的 STOCK_LIST
        if stock_codes is None:
            config.refresh_stock_list()

        # Issue #373: 交易日过滤（按股 / 按市场分别独立判断）
        effective_codes = stock_codes if stock_codes is not None else config.stock_list
        filtered_codes, effective_region, should_skip = _compute_trading_day_filter(
            config, args, effective_codes
        )
        if should_skip:
            if portfolio_is_empty:
                logger.info("真实账户中无符合条件的 Futu 持仓，本轮跳过执行。")
            else:
                logger.info(
                    "今日所有相关市场均为非交易日，跳过执行。可使用 --force-run 强制执行。"
                )
            return True
        if set(filtered_codes) != set(effective_codes):
            skipped = set(effective_codes) - set(filtered_codes)
            logger.info("今日休市股票已跳过: %s", skipped)
        stock_codes = filtered_codes

        # 命令行参数 --single-notify 覆盖配置（#55）
        if getattr(args, 'single_notify', False):
            config.single_stock_notify = True

        # Issue #190: 个股与大盘复盘合并推送
        merge_notification = (
            getattr(config, 'merge_email_notification', False)
            and config.market_review_enabled
            and not getattr(args, 'no_market_review', False)
            and not config.single_stock_notify
        )

        # 创建调度器
        save_context_snapshot = None
        if getattr(args, 'no_context_snapshot', False):
            save_context_snapshot = False
        query_id = uuid.uuid4().hex
        pipeline = StockAnalysisPipeline(
            config=config,
            max_workers=args.workers,
            query_id=query_id,
            query_source="cli",
            save_context_snapshot=save_context_snapshot
        )

        # 1. 运行个股分析
        if portfolio_codes is not None and not stock_codes:
            logger.info("真实账户中无符合条件的 Futu 持仓，跳过个股分析。")
            results = []
        else:
            results = pipeline.run(
                stock_codes=stock_codes,
                dry_run=args.dry_run,
                send_notification=not args.no_notify,
                merge_notification=merge_notification
            )

        # Issue #128: 分析间隔 - 在个股分析和大盘分析之间添加延迟
        # 大盘复盘通常会瞬时调用多个 LLM，延迟能错开限流窗口
        analysis_delay = getattr(config, 'analysis_delay', 0)
        if (
            analysis_delay > 0
            and config.market_review_enabled
            and not args.no_market_review
            and effective_region != ''
        ):
            logger.info(f"等待 {analysis_delay} 秒后执行大盘复盘（避免API限流）...")
            time.sleep(analysis_delay)

        # 2. 运行大盘复盘（如果启用且不是仅个股模式）
        market_report = ""
        if (
            config.market_review_enabled
            and not args.no_market_review
            and effective_region != ''
        ):
            review_result = _run_market_review_with_shared_lock(
                config,
                run_market_review,
                notifier=pipeline.notifier,
                analyzer=pipeline.analyzer,
                search_service=pipeline.search_service,
                send_notification=not args.no_notify,
                merge_notification=merge_notification,
                override_region=effective_region,
            )
            # 如果有结果，赋值给 market_report 用于后续飞书文档生成
            if review_result:
                market_report = review_result

        # Issue #190: 合并推送（个股+大盘复盘）
        if merge_notification and (results or market_report) and not args.no_notify:
            parts = []
            if market_report:
                parts.append(f"# 📈 大盘复盘\n\n{market_report}")
            if results:
                dashboard_content = pipeline.notifier.generate_aggregate_report(
                    results,
                    getattr(config, 'report_type', 'simple'),
                )
                parts.append(f"# 🚀 个股决策仪表盘\n\n{dashboard_content}")
            if parts:
                combined_content = "\n\n---\n\n".join(parts)
                if pipeline.notifier.is_available():
                    if pipeline.notifier.send(combined_content, email_send_to_all=True, route_type="report"):
                        logger.info("已合并推送（个股+大盘复盘）")
                    else:
                        logger.warning("合并推送失败")

        # 输出摘要
        if results:
            logger.info("\n===== 分析结果摘要 =====")
            for r in sorted(results, key=lambda x: x.sentiment_score, reverse=True):
                emoji = r.get_emoji()
                logger.info(
                    f"{emoji} {r.name}({r.code}): {r.operation_advice} | "
                    f"评分 {r.sentiment_score} | {r.trend_prediction}"
                )

        logger.info("\n任务执行完成")

        # === 新增：生成飞书云文档 ===
        try:
            from src.feishu_doc import FeishuDocManager

            feishu_doc = FeishuDocManager()
            if feishu_doc.is_configured() and (results or market_report):
                logger.info("正在创建飞书云文档...")

                # 1. 准备标题 "01-01 13:01大盘复盘"
                tz_cn = timezone(timedelta(hours=8))
                now = datetime.now(tz_cn)
                doc_title = f"{now.strftime('%Y-%m-%d %H:%M')} 大盘复盘"

                # 2. 准备内容 (拼接个股分析和大盘复盘)
                full_content = ""

                # 添加大盘复盘内容（如果有）
                if market_report:
                    full_content += f"# 📈 大盘复盘\n\n{market_report}\n\n---\n\n"

                # 添加个股决策仪表盘（使用 NotificationService 生成，按 report_type 分支）
                if results:
                    dashboard_content = pipeline.notifier.generate_aggregate_report(
                        results,
                        getattr(config, 'report_type', 'simple'),
                    )
                    full_content += f"# 🚀 个股决策仪表盘\n\n{dashboard_content}"

                # 3. 创建文档
                doc_url = feishu_doc.create_daily_doc(doc_title, full_content)
                if doc_url:
                    logger.info(f"飞书云文档创建成功: {doc_url}")
                    # 可选：将文档链接也推送到群里
                    if not args.no_notify:
                        pipeline.notifier.send(
                            f"[{now.strftime('%Y-%m-%d %H:%M')}] 复盘文档创建成功: {doc_url}",
                            route_type="report",
                        )

        except Exception as e:
            logger.error(f"飞书文档生成失败: {e}")

        # === Auto backtest ===
        try:
            if getattr(config, 'backtest_enabled', False):
                from src.services.backtest_service import BacktestService

                logger.info("开始自动回测...")
                service = BacktestService()
                stats = service.run_backtest(
                    force=False,
                    eval_window_days=getattr(config, 'backtest_eval_window_days', 10),
                    min_age_days=getattr(config, 'backtest_min_age_days', 14),
                    limit=200,
                )
                try:
                    from src.services.skill_opinion_outcome_service import SkillOpinionOutcomeService
                    skill_stats = SkillOpinionOutcomeService().evaluate_pending(limit=200)
                    logger.info("策略意见结果评估完成: %s", skill_stats)
                except Exception as exc:
                    logger.warning("策略意见结果评估失败，不影响主任务: %s", exc)
                logger.info(
                    f"自动回测完成: processed={stats.get('processed')} saved={stats.get('saved')} "
                    f"completed={stats.get('completed')} insufficient={stats.get('insufficient')} errors={stats.get('errors')}"
                )
        except Exception as e:
            logger.warning(f"自动回测失败（已忽略）: {e}")

        return True

    except Exception as e:
        logger.exception(f"分析流程执行失败: {e}")
        return False


def start_api_server(
    host: str,
    port: int,
    config: Config,
    serve_frontend: bool = False,
) -> None:
    """在后台线程启动 FastAPI 服务。

    Args:
        host: 监听地址。
        port: 监听端口。
        config: 配置对象。
        serve_frontend: 是否托管 WebUI 静态资源。
    """
    import threading
    import uvicorn
    from api.app import create_app

    app = create_app(serve_frontend=serve_frontend)

    def run_server():
        """在后台服务线程中运行 FastAPI 应用。"""
        level_name = (config.log_level or "INFO").lower()
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level=level_name,
            log_config=None,
        )

    # daemon=True 让主线程退出时强制结束 Web 服务，避免容器退出卡住
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    logger.info(f"FastAPI 服务已启动: http://{host}:{port}")


def _should_prepare_webui_frontend_assets(args, config: Config) -> bool:
    """判断启动时是否需要准备 WebUI 前端静态资源。

    同时覆盖显式 ``--webui/--webui-only`` 与旧版 ``WEBUI_ENABLED`` 环境变量路径。
    """
    explicit_webui_requested = bool(
        getattr(args, "webui", False) or getattr(args, "webui_only", False)
    )
    legacy_webui_enabled = bool(
        getattr(config, "webui_enabled", False)
        and not (getattr(args, "serve", False) or getattr(args, "serve_only", False))
    )
    return explicit_webui_requested or legacy_webui_enabled


def start_bot_stream_clients(config: Config) -> None:
    """根据配置启动机器人 Stream 客户端（如钉钉 / 飞书 WebSocket 长连接）。"""
    # 启动钉钉 Stream 客户端
    if config.dingtalk_stream_enabled:
        try:
            from bot.platforms import start_dingtalk_stream_background, DINGTALK_STREAM_AVAILABLE
            if DINGTALK_STREAM_AVAILABLE:
                if start_dingtalk_stream_background():
                    logger.info("[Main] Dingtalk Stream client started in background.")
                else:
                    logger.warning("[Main] Dingtalk Stream client failed to start.")
            else:
                logger.warning("[Main] Dingtalk Stream enabled but SDK is missing.")
                logger.warning("[Main] Run: uv sync --locked")
        except Exception as exc:
            logger.error(f"[Main] Failed to start Dingtalk Stream client: {exc}")

    # 启动飞书 Stream 客户端
    if getattr(config, 'feishu_stream_enabled', False):
        try:
            from bot.platforms import start_feishu_stream_background, FEISHU_SDK_AVAILABLE
            if FEISHU_SDK_AVAILABLE:
                if start_feishu_stream_background():
                    logger.info("[Main] Feishu Stream client started in background.")
                else:
                    logger.warning("[Main] Feishu Stream client failed to start.")
            else:
                logger.warning("[Main] Feishu Stream enabled but SDK is missing.")
                logger.warning("[Main] Run: uv sync --locked")
        except Exception as exc:
            logger.error(f"[Main] Failed to start Feishu Stream client: {exc}")


def _warn_scheduled_stock_codes_ignored(stock_codes: Optional[List[str]]) -> None:
    """定时运行只处理用户自选股，提醒用户传入的全局 ``--stocks`` 不会生效。"""
    if stock_codes is not None:
        logger.warning(
            "定时模式下检测到 --stocks 参数；每日定时分析仅处理开启每日推送的用户自选股，"
            "不会使用启动时股票快照或全局 STOCK_LIST。"
        )


def _reload_runtime_config() -> Config:
    """定时任务专用：从最新持久化的 ``.env`` 重载配置。"""
    _reload_env_file_values_preserving_overrides()
    Config.reset_instance()
    return get_config()


def _build_schedule_time_provider(default_schedule_time: str):
    """构造调度时间解析器，按以下优先级从最新配置中读取：

    1. 进程级环境变量覆盖（启动前设置）—— 优先尊重它。
    2. 持久化配置文件值（WebUI 写入）—— 使用它。
    3. 系统默认值 ``"18:00"`` —— WebUI 清空时也能正确重置为默认。
    """
    from src.core.config_manager import ConfigManager

    _SYSTEM_DEFAULT_SCHEDULE_TIME = "18:00"
    manager = ConfigManager()

    def _provider() -> str:
        """从进程环境、持久化配置或默认值中解析调度时间。"""
        if "SCHEDULE_TIME" in _INITIAL_PROCESS_ENV:
            return os.getenv("SCHEDULE_TIME", default_schedule_time)

        config_map = manager.read_config_map()
        schedule_time = (config_map.get("SCHEDULE_TIME", "") or "").strip()
        if schedule_time:
            return schedule_time
        return _SYSTEM_DEFAULT_SCHEDULE_TIME

    return _provider


def main() -> int:
    """主入口函数。

    Returns:
        进程退出码（0 表示成功）。
    """
    # 解析命令行参数
    args = parse_arguments()

    # 在配置加载前先初始化 bootstrap 日志，确保早期失败也能落盘
    try:
        _setup_bootstrap_logging(debug=args.debug)
    except Exception as exc:
        # bootstrap 日志失败时退回 basicConfig，绝不让 CLI 直接异常退出
        logging.basicConfig(
            level=logging.DEBUG if getattr(args, "debug", False) else logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            stream=sys.stderr,
        )
        logger.warning("Bootstrap 日志初始化失败，已回退到 stderr: %s", exc)

    # 加载配置（在 bootstrap logging 之后执行，确保异常有日志）
    try:
        config = get_config()
    except Exception as exc:
        logger.exception("加载配置失败: %s", exc)
        return 1

    # 配置日志（输出到控制台和文件）
    try:
        _setup_runtime_logging(config.log_dir, debug=args.debug)
    except Exception as exc:
        logger.exception("切换到配置日志目录失败: %s", exc)
        return 1

    logger.info("=" * 60)
    logger.info("A股自选股智能分析系统 启动")
    logger.info(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    from src.llm.observability import setup_llm_observability
    setup_llm_observability()

    # 验证配置
    warnings = config.validate()
    for warning in warnings:
        logger.warning(warning)

    if getattr(args, "check_notify", False):
        from src.services.notification_diagnostics import (
            format_notification_diagnostics,
            run_notification_diagnostics,
        )

        result = run_notification_diagnostics(config)
        print(format_notification_diagnostics(result))
        return 0 if result.ok else 1

    # 解析股票列表（统一为大写 Issue #355）
    stock_codes = None
    if args.stocks:
        stock_codes = [canonical_stock_code(c) for c in args.stocks.split(',') if (c or "").strip()]
        logger.info(f"使用命令行指定的股票列表: {stock_codes}")

    prepare_webui_frontend = _should_prepare_webui_frontend_assets(args, config)

    # === 处理 --webui / --webui-only 参数，映射到 --serve / --serve-only ===
    if args.webui:
        args.serve = True
    if args.webui_only:
        args.serve_only = True

    # 兼容旧版 WEBUI_ENABLED 环境变量
    if config.webui_enabled and not (args.serve or args.serve_only):
        args.serve = True

    # === 启动 Web 服务 (如果启用) ===
    start_serve = (args.serve or args.serve_only) and os.getenv("GITHUB_ACTIONS") != "true"

    # 兼容旧版 WEBUI_HOST/WEBUI_PORT：如果用户未通过 --host/--port 指定，则使用旧变量
    if start_serve:
        if args.host == '0.0.0.0' and os.getenv('WEBUI_HOST'):
            args.host = os.getenv('WEBUI_HOST')
        if args.port == 8000 and os.getenv('WEBUI_PORT'):
            args.port = int(os.getenv('WEBUI_PORT'))

    bot_clients_started = False
    if start_serve:
        if prepare_webui_frontend:
            from src.webui_frontend import prepare_webui_frontend_assets

            if not prepare_webui_frontend_assets():
                logger.warning("前端静态资源未就绪，继续启动 FastAPI 服务（Web 页面可能不可用）")
        else:
            logger.info("跳过 WebUI 前端静态资源准备（不会安装依赖或构建前端）")
        try:
            start_api_server(
                host=args.host,
                port=args.port,
                config=config,
                serve_frontend=prepare_webui_frontend,
            )
            bot_clients_started = True
        except Exception as e:
            logger.error(f"启动 FastAPI 服务失败: {e}")

    if bot_clients_started:
        start_bot_stream_clients(config)

    # === 仅服务模式：不自动执行分析 ===
    if args.serve_only:
        if prepare_webui_frontend:
            logger.info("模式: 仅 WebUI 服务")
            logger.info(f"WebUI 服务运行中: http://{args.host}:{args.port}")
        else:
            logger.info("模式: 仅 API 服务")
            logger.info(f"API 服务运行中: http://{args.host}:{args.port}")
        logger.info("通过 /api/v1/analysis/analyze 接口触发分析")
        logger.info(f"API 文档: http://{args.host}:{args.port}/docs")
        logger.info("按 Ctrl+C 退出...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("\n用户中断，程序退出")
        return 0

    try:
        # 模式0: 回测
        if getattr(args, 'sync_daily_quotes', False):
            logger.info("模式: 全量日线行情同步")
            stats = run_daily_quote_sync(config, args)
            logger.info("全量日线行情同步完成: %s", stats.to_dict())
            return 0

        if getattr(args, 'backtest', False):
            logger.info("模式: 回测")
            from src.services.backtest_service import BacktestService

            service = BacktestService()
            stats = service.run_backtest(
                code=getattr(args, 'backtest_code', None),
                force=getattr(args, 'backtest_force', False),
                eval_window_days=getattr(args, 'backtest_days', None),
            )
            logger.info(
                f"回测完成: processed={stats.get('processed')} saved={stats.get('saved')} "
                f"completed={stats.get('completed')} insufficient={stats.get('insufficient')} errors={stats.get('errors')}"
            )
            return 0

        # 模式1: 仅大盘复盘
        if args.market_review:
            from src.core.market_review import run_market_review
            from src.core.market_review_runtime import build_market_review_runtime

            # Issue #373: 仅大盘复盘模式的交易日判断。
            # 这里**不要**复用 _compute_trading_day_filter，因为它内部会判断
            # config.market_review_enabled，会误把"用户显式传 --market-review
            # 但配置里关闭了"的合法调用给拦截掉。
            effective_region = None
            if not getattr(args, 'force_run', False) and getattr(config, 'trading_day_check_enabled', True):
                from src.core.trading_calendar import get_open_markets_today, compute_effective_region as _compute_region
                open_markets = get_open_markets_today()
                effective_region = _compute_region(
                    getattr(config, 'market_review_region', 'cn') or 'cn', open_markets
                )
                if effective_region == '':
                    logger.info("今日大盘复盘相关市场均为非交易日，跳过执行。可使用 --force-run 强制执行。")
                    return 0

            logger.info("模式: 仅大盘复盘")
            notifier, analyzer, search_service = build_market_review_runtime(config)

            _run_market_review_with_shared_lock(
                config,
                run_market_review,
                notifier=notifier,
                analyzer=analyzer,
                search_service=search_service,
                send_notification=not args.no_notify,
                override_region=effective_region,
            )
            return 0

        # 模式2: 定时任务模式
        if args.schedule or config.schedule_enabled:
            logger.info("模式: 定时任务")
            logger.info(f"每日执行时间: {config.schedule_time}")

            # 判断是否立即运行：
            # 命令行参数 --no-run-immediately 若出现则覆盖配置；
            # 否则使用配置（默认 True）。
            should_run_immediately = config.schedule_run_immediately
            if getattr(args, 'no_run_immediately', False):
                should_run_immediately = False

            logger.info(f"启动时立即执行: {should_run_immediately}")

            from src.scheduler import run_with_schedule
            _warn_scheduled_stock_codes_ignored(stock_codes)
            if getattr(args, "portfolio", None):
                logger.warning(
                    "定时模式下检测到 --portfolio 参数；当前多租户调度仅处理开启每日推送的用户自选股，"
                    "不会读取券商真实持仓。"
                )
            schedule_time_provider = _build_schedule_time_provider(config.schedule_time)

            def scheduled_task():
                """使用最新配置运行定时分析及账号生命周期相关任务。"""
                runtime_config = _reload_runtime_config()
                if getattr(runtime_config, 'daily_quote_sync_enabled', False):
                    try:
                        stats = run_daily_quote_sync(runtime_config, args)
                        logger.info("定时全量日线行情同步完成: %s", stats.to_dict())
                    except Exception as exc:
                        # 行情同步失败不应中断随后的用户分析任务
                        logger.exception("定时全量日线行情同步失败，继续执行后续定时任务: %s", exc)
                run_per_user_scheduled_analysis(runtime_config, args)
                run_plan_lifecycle_task(runtime_config, args)
                run_account_lifecycle_task(runtime_config, args)

            background_tasks = []
            if getattr(config, 'agent_event_monitor_enabled', False):
                from src.services.alert_worker import AlertWorker

                interval_minutes = max(1, getattr(config, 'agent_event_monitor_interval_minutes', 5))
                alert_worker = AlertWorker(config_provider=_reload_runtime_config)

                def event_monitor_task():
                    """运行一轮告警 Worker 轮询，并记录触发的提醒数量。"""
                    stats = alert_worker.run_once()
                    triggered_count = stats.get("triggered", 0)
                    if triggered_count:
                        logger.info("[EventMonitor] 本轮触发 %d 条提醒", triggered_count)

                background_tasks.append({
                    "task": event_monitor_task,
                    "interval_seconds": interval_minutes * 60,
                    "run_immediately": True,
                    "name": "agent_event_monitor",
                })

            run_with_schedule(
                task=scheduled_task,
                schedule_time=config.schedule_time,
                run_immediately=should_run_immediately,
                background_tasks=background_tasks,
                schedule_time_provider=schedule_time_provider,
            )
            return 0

        # 模式3: 正常单次运行
        if config.run_immediately:
            run_full_analysis(config, args, stock_codes)
        else:
            logger.info("配置为不立即运行分析 (RUN_IMMEDIATELY=false)")

        logger.info("\n程序执行完成")

        # 如果启用了服务且是非定时任务模式，保持程序运行
        keep_running = start_serve and not (args.schedule or config.schedule_enabled)
        if keep_running:
            logger.info("API 服务运行中 (按 Ctrl+C 退出)...")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass

        return 0

    except KeyboardInterrupt:
        logger.info("\n用户中断，程序退出")
        return 130

    except Exception as e:
        logger.exception(f"程序执行失败: {e}")
        return 1


if __name__ == "__main__":
    exit_code = main()
    try:
        from src.llm.observability import flush_llm_observability

        # 进程退出前尽量把 LLM 可观测性数据落盘，避免丢失
        flush_llm_observability()
    except Exception as exc:  # noqa: BLE001
        logger.debug("LLM observability flush skipped: %s", exc)
    sys.exit(exit_code)
