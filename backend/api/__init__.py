# -*- coding: utf-8 -*-
"""API package marker and version metadata.

路由、依赖注入、中间件与应用工厂分别位于本包下的子模块中。这里保持轻量，
只暴露 API 包版本，避免包导入阶段触发 FastAPI app 创建、数据库连接或其他
运行时副作用。
"""

__version__ = "1.0.0"
