# To C 多用户模式

本文档描述 DSA 当前的多用户运行模型。业务 API 默认使用 `dsa_user_session` 识别 `AppUser`，不再提供单用户 / 单管理员兼容放行路径。

## 1. 运行模型

| 范围 | 行为 |
|------|------|
| `/api/v1/account/*` | 注册、登录、状态查询、密码重置、兑换码、模型偏好等账号能力。 |
| `/api/v1/billing/plans` | 公开读取套餐目录；如请求带有效 `dsa_user_session`，同时返回当前 plan 摘要。 |
| 其它 `/api/v1/*` 业务接口 | 必须携带有效 `dsa_user_session`；中间件解析后写入 `request.state.user`。 |
| Bot / CLI / 调度等内部路径 | 仍可通过显式 `user_id` 参数写入；调度器已按用户自选股与通知偏好分桶执行。 |

`ENABLE_USER_REGISTRATION` 已不再作为总开关。`src.users.config.load_user_mode_settings()` 固定返回 `enabled=True`，`is_user_mode_enabled()` 固定返回 `True`。

## 2. 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `USER_PUBLIC_REGISTRATION_ENABLED` | `true` | 是否允许公开注册；关闭后仍保留多用户体系，但注册入口应由运营建号或邀请流程承接。 |
| `USER_REQUIRE_EMAIL_VERIFICATION` | `false` | 启用后注册不会立即创建 session，必须验证邮箱。 |
| `USER_INVITE_CODES` |  | 逗号分隔，配置后注册必填邀请码。 |
| `USER_SESSION_TTL_HOURS` | `336`（14 天） | session cookie 过期时间。 |
| `USER_VERIFICATION_TTL_HOURS` | `24` | 邮箱验证 token 有效期。 |
| `USER_RESET_TTL_HOURS` | `2` | 密码重置 token 有效期。 |
| `USER_FREE_DAILY_ANALYSIS` | `5` | free 档每日分析次数默认值。 |
| `USER_FREE_DAILY_AGENT` | `5` | free 档每日 Agent 次数默认值。 |
| `USER_FREE_MAX_STOCKS` | `3` | free 档自选股上限默认值。 |
| `USER_EMAIL_BACKEND` | `log` | `log`（默认，仅写日志）/ `smtp`（使用 `EMAIL_SENDER` / `EMAIL_PASSWORD` / `SMTP_HOST` / `SMTP_PORT`）。 |
| `USER_PUBLIC_BASE_URL` |  | 邮件 / Webhook 内引用本服务的公开 base URL（不含末尾 `/`），用于拼接一键退订链接。未配置时按 `PUBLIC_BASE_URL` → `APP_BASE_URL` → `http://localhost:8000` 兜底。 |
| `UNSUBSCRIBE_SIGNING_KEY` |  | 一键退订 token 的 HMAC 签名密钥；缺失时按 `ADMIN_API_SECRET` 兜底，生产环境必须显式配置以保证 token 不可伪造。 |
| `USER_REGISTER_DISPOSABLE_BLOCK` | `true` | 是否拦截一次性 / 临时邮箱注册（Phase 6 §5.8.1）；命中后注册接口返回 `invalid_email` 且写一条 `auth.register.blocked` 审计日志。 |
| `USER_DISPOSABLE_EMAIL_DOMAINS` |  | 逗号分隔的额外 disposable 邮箱域名，与内置黑名单合并生效（大小写不敏感）。 |
| `USER_DISPOSABLE_EMAIL_DOMAINS_REPLACE` | `false` | 设为 `true` 时用 `USER_DISPOSABLE_EMAIL_DOMAINS` **替换** 内置黑名单，便于全自定义运营策略。 |
| `USER_REGISTER_RATE_WINDOW_HOURS` | `24` | 注册尝试频率限制的滚动窗口长度（小时）。 |
| `USER_REGISTER_IP_DAILY_MAX` | `10` | 同一 IP 在滚动窗口内允许的最大注册尝试次数；超过后续请求返回 `rate_limited`。设为 `0` 关闭 IP 限频。 |
| `USER_REGISTER_EMAIL_DAILY_MAX` | `3` | 同一邮箱（哈希）在滚动窗口内允许的最大注册尝试次数；超过后续请求返回 `rate_limited`。设为 `0` 关闭邮箱限频。 |

## 3. API

挂载在 `/api/v1/account/*`：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/status` | 拉取登录状态、当前用户、当前 plan 快照、配额。 |
| POST | `/register` | 邮箱+密码注册；启用邮箱验证时不下发 session。 |
| POST | `/login` | 邮箱+密码登录，下发 `dsa_user_session` cookie。 |
| POST | `/logout` | 吊销当前 session 并清 cookie。 |
| POST | `/verify-email` | 用 token 完成邮箱验证。 |
| POST | `/request-password-reset` | 发送重置邮件，存在性不暴露。 |
| POST | `/reset-password` | 用 token 重置密码并吊销该用户所有 session。 |
| POST | `/change-password` | 登录态下修改密码，并吊销当前 session。 |
| GET  | `/me` | 当前登录用户信息。 |
| POST | `/redeem` | 使用兑换码升级套餐。 |
| GET  | `/model-preference` | 列出管理员配置且当前套餐允许的模型，并返回当前用户首选模型。 |
| PATCH | `/model-preference` | 更新当前用户首选模型；只能选择管理员配置且当前套餐允许的模型。 |
| GET | `/watchlist` | 查询当前用户自选股列表，含 `maxStocks` 上限。 |
| POST | `/watchlist` | 添加一只自选股；超出 `plan.max_stocks` 返回 422。 |
| PUT | `/watchlist` | 全量替换自选股列表。 |
| DELETE | `/watchlist/{stock_code}` | 删除一只自选股。 |
| GET | `/notification-prefs` | 查询当前用户通知偏好。 |
| PATCH | `/notification-prefs` | 更新通知偏好（`dailyPushEnabled` / `emailEnabled` / `webhookUrl` / `webhookType`）；开启 `emailEnabled=true` 或 `dailyPushEnabled=true` 需 `plan.is_pro=true`；Webhook 需 `plan.canWebhook=true`。 |
| GET | `/notification-prefs/unsubscribe` | 通过邮件中的 HMAC token 一键退订（无需登录，已加入 `AuthMiddleware` 白名单）；成功后关闭 `dailyPushEnabled`（`action=daily`）或同时关闭 `emailEnabled`（`action=email`），并写入 `app_audit_logs`。 |

挂载在 `/api/v1/billing/*`：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/plans` | 列出当前可见套餐目录；当 `app_plans` 表为空时回退到内置 `free` + `pro` + `pro_yearly` 兜底目录。 |
| GET | `/subscription` | 查询当前用户的订阅状态 + 历史记录，要求登录。 |
| GET | `/orders` | 查询当前用户订单列表。 |
| POST | `/orders` | 创建支付订单。 |
| GET | `/orders/{order_no}` | 查询单个订单状态，供支付弹窗轮询。 |
| POST | `/orders/{order_no}/pay` | 拉起微信 / 支付宝支付，返回二维码内容；mock 模式下返回 mock 二维码。 |
| POST | `/orders/{order_no}/mock-pay` | mock 支付模式下模拟支付成功。 |
| POST | `/orders/{order_no}/cancel` | 取消未支付订单。 |
| POST | `/refunds` | 对已支付订单提交退款申请。 |
| GET | `/refunds/{refund_no}` | 查询退款状态。 |
| POST | `/invoices` | 提交发票申请。 |
| GET | `/invoices` | 查询当前用户发票列表。 |

## 4. 数据表

- `app_users`：用户主表（邮箱唯一、PBKDF2 哈希密码、`plan_code`、`status`）。
- `app_user_sessions`：服务端 session（`token_hash` / `expires_at` / `revoked_at`）。
- `app_user_email_verifications`：邮箱验证 / 密码重置一次性 token。
- `app_user_usage_counters`：按用户 + 日期 + kind 的用量计数。
- `app_plans` / `app_subscriptions` / `app_redeem_codes`：套餐、订阅和兑换码。
- `app_user_watchlists`：用户自选股（Phase 3），上限由 `plan.max_stocks` 控制。
- `app_user_notification_prefs`：用户通知偏好（Phase 3），含每日推送开关、邮件开关、Pro Webhook。
- `app_orders` / `app_payment_events` / `app_refunds` / `app_invoices`：订单、支付回调流水、退款、发票（Phase 5）。
- `app_user_consents`：用户同意协议记录（版本 / IP / UA / 场景，Phase 5/6）。`app_users` 包含 `is_admin` 与 `terms_version` 列；schema 变更统一通过 Alembic migration 管理。
- `app_reconciliation_diffs` / `app_reconciliation_reports`：每日对账脚本输出的差异明细与汇总（Phase 5）。
- `app_audit_logs`：关键账号、支付、退订、管理员等操作审计日志。
- `app_notices`：公告中心。
- `app_growth_events`：增长埋点事件。

## 5. 中间件与依赖

- `api/middlewares/auth.py`：保护非豁免 `/api/v1/*` 业务接口，只接受有效 `dsa_user_session`。
- `api.deps.get_current_user`：业务 endpoint 的默认依赖；未登录直接返回 401。
- `api.deps.get_optional_current_user`：仅作为 `get_current_user` 和公开账号类接口内部复用的解析 helper，不应用于普通业务接口放行。
- `api.deps.get_admin_user`：`/api/v1/admin/*` 与 `/api/v1/system/config*` 管理接口专用依赖，要求登录且 `is_admin=True`，否则 401/403。`scripts/grant_admin.py` 提供命令行授予 / 撤销 admin 角色。

## 6. 前端入口

- `/login`：邮箱+密码登录。
- `/register`：邮箱注册，是否允许公开注册由 `USER_PUBLIC_REGISTRATION_ENABLED` 控制。
- `/forgot-password`：发送重置邮件 → 用 token 重置密码两步流程。
- `/account`：账户信息 + 修改密码 + 通知偏好 + 模型偏好 + 退出登录，入口跳转 `/billing`、`/watchlist`、`/account/orders` 与 `/account/invoices`。
- `/watchlist`：管理当前用户自选股列表，受 `plan.maxStocks` 限制。
- `/billing`：套餐对比 + 兑换码 + 订阅历史 + `PaymentDialog`（二维码 + 轮询，mock 模式下提供“模拟支付成功”按钮）。
- `/account/orders` / `/account/invoices`：订单 / 发票列表与取消 / 退款入口。
- `/legal/terms` / `/legal/privacy` / `/legal/risk-disclosure`：协议三件套静态页，未登录可访问；注册表单勾选同意后 `register_user` 调 `record_consent` 写 `app_user_consents`。
- `/settings`：部署级系统设置入口，仅非用户模式部署者或 `is_admin=True` 平台管理员可访问；普通 C 端用户侧边栏不展示该入口，直接访问会跳回 `/account`。
- `/admin`：运营后台，仅 `is_admin=True` 可访问，含概览 / 订单 / 退款审核 / 发票审核 / 用户 / 手动 grant-plan 六个标签页。
- `/notices`：公告中心，公开可访问。
- `/help`：帮助中心，提供 FAQ、反馈指引、配置入口与免责声明。
- `/onboarding`：注册后自选股引导页，可添加最多 `min(plan.maxStocks, 3)` 只股票。

## 7. 配额与 Plan 解析

- `src/users/plans.py` 提供 `resolve_user_plan` / `grant_plan` / `redeem_code`。
- `src/users/quota_guard.py` 提供 `enforce_quota` / `refund_quota` / `quota_exceeded_payload`。
- `enforce_quota` 要求调用方传入已认证 `AppUser`；未登录调用属于调用错误，不再 bypass。
- `enforce_quota` 已接入：
  - `/api/v1/analysis/analyze`：同步 1 次，异步按提交股票数扣减；队列拒绝 / 重复任务 / 同步或异步业务失败自动 refund。
  - `/api/v1/agent/chat`、`/api/v1/agent/chat/stream`、`/api/v1/agent/research`：进入业务前扣 1 次，异常、超时、流式 error 或非流式结果 `success=false` 时退还。

## 8. 已落地的数据隔离

- `/api/v1/history/*`：强制登录，并以 `current_user.id` 过滤历史记录。
- `/api/v1/analysis/analyze`：强制登录，分析写入和异步任务透传 `current_user.id`；`/api/v1/analysis/tasks`、`/tasks/stream`、`/status/{task_id}` 均按当前用户过滤。
- `/api/v1/portfolio/*`：强制登录，并以 `str(current_user.id)` 作为 `owner_id` 创建、查询、修改、删除。
- `/api/v1/alerts/*`：强制登录，规则及触发 / 通知历史按当前用户隔离。
- `/api/v1/agent/*`：强制登录，会话以当前用户隔离。

## 9. 已落地 / 尚未落地

**已落地能力**：
- **账号与隔离**：邮箱注册 / 登录 / 验证 / 找回密码、业务 API 强制 `dsa_user_session`、历史 / 分析 / 投资组合 / 告警 / Agent 会话按用户隔离。
- **套餐与配额**：`quota_guard` 已接入分析与 Agent；超额返回 402 `quota_exceeded`，前端全局弹出升级引导。
- **自选股与通知**：`app_user_watchlists`、`app_user_notification_prefs`、`/watchlist`、账户页通知偏好、HTML 邮件、一键退订和 Pro Webhook 已上线；免费档不能开启 AI 分析报告邮件推送。
- **模型路由**：`src/users/model_router.py` 已接入 `GeminiAnalyzer` 与 `LLMToolAdapter`；用户只能从管理员配置的模型中选择首选模型，运行时在 `plan.allowed_models` 非空时过滤平台模型，空列表表示不额外限制。
- **支付与商业化**：订单、支付、退款、发票、微信 / 支付宝下单与退款 gateway、支付回调验签、IP 白名单、签名失败告警、真实账单拉取与对账脚本均已接入；mock 支付仍可用于本地联调。
- **运营与合规**：协议三件套、注册同意落库、运营后台、公告中心、帮助中心、Sentry、增长埋点、账号注销、个人数据导出、续费提醒与 SQLite 备份脚本已上线。

**尚未落地 / 待运营确认**：
- HTML 邮件模板视觉打磨（运营素材定稿后再迭代）。
- 多用户并发调度与 provider rate limit 节流（当前按用户串行执行）。
- 语言与报告偏好等个人配置迁移（模型偏好已支持 per-user）。
- `allowed_models` 具体套餐分档运营配置与平台 Key 池用量看板。
- 支付通道生产小额端到端验证（待商户准入与证书下发后执行）。
- 邀请奖励 / 推荐裂变与公告同步邮件给付费用户。
