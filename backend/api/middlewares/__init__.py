# -*- coding: utf-8 -*-
"""Public exports for API middleware implementations.

中间件的注册顺序由 ``api.app.create_app`` 控制；本模块只提供稳定导出，
方便历史代码或测试继续通过 ``api.middlewares`` 获取中间件类。
"""

from api.middlewares.error_handler import ErrorHandlerMiddleware

__all__ = ["ErrorHandlerMiddleware"]
