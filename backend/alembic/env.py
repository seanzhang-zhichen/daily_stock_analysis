# -*- coding: utf-8 -*-
"""
Alembic 迁移环境配置。

- 数据库 URL 从项目配置（src.config.get_config）动态读取，不硬编码。
- 导入所有 ORM 模型以确保 Base.metadata 完整，autogenerate 可正确 diff。
"""

import sys
from pathlib import Path
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# 将项目根目录加入 sys.path，保证 src.* 导入可用
sys.path.insert(0, str(Path(__file__).parent.parent))

# 导入 Base 及所有 ORM 模型（必须在 target_metadata 赋值前完成）
from src.storage.base import Base
import src.storage.models  # noqa: F401 — 注册所有 ORM 模型到 Base.metadata

from src.config import get_config

# Alembic Config 对象，提供对 .ini 文件的访问
config = context.config

# 若存在配置文件且允许配置日志，则加载日志配置
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# SQLAlchemy 元数据对象，供 Alembic 对比表结构变更
target_metadata = Base.metadata


def get_url() -> str:
    """从 Alembic attributes、ini 配置或应用配置中解析数据库 URL。

    优先级：
    1. 通过 ``config.attributes`` 传入的 ``database_url``
    2. alembic.ini 中的 ``sqlalchemy.url``
    3. 应用运行时配置 ``get_config().get_db_url()``

    Returns:
        str: 数据库连接 URL
    """
    configured_url = config.attributes.get("database_url")
    if configured_url:
        return str(configured_url)
    ini_url = config.get_main_option("sqlalchemy.url")
    if ini_url:
        return ini_url
    return get_config().get_db_url()


def run_migrations_offline() -> None:
    """离线模式：不需要实际数据库连接，仅生成 SQL 脚本。

    通过 ``literal_binds=True`` 将参数绑定为字面量，
    输出可直接在目标数据库执行的 SQL。
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库并直接执行迁移。

    使用 ``NullPool`` 避免连接池缓存问题，
    每次迁移结束后连接自动释放。
    """
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


# 根据 Alembic 运行模式选择离线或在线迁移
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
