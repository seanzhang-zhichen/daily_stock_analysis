# -*- coding: utf-8 -*-
"""协议同意 (Terms of Service / Privacy / Risk Disclosure) 服务 (Phase 6)。

职责:

- 维护协议三件套的当前版本号 (``CURRENT_TERMS_VERSION``)。
- 注册 / 后续协议升版时调用 :func:`record_consent` 写一条 ``AppUserConsent``,
  并同步更新 ``AppUser.terms_version``。
- 提供 :func:`needs_reaccept` 判断当前用户是否需要重新勾选协议。

调用方:

- ``src/users/service.py::register_user`` 在用户注册成功后调用。
- 未来 ``/api/v1/account/accept-terms`` 可在用户登录后弹出确认时调用 ``record_consent``。

协议三件套静态页路径:

- ``/legal/terms`` - 用户服务协议
- ``/legal/privacy`` - 隐私政策
- ``/legal/risk-disclosure`` - 投资风险揭示书
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from src.storage import AppUser, AppUserConsent

logger = logging.getLogger(__name__)


# 当前协议版本号。每次协议三件套有实质变更时, 在此 bump 一次,
# 已注册用户登录后会被引导重新勾选。
CURRENT_TERMS_VERSION = "2026-05-18"


def record_consent(
    db: Session,
    *,
    user: AppUser,
    terms_version: str = CURRENT_TERMS_VERSION,
    purpose: str = "register",
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> AppUserConsent:
    """记录用户同意协议, 并同步 ``AppUser.terms_version``。

    Args:
        db: SQLAlchemy Session, 由调用方负责事务提交。
        user: 当前用户。
        terms_version: 用户同意的协议版本号 (默认当前版本)。
        purpose: ``register`` (首次注册) / ``reaccept`` (协议升版后重新确认)。
        ip: 用户 IP, 用于合规审计。
        user_agent: 浏览器 UA。

    Returns:
        新写入的 :class:`AppUserConsent` 记录。
    """
    record = AppUserConsent(
        user_id=user.id,
        terms_version=terms_version,
        purpose=purpose,
        ip=(ip or "")[:64] or None,
        user_agent=(user_agent or "")[:512] or None,
    )
    db.add(record)
    user.terms_version = terms_version
    db.add(user)
    db.flush()
    logger.info(
        "user_consent recorded: user_id=%s version=%s purpose=%s",
        user.id,
        terms_version,
        purpose,
    )
    return record


def needs_reaccept(user: AppUser, current_version: str = CURRENT_TERMS_VERSION) -> bool:
    """判断用户是否需要重新确认协议 (协议升版后)。"""
    if user is None:
        return False
    if not user.terms_version:
        return True
    return str(user.terms_version) != str(current_version)
