# -*- coding: utf-8 -*-
"""Regression tests for application logging configuration."""

import io
import logging
import re
import sys

import pytest
from loguru import logger as loguru_logger

from src.logging_config import LITELLM_LOGGERS, setup_logging


class _TTYBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def restore_logging_state():
    root_logger = logging.getLogger()
    original_root_level = root_logger.level
    original_handlers = list(root_logger.handlers)
    original_litellm_levels = {
        logger_name: logging.getLogger(logger_name).level
        for logger_name in LITELLM_LOGGERS
    }

    yield

    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        if handler not in original_handlers:
            handler.close()
    for handler in original_handlers:
        root_logger.addHandler(handler)
    root_logger.setLevel(original_root_level)

    loguru_logger.remove()
    loguru_logger.add(sys.stderr)

    for logger_name, level in original_litellm_levels.items():
        logging.getLogger(logger_name).setLevel(level)


def _read_debug_log(log_dir) -> str:
    for handler in logging.getLogger().handlers:
        handler.flush()
    loguru_logger.complete()
    debug_log = next(log_dir.glob("stock_analysis_debug_*.log"))
    return debug_log.read_text(encoding="utf-8")


@pytest.mark.parametrize("env_value", [None, "", "  "])
def test_litellm_debug_is_quiet_by_default_and_empty_env(tmp_path, monkeypatch, env_value):
    if env_value is None:
        monkeypatch.delenv("LITELLM_LOG_LEVEL", raising=False)
    else:
        monkeypatch.setenv("LITELLM_LOG_LEVEL", env_value)

    setup_logging(log_prefix="stock_analysis", log_dir=str(tmp_path), debug=False)

    for logger_name in LITELLM_LOGGERS:
        logging.getLogger(logger_name).debug("%s token debug should be filtered", logger_name)
    logging.getLogger("LiteLLM").warning("litellm warning should remain")
    logging.getLogger("src.sample").debug("project debug should remain")

    debug_log_text = _read_debug_log(tmp_path)

    for logger_name in LITELLM_LOGGERS:
        assert f"{logger_name} token debug should be filtered" not in debug_log_text
    assert "litellm warning should remain" in debug_log_text
    assert "project debug should remain" in debug_log_text


def test_litellm_log_level_debug_restores_litellm_debug(tmp_path, monkeypatch):
    monkeypatch.setenv("LITELLM_LOG_LEVEL", "DEBUG")

    setup_logging(log_prefix="stock_analysis", log_dir=str(tmp_path), debug=False)

    for logger_name in LITELLM_LOGGERS:
        logging.getLogger(logger_name).debug("%s debug should remain", logger_name)

    debug_log_text = _read_debug_log(tmp_path)

    for logger_name in LITELLM_LOGGERS:
        assert f"{logger_name} debug should remain" in debug_log_text


def test_invalid_litellm_log_level_falls_back_to_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("LITELLM_LOG_LEVEL", "verbose")

    setup_logging(log_prefix="stock_analysis", log_dir=str(tmp_path), debug=False)

    logging.getLogger("LiteLLM").debug("invalid level debug should be filtered")
    logging.getLogger("LiteLLM").warning("invalid level warning should remain")

    debug_log_text = _read_debug_log(tmp_path)

    assert "invalid level debug should be filtered" not in debug_log_text
    assert "invalid level warning should remain" in debug_log_text
    assert "LITELLM_LOG_LEVEL" in debug_log_text
    assert "已回退为 WARNING" in debug_log_text


def test_loguru_and_standard_logging_share_configured_sinks(tmp_path):
    setup_logging(log_prefix="stock_analysis", log_dir=str(tmp_path), debug=False)

    logging.getLogger("src.sample").info("stdlib logging should be bridged")
    loguru_logger.info("loguru direct logging should be written")

    debug_log_text = _read_debug_log(tmp_path)

    assert "stdlib logging should be bridged" in debug_log_text
    assert "loguru direct logging should be written" in debug_log_text
    assert "tests\\test_logging_config.py" in debug_log_text or "tests/test_logging_config.py" in debug_log_text


def test_console_uses_distinct_colors_for_log_levels(tmp_path, monkeypatch):
    console = _TTYBuffer()
    monkeypatch.setattr(sys, "stdout", console)
    setup_logging(log_prefix="stock_analysis", log_dir=str(tmp_path), debug=True)

    for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        loguru_logger.log(level, f"color-{level.lower()}")
    loguru_logger.complete()

    output = console.getvalue()
    expected_colors = {
        "debug": "36",
        "info": "32",
        "warning": "33",
        "error": "31",
        "critical": "35",
    }
    for level, color_code in expected_colors.items():
        line = next(line for line in output.splitlines() if f"color-{level}" in line)
        assert re.search(rf"\x1b\[[0-9;]*{color_code}m", line)


def test_file_logs_do_not_contain_ansi_color_codes(tmp_path, monkeypatch):
    console = _TTYBuffer()
    monkeypatch.setattr(sys, "stdout", console)
    setup_logging(log_prefix="stock_analysis", log_dir=str(tmp_path), debug=True)

    loguru_logger.error("plain-file-log")
    debug_log_text = _read_debug_log(tmp_path)

    assert "plain-file-log" in debug_log_text
    assert "\x1b[" not in debug_log_text
