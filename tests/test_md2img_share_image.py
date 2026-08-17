import sys
from types import SimpleNamespace

from src import md2img


def _install_fake_imgkit(monkeypatch, captured):
    def from_string(html, output_path, options):
        captured.update({"html": html, "output_path": output_path, "options": options})
        return b"png"

    monkeypatch.setitem(sys.modules, "imgkit", SimpleNamespace(from_string=from_string))
    monkeypatch.setattr(md2img.shutil, "which", lambda command: command)


def test_wkhtml_returns_none_before_loading_imgkit_when_executable_is_missing(monkeypatch):
    monkeypatch.setattr(md2img.shutil, "which", lambda _command: None)
    monkeypatch.delitem(sys.modules, "imgkit", raising=False)

    assert md2img._markdown_to_image_wkhtml("# report") is None


def test_wkhtml_keeps_legacy_markdown_document_without_share_payload(monkeypatch):
    captured = {}
    _install_fake_imgkit(monkeypatch, captured)
    monkeypatch.setattr(md2img, "markdown_to_html_document", lambda markdown: f"legacy:{markdown}")
    monkeypatch.setattr(
        md2img,
        "build_share_image_html",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("poster should not be used")),
    )

    assert md2img._markdown_to_image_wkhtml("# report") == b"png"
    assert captured["html"] == "legacy:# report"
    assert "width" not in captured["options"]


def test_wkhtml_uses_share_poster_for_structured_payload(monkeypatch):
    captured = {}
    _install_fake_imgkit(monkeypatch, captured)
    monkeypatch.setattr(md2img, "build_share_image_html", lambda *_args, **_kwargs: "poster")

    assert md2img._markdown_to_image_wkhtml("# report", {"code": "600519"}) == b"png"
    assert captured["html"] == "poster"
    assert captured["options"]["width"] == 1080
