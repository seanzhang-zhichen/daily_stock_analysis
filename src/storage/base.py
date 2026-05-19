# -*- coding: utf-8 -*-
"""
SQLAlchemy ORM 基类。

所有数据模型共享同一 ``Base``，便于 ``Base.metadata.create_all`` 统一建表。
"""

from sqlalchemy.orm import declarative_base

Base = declarative_base()

__all__ = ["Base"]
