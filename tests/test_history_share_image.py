# -*- coding: utf-8 -*-

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.v1.endpoints import history as history_endpoint


class _FakeHistoryService:
    def __init__(self, result, markdown="# 中钨高新 000657 分析报告"):
        self.result = result
        self.markdown = markdown
        self.detail_user_ids = []
        self.markdown_user_ids = []

    def resolve_and_get_detail(self, record_id, user_id=None):
        self.detail_user_ids.append(user_id)
        return self.result

    def get_markdown_report(self, record_id, user_id=None):
        self.markdown_user_ids.append(user_id)
        return self.markdown


def _patch_service(monkeypatch, result, markdown="# 中钨高新 000657 分析报告"):
    service = _FakeHistoryService(result, markdown)
    monkeypatch.setattr(history_endpoint, "HistoryService", lambda _db: service)
    monkeypatch.setattr(
        history_endpoint,
        "get_config",
        lambda: SimpleNamespace(
            markdown_to_image_max_chars=15000,
            md2img_engine="markdown-to-file",
        ),
    )
    return service


def _user():
    return SimpleNamespace(id=42)


def test_history_share_image_uses_user_scope_and_stock_payload(monkeypatch):
    raw_result = {"code": "000657", "name": "中钨高新", "dashboard": {}}
    service = _patch_service(
        monkeypatch,
        {"id": 17, "report_type": "detailed", "raw_result": raw_result, "context_snapshot": {}},
    )
    captured = {}

    def fake_markdown_to_image(markdown, **kwargs):
        captured.update(kwargs)
        return b"\x89PNG\r\n\x1a\nposter"

    monkeypatch.setattr(history_endpoint, "markdown_to_image", fake_markdown_to_image)
    response = history_endpoint.get_history_share_image(
        "17",
        db_manager=object(),
        current_user=_user(),
    )

    assert response.status_code == 200
    assert response.media_type == "image/png"
    assert response.headers["content-disposition"] == 'attachment; filename="alphalens-report-17.png"'
    assert captured["structured_payload"] is raw_result
    assert service.detail_user_ids == [42]
    assert service.markdown_user_ids == [42]


def test_history_share_image_prefers_market_review_payload(monkeypatch):
    market_payload = {"kind": "market_review", "date": "2026-08-01"}
    _patch_service(
        monkeypatch,
        {
            "id": 18,
            "report_type": "market_review",
            "raw_result": {"raw_response": "market report"},
            "context_snapshot": {"market_review_payload": market_payload},
        },
        markdown="# A股市场复盘",
    )
    captured = {}
    monkeypatch.setattr(
        history_endpoint,
        "markdown_to_image",
        lambda _markdown, **kwargs: captured.update(kwargs) or b"png",
    )

    history_endpoint.get_history_share_image("18", db_manager=object(), current_user=_user())

    assert captured["structured_payload"] is market_payload


def test_history_share_image_html_has_restrictive_csp_and_no_social_default(monkeypatch):
    raw_result = {"code": "000657", "name": "中钨高新", "dashboard": {}}
    _patch_service(
        monkeypatch,
        {"id": 20, "report_type": "detailed", "raw_result": raw_result, "context_snapshot": {}},
    )
    captured = {}

    def fake_build_share_image_html(markdown, **kwargs):
        captured.update(kwargs)
        return "<!DOCTYPE html><html><body>poster</body></html>"

    monkeypatch.setattr(history_endpoint, "build_share_image_html", fake_build_share_image_html)
    response = history_endpoint.get_history_share_image_html(
        "20",
        db_manager=object(),
        current_user=_user(),
    )

    assert response.status_code == 200
    assert captured["structured_payload"] is raw_result
    assert captured["branding"].has_xiaohongshu is False
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"] == (
        "default-src 'none'; img-src data:; style-src 'unsafe-inline'"
    )


def test_history_share_image_html_rejects_oversized_report(monkeypatch):
    _patch_service(
        monkeypatch,
        {"id": 21, "report_type": "detailed", "raw_result": {}, "context_snapshot": {}},
        markdown="x" * 15001,
    )

    with pytest.raises(HTTPException) as exc_info:
        history_endpoint.get_history_share_image_html(
            "21",
            db_manager=object(),
            current_user=_user(),
        )

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail["error"] == "share_image_too_large"


def test_history_share_image_reports_renderer_unavailable(monkeypatch):
    _patch_service(
        monkeypatch,
        {"id": 19, "report_type": "detailed", "raw_result": {}, "context_snapshot": {}},
    )
    monkeypatch.setattr(history_endpoint, "markdown_to_image", lambda *_args, **_kwargs: None)

    with pytest.raises(HTTPException) as exc_info:
        history_endpoint.get_history_share_image(
            "19",
            db_manager=object(),
            current_user=_user(),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"] == "share_image_unavailable"


def test_history_share_image_returns_not_found_with_user_scope(monkeypatch):
    service = _patch_service(monkeypatch, None)

    with pytest.raises(HTTPException) as exc_info:
        history_endpoint.get_history_share_image(
            "missing",
            db_manager=object(),
            current_user=_user(),
        )

    assert exc_info.value.status_code == 404
    assert service.detail_user_ids == [42]
