# -*- coding: utf-8 -*-
"""Daily Stock Analysis 的 FastAPI 服务入口。

本文件只负责“可被 Uvicorn 发现并启动”的薄入口：先完成环境变量与日志
初始化，再从 ``api.app`` 导出真正的 FastAPI ``app`` 实例。应用创建、
中间件、路由、生命周期任务都集中在 ``api.app.create_app`` 中维护。

默认导出的 ``app`` 是 API-only 模式，不托管 Web 前端静态文件；需要 WebUI
时应通过 ``backend/main.py --webui-only`` 或 ``backend/webui.py`` 走显式入口，
这样桌面端/服务端/API-only 三类启动语义不会互相混淆。

启动示例：
    uvicorn backend.server:app --reload --host 0.0.0.0 --port 8000
    python backend/main.py --serve-only      # 仅启动 API 服务
    python backend/main.py --serve           # API 服务 + 执行分析
"""

import logging

from src.config import setup_env, get_config
from src.logging_config import setup_logging

# 在导入 app 前完成环境与日志初始化；api.app 的模块级代码会读取配置。
setup_env()

config = get_config()
level_name = (config.log_level or "INFO").upper()
level = getattr(logging, level_name, logging.INFO)

setup_logging(
    log_prefix="api_server",
    console_level=level,
    extra_quiet_loggers=['uvicorn', 'fastapi'],
)

# 从 api.app 导入应用实例，供 ``uvicorn backend.server:app`` 直接加载。
from api.app import app  # noqa: E402

# 明确公开导出，避免外部误依赖本入口中的临时初始化变量。
__all__ = ['app']


if __name__ == "__main__":
    import uvicorn

    # 直接运行本文件时提供本地开发默认值；生产/脚本启动推荐使用显式 uvicorn 命令。
    uvicorn.run(
        "backend.server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
