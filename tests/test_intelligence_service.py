from types import SimpleNamespace
from unittest.mock import patch

from src.services.intelligence_service import IntelligenceService


def _service() -> IntelligenceService:
    return IntelligenceService(
        repository=SimpleNamespace(),
        config=SimpleNamespace(
            newsnow_base_url="https://newsnow.example.com",
            news_intel_max_items_per_source=50,
            news_intel_fetch_timeout_sec=8,
            news_intel_auto_fetch_enabled=False,
            news_intel_retention_days=30,
        ),
    )


def test_parses_rss_without_link_using_stable_local_key() -> None:
    service = _service()
    entries = service._feed(b"""<rss><channel><item><title>A share update</title><description>market breadth improved</description><pubDate>Mon, 01 Sep 2026 08:00:00 GMT</pubDate></item></channel></rss>""")
    assert entries[0]["title"] == "A share update"
    assert entries[0]["url"].startswith("no-url:intel:")


def test_parses_atom_and_newsnow_payloads() -> None:
    service = _service()
    atom = b"""<feed xmlns='http://www.w3.org/2005/Atom'><entry><title>Atom update</title><summary>summary</summary><link href=''/></entry></feed>"""
    assert service._feed(atom)[0]["title"] == "Atom update"
    newsnow = b'{"items":[{"title":"NewsNow update","mobileUrl":"","extra":{"info":"brief"}}]}'
    assert service._newsnow(newsnow)[0]["summary"] == "brief"


def test_only_cn_sources_are_accepted() -> None:
    service = _service()
    fields = service._fields({"name": "A-share feed", "url": "https://example.com/rss", "source_type": "rss", "market": "cn"})
    assert fields["market"] == "cn"
    try:
        service._fields({"name": "other market", "url": "https://example.com/rss", "market": "us"})
    except ValueError as exc:
        assert "A-share" in str(exc)
    else:
        raise AssertionError("non-CN source unexpectedly accepted")


class _Response:
    def __init__(self, *, status_code=200, content=b"", headers=None, url="https://example.com/feed"):
        self.status_code = status_code
        self._content = content
        self.headers = headers or {}
        self.url = url
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("request failed")

    def iter_content(self, chunk_size=8192):
        del chunk_size
        yield self._content

    def close(self):
        self.closed = True


def test_rss_redirect_is_revalidated_before_following() -> None:
    service = _service()
    rss = b"<rss><channel><item><title>test</title></item></channel></rss>"
    redirects = [
        _Response(status_code=302, headers={"Location": "/next"}),
        _Response(content=rss, url="https://example.com/next"),
    ]
    with patch.object(service, "_validate_url") as validate_url, patch(
        "src.services.intelligence_service.requests.get", side_effect=redirects
    ) as get:
        entries = service._fetch({"url": "https://example.com/feed", "source_type": "rss"})

    assert entries[0]["title"] == "test"
    assert get.call_count == 2
    assert validate_url.call_count >= 3
    assert redirects[0].closed


def test_newsnow_redirect_is_rejected() -> None:
    service = _service()
    with patch.object(service, "_validate_url"), patch(
        "src.services.intelligence_service.requests.get",
        return_value=_Response(status_code=302, headers={"Location": "https://example.com/next"}),
    ):
        try:
            service._fetch({"url": "https://example.com/api/s?id=source", "source_type": "newsnow"})
        except ValueError as exc:
            assert "redirect" in str(exc)
        else:
            raise AssertionError("NewsNow redirect unexpectedly followed")


def test_auto_refresh_reuses_result_during_cooldown() -> None:
    service = _service()
    service.config.news_intel_auto_fetch_enabled = True
    IntelligenceService.reset_auto_fetch_state()
    try:
        with patch.object(service, "create_default_sources") as create_defaults, patch.object(
            service, "fetch_enabled_sources", return_value={"saved_count": 3}
        ) as fetch:
            first = service.refresh_auto_sources()
            second = service.refresh_auto_sources()

        assert first == {"ok": True, "skipped": False, "fetch": {"saved_count": 3}, "saved_count": 3}
        assert second == {"ok": True, "skipped": True, "reason": "cooldown"}
        assert create_defaults.call_count == 1
        assert fetch.call_count == 1
    finally:
        IntelligenceService.reset_auto_fetch_state()


def test_limited_response_rejects_large_stream_without_joining() -> None:
    service = _service()

    class _LargeResponse:
        def iter_content(self, chunk_size=8192):
            del chunk_size
            yield b"x" * (2 * 1024 * 1024)
            yield b"x"

    try:
        service._read_limited_response(_LargeResponse())
    except ValueError as exc:
        assert "too large" in str(exc)
    else:
        raise AssertionError("oversized streamed response unexpectedly accepted")


def test_source_state_and_delete_delegate_to_repository() -> None:
    repo = SimpleNamespace(
        set_source_enabled=lambda source_id, enabled: SimpleNamespace(
            id=source_id, name="feed", source_type="rss", url="https://example.com/rss",
            enabled=enabled, scope_type="market", scope_value=None, market="cn",
            description=None, last_status=None, last_error=None,
        ),
        delete_source=lambda source_id: source_id == 7,
    )
    service = IntelligenceService(repository=repo, config=_service().config)

    assert service.set_source_enabled(7, False)["enabled"] is False
    assert service.delete_source(7) == {"ok": True, "source_id": 7}
    try:
        service.delete_source(8)
    except ValueError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("missing source was not rejected")
