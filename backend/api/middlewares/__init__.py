# -*- coding: utf-8 -*-
"""API 中间件实现的公共导出层。

中间件的注册顺序由 ``api.app.create_app`` 控制；本模块只提供稳定导出，
方便历史代码或测试继续通过 ``api.middlewares`` 获取中间件类。
"""

# 从 error_handler 模块导入错误处理中间件类
from api.middlewares.error_handler import ErrorHandlerMiddleware

# 对外公开的可导出名称列表
__all__ = ["ErrorHandlerMiddleware"]
