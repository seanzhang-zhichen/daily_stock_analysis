# -*- coding: utf-8 -*-
"""Regression tests for bounded AkShare calls used by screening hotspots."""

import multiprocessing
import sys
import time
from types import SimpleNamespace

import pandas as pd
import pytest

from data_provider.akshare_fetcher import _akshare_call_with_timeout
from src.services.screening_service import DsaEastMoneyHotspotProvider


def _return_value(value):
    return value


def _sleep_for(seconds: float) -> None:
    time.sleep(seconds)


def test_akshare_call_with_timeout_uses_spawn_context(monkeypatch) -> None:
    requested_methods = []
    call_order = []

    class FakeConnection:
        def __init__(self, messages):
            self.messages = messages

        def send(self, value):
            self.messages.append(value)

        def poll(self, timeout):
            return bool(self.messages)

        def recv(self):
            if not self.messages:
                raise EOFError
            return self.messages.pop(0)

        def close(self):
            pass

    class FakeProcess:
        def __init__(self, target, args, name, daemon):
            self.target = target
            self.args = args
            self.name = name
            self.daemon = daemon

        def start(self):
            self.target(*self.args)

        def join(self, timeout=None):
            pass

        def is_alive(self):
            return False

        def terminate(self):
            pass

        def kill(self):
            pass

    class FakeContext:
        def Pipe(self, duplex=False):
            messages = []
            return FakeConnection(messages), FakeConnection(messages)

        Process = FakeProcess

    def fake_get_context(method=None):
        call_order.append("get_context")
        requested_methods.append(method)
        return FakeContext()

    def fake_freeze_support():
        call_order.append("freeze_support")

    monkeypatch.setattr(
        "data_provider.akshare_fetcher.multiprocessing.get_context",
        fake_get_context,
    )
    monkeypatch.setattr(
        "data_provider.akshare_fetcher.multiprocessing.freeze_support",
        fake_freeze_support,
    )

    result = _akshare_call_with_timeout(
        _return_value,
        "ok",
        timeout=1,
        call_name="unit-default-context",
    )

    assert result == "ok"
    assert requested_methods == ["spawn"]
    assert call_order == ["freeze_support", "get_context"]


def test_akshare_call_with_timeout_reaps_timed_out_worker() -> None:
    call_name = "unit-hang-reap"
    started = time.monotonic()

    with pytest.raises(TimeoutError, match=call_name):
        _akshare_call_with_timeout(
            _sleep_for,
            5,
            timeout=0.01,
            call_name=call_name,
        )

    assert time.monotonic() - started < 2.0
    assert [
        process
        for process in multiprocessing.active_children()
        if process.name == f"akshare-{call_name}"
    ] == []


def test_hotspot_board_changes_use_bounded_akshare_call(monkeypatch) -> None:
    provider = DsaEastMoneyHotspotProvider()
    expected = pd.DataFrame([{"板块名称": "机器人概念", "涨跌幅": 3.5}])
    stock_board_change_em = object()
    captured = {}

    def fake_bounded_call(func, *args, **kwargs):
        captured["func"] = func
        captured["args"] = args
        captured["kwargs"] = kwargs
        return expected

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_board_change_em=stock_board_change_em),
    )
    monkeypatch.setattr(
        "data_provider.akshare_fetcher._akshare_call_with_timeout",
        fake_bounded_call,
    )

    result = provider._fetch_board_changes_raw()

    pd.testing.assert_frame_equal(result, expected)
    assert result is not expected
    assert captured["func"] is stock_board_change_em
    assert captured["args"] == ()
    assert captured["kwargs"]["call_name"] == "screening.stock_board_change_em"
    assert captured["kwargs"]["timeout"] == provider._AKSHARE_CALL_TIMEOUT_SECONDS
