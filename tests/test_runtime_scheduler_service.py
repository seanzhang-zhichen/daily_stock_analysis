"""Focused tests for the C-end isolated runtime scheduler."""

from types import SimpleNamespace
from unittest.mock import patch

from src.services.runtime_scheduler import (
    DEFAULT_TIMEOUT_SECONDS,
    CLI_SCHEDULER_OWNER_ENV,
    RuntimeSchedulerService,
)


def _config(**overrides):
    values = {
        "schedule_enabled": True,
        "schedule_time": "18:00",
        "schedule_run_immediately": False,
        "runtime_scheduler_timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_cli_owner_does_not_start_runtime_scheduler():
    service = RuntimeSchedulerService()
    config = _config()
    with patch.dict("os.environ", {CLI_SCHEDULER_OWNER_ENV: "true"}, clear=False), patch(
        "src.services.runtime_scheduler.get_config", return_value=config
    ), patch.object(service, "_loop") as loop:
        service.start()
        loop.assert_not_called()
    service.stop()


def test_disabled_schedule_does_not_start_runtime_scheduler():
    service = RuntimeSchedulerService()
    with patch.dict("os.environ", {}, clear=False), patch(
        "src.services.runtime_scheduler.get_config", return_value=_config(schedule_enabled=False)
    ), patch.object(service, "_loop") as loop:
        service.start()
        loop.assert_not_called()
    service.stop()


def test_default_watchdog_budget_is_45_minutes():
    service = RuntimeSchedulerService()
    with patch("src.services.runtime_scheduler.get_config", return_value=_config()):
        assert service._timeout_seconds() == 45 * 60


def test_run_now_rejects_an_overlapping_watchdog():
    service = RuntimeSchedulerService()
    with patch.object(service, "_start_watchdog", side_effect=[True, False]):
        assert service.run_now()["accepted"] is True
        rejected = service.run_now()
    assert rejected == {"accepted": False, "running": False, "reason": "analysis_already_running"}
