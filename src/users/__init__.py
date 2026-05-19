# -*- coding: utf-8 -*-
"""To C 用户体系（Phase 1）。

模块边界:
- :mod:`src.users.config` 解析用户体系环境变量。
- :mod:`src.users.passwords` 处理密码哈希、强度校验、token 哈希。
- :mod:`src.users.sessions` 处理签名 cookie 的生成与校验。
- :mod:`src.users.repository` 用 SQLAlchemy 访问 ``app_users`` 等表。
- :mod:`src.users.service` 编排注册、登录、邮箱验证、密码重置等用例。
- :mod:`src.users.email` 抽象邮件发送（默认开发模式只写日志）。
- :mod:`src.users.errors` 统一业务错误码。

该模块是业务 API 的默认认证与用户上下文来源。
"""

from src.users.config import is_user_mode_enabled  # noqa: F401
from src.users.errors import UserError, UserErrorCode  # noqa: F401
from src.users.quota import (  # noqa: F401
    KIND_AGENT,
    KIND_ANALYSIS,
    KIND_NOTIFY,
    QuotaConfig,
    QuotaSnapshot,
    get_quota_snapshot,
    get_remaining,
    refund,
    try_consume,
)
from src.users.quota_guard import (  # noqa: F401
    QuotaOutcome,
    enforce_quota,
    quota_exceeded_payload,
    refund_quota,
)
from src.users.plans import (  # noqa: F401
    ResolvedPlan,
    grant_plan,
    redeem_code,
    resolve_user_plan,
)
from src.users.byok import (  # noqa: F401
    ByokCredentialView,
    SUPPORTED_PROVIDERS,
    decrypt_secret,
    delete_credential,
    encrypt_secret,
    get_decrypted_key,
    list_credentials,
    upsert_credential,
)
from src.users.model_router import (  # noqa: F401
    ModelRoute,
    as_litellm_kwargs,
    resolve_model_route,
)
from src.users.watchlist import (  # noqa: F401
    WatchlistItem,
    add_stock,
    count_stocks,
    list_stocks,
    remove_stock,
    set_watchlist,
)
from src.users.notification_prefs import (  # noqa: F401
    ALLOWED_WEBHOOK_TYPES,
    NotificationPrefs,
    get_prefs,
    get_users_with_daily_push,
    update_prefs,
)
