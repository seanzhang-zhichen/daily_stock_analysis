# -*- coding: utf-8 -*-
"""Versioned API exports.

v1 的所有 endpoint 都通过 ``api_v1_router`` 汇总后挂载到应用工厂中。外部代码
只需要依赖这个聚合路由，避免绕过版本前缀直接引用具体 endpoint 模块。
"""

from api.v1.router import router as api_v1_router

__all__ = ["api_v1_router"]
