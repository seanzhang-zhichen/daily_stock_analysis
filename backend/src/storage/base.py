# -*- coding: utf-8 -*-
"""
SQLAlchemy ORM 基类。

所有数据模型共享同一 ``Base``，便于 ``Base.metadata.create_all`` 统一建表。
本模块是 ORM 模型的根基，任何新增的业务表模型都应继承自此 ``Base``。
"""

from sqlalchemy.orm import declarative_base

# 创建 SQLAlchemy 声明式基类实例
# 所有 ORM 模型均继承自此 Base，共享同一个 metadata 对象
Base = declarative_base()

__all__ = ["Base"]
