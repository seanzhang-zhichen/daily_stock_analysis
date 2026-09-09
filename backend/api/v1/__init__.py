# -*- coding: utf-8 -*-
"""API 版本化路由的对外导出层。

v1 的所有 endpoint 都通过 :data:`api_v1_router` 汇总后挂载到应用工厂中。外部代码
只需要依赖这个聚合路由，避免绕过版本前缀直接引用具体 endpoint 模块。
"""


def __getattr__(name):
    """惰性导出 v1 聚合路由 ``api_v1_router``。

    把 ``api.v1.router`` 的导入延迟到第一次访问时执行，从而：

    - 避免 ``import api.v1`` 时就把所有 endpoint 子模块加载进来；
    - 让单元测试 / 启动期诊断可以更早失败（缺依赖时尽早暴露）。

    Args:
        name: 调用方访问的属性名。

    Returns:
        :data:`api.v1.router.router`：v1 版本的聚合 :class:`APIRouter`。

    Raises:
        AttributeError: 属性名不在白名单时抛出。
    """
    if name == "api_v1_router":
        from api.v1.router import router as api_v1_router

        # 缓存到 globals，后续访问直接命中，不再走 import_module
        globals()[name] = api_v1_router
        return api_v1_router
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["api_v1_router"]
