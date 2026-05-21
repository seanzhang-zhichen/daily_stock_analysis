# To C 多用户模式

本文档描述 DSA 当前的多用户运行模型。业务 API 默认使用 `dsa_user_session` 识别 `AppUser`，不再提供单用户 / 单管理员兼容放行路径。

## 1. 运行模型

| 范围 | 行为 |
|------|------|
| `/api/v1/account/*` | 注册、登录、状态查询、密码重置、兑换码、BYOK 管理等账号能力。 |
| `/api/v1/billing/plans` | 公开读取套餐目录；如请求带有效 `dsa_user_session`，同时返回当前 plan 摘要。 |
| 其它 `/api/v1/*` 业务接口 | 必须携带有效 `dsa_user_session`；中间件解析后写入 `request.state.user`。 |
| Bot / CLI / 调度等内部路径 | 仍可通过显式 `user_id` 参数写入；后续 Phase 继续补齐按用户分桶与通知偏好。 |

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
| `UNSUBSCRIBE_SIGNING_KEY` |  | 一键退订 token 的 HMAC 签名密钥；缺失时按 `DATA_ENCRYPTION_KEY` → `ADMIN_API_SECRET` 兜底，生产环境必须显式配置以保证 token 不可伪造。 |
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
| GET  | `/api-keys` | 列出当前用户的 BYOK Key（脱敏掩码）。 |
| POST | `/api-keys` | 新增 / 覆盖一个 provider 的 BYOK Key，仅 `plan.canByok=true` 可用。 |
| DELETE | `/api-keys/{provider}` | 删除指定 provider 的 BYOK Key。 |
| GET | `/watchlist` | 查询当前用户自选股列表，含 `maxStocks` 上限。 |
| POST | `/watchlist` | 添加一只自选股；超出 `plan.max_stocks` 返回 422。 |
| PUT | `/watchlist` | 全量替换自选股列表。 |
| DELETE | `/watchlist/{stock_code}` | 删除一只自选股。 |
| GET | `/notification-prefs` | 查询当前用户通知偏好。 |
| PATCH | `/notification-prefs` | 更新通知偏好（`dailyPushEnabled` / `emailEnabled` / `webhookUrl`）；开启 `emailEnabled=true` 或 `dailyPushEnabled=true` 需 `plan.is_pro=true`；Webhook 需 `plan.canWebhook=true`。 |
| GET | `/notification-prefs/unsubscribe` | 通过邮件中的 HMAC token 一键退订（无需登录，已加入 `AuthMiddleware` 白名单）；成功后关闭 `dailyPushEnabled`（`action=daily`）或同时关闭 `emailEnabled`（`action=email`），并写入 `app_audit_logs`。 |

挂载在 `/api/v1/billing/*`：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/plans` | 列出当前可见套餐目录；当 `app_plans` 表为空时回退到内置 `free` + `pro` + `pro_yearly` 兜底目录。 |
| GET | `/subscription` | 查询当前用户的订阅状态 + 历史记录，要求登录。 |

## 4. 数据表

- `app_users`：用户主表（邮箱唯一、PBKDF2 哈希密码、`plan_code`、`status`）。
- `app_user_sessions`：服务端 session（`token_hash` / `expires_at` / `revoked_at`）。
- `app_user_email_verifications`：邮箱验证 / 密码重置一次性 token。
- `app_user_usage_counters`：按用户 + 日期 + kind 的用量计数。
- `app_plans` / `app_subscriptions` / `app_redeem_codes`：套餐、订阅和兑换码。
- `app_user_byok_credentials`：用户自带 API Key（加密落库）。
- `app_user_watchlists`：用户自选股（Phase 3），上限由 `plan.max_stocks` 控制。
- `app_user_notification_prefs`：用户通知偏好（Phase 3），含每日推送开关、邮件开关、Pro Webhook。
- `app_orders` / `app_payment_events` / `app_refunds` / `app_invoices`：订单、支付回调流水、退款、发票（Phase 5）。
- `app_user_consents`：用户同意协议记录（版本 / IP / UA / 场景，Phase 5/6）。`app_users` 同步增加 `is_admin` 与 `terms_version` 列，启动时通过 `_ensure_app_users_extra_columns` 幂等补列。
- `app_reconciliation_diffs` / `app_reconciliation_reports`：每日对账脚本输出的差异明细与汇总（Phase 5）。

## 5. 中间件与依赖

- `api/middlewares/auth.py`：保护非豁免 `/api/v1/*` 业务接口，只接受有效 `dsa_user_session`。
- `api.deps.get_current_user`：业务 endpoint 的默认依赖；未登录直接返回 401。
- `api.deps.get_optional_current_user`：仅作为 `get_current_user` 和公开账号类接口内部复用的解析 helper，不应用于普通业务接口放行。
- `api.deps.get_admin_user`：`/api/v1/admin/*` 与 `/api/v1/system/config*` 管理接口专用依赖，要求登录且 `is_admin=True`，否则 401/403。`scripts/grant_admin.py` 提供命令行授予 / 撤销 admin 角色。

## 6. 前端入口

- `/login`：邮箱+密码登录。
- `/register`：邮箱注册，是否允许公开注册由 `USER_PUBLIC_REGISTRATION_ENABLED` 控制。
- `/forgot-password`：发送重置邮件 → 用 token 重置密码两步流程。
- `/account`：账户信息 + 修改密码 + 退出登录，入口跳转 `/billing` 与 `/account/api-keys`。
- `/billing`：套餐对比 + 兑换码 + 订阅历史 + `PaymentDialog`（二维码 + 轮询，mock 模式下提供“模拟支付成功”按钮）。
- `/account/api-keys`：BYOK 管理，free 用户会被引导到 `/billing` 升级。
- `/account/orders` / `/account/invoices`：订单 / 发票列表与取消 / 退款入口。
- `/legal/terms` / `/legal/privacy` / `/legal/risk-disclosure`：协议三件套静态页，未登录可访问；注册表单勾选同意后 `register_user` 调 `record_consent` 写 `app_user_consents`。
- `/settings`：部署级系统设置入口，仅非用户模式部署者或 `is_admin=True` 平台管理员可访问；普通 C 端用户侧边栏不展示该入口，直接访问会跳回 `/account`。
- `/admin`：运营后台，仅 `is_admin=True` 可访问，含概览 / 订单 / 退款审核 / 发票审核 / 用户 / 手动 grant-plan 六个标签页。

## 7. 配额与 Plan 解析

- `src/users/plans.py` 提供 `resolve_user_plan` / `grant_plan` / `redeem_code`。
- `src/users/quota_guard.py` 提供 `enforce_quota` / `refund_quota` / `quota_exceeded_payload`。
- `enforce_quota` 要求调用方传入已认证 `AppUser`；未登录调用属于调用错误，不再 bypass。
- `enforce_quota` 已接入：
  - `/api/v1/analysis/analyze`：同步 1 次，异步按提交股票数扣减；队列拒绝 / 重复任务自动 refund。
  - `/api/v1/agent/chat`、`/api/v1/agent/chat/stream`、`/api/v1/agent/research`：进入业务前扣 1 次，失败 / 超时 / 流式 error 时退还。

## 8. 已落地的数据隔离

- `/api/v1/history/*`：强制登录，并以 `current_user.id` 过滤历史记录。
- `/api/v1/analysis/analyze`：强制登录，分析写入和异步任务透传 `current_user.id`。
- `/api/v1/portfolio/*`：强制登录，并以 `str(current_user.id)` 作为 `owner_id` 创建、查询、修改、删除。
- `/api/v1/alerts/*`：强制登录，规则及触发 / 通知历史按当前用户隔离。
- `/api/v1/agent/*`：强制登录，会话以当前用户隔离。

## 9. 已落地 / 尚未落地

**Phase 3 已落地（本次）**：
- `app_user_watchlists` 与 `app_user_notification_prefs` 数据表。
- `src/users/watchlist.py` 与 `src/users/notification_prefs.py` 服务层。
- `/api/v1/account/watchlist` (GET/POST/PUT/DELETE) 与 `/api/v1/account/notification-prefs` (GET/PATCH) endpoint。
- `run_per_user_scheduled_analysis`：调度器每次触发后按用户分桶执行分析，基于 `app_user_notification_prefs.daily_push_enabled` 筛选订阅用户。
- `src/users/notification_delivery.py`：HTML 邮件渲染（`markdown2` + 一键退订链接 + 免责声明）与多渠道 Webhook 投递（飞书 / 企业微信 / Discord / Telegram / 通用 JSON），单用户 / 单渠道失败仅日志不抛。
- `src/users/unsubscribe.py`：HMAC 无状态退订 token；公开 endpoint `GET /api/v1/account/notification-prefs/unsubscribe?token=...` 已加入 `AuthMiddleware` 白名单。

**Phase 5/6 骨架本次落地**：
- 协议三件套静态页 + 注册勾选同意 + `app_user_consents` 落库（`src/users/consents.py` 集中维护 `CURRENT_TERMS_VERSION`）。
- 前端 `PaymentDialog`：`/billing` 创建订单 → 二维码 → 每 2s 轮询订单状态（最多 15min）→ 成功后刷新订阅；`PAYMENT_MOCK_ENABLED=true` 时提供手动模拟成功按钮。
- 运营后台：`/api/v1/admin/*` + `/admin` Web 页面（订单 / 退款审核 / 发票审核 / 手动 grant-plan / 用户 / 统计）；`scripts/grant_admin.py` 命令行授权。
- 对账脚本骨架：`scripts/reconcile_payments.py`（默认 dry-run，`--commit` 写库），输出 `channel_only` / `local_only` / `amount_mismatch` / `status_mismatch` 四类差异，落 `app_reconciliation_diffs` / `app_reconciliation_reports`；通道拉取接口 `fetch_channel_settlements` 预留 SDK 接入点。

**后续 Phase 尚未落地**：
- HTML 邮件模板的视觉打磨（运营素材定稿后再迭代）。
- 多用户并发调度（当前串行，用户量上来后再改线程池）；按 provider rate limit 节流。
- 语言、模型偏好等个人配置迁移（仍依赖全局 `SystemConfig`）。
- BYOK 凭证已可写入，但 LLM 路由仍需进一步按 `current_user` / plan 动态切换。
- 微信 / 支付宝 SDK 真实接入（替换 mock 分支）；对账脚本的真实账单拉取 + 邮件 / Webhook 告警。
- 退款通道 API 调用与发票 `issued_url` 写回 + 邮件回执。
