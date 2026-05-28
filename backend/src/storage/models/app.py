# -*- coding: utf-8 -*-
"""
To C 用户体系（Phase 1+）相关数据模型。

包含用户、会话、邮件验证、用量计数、套餐、订阅、兑换码、自选股、
通知偏好、订单、支付事件、退款、发票、用户协议、对账以及审计日志等表。
"""

from datetime import datetime
from typing import Any, Dict

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from src.storage.base import Base


# ============================================================
# To C 用户体系（Phase 1 追加）
# 这些表是多用户业务逻辑的基础表。
# ============================================================


class AppUser(Base):
    """C 端用户表（与单管理员 .admin_password_hash 解耦）。"""

    __tablename__ = 'app_users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)  # 格式: pbkdf2_sha256$iter$salt_b64$hash_b64
    status = Column(String(16), nullable=False, default='active', index=True)  # active/disabled
    plan_code = Column(String(32), nullable=False, default='free', index=True)
    plan_expires_at = Column(DateTime, nullable=True)
    preferred_model = Column(String(128), nullable=True)
    email_verified_at = Column(DateTime, nullable=True)
    last_login_at = Column(DateTime, nullable=True)
    # Phase 5/6 追加: 平台运营管理员标记 + 协议同意版本
    is_admin = Column(Boolean, nullable=False, default=False, index=True)
    terms_version = Column(String(32), nullable=True)  # 用户最近一次接受的协议版本号
    # Phase 6: 账号注销冷静期字段（PIPL 合规）
    deletion_requested_at = Column(DateTime, nullable=True, index=True)  # 注销申请时间；非 NULL 表示进入冷静期
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'email': self.email,
            'status': self.status,
            'plan_code': self.plan_code,
            'plan_expires_at': self.plan_expires_at.isoformat() if self.plan_expires_at else None,
            'preferred_model': self.preferred_model,
            'email_verified_at': self.email_verified_at.isoformat() if self.email_verified_at else None,
            'last_login_at': self.last_login_at.isoformat() if self.last_login_at else None,
            'is_admin': bool(self.is_admin),
            'terms_version': self.terms_version,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class AppUserSession(Base):
    """服务端 session（cookie 仅承载 token，详细信息查表）。"""

    __tablename__ = 'app_user_sessions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('app_users.id'), nullable=False, index=True)
    token_hash = Column(String(128), nullable=False, unique=True, index=True)  # sha256(token)
    issued_at = Column(DateTime, default=datetime.now, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, nullable=True, index=True)
    user_agent = Column(String(255))
    ip = Column(String(64))


class AppUserEmailVerification(Base):
    """邮箱验证 / 密码重置一次性 token。"""

    __tablename__ = 'app_user_email_verifications'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('app_users.id'), nullable=False, index=True)
    token_hash = Column(String(128), nullable=False, unique=True, index=True)
    purpose = Column(String(16), nullable=False, index=True)  # verify / reset
    expires_at = Column(DateTime, nullable=False, index=True)
    consumed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)


class AppUserUsageCounter(Base):
    """按用户 + 日期 + kind 的用量计数（quota 服务读写）。"""

    __tablename__ = 'app_user_usage_counters'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('app_users.id'), nullable=False, index=True)
    counter_date = Column(Date, nullable=False, index=True)
    kind = Column(String(16), nullable=False, index=True)  # analysis / agent / notify
    count = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint('user_id', 'counter_date', 'kind', name='uix_app_user_usage_user_date_kind'),
    )


class AppPlan(Base):
    """套餐定义表 (Phase 2)。

    与 ``AppUser.plan_code`` 通过 ``code`` 软关联。
    """

    __tablename__ = 'app_plans'

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(32), nullable=False, unique=True, index=True)  # free / pro / pro_yearly
    name = Column(String(64), nullable=False)
    daily_analysis_limit = Column(Integer, nullable=False, default=5)
    daily_agent_limit = Column(Integer, nullable=False, default=5)
    max_stocks = Column(Integer, nullable=False, default=3)
    allowed_models = Column(Text)  # JSON: ["gpt-4o-mini"] 等
    can_webhook = Column(Boolean, nullable=False, default=False)
    price_cents = Column(Integer, nullable=False, default=0)
    currency = Column(String(8), nullable=False, default='CNY')
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class AppPlatformSetting(Base):
    __tablename__ = 'app_platform_settings'

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(64), nullable=False, unique=True, index=True)
    value = Column(Text, nullable=True)
    updated_by = Column(Integer, ForeignKey('app_users.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class AppSubscription(Base):
    """用户订阅历史 (含 trial / paid / invite)。

    Phase 2 MVP 不接支付, 仅记录手动开通 / 兑换码 / 邀请码三种来源。
    """

    __tablename__ = 'app_subscriptions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('app_users.id'), nullable=False, index=True)
    plan_code = Column(String(32), nullable=False, index=True)
    source = Column(String(16), nullable=False, default='manual', index=True)  # trial/manual/invite/redeem/paid
    started_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=True, index=True)
    note = Column(String(255))
    created_at = Column(DateTime, default=datetime.now, nullable=False)


class AppRedeemCode(Base):
    """兑换码 (一次性, 与邀请码不同, 兑换后赠送套餐时长)。"""

    __tablename__ = 'app_redeem_codes'

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(64), nullable=False, unique=True, index=True)
    plan_code = Column(String(32), nullable=False, index=True)
    grant_days = Column(Integer, nullable=False, default=30)
    redeemed_by = Column(Integer, ForeignKey('app_users.id'), index=True)
    redeemed_at = Column(DateTime, index=True)
    expires_at = Column(DateTime, nullable=True, index=True)  # 兑换码本身的过期, 与 grant_days 区分
    note = Column(String(255))
    created_at = Column(DateTime, default=datetime.now, nullable=False)


class AppUserWatchlist(Base):
    """用户自选股表 (Phase 3)。

    每个用户有独立的自选股列表，上限由 ``plan.max_stocks`` 控制。
    各套餐上限由 ``AppPlan.max_stocks`` 决定。
    """

    __tablename__ = 'app_user_watchlists'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('app_users.id'), nullable=False, index=True)
    stock_code = Column(String(32), nullable=False, index=True)
    stock_name = Column(String(128))
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    __table_args__ = (
        UniqueConstraint('user_id', 'stock_code', name='uix_app_user_watchlist_user_code'),
    )


class AppUserNotificationPref(Base):
    """用户通知偏好表 (Phase 3)。

    存储用户级通知设置：每日订阅推送开关、邮件开关、自定义 Webhook 等。
    每个用户至多一行（upsert 语义），缺行时使用兜底默认值。
    """

    __tablename__ = 'app_user_notification_prefs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('app_users.id'), nullable=False, unique=True, index=True)
    daily_push_enabled = Column(Boolean, nullable=False, default=False)  # 每日定时推送开关（Pro 专属）
    email_enabled = Column(Boolean, nullable=False, default=True)  # 邮件推送开关（Pro 专属）
    webhook_url = Column(String(1024))  # Pro 专属自定义 Webhook
    webhook_type = Column(String(32))  # feishu / wecom / discord / telegram / generic
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class AppOrder(Base):
    """订单主表 (Phase 5)。

    一笔付费对应一条订单记录；支付成功后通过 ``grant_plan`` 开通订阅。
    状态机：created → pending → paid → refunded/partial_refunded
                          └→ failed
             created → closed (超时或用户主动取消)
    """

    __tablename__ = 'app_orders'

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_no = Column(String(32), nullable=False, unique=True, index=True)  # DSA{yyyymmdd}{random10}
    user_id = Column(Integer, ForeignKey('app_users.id'), nullable=False, index=True)
    plan_code = Column(String(32), nullable=False, index=True)
    grant_days = Column(Integer, nullable=False, default=30)
    amount_cents = Column(Integer, nullable=False, default=0)
    original_amount_cents = Column(Integer, nullable=False, default=0)
    discount_cents = Column(Integer, nullable=False, default=0)
    coupon_code = Column(String(64))
    currency = Column(String(8), nullable=False, default='CNY')
    provider = Column(String(16), nullable=False, default='manual')  # wechat/alipay/manual
    provider_trade_no = Column(String(64), unique=True)  # 允许 NULL（未支付）
    status = Column(String(24), nullable=False, default='created', index=True)
    # created / pending / paid / failed / closed / refunded / partial_refunded
    client_ip = Column(String(64))
    user_agent = Column(String(512))
    quote_snapshot = Column(Text)  # JSON: 下单时套餐快照，防价格漂移
    paid_at = Column(DateTime)
    expires_at = Column(DateTime)  # 订单超时时间（默认 15min 关单）
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    __table_args__ = (
        Index('ix_app_orders_user_created', 'user_id', 'created_at'),
        Index('ix_app_orders_provider_status', 'provider', 'status'),
    )


class AppPaymentEvent(Base):
    """支付通道回调流水 (Phase 5)。

    所有回调原样落库，便于线下回放、对账与审计；
    ``signature_valid=False`` 的事件仅落库不驱动业务。
    ``provider_event_id`` 唯一约束保证幂等去重。
    """

    __tablename__ = 'app_payment_events'

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_no = Column(String(32), nullable=False, index=True)
    provider = Column(String(16), nullable=False, index=True)  # wechat / alipay
    event_type = Column(String(32), nullable=False)  # pay.success / pay.fail / refund.success / refund.fail
    provider_event_id = Column(String(128), nullable=False, unique=True)
    raw_payload = Column(Text)  # 脱敏后的原始回调 body
    signature = Column(String(512))
    signature_valid = Column(Boolean, nullable=False, default=False)
    processed = Column(Boolean, nullable=False, default=False)
    processed_at = Column(DateTime)
    received_at = Column(DateTime, default=datetime.now, nullable=False)


class AppRefund(Base):
    """退款记录 (Phase 5)。

    申请退款后等待运营审核；审核通过后调用通道退款 API。
    """

    __tablename__ = 'app_refunds'

    id = Column(Integer, primary_key=True, autoincrement=True)
    refund_no = Column(String(32), nullable=False, unique=True, index=True)  # RF{yyyymmdd}{random10}
    order_no = Column(String(32), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('app_users.id'), nullable=False, index=True)
    amount_cents = Column(Integer, nullable=False, default=0)
    reason = Column(String(512))
    reviewer_id = Column(Integer, ForeignKey('app_users.id'))  # 运营审核人
    status = Column(String(16), nullable=False, default='pending', index=True)
    # pending / approved / rejected / refunded / failed
    provider_refund_no = Column(String(64))
    revoke_subscription = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    approved_at = Column(DateTime)
    refunded_at = Column(DateTime)


class AppInvoice(Base):
    """电子发票申请 (Phase 5)。

    MVP 阶段手工开具，后期接电子发票 SaaS 自动开票。
    仅开电子普通发票（增值税普通发票）。
    """

    __tablename__ = 'app_invoices'

    id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_no = Column(String(32), nullable=False, unique=True, index=True)  # INV{yyyymmdd}{random10}
    user_id = Column(Integer, ForeignKey('app_users.id'), nullable=False, index=True)
    order_no = Column(String(32), nullable=False, index=True)
    invoice_type = Column(String(16), nullable=False, default='personal')  # personal / company
    title = Column(String(255), nullable=False)
    tax_id = Column(String(64))  # 公司必填
    amount_cents = Column(Integer, nullable=False, default=0)
    email = Column(String(255), nullable=False)
    status = Column(String(16), nullable=False, default='pending', index=True)  # pending / issued / rejected
    issued_url = Column(String(1024))  # 电子发票下载链接
    reviewer_id = Column(Integer, ForeignKey('app_users.id'))
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    issued_at = Column(DateTime)


class AppUserConsent(Base):
    """用户协议同意历史 (Phase 6)。

    每当用户首次注册、或在协议升版后重新接受协议时, 写入一条记录。
    用于合规审计 (PIPL / 用户协议变更) 与争议追溯。
    """

    __tablename__ = 'app_user_consents'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('app_users.id'), nullable=False, index=True)
    terms_version = Column(String(32), nullable=False, index=True)
    purpose = Column(String(32), nullable=False, default='register', index=True)  # register / reaccept
    ip = Column(String(64))
    user_agent = Column(String(512))
    agreed_at = Column(DateTime, default=datetime.now, nullable=False, index=True)


class AppReconciliationDiff(Base):
    """对账差异落库 (Phase 5)。

    由 ``scripts/reconcile_payments.py`` 每日跑后写入: 通道有/本地无, 本地有/通道无,
    金额不一致, 状态不一致等类型。
    """

    __tablename__ = 'app_reconciliation_diffs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    reconcile_date = Column(Date, nullable=False, index=True)
    provider = Column(String(16), nullable=False, index=True)  # wechat / alipay
    diff_type = Column(String(32), nullable=False, index=True)
    # channel_only / local_only / amount_mismatch / status_mismatch
    order_no = Column(String(32), index=True)  # 本地订单号 (channel_only 时可能为空)
    provider_trade_no = Column(String(64), index=True)
    local_amount_cents = Column(Integer)
    channel_amount_cents = Column(Integer)
    local_status = Column(String(24))
    channel_status = Column(String(24))
    detail = Column(Text)  # JSON: 通道原始账单行
    resolved = Column(Boolean, nullable=False, default=False, index=True)
    resolved_at = Column(DateTime)
    resolution_note = Column(String(512))
    created_at = Column(DateTime, default=datetime.now, nullable=False)


class AppReconciliationReport(Base):
    """每日对账总览 (Phase 5)。

    一次对账任务跑完后写一条; 便于审计 / 追溯当日是否成功对账。
    """

    __tablename__ = 'app_reconciliation_reports'

    id = Column(Integer, primary_key=True, autoincrement=True)
    reconcile_date = Column(Date, nullable=False, index=True)
    provider = Column(String(16), nullable=False, index=True)
    status = Column(String(16), nullable=False, default='clean')  # clean / has_diff / failed
    total_channel = Column(Integer, nullable=False, default=0)
    total_local = Column(Integer, nullable=False, default=0)
    diff_count = Column(Integer, nullable=False, default=0)
    note = Column(Text)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    __table_args__ = (
        UniqueConstraint('reconcile_date', 'provider', name='uix_app_recon_report_date_provider'),
    )


class AppPlanReminder(Base):
    """Plan 到期 / 续费提醒发送记录 (Phase 2 + Phase 4 收尾)。

    用于 ``run_plan_lifecycle_check`` 的幂等控制：同一个 ``(user_id, plan_code,
    expires_at, reminder_type)`` 只发送一次邮件，避免重复打扰。

    ``reminder_type`` 取值::

        expiring_7d / expiring_3d / expiring_1d  # 到期前邮件提醒
        expired                                  # 过期当日降级通知
    """

    __tablename__ = 'app_plan_reminders'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('app_users.id'), nullable=False, index=True)
    plan_code = Column(String(32), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    reminder_type = Column(String(16), nullable=False, index=True)
    sent_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            'user_id', 'plan_code', 'expires_at', 'reminder_type',
            name='uix_app_plan_reminders_user_plan_expires_type',
        ),
    )


class AppAuditLog(Base):
    """用户 / 管理员关键操作审计日志 (Phase 6)。

    永不删除；写入后只读，便于合规追溯。

    常见 action 值::

        auth.login / auth.register / auth.change_password / auth.reset_password
        plan.redeem / plan.grant
        order.create / order.cancel
        refund.create / refund.approve / refund.reject
        invoice.issue / invoice.reject
        admin.grant_plan / admin.approve_refund / admin.reject_refund
        admin.issue_invoice / admin.reject_invoice
    """

    __tablename__ = 'app_audit_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    action = Column(String(64), nullable=False, index=True)
    user_id = Column(Integer, index=True)          # 操作发起人 (C 端用户)
    admin_id = Column(Integer, index=True)         # 操作发起人 (管理员)
    target_user_id = Column(Integer, index=True)   # 被操作对象用户 ID
    target_ref = Column(String(128))               # 订单号 / 退款单号 / provider 等业务标识
    detail = Column(Text)                          # JSON 附加信息 (脱敏)
    ip = Column(String(64))
    user_agent = Column(String(512))
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)


class AppGrowthEvent(Base):
    """增长埋点事件表 (Phase 6)。

    记录用户关键转化漏斗事件，用于统计注册→首单分析→付费的转化率。
    前端通过 ``POST /api/v1/usage/events`` 上报；后端关键节点直接写库。

    常见 event 值::

        page.view / user.register / user.first_analysis / user.upgrade_click
        user.upgrade_success / user.daily_push_enable
        quota.exceeded / payment.initiated / payment.success
    """

    __tablename__ = 'app_growth_events'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('app_users.id'), nullable=True, index=True)  # 未登录时为 NULL
    session_id = Column(String(128), nullable=True, index=True)  # 匿名标识（浏览器生成）
    event = Column(String(64), nullable=False, index=True)
    props = Column(Text)  # JSON: 附加属性（脱敏后写入）
    ip = Column(String(64))
    user_agent = Column(String(512))
    ts = Column(DateTime, default=datetime.now, nullable=False, index=True)


class AppNotice(Base):
    """平台公告表（Phase 6 公告中心）。

    运营通过管理后台创建公告，用户通过 /notices 页面和顶栏铃铛查看。
    支持 priority（info/warning/danger）、is_pinned（置顶）、is_published（发布状态）。
    """

    __tablename__ = 'app_notices'

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)  # Markdown 内容
    notice_type = Column(String(16), nullable=False, default='info')  # info / warning / danger
    is_pinned = Column(Boolean, nullable=False, default=False)
    is_published = Column(Boolean, nullable=False, default=False, index=True)
    target_plan = Column(String(32), nullable=True)  # NULL=所有用户; 'pro'=仅付费用户
    author_id = Column(Integer, ForeignKey('app_users.id'), nullable=True)
    published_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)  # NULL=永不过期
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    __table_args__ = (
        Index('ix_app_notices_published_pinned', 'is_published', 'is_pinned'),
    )


__all__ = [
    "AppUser",
    "AppUserSession",
    "AppUserEmailVerification",
    "AppUserUsageCounter",
    "AppPlan",
    "AppPlatformSetting",
    "AppSubscription",
    "AppRedeemCode",
    "AppUserWatchlist",
    "AppUserNotificationPref",
    "AppOrder",
    "AppPaymentEvent",
    "AppRefund",
    "AppInvoice",
    "AppUserConsent",
    "AppReconciliationDiff",
    "AppReconciliationReport",
    "AppPlanReminder",
    "AppAuditLog",
    "AppGrowthEvent",
    "AppNotice",
]
