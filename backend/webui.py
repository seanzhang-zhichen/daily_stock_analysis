# -*- coding: utf-8 -*-
"""WebUI 专用启动脚本。

该入口面向“本地打开完整 Web 界面”的场景，和 ``backend/server.py`` 的
API-only 入口分开维护。它会读取 ``WEBUI_HOST`` / ``WEBUI_PORT``，
并向后兼容旧版 ``API_HOST`` / ``API_PORT`` 环境变量，然后启动
``api.app:app``。

等效命令：
    uv run --locked python backend/main.py --webui-only

Usage:
    uv run --locked python backend/webui.py
    WEBUI_HOST=0.0.0.0 WEBUI_PORT=8000 uv run --locked python backend/webui.py
"""

from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)


def main() -> int:
    """读取 WebUI 监听配置并启动 Uvicorn 服务。"""
    # 优先使用 WebUI 专属变量，保留 API_* 作为历史脚本的兼容兜底。
    host = os.getenv("WEBUI_HOST", os.getenv("API_HOST", "127.0.0.1"))
    port = int(os.getenv("WEBUI_PORT", os.getenv("API_PORT", "8000")))

    print(f"正在启动 Web 服务: http://{host}:{port}")
    print(f"API 文档: http://{host}:{port}/docs")
    print()

    try:
        import uvicorn
        from src.config import setup_env
        from src.logging_config import setup_logging

        # 延迟导入并初始化配置，避免仅查看帮助/导入模块时产生额外副作用。
        setup_env()
        setup_logging(log_prefix="web_server")

        uvicorn.run(
            "api.app:app",
            host=host,
            port=port,
            log_level="info",
        )
    except KeyboardInterrupt:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
