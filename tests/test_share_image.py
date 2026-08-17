from datetime import date

from src.share_image import ShareImageBranding, build_share_image_html


def test_stock_poster_uses_alphalens_brand_and_escapes_report_content():
    html = build_share_image_html(
        "# 测试股票 600000 分析报告\n\n> 核心结论：<script>alert(1)</script>\n\n## 风险提示\n- 波动风险",
        generated_on=date(2026, 8, 17),
        structured_payload={"code": "600000", "name": "测试股票"},
    )

    assert "AlphaLens" in html
    assert "ZhuLinsen" not in html
    assert "霸天土小豆" not in html
    assert "<script>alert(1)</script>" not in html
    assert "2026-08-17" in html


def test_social_card_is_absent_by_default():
    html = build_share_image_html(
        "# A股市场复盘\n\n## 今日结论\n市场震荡",
        branding=ShareImageBranding(),
    )

    assert '<div class="qr-card' not in html
