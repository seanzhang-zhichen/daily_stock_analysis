# -*- coding: utf-8 -*-
"""LLM 可观测初始化。

目前支持 Langfuse（https://langfuse.com）。
当 LANGFUSE_SECRET_KEY / LANGFUSE_PUBLIC_KEY 已配置时激活；未配置时静默跳过，无副作用。

用法：在应用启动阶段调用一次 setup_llm_observability()，之后所有
litellm.completion() 调用均会自动上报 Trace。
"""

import logging
import os

logger = logging.getLogger(__name__)


def setup_llm_observability() -> None:
    """初始化 LiteLLM 观测回调（当前支持 Langfuse）。

    所需环境变量：
        LANGFUSE_SECRET_KEY  Langfuse Secret Key（必填，留空则跳过）
        LANGFUSE_PUBLIC_KEY  Langfuse Public Key（必填）

    可选环境变量：
        LANGFUSE_BASE_URL    Langfuse 地址，默认 https://cloud.langfuse.com
        LANGFUSE_HOST        兼容旧版变量；未配置 LANGFUSE_BASE_URL 时自动映射
    """
    _setup_langfuse()


def flush_llm_observability() -> None:
    """Best-effort flush for short-lived CLI runs."""
    if not _langfuse_is_configured():
        return

    try:
        from langfuse import Langfuse

        Langfuse().flush()
    except ImportError:
        return
    except Exception as exc:  # noqa: BLE001
        logger.debug("Langfuse flush skipped: %s", exc)


def _langfuse_is_configured() -> bool:
    return bool(
        os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
        and os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    )


def _resolve_langfuse_base_url() -> str:
    base_url = os.environ.get("LANGFUSE_OTEL_HOST", "").strip()
    host = os.environ.get("LANGFUSE_HOST", "").strip()
    if base_url:
        if not host:
            os.environ["LANGFUSE_HOST"] = base_url
        return base_url
    if host:
        os.environ["LANGFUSE_OTEL_HOST"] = host
        return host
    return ""


def _setup_langfuse() -> None:
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    if not secret_key:
        return

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    if not public_key:
        logger.warning("LANGFUSE_SECRET_KEY 已配置，但 LANGFUSE_PUBLIC_KEY 为空，已跳过 Langfuse 初始化。")
        return

    base_url = _resolve_langfuse_base_url()

    try:
        import litellm

        litellm.callbacks = ["langfuse_otel"]

        if "langfuse" not in litellm.success_callback:
            litellm.success_callback.append("langfuse_otel")
        if "langfuse" not in litellm.failure_callback:
            litellm.failure_callback.append("langfuse_otel")

        base_url_info = f" base_url={base_url}" if base_url else " base_url=cloud.langfuse.com"
        logger.info("Langfuse LLM observability enabled (%s)", base_url_info.strip())
    except ImportError:
        logger.warning(
            "LANGFUSE_SECRET_KEY 已配置，但 langfuse 包未安装。"
            "请执行 pip install langfuse 后重启。"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Langfuse init failed: %s", exc)
