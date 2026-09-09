# -*- coding: utf-8 -*-
"""
===================================
Markdown 转图片工具模块
===================================

将 Markdown 转为 PNG 字节流，供不支持 Markdown 渲染的通知渠道（企业微信、邮件等）使用。
支持两种渲染引擎：
- wkhtmltoimage（imgkit，默认）：把 HTML 通过 stdin 喂给 wkhtmltoimage，命令注入不适用
- markdown-to-file（m2f，备选）：对 emoji 支持更好（Issue #455）

安全说明：imgkit 通过 stdin 传入 HTML 而非 argv，所以内容侧的"命令注入"并不适用；
输出仅为光栅化 PNG（无脚本执行）；输入来自系统自动生成的报告而非用户原始输入。
当前用例下风险评估为低。
"""

import logging
import os
import shutil
import subprocess
import tempfile
from typing import Any, Mapping, Optional

from src.formatters import markdown_to_html_document
from src.share_image import (
    ShareImageBranding,
    build_share_image_html,
    share_image_branding_from_config,
)

logger = logging.getLogger(__name__)


def _share_image_branding(config: object) -> ShareImageBranding:
    """从任意配置对象构造分享图品牌信息（仅作为方便调用层）。"""
    return share_image_branding_from_config(config)


def _markdown_to_image_m2f(
    markdown_text: str,
    structured_payload: Optional[Mapping[str, Any]] = None,
    branding: Optional[ShareImageBranding] = None,
) -> Optional[bytes]:
    """通过 markdown-to-file (m2f) CLI 将 Markdown 转为 PNG，对 emoji 支持更好（Issue #455）。"""
    m2f_command = shutil.which("m2f")
    # 缺少二进制时直接降级，让调用方走文本通道
    if m2f_command is None:
        logger.warning(
            "m2f (markdown-to-file) not found in PATH. "
            "Install with: npm i -g markdown-to-file. Fallback to text."
        )
        return None

    temp_dir = None
    try:
        # m2f 通过文件名约定输出 PNG，整个流程需要临时目录中转
        temp_dir = tempfile.mkdtemp()
        md_path = os.path.join(temp_dir, "report.md")
        with open(md_path, "w", encoding="utf-8") as f:
            if structured_payload is None:
                f.write(markdown_text)
            else:
                # m2f 会保留 Markdown 中的原始 HTML，因此可以复用 share_image 的同一布局
                f.write(build_share_image_html(
                    markdown_text,
                    structured_payload=structured_payload,
                    branding=branding,
                ))

        result = subprocess.run(
            [m2f_command, md_path, "png", f"outputDirectory={temp_dir}"],
            capture_output=True,
            # 60s 超时上限：m2f 在大文档/冷启动时可能较慢，但不应无限挂起
            timeout=60,
            check=False,
        )
        png_path = os.path.join(temp_dir, "report.png")
        if result.returncode != 0 or not os.path.isfile(png_path):
            logger.warning(
                "m2f conversion failed: returncode=%s, stderr=%s",
                result.returncode,
                (result.stderr or b"").decode("utf-8", errors="replace")[:200],
            )
            return None

        with open(png_path, "rb") as f:
            return f.read()
    except subprocess.TimeoutExpired:
        logger.warning("m2f conversion timed out (60s)")
        return None
    except Exception as e:
        logger.warning("markdown_to_image (m2f) failed: %s", e)
        return None
    finally:
        # 无论成功失败都要清理临时目录，避免长时间运行下磁盘被占满
        if temp_dir and os.path.isdir(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except OSError as e:
                logger.debug("Failed to remove temp dir %s: %s", temp_dir, e)


def _markdown_to_image_wkhtml(
    markdown_text: str,
    structured_payload: Optional[Mapping[str, Any]] = None,
    branding: Optional[ShareImageBranding] = None,
) -> Optional[bytes]:
    """通过 imgkit / wkhtmltoimage 将 Markdown 渲染为 PNG。"""
    # 先检测二进制是否可用：缺失则静默降级到文本通道，避免日志噪音
    if shutil.which("wkhtmltoimage") is None:
        logger.warning(
            "wkhtmltoimage not found in PATH. Install wkhtmltopdf to enable server-side image rendering."
        )
        return None

    try:
        import imgkit
    except ImportError:
        logger.debug("imgkit not installed, markdown_to_image unavailable")
        return None

    try:
        # 是否有结构化 payload 决定走通用 HTML 模板还是分享图专用布局
        html = (
            markdown_to_html_document(markdown_text)
            if structured_payload is None
            else build_share_image_html(
                markdown_text,
                structured_payload=structured_payload,
                branding=branding,
            )
        )
        options = {
            "format": "png",
            "encoding": "UTF-8",
            "quiet": "",
        }
        # 分享图场景固定 1080 宽并提高质量，保证移动端阅读体验
        if structured_payload is not None:
            options.update({
                "width": 1080,
                "disable-smart-width": "",
                "quality": 95,
            })
        out = imgkit.from_string(html, False, options=options)
        if out and isinstance(out, bytes) and len(out) > 0:
            return out
        logger.warning("imgkit.from_string returned empty or invalid result")
        return None
    except OSError as e:
        # 二进制缺失与真正 OSError 区分：前者视为可降级，后者需要排查
        if "wkhtmltoimage" in str(e).lower() or "wkhtmltopdf" in str(e).lower():
            logger.debug("wkhtmltopdf/wkhtmltoimage not found: %s", e)
        else:
            logger.warning("imgkit/wkhtmltoimage error: %s", e)
        return None
    except Exception as e:
        logger.warning("markdown_to_image conversion failed: %s", e)
        return None


def markdown_to_image(
    markdown_text: str,
    max_chars: int = 15000,
    structured_payload: Optional[Mapping[str, Any]] = None,
) -> Optional[bytes]:
    """将 Markdown 文本转换为 PNG 字节流。

    渲染引擎通过 ``config.md2img_engine`` 选取，可选 ``wkhtmltoimage``（默认）或
    ``markdown-to-file``。当转换失败或依赖不可用时返回 ``None``，由调用方降级为文本发送。

    Args:
        markdown_text: 原始 Markdown 内容。
        max_chars: 内容长度上限；超过则跳过图片转换，避免生成超大图片，默认 15000。
        structured_payload: 可选的结构化 JSON（个股/大盘复盘结构）。
            提供时，结构化字段优先于 Markdown 抽取，用于构造分享图布局。

    Returns:
        PNG 字节；转换失败或依赖不可用时返回 None。
    """
    # 内容过长直接放弃图片转换，避免下游接收超大文件或内存暴涨
    if len(markdown_text) > max_chars:
        logger.warning(
            "Markdown content (%d chars) exceeds max_chars (%d), skipping image conversion",
            len(markdown_text),
            max_chars,
        )
        return None

    try:
        from src.config import get_config

        config = get_config()
        engine = getattr(config, "md2img_engine", "wkhtmltoimage")
        branding = _share_image_branding(config)
    except Exception:
        # 配置加载失败时回退到最稳的默认值：默认引擎 + 空 branding
        engine = "wkhtmltoimage"
        branding = share_image_branding_from_config(object())

    if engine == "markdown-to-file":
        return _markdown_to_image_m2f(markdown_text, structured_payload, branding)
    return _markdown_to_image_wkhtml(markdown_text, structured_payload, branding)
