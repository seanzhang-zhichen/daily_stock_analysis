# -*- coding: utf-8 -*-
"""Tests for optional LLM observability bootstrap."""

import importlib
import os
import sys
import types


def _install_litellm_stub(monkeypatch):
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.success_callback = []
    litellm_stub.failure_callback = []
    monkeypatch.setitem(sys.modules, "litellm", litellm_stub)
    return litellm_stub


def test_langfuse_host_maps_to_base_url(monkeypatch):
    litellm_stub = _install_litellm_stub(monkeypatch)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public")
    monkeypatch.setenv("LANGFUSE_HOST", "https://jp.cloud.langfuse.com")
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)

    observability = importlib.import_module("src.llm.observability")
    observability.setup_llm_observability()

    assert "langfuse" in litellm_stub.success_callback
    assert "langfuse" in litellm_stub.failure_callback
    assert sys.modules["litellm"].success_callback.count("langfuse") == 1
    assert sys.modules["litellm"].failure_callback.count("langfuse") == 1
    assert os.environ["LANGFUSE_BASE_URL"] == "https://jp.cloud.langfuse.com"


def test_langfuse_base_url_maps_to_legacy_host(monkeypatch):
    _install_litellm_stub(monkeypatch)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://jp.cloud.langfuse.com")
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)

    observability = importlib.import_module("src.llm.observability")
    observability.setup_llm_observability()

    assert os.environ["LANGFUSE_HOST"] == "https://jp.cloud.langfuse.com"


def test_langfuse_requires_public_key(monkeypatch):
    litellm_stub = _install_litellm_stub(monkeypatch)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)

    observability = importlib.import_module("src.llm.observability")
    observability.setup_llm_observability()

    assert litellm_stub.success_callback == []
    assert litellm_stub.failure_callback == []
