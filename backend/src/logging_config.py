# -*- coding: utf-8 -*-
"""
===================================
日志配置模块 - 统一的日志系统初始化
===================================

职责：
1. 基于 Loguru 提供统一的日志格式和配置常量
2. 支持控制台 + 文件（常规/调试）三层日志输出
3. 自动降低第三方库日志级别
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple, Union

from loguru import logger as loguru_logger


LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(pathname)s:%(lineno)d | %(message)s"
LOGURU_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | "
    "{extra[relative_path]}:{extra[source_line]} | {message}\n{exception}"
)
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_ALLOWED_LOG_LEVELS = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL,
}
_DEFAULT_LITELLM_LOG_LEVEL = 'WARNING'


class InterceptHandler(logging.Handler):
    """将标准库 logging 记录转发给 Loguru。"""

    def emit(self, record: logging.LogRecord) -> None:
        """Forward one standard logging record into Loguru with source metadata."""
        try:
            level = loguru_logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        loguru_logger.bind(
            source_path=record.pathname,
            source_line=record.lineno,
        ).opt(exception=record.exc_info).log(
            level,
            record.getMessage(),
        )


def _make_loguru_format(project_root: Path) -> Callable[[dict], str]:
    """创建输出项目相对路径的 Loguru formatter。"""

    def _format(record: dict) -> str:
        """Populate Loguru extra fields used by the shared format string."""
        source_path = record["extra"].get("source_path", record["file"].path)
        source_line = record["extra"].get("source_line", record["line"])
        path = Path(source_path)
        try:
            relative_path = path.resolve().relative_to(project_root)
        except ValueError:
            relative_path = path
        record["extra"]["relative_path"] = str(relative_path)
        record["extra"]["source_line"] = source_line
        return LOGURU_FORMAT

    return _format


def _logging_level_to_loguru(level: int) -> Union[int, str]:
    """Convert stdlib logging levels into values accepted by Loguru sinks."""
    if level <= logging.NOTSET:
        return "DEBUG"
    level_name = logging.getLevelName(level)
    if isinstance(level_name, str) and not level_name.startswith("Level "):
        return level_name
    return level


# 默认需要降低日志级别的第三方库
DEFAULT_QUIET_LOGGERS = [
    'urllib3',
    'sqlalchemy',
    'google',
    'httpx',
]

LITELLM_LOGGERS = [
    'LiteLLM',
    'LiteLLM Router',
    'LiteLLM Proxy',
    'litellm',
]


def _resolve_litellm_log_level(raw_level: Optional[str] = None) -> Tuple[int, Optional[str]]:
    """Resolve LiteLLM logger level from env, returning invalid raw value if any."""
    if raw_level is None:
        raw_level = os.getenv('LITELLM_LOG_LEVEL', '')

    normalized = (raw_level or '').strip().upper()
    if not normalized:
        normalized = _DEFAULT_LITELLM_LOG_LEVEL

    level = _ALLOWED_LOG_LEVELS.get(normalized)
    if level is None:
        return _ALLOWED_LOG_LEVELS[_DEFAULT_LITELLM_LOG_LEVEL], raw_level
    return level, None


def setup_logging(
    log_prefix: str = "app",
    log_dir: str = "./logs",
    console_level: Optional[int] = None,
    debug: bool = False,
    extra_quiet_loggers: Optional[List[str]] = None,
) -> None:
    """
    统一的日志系统初始化

    配置三层日志输出：
    1. 控制台：根据 debug 参数或 console_level 设置级别
    2. 常规日志文件：INFO 级别，10MB 轮转，保留 5 个备份
    3. 调试日志文件：DEBUG 级别，50MB 轮转，保留 3 个备份

    Args:
        log_prefix: 日志文件名前缀（如 "api_server" -> api_server_20240101.log）
        log_dir: 日志文件目录，默认 ./logs
        console_level: 控制台日志级别（可选，优先于 debug 参数）
        debug: 是否启用调试模式（控制台输出 DEBUG 级别）
        extra_quiet_loggers: 额外需要降低日志级别的第三方库列表
    """
    # 确定控制台日志级别
    if console_level is not None:
        level = console_level
    else:
        level = logging.DEBUG if debug else logging.INFO

    # 创建日志目录
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # 日志文件路径（按日期分文件）
    today_str = datetime.now().strftime('%Y%m%d')
    log_file = log_path / f"{log_prefix}_{today_str}.log"
    debug_log_file = log_path / f"{log_prefix}_debug_{today_str}.log"

    # 创建相对路径 Formatter（相对于项目根目录）
    project_root = Path.cwd()
    loguru_format = _make_loguru_format(project_root)

    # Loguru sinks: 控制台 + 常规日志 + 调试日志
    loguru_logger.remove()
    loguru_logger.add(
        sys.stdout,
        level=_logging_level_to_loguru(level),
        format=loguru_format,
        colorize=False,
        backtrace=False,
        diagnose=False,
        enqueue=False,
    )
    loguru_logger.add(
        log_file,
        level="INFO",
        format=loguru_format,
        rotation=10 * 1024 * 1024,
        retention=5,
        encoding='utf-8',
        backtrace=False,
        diagnose=False,
        enqueue=False,
    )
    loguru_logger.add(
        debug_log_file,
        level="DEBUG",
        format=loguru_format,
        rotation=50 * 1024 * 1024,
        retention=3,
        encoding='utf-8',
        backtrace=False,
        diagnose=False,
        enqueue=False,
    )

    # 标准库 logging 兼容层：保留现有 logging.getLogger(...) 调用方式。
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.DEBUG)  # 根 logger 设为 DEBUG，由 loguru sink 控制输出级别
    root_logger.addHandler(InterceptHandler())

    # 降低第三方库的日志级别
    quiet_loggers = DEFAULT_QUIET_LOGGERS.copy()
    if extra_quiet_loggers:
        quiet_loggers.extend(extra_quiet_loggers)

    for logger_name in quiet_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    litellm_level, invalid_litellm_level = _resolve_litellm_log_level()
    for logger_name in LITELLM_LOGGERS:
        logging.getLogger(logger_name).setLevel(litellm_level)

    # 输出初始化完成信息（使用相对路径）
    try:
        rel_log_path = log_path.resolve().relative_to(project_root)
    except ValueError:
        rel_log_path = log_path

    try:
        rel_log_file = log_file.resolve().relative_to(project_root)
    except ValueError:
        rel_log_file = log_file

    try:
        rel_debug_log_file = debug_log_file.resolve().relative_to(project_root)
    except ValueError:
        rel_debug_log_file = debug_log_file

    loguru_logger.info(f"日志系统初始化完成，日志目录: {rel_log_path}")
    loguru_logger.info(f"常规日志: {rel_log_file}")
    loguru_logger.info(f"调试日志: {rel_debug_log_file}")
    if invalid_litellm_level is not None:
        loguru_logger.warning(
            "LITELLM_LOG_LEVEL={!r} 无效，已回退为 {}；可选值：{}",
            invalid_litellm_level,
            _DEFAULT_LITELLM_LOG_LEVEL,
            ", ".join(_ALLOWED_LOG_LEVELS),
        )
