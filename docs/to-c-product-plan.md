# DSA To C 化产品规划

把当前单租户「自选股 AI 分析系统」改造为面向个人投资者的多租户 To C 产品：邮箱密码注册登录、按用户隔离自选股/报告/通知，所有用户使用平台管理员配置的模型渠道，用户只在当前套餐允许范围内选择模型，存量数据不迁移、全新建库。

> 本文档锁定**产品形态、用户分层、阶段拆分与风险点**，是后续多 Phase 改造的总纲。
> 当前已落地的工程骨架（环境变量、API、表结构、回滚方式）请参考 [`docs/to-c-mode.md`](./to-c-mode.md)；
> 关键页面线框请参考 [`docs/to-c-product-wireframes.md`](./to-c-product-wireframes.md)；
> 用户故事、验收口径与实现映射请参考 [`docs/to-c-user-stories.md`](./to-c-user-stories.md)。

## 0. 当前进度速览

| Phase | 状态 | 说明 |
| --- | --- | --- |
| Phase 0：产品规划 | ✅ 已完成 | 本文档 + 线框 + 用户故事 + 决策锁定。 |
| Phase 1：用户体系骨架 | ✅ 基本闭环 | `app_users` / `app_user_sessions` / `app_user_email_verifications` / `app_user_usage_counters` / `app_plans` / `app_subscriptions` / `app_redeem_codes` 等表已建好，`/api/v1/account/*` 已上线，前端 `/login` `/register` `/forgot-password` `/verify-email` 均可用，首次登录且自选股为空时进入 `/onboarding`，邮箱验证成功后的登录入口也携带 `/onboarding` 跳转；业务 API 已默认要求 `dsa_user_session`，history / analysis / portfolio / alerts / agent 已按当前用户隔离。剩余：语言偏好仍依赖全局 `SystemConfig`。详见 [`to-c-mode.md`](./to-c-mode.md)。 |
| Phase 2：商业化骨架 | 🟡 主要落地 | 配额服务已通过 `src/users/quota_guard.py` 接入 `/analysis/analyze`、`/agent/chat`、`/agent/chat/stream`、`/agent/research`；`AppPlan` / `AppSubscription` / `AppRedeemCode` 三张表 + `resolve_user_plan` / `redeem_code` / `grant_plan` 已上线；`/api/v1/account/redeem`、`/api/v1/account/model-preference`、`/api/v1/billing/{plans,subscription}` endpoint 已上线；前端 `/account` `/billing` 页面 + 顶栏 `QuotaIndicator` + 全局 `QuotaExceededDialog` 已可用；模型路由运行时已接入（见 Phase 4）。Plan 到期 / 续费闭环已落地：`app_plan_reminders` + `src/users/plan_lifecycle.py` 支持 7/3/1 天到期前邮件提醒、过期当日自动降级 free + 通知邮件，`/api/v1/account/status.renewal` + 前端 `RenewalBanner` 顶栏续费提示已上线。剩余：套餐 / 订阅的运营后台、平台 Key 配额池统一管理、支付闭环。 |
| Phase 3：调度与通知 To C 化 | ✅ 基本闭环 | 新增 `app_user_watchlists`（per-user 自选股，含 `max_stocks` 上限）与 `app_user_notification_prefs`（`daily_push_enabled` / `email_enabled` / Webhook）两张表；服务层 `src/users/watchlist.py` / `src/users/notification_prefs.py`；API `/api/v1/account/watchlist` (GET/POST/PUT/DELETE) 与 `/api/v1/account/notification-prefs` (GET/PATCH) 已上线；`main.py` `run_per_user_scheduled_analysis` 按开启日推且当前仍为 Pro 的用户分桶执行分析、调用 `src/users/notification_delivery.py` 发送 HTML 邮件（含一键退订链接，渲染走 `markdown2`）并按 `webhook_type` 分发到飞书 / 企业微信 / Discord / Telegram / 通用 JSON；新增 `src/users/unsubscribe.py` HMAC 无状态退订 token + `GET /api/v1/account/notification-prefs/unsubscribe` 公开端点（已加入 `AuthMiddleware` 白名单），操作写 `app_audit_logs`。前端 `AccountPage` 已新增「我的自选股」与「通知偏好」卡片（含 Pro Webhook 配置 UI）。剩余：HTML 邮件模板的视觉打磨（运营素材定稿后）、多用户并发调度（当前串行）。 |
| Phase 4：模型偏好 + Pro 能力 | ✅ 主要落地 | `app_users.preferred_model` 与 `/api/v1/account/model-preference` 已就位，前端账户页支持用户选择管理员配置模型；`src/users/model_router.py` 已接入 `GeminiAnalyzer._call_litellm` 与 `LLMToolAdapter.call_completion`：平台路由在 `plan.allowed_models` 非空时过滤模型列表，空列表表示不额外限制，并优先使用用户首选模型；`user_id` 从 API 层 → factory → adapter 全链路已透传。Plan 到期自动降级已通过 `src/users/plan_lifecycle.py::downgrade_expired_user` 落地，调度器每日触发后回退 `plan_code` 至 free 并写 `app_subscriptions` source='expire'。剩余：`allowed_models` 具体运营配置与平台 Key 用量看板。 |
| Phase 5：商业化闭环（含支付） | 🟡 数据层 + API + 前端 + 下单/退款 SDK 已落地，待生产验证 | **已落地**：`app_orders` / `app_payment_events` / `app_refunds` / `app_invoices` 四张 ORM 表 + `src/services/billing/order_service.py` 服务层（订单创建幂等、状态机流转、`fulfill_order` 调 `grant_plan`、退款/发票申请、回调落库）；`/api/v1/billing/` 扩充 orders / pay / cancel / callbacks/wechat / callbacks/alipay / refunds / invoices 全部用户侧 endpoint，`PAYMENT_MOCK_ENABLED=true` 时 `/pay` 返回 mock 二维码 URL + `/orders/{order_no}/mock-pay` 手动模拟成功；前端 `BillingPage` 接入 `PaymentDialog`（创建订单 → 二维码 → 每 2s 轮询状态 → 成功刷新订阅），`/account/orders` / `/account/invoices` 页面已上线；`OrdersPage` 已补充 paid 订单「申请退款」按钮 + 退款理由弹窗；运营后台 `/api/v1/admin/*` + `/admin` Web 页面（订单 / 退款审核 / 发票审核 / 手动 grant-plan / 用户管理）已上线；对账脚本骨架 `scripts/reconcile_payments.py` + `app_reconciliation_diffs` / `app_reconciliation_reports` 表已落地（dry-run 可跑，SDK 拉单接口待接入）；**新**：`WechatGateway.place_order`（V3 Native 下单 → 返回 `code_url`）/ `refund`（`/v3/refund/domestic/refunds`）与 `AlipayGateway.place_order`（`alipay.trade.precreate` → 返回 `qr_code`）/ `refund`（`alipay.trade.refund`）均已接入；`/pay` 在 `PAYMENT_ENABLED=true` 时调用 `gateway.place_order()`；退款后收回 Pro 权益 `order_service.approve_refund` 已落地（`plan_expires_at` 回退 + `plan_code='free'`）；退款审核结果邮件通知用户（approve/reject 均发）已在 `api/v1/endpoints/admin.py` 落地。**剩余**：发票 `issued_url` 写回（依赖电子发票 SaaS 接入）、生产小额端对端验证（待商户准入下证后执行）。 |
| Phase 6：可观测、运营与合规 | 🟡 主要落地 | **已落地**：协议三件套 `/legal/terms` `/legal/privacy` `/legal/risk-disclosure` 静态页 + 注册勾选同意 + `app_user_consents` 落库（含协议版本 / IP / UA）；`app_users.is_admin` + `get_admin_user` 依赖 + `/admin` 运营后台 + `scripts/grant_admin.py` 命令行已上线；对账脚本骨架 `scripts/reconcile_payments.py` 已就绪；注册防刷护栏 `src/users/registration_guard.py` 已上线（含一次性邮箱黑名单、IP/邮箱限频、MX 域名校验可选开关 `USER_EMAIL_MX_CHECK_ENABLED`）；**账号注销**（7 天冷静期 + 软删 + 30 天物理清除个人数据，保留订单/发票 5 年）`src/users/deletion.py` + `/api/v1/account/deletion` GET/POST/DELETE 已上线；**个人数据导出** `src/users/data_export.py` + `POST /api/v1/account/data-export` 已上线；**增长埋点** `AppGrowthEvent` + `POST /api/v1/usage/events` 已上线；**Sentry 错误监控** `sentry-sdk[fastapi]` + `api/app.py::_init_sentry` 已集成，配置 `SENTRY_DSN` 即启用；**公告中心** `AppNotice` 表 + `/api/v1/notices` endpoint（公开列表 + 管理员 CRUD）+ 前端 `/notices` 页面 + 侧边栏铃铛图标（含近期公告数角标）+ Admin 标签「公告管理」已上线；**客服入口 / FAQ** 侧边栏「帮助」进入站内 `/help` 页面，展示 FAQ、反馈指引、配置入口与免责声明；前端 `AccountPage` 底部仅保留退出登录入口，不展示数据导出与账号注销区块；**SQLite 备份脚本** `scripts/backup_db.py` 已创建（在线热备份 + 时间戳命名 + 保留策略 + gzip 可选 + 外部上传钩子 `BACKUP_UPLOAD_SCRIPT`，支持 dry-run）。**剩余**：恢复演练（人工执行）、行为验证码 / hCaptcha、Prometheus 监控看板（可用 Sentry 替代 MVP）。 |
| Phase 7：移动端 / 小程序 | ⬜ 后置 | MVP 不投入；现网 Web 站满足移动端响应式即可，原生入口留到 PMF 验证之后。 |

## 1. 现状速览（决定改造边界）

- **认证**：业务 API 已切换为 `dsa_user_session` 多用户 session；未登录业务请求返回 401。
- **数据**：历史、分析、Portfolio、Alert、Agent 会话、自选股、通知偏好已在 API 层按当前用户隔离；LLM 渠道仍由平台管理员全局配置。
- **配置**：`.env` + `SystemConfig` 是「机器一份」全局配置；自选股、通知渠道和模型偏好已 per-user 化，用户模型偏好只允许从管理员配置且套餐允许的模型中选择。
- **任务**：调度器已改为按用户分桶（`run_per_user_scheduled_analysis`），读取各用户当前套餐、自选股与通知偏好，仅对仍具备 Pro 权益的用户执行每日自动分析，单用户失败不影响其他用户；分析任务队列、任务状态和 SSE 已按当前用户隔离；Webhook 通知已在调度层通过 `dispatch_user_webhook` 实际发送（Pro 用户可用）。
- **前端**：`frontend/web` 已有邮箱登录（含 `/verify-email`）、登录后 `/onboarding` 首次引导、账户页（自选股 + 通知偏好 + 模型偏好 + 底部退出登录）、会员中心（含 PaymentDialog）、配额提示、`/account/orders` / `/account/invoices` / `/admin` / `/notices` / `/help` / `/legal/*` 协议三件套；自选股、通知偏好和模型偏好已完成 per-user 化。
- **桌面端**：`frontend/desktop` 是 Electron 单机壳，To C 化默认不强依赖它。

## 2. 目标产品形态（一句话）

「一个网页就能用的个人股票 AI 分析助手」：免费用户每天可分析少量自选股、查看 AI 决策仪表盘和大盘复盘，付费用户解锁更多自选股、更高级模型、订阅推送、Agent 问股；所有模型调用均通过平台管理员配置的模型渠道完成。

**第一版形态收敛**：

- 仅 Web 端（PC + 移动端响应式），桌面端 / 小程序不在 MVP 范围。
- 国内为主、中文落地页；支付通道接入**微信支付（Native 扫码）**与**支付宝（PC 扫码）**。
- 默认主体为个人 / 个体工商户（商户准入比公司主体快），公司主体落定后再切。
- 全程产品定位为「AI 分析助手」，**不出具荐股结论**，避免触碰投顾牌照与广告法红线，文案口径见 §5.11。

## 3. 用户分层与权益

| 档位 | 价格示意 | 自选股上限 | 单日 AI 分析次数 | 模型 | Agent 问股 | 通知渠道 | 续费 / 退款 |
|------|---------|----------|------------------|------|-----------|----------|------------|
| **游客** | 免费 | 仅查看 demo 报告 | 0 | - | 不可用 | - | - |
| **免费会员** | 0 | 3 只 | 5 次/天 | 管理员允许的基础模型 | 5 次/天 | 邮箱 | - |
| **Pro 月付** | ¥39 / 月（建议起价） | 30 只 | 50 次/天 | 管理员允许的高级模型 | 50 次/天 | 邮箱 + 自定义 webhook | 手动续费；7 天无理由退款（未消费） |
| **Pro 年付** | ¥299 / 年（约 75 折） | 30 只 | 50 次/天 | 管理员允许的高级模型 | 50 次/天 | 邮箱 + 自定义 webhook | 手动续费；7 天无理由退款（按已使用天数比例扣除） |

> 上限数值与价格仅为基线方案，具体由运营压价后定；核心是**配额** + **能力门槛** + **模型分档** + **明确退款口径**。
> 已在 `.env` 中以 `USER_FREE_DAILY_ANALYSIS=5` / `USER_FREE_DAILY_AGENT=5` / `USER_FREE_MAX_STOCKS=3` 锁定免费档初值，详见 [`to-c-mode.md`](./to-c-mode.md)。
> 第一版**不做自动续费 / 连续包月**（合规门槛与回款风险更低），续费需用户主动下单；自动续费推迟到二期。
> 新用户首单可叠加**限时折扣**（如首月 ¥9.9 体验价）通过 `app_redeem_codes` + 活动码方式发放，避免影响标价。

## 4. 核心用户故事（MVP）

> 本节保留 MVP 总览；更细的角色拆分、验收标准和当前代码映射请参考 [`docs/to-c-user-stories.md`](./to-c-user-stories.md)。

1. **注册登录**：用户用邮箱+密码注册，邮箱验证码激活；忘记密码可通过邮箱重置。
2. **首次引导**：填 1–3 只自选股 → 立即生成一份 demo 决策报告。
3. **每日订阅**：开启「每日推送」后，每个交易日傍晚收到自己自选股的报告邮件。
4. **手动分析**：在 Web 上随时点击「立即分析」生成单只股票决策报告（受配额）。
5. **Agent 问股**：在 `/chat` 页面与 AI 多轮交互，问询某只股票（受配额）。
6. **历史报告**：能查看自己历史报告、对比变化（仅自己可见）。
7. **配额可见**：Header 显示「今日剩余 X 次」，达到上限引导升级。
8. **升级 / 模型偏好**：在「会员中心」选择套餐升级，使用**微信扫码或支付宝扫码**付款，到账后立即开通；用户可在账户页从管理员配置且套餐允许的模型中选择首选模型。
9. **续费**：到期前 7 天 + 1 天分别邮件提醒，并在顶栏弹出续费提示；用户一键发起续费订单，过期不续自动降级 free。
10. **退款**：在 `/account/orders` 申请退款，未消费订单 7 天内无理由退款，已消费订单按规则计算应退金额；退款由运营在后台审核 → 调用支付通道退款 API。
11. **发票**：付费成功后可在 `/account/invoices` 申请电子普通发票，运营审核后发邮件回执（MVP 可手工开具，二期接电子发票 SaaS）。

### 4.1 MVP 用户故事落地状态（2026-05-19 核查）

| # | 用户故事 | 状态 | 当前证据 / 缺口 |
| --- | --- | --- | --- |
| 1 | 注册登录 | ✅ 已落地 | `/api/v1/account/register` / `login` / `request-password-reset` / `reset-password` / `verify-email` 已上线，前端 `/login` / `/register` / `/forgot-password` / `/verify-email` 均可用；`VerifyEmailPage` 自动读取 URL `?token=` 参数完成邮箱验证，展示加载/成功/失败三态。 |
| 2 | 首次引导 | ✅ 已落地 | per-user 自选股 API 已上线；登录成功且无显式 `redirect` 时会检查自选股数量，为空则进入 `/onboarding`；邮箱验证成功页的登录入口携带 `/onboarding` redirect；引导页通过 `StockAutocomplete` 添加 1–3 只自选股（调用 watchlist API），进度条显示填写状态，完成后进入主页；`AccountPage` 亦可随时补充自选股。 |
| 3 | 每日订阅 | ✅ 已落地 | 用户可通过 `PATCH /api/v1/account/notification-prefs` 开启 `daily_push_enabled`；调度器 `run_per_user_scheduled_analysis` 按用户当前套餐、自选股和通知偏好分桶，仅对当前仍为 Pro 的用户执行每日自动分析；分析后调用 `send_daily_email`（HTML 模板 + `markdown2` 渲染 + 一键退订链接）和 `dispatch_user_webhook`（按 `webhook_type` 投递到飞书 / 企业微信 / Discord / Telegram / 通用 JSON），单用户 / 单渠道失败不影响其他用户；点击邮件中的「一键退订」即可通过 `GET /api/v1/account/notification-prefs/unsubscribe?token=...` 自助关闭推送，写入 `app_audit_logs`。前端 `AccountPage` 已支持每日推送 / 邮件通知 / Pro Webhook 开关。剩余：HTML 模板视觉打磨与多用户并发调度优化。 |
| 4 | 手动分析 | ✅ 已落地 | `/api/v1/analysis/analyze` 已要求登录、注入 `current_user.id` 并接入 `quota_guard`；同步 / 异步任务均记录归属用户；`/analysis/tasks`、`/tasks/stream` 和 `/status/{task_id}` 均按当前用户过滤；异步后台业务失败会按任务 `user_id` 返还分析配额。 |
| 5 | Agent 问股 | ✅ 主要落地 | `/api/v1/agent/*` 已要求登录并接入 Agent 配额；非流式 `/agent/chat` 和 `/agent/research` 返回 `success=false` 时会返还已扣配额，异常 / 超时 / 流式 error 也保持返还；`LLMToolAdapter.call_completion` 已通过 `_resolve_user_model_route` 接入模型路由，平台路由在 `plan.allowed_models` 非空时过滤可用模型并优先使用用户首选模型；plan 到期已由 `plan_lifecycle` 自动降级到 free，过期用户不会继续吃 Pro 模型。剩余：`allowed_models` 具体运营配置。 |
| 6 | 历史报告 | ✅ 已落地 | `/api/v1/history/*` 已按 `current_user.id` 查询，`analysis_history.user_id` 写入链路已补齐。 |
| 7 | 配额可见 | ✅ 已落地 | 前端顶栏 `QuotaIndicator` 与全局 `QuotaExceededDialog` 已可用，后端 `get_quota_snapshot` / `quota_exceeded_payload` 已接入。 |
| 8 | 升级 / 模型偏好 | 🟡 主要落地 | `/billing` PaymentDialog（创建订单 → 二维码 → 每 2s 轮询 → 成功刷新）已上线；`WechatGateway.place_order` / `AlipayGateway.place_order` SDK 已接入，`PAYMENT_ENABLED=true` 时调用真实通道；`/account/model-preference` 已支持用户选择管理员配置且当前套餐允许的模型，`model_router.py` 已接入 `GeminiAnalyzer._call_litellm` 与 `LLMToolAdapter.call_completion`。剩余：生产环境小额端对端验证（待商户准入下证后执行）。 |
| 9 | 续费 | 🟡 主要落地 | `app_plan_reminders` + `src/users/plan_lifecycle.py` 已上线：调度器每日触发后扫描到期前 7/3/1 天用户发邮件提醒（按 `(user_id, plan_code, expires_at, reminder_type)` 幂等），过期用户写一条 `app_subscriptions` source='expire' 并发送降级通知邮件，所有动作落 `app_audit_logs`；`/api/v1/account/status.renewal` 暴露 `daysRemaining` / `willExpireSoon` / `expired` 字段，前端 `RenewalBanner` 在顶部渲染粘性提示并提供「立即续费」入口 → `/billing?renew=1`。剩余：邮件模板 HTML / 落地页风格美化、续费成功后的二次邮件回执。 |
| 10 | 退款 | 🟡 主要落地 | `app_refunds` 表、`OrderService.create_refund`、`POST /api/v1/billing/refunds`、`GET /api/v1/billing/refunds/{refund_no}` 已上线；`OrdersPage` 已补充 paid 订单「申请退款」按钮 + 退款理由弹窗（`billingApi.requestRefund`）；`/api/v1/admin/refunds/{refund_no}/approve` + `reject` 已上线，`approve_refund` 调用 `gateway.refund()` 拿通道退款单号并立即降级用户 `plan_code='free'` + `plan_expires_at` 回退；退款审核结果（通过/拒绝）邮件通知用户已在 `api/v1/endpoints/admin.py` 落地；运营后台 `/admin` 退款审核页面已可用。剩余：生产小额验证。 |
| 11 | 发票 | 🟡 主要落地 | `app_invoices` 表、`OrderService.create_invoice`、`POST /api/v1/billing/invoices`、`GET /api/v1/billing/invoices` 已上线；前端 `/account/invoices` 申请表单 + 历史列表已可用；`/api/v1/admin/invoices/{invoice_no}/issue` + `reject` 已上线，`issue_invoice` 审批后自动发送邮件回执（含 `issued_url`）。剩余：运营将 `issued_url` 写回（依赖电子发票 SaaS 接入或手工开具后填入）、生产小额验证。 |

## 5. 技术改造模块清单

### 5.1 用户体系（新增）

- 新表：`users`（id, email, password_hash, status, plan, plan_expires_at, created_at, last_login_at, email_verified_at, ...）。
- 新表：`email_verifications`（token, user_id, type=verify|reset, expires_at）。
- 新增模块：`src/users/`（auth、注册、邮箱发送、密码重置、JWT 或基于 cookie 的 session）。
- 重构方向：用户侧业务 API 统一走 `src/users/` 多用户模块，不保留单用户放行路径。

> 当前实现：`app_users` / `app_user_sessions` / `app_user_email_verifications` 等已上线，模块代码位于 `src/users/`，详见 [`to-c-mode.md`](./to-c-mode.md) §3–§5。

### 5.2 认证与授权（替换/扩展）

- API 中间件 `api/middlewares/auth.py` 改造：识别用户 session → 注入 `request.state.user`；普通业务接口统一要求登录。
- 引入「访客 / 用户」产品态；普通业务接口统一要求登录。
- 邮件发送依赖：MVP 用 SMTP（复用现有邮件通知模块），后续可选 SendGrid/SES。

> 当前实现：中间件已强制识别 `dsa_user_session` cookie；邮件后端默认 `LoggingEmailBackend`，配置 `USER_EMAIL_BACKEND=smtp` 后复用 `EMAIL_SENDER` / `SMTP_HOST` 等变量。

### 5.3 数据多租户改造

- 给业务表统一加 `user_id`（已经有 `owner_id` 的统一改名/语义对齐）：
  - `analysis_history`、`portfolio_*`、`alerts_*`、自选股表、Agent 会话表、用户配置表。
- 仓储层（`src/repositories/*`、`src/services/*`）所有查询/写入加 `user_id` 过滤。
- 新增「用户配置」表 / 服务，替代部分原 `SystemConfig` 中属于个人的字段（自选股、通知偏好、语言、模型偏好等）。
- 行情、新闻、基本面这些**全市场公共数据**保持全局共享、按 `code` 缓存，不做用户隔离。
- 旧数据：直接丢弃 / 重置 schema（用户已确认）。

> 当前实现（按表逐个核对）：
> - `analysis_history`：列已加 `user_id`；查询侧 (`AnalysisRepository.get_*` / `HistoryService.*`) 已支持 `user_id` 过滤，`/api/v1/history/*` endpoint 已强制注入 `current_user.id`；`/api/v1/analysis/analyze` 同步与异步任务也已透传 `current_user.id`。
> - `portfolio_*`：**已修复**。`/api/v1/portfolio/*` 所有端点已注入 `current_user`，登录状态下自动以 `str(current_user.id)` 作为 `owner_id` 进行创建/过滤/强校验（`owner_id_check`）；写操作（trade/cash-ledger/corporate-action 的 create、delete）、列表查询、snapshot、risk、fx-refresh 均已闭环。`portfolio_service` 对应方法新增了 `owner_id` / `owner_id_check` 参数；`portfolio_repo` 新增了事件行的 `get_*_account_id` 查询辅助方法用于 delete 操作的归属预校验。CSV 导入端点也增加了账户归属校验前置检查。
> - `alert_*`：API 端点已强制登录，规则、触发历史、通知历史通过当前用户过滤；后台评估 worker 的按用户分桶仍需在 Phase 3 继续补齐。
> - `conversation_messages`：Web API 会话已按当前 `AppUser.id` 隔离；Bot / CLI 路径仍保留平台维度 session_id。
> - 自选股：**已完成（Phase 3）**。`app_user_watchlists` 表 + `src/users/watchlist.py` 服务 + `/api/v1/account/watchlist` CRUD 已上线，含 `plan.max_stocks` 上限检查。
> - 通知偏好：**已完成（Phase 3）**。`app_user_notification_prefs` 表 + `src/users/notification_prefs.py` 服务 + `/api/v1/account/notification-prefs` GET/PATCH 已上线；`daily_push_enabled` / `email_enabled` 仅 Pro 可开启，Webhook 需 `plan.can_webhook=true`。
> - 语言、模型偏好等：仍依赖 `SystemConfig` 全局表，没有 per-user 视图。

### 5.4 商业化与配额

- 已有表：`app_plans` / `app_subscriptions` / `app_user_usage_counters` / `app_redeem_codes`（详见 `src/storage/`）。
- **新增表（Phase 5 已落地，详见 §11.2）**：
  - `app_orders`：订单主表（含金额、币种、状态、provider、外部交易号、quote 快照）。
  - `app_payment_events`：支付通道回调流水（用于回放对账，幂等去重）。
  - `app_refunds`：退款记录。
  - `app_invoices`：电子发票申请（MVP 可手工，但请求记录入库）。
  - `app_promotions` / `app_coupons`（可选）：限时活动 / 优惠券，先复用 `app_redeem_codes` 也可。
- 已有 `quota` 服务：分析 / Agent / 通知前先扣配额；管理员等不限额场景可显式 bypass。
- 模型路由：`src/users/model_router.py` 已新增按 `plan.allowed_models` 解析的统一入口，并已接入 `GeminiAnalyzer._call_litellm` 与 `LLMToolAdapter.call_completion`；详见 §5.4.1。
- 支付：第一版**接入微信 Native + 支付宝 PC**，升级闭环为「下单 → 拉起扫码 → 通道回调 → 服务端开通 → 前端轮询确认」，详见 §11。
- 兑换码 / 邀请码 / 后台手动开通继续保留，作为线下渠道（KOL 合作、内测用户）的开通方式。

#### 5.4.1 模型路由统一入口（缺口）

- `src/users/model_router.py` 已接入 `GeminiAnalyzer._call_litellm` 与 `LLMToolAdapter.call_completion`（通过 `_resolve_user_model_route`），按以下优先级解析：
  1. 按 `plan.allowed_models` 过滤管理员配置的可用模型。
  2. 若用户设置了 `preferred_model` 且该模型仍在允许列表中，则优先使用该模型。
  3. 平台 Key 池失败时按 channel `fallback_chain` 重试；`user_id` 已从 API 层 → factory → adapter 全链路透传。
- **剩余缺口**：`allowed_models` 具体配置尚未入库（`app_plans` 中该字段为空时降级为不限制）；平台 Key 池每日/每分钟限额监控与用量看板。

> 当前实现：`src/users/quota.py` 提供 `try_consume` / `refund` / `get_quota_snapshot`；`quota_guard.py` 把两者串成 `enforce_quota` / `refund_quota` / `quota_exceeded_payload`，已接入 `/analysis/analyze`、`/agent/chat`、`/agent/chat/stream`、`/agent/research`；未登录调用不再 bypass。`model_router.py` 提供 `resolve_model_route`，已在 `GeminiAnalyzer` 与 `LLMToolAdapter` 的实际 LLM 调用前生效；平台路由按 `plan.allowed_models` 过滤可用模型并优先采用 `app_users.preferred_model`。

### 5.5 调度与推送

- 现有「全局调度跑全局自选股」改为：「调度时按用户分桶，按需并发跑」。
- 推送渠道按用户偏好：AI 分析报告自动推送仅 Pro 可开启；Pro 可使用邮件与 webhook（飞书/企业微信/Discord/TG 等）。
- 引入失败降级：单用户的某个渠道失败不影响其它用户。
- 节流：调度器尊重每个 provider 的 rate limit，整体并发上限可配置。

> 当前实现（Phase 3 已落地）：`main.py` `run_per_user_scheduled_analysis` 每轮调度后依次：① 查询所有 `daily_push_enabled=True` 的用户 ID；② 读取各用户 `app_user_watchlists` 与 `app_user_notification_prefs`，解析 `resolve_user_plan` 得到 `can_webhook` 等权益；③ 经交易日过滤后，创建独立 `StockAnalysisPipeline`（`user_id=user.id`，`send_notification=False`）执行分析；④ 调用 `src/users/notification_delivery.py::send_daily_email` 发送 HTML 邮件（`markdown2` 渲染报告 + 免责声明 + 一键退订链接，链接 token 由 `src/users/unsubscribe.py` HMAC 签发）；⑤ 若 `plan.can_webhook=True` 且用户配置了 Webhook，则调用 `dispatch_user_webhook` 按 `webhook_type` 投递到飞书 / 企业微信 / Discord / Telegram / 通用 JSON 五种 schema，每种渠道独立 try/except，失败仅日志；⑥ 单用户任意阶段失败只记日志，不影响其他用户。`GET /api/v1/account/notification-prefs/unsubscribe` 公开 endpoint 已加入 `AuthMiddleware` 白名单，验证 token 后关闭对应偏好并写 `app_audit_logs`。
> 未完成：HTML 模板视觉打磨（运营素材定稿后再迭代）；多用户并发调度（当前串行，用户量大时需改线程池）；按 provider rate limit 节流。

### 5.6 前端改造（`frontend/web`）

- 路由：增加 `/register`、`/forgot-password`、`/billing`、`/account`；现有 `LoginPage` 重写为「登录 / 注册 / 找回密码」三态。
- AuthContext 改为基于 user session，含 `user.plan`、`quota` 等。
- 顶栏显示「今日剩余配额」，超额时弹出升级引导。
- `SettingsPage` 拆分：
  - `账户设置`（邮箱、密码、订阅状态）。
  - `自选股 & 通知偏好`（per-user）。
  - `模型偏好`（从管理员配置且当前套餐允许的模型中选择）。
- 桌面端 `frontend/desktop`：MVP 不强改，仅改登录调用。后续可作为 Pro 用户福利。

> 当前实现：`/login`、`/register`、`/forgot-password`、`/verify-email`、`/onboarding`、`/account`、`/billing`、`/account/orders`、`/account/invoices`、`/admin`、`/notices`、`/help`、`/legal/terms`、`/legal/privacy`、`/legal/risk-disclosure` 均已上线。`/verify-email`（`VerifyEmailPage`）自动读取 `?token=` 参数完成邮箱验证，展示加载/成功/失败三态。注册成功后自动跳转 `/onboarding` 引导添加自选股；`AccountPage` 已新增「我的自选股」「通知偏好」与「模型偏好」卡片，含每日推送开关、邮件通知开关、Pro Webhook 配置 UI 和当前套餐可选模型选择，页面底部仅保留退出登录入口；顶栏 `QuotaIndicator` 在登录后显示当日剩余，全局 `QuotaExceededDialog` 监听 `dsa:quota-exceeded` 事件并提供升级引导。`/billing` 已接入 `PaymentDialog`（二维码 + 2s 轮询 + mock 调试按钮）。`/account/orders` 支持列表 + 取消 + 退款弹窗，`/account/invoices` 支持申请 + 历史列表。侧边栏铃铛图标显示近期公告数角标，帮助客服入口进入站内 `/help` 页面。`UserAuthPage` 注册表单已有协议三件套勾选框，未勾选时禁用提交。AuthContext 已暴露 `userMode`（含 `plan` / `quota`）/ `loginWithEmail` / `registerWithEmail` / `effectiveLoggedIn`。
> 未完成：邀请/推荐功能入口。
> 关键页面线框请参考 [`docs/to-c-product-wireframes.md`](./to-c-product-wireframes.md)。

### 5.7 运营后台（最小）

- 单独设计平台运营入口，仅给「平台管理员」用：用户列表、订阅状态、配额查询、兑换码生成、平台 Key 管理、用量看板。
- 路由建议放在 `/admin/*`，认证与权限模型需独立于 C 端用户 session 设计。

> 当前实现：`/api/v1/admin/*` 已上线（`api/v1/endpoints/admin.py`），覆盖 `/me`、`/users`、`/orders`、`/orders/{order_no}`、`/refunds`、`/refunds/{refund_no}/approve|reject`、`/invoices`、`/invoices/{invoice_no}/issue|reject`、`/grant-plan`、`/audit-logs`、`/stats`；统一由 `get_admin_user`（要求 `app_users.is_admin=True`）鉴权，与 C 端 session 共用 cookie 但独立鉴权层。前端 `/admin`（`AdminPage.tsx`）已可用；`scripts/grant_admin.py` 命令行可授权管理员。剩余：兑换码生成 / 批量分发后台 UI、平台 Key 用量看板、`/admin/reconciliation` 对账报表 Web 视图（API 已有，Web 页面待接入）。

### 5.8 安全与合规

- 密码：bcrypt / argon2，不长期沿用当前 PBKDF2 哈希方案。
- session：httpOnly cookie + CSRF token；或改 JWT（看部署形态）。
- 邮件验证、密码重置：一次性 token + 短 TTL + 速率限制（沿用 `check_rate_limit` 思路按 IP/邮箱）。
- 免责声明：注册流程显式同意「投资风险自负」「服务条款」「隐私政策」。
- 数据隔离测试：所有业务接口必须有 per-user 隔离的回归测试。

> 当前实现：Phase 1 暂用 PBKDF2；后续切换到 bcrypt / argon2 时需要同时迁移历史哈希。session 走 httpOnly cookie。登录失败 / 密码重置请求有基础内存限频。注册流程已强制勾选协议三件套，`app_user_consents` 表落库协议版本 / IP / UA，`/api/v1/account/status` 返回 `termsVersion` / `needsReacceptTerms`。未完成：CSRF token（session 仅 httpOnly cookie，尚无 double-submit CSRF 防护）；行为验证码 / hCaptcha（注册防刷仍依赖限频 + 黑名单）；密码哈希迁移到 bcrypt/argon2。

#### 5.8.1 注册防刷与账号风控

- 注册接口必须接入**图形验证码 / 行为验证码**（推荐极验 / 腾讯防水墙；MVP 可先用 `hCaptcha` 免费版）。
- 邮箱按 24h 限频 + IP 按 1h 限频 + 全局 token bucket，三层拦截。
- 一次性邮箱（`mailinator.com` / `10minutemail.com` 等）黑名单 + 域名 MX 校验。
- 同一 IP 单日注册数、邀请关系深度都需要监控并落审计日志。
- 检测到风控触发后，用户进入「需要邮箱二次验证」状态，但**不暴露原因**（防止穷举）。

> 当前实现：`src/users/registration_guard.py` 已上线一次性邮箱黑名单（内置常见 disposable 域名 + `USER_DISPOSABLE_EMAIL_DOMAINS` / `_REPLACE` env 扩展）和滚动窗口频率限制（`USER_REGISTER_IP_DAILY_MAX` 默认 10、`USER_REGISTER_EMAIL_DAILY_MAX` 默认 3、`USER_REGISTER_RATE_WINDOW_HOURS` 默认 24，邮箱按 SHA-256 哈希记录），命中拦截统一返回 `rate_limited` / `invalid_email`，事件写 `app_audit_logs`；MX 域名校验已实现（`check_mx_domain`，`USER_EMAIL_MX_CHECK_ENABLED=true` 启用，默认关闭，fail-open 策略）。仍未落地：`hCaptcha` / 行为验证码接入（需要前端 + 后端两侧对接）；邀请关系深度监控；登录失败 / 密码重置请求保留原内存级限频。

#### 5.8.2 支付与订单安全

- 详见 §11.7；要点：HTTPS only、回调签名验证、幂等键、订单状态机不可回退、敏感字段（如商户号、API v3 私钥）只读环境变量并加密落库。
- 严禁在前端透传 `amount` 决定开通时长——所有计价以**服务端订单快照**为准。

### 5.9 可观测、部署与运维

第一版必须就绪的运维基线，避免上线后出问题查无可查。

- **数据库选型**：
  - SQLite 适合自部署单机，但 To C 多用户 + 订单写入并发风险高。**建议 Phase 5 前迁 PostgreSQL**（云数据库即可，1c2g 即可起步）。
  - 迁移工具：现有 `src/storage/` 走 SQLAlchemy ORM，schema 已通过 Alembic 管理，迁移成本可控；需补一份 `scripts/migrate_sqlite_to_pg.py` 数据搬迁脚本。
- **部署形态**：
  - MVP：单节点 Docker（`docker/Dockerfile`）+ Nginx 反代 + 云 RDS；预算控制在 ¥200/月以内。
  - 二期：双节点 + 云 LB + 共享 RDS + Redis（用于 session / 配额计数器，替换数据库写入热点）。
- **可观测**：
  - 错误监控：**Sentry 免费版** 接 `api/app.py` 与前端 `frontend/web`，1 万条/月足够 MVP。
  - 健康检查：复用 `/health` endpoint + Uptime Robot 免费版（5 分钟拨测）。
  - 日志：结构化 JSON 输出 + 日志卷映射到宿主机；二期再考虑 Loki / CloudWatch。
  - 关键业务埋点：分析 / Agent 调用次数、配额拒绝率、支付下单 → 支付成功率、退款率（默认从 DB 聚合查询，不接专业 BI）。
- **告警**：
  - 服务 5 分钟无心跳 / 5xx 比例 > 1% / 支付回调失败率 > 0.5% → 触发邮件 / 飞书机器人。
  - 配额池剩余 < 10% / 数据库连接池 > 80% → 触发预警。
- **备份**：
  - PostgreSQL：每日 pg_dump，**保留 7 日 + 周度归档保留 4 周**；备份文件加密上传 OSS / S3。
  - SQLite 期内：每日 cron 复制 `data/*.db` 到云盘；上线前演练一次恢复流程。
- **审计日志**：
  - 关键操作（登录、密码修改、邮箱修改、套餐变更、订单创建 / 支付 / 退款、模型偏好修改、管理员操作）写入独立的 `app_audit_logs` 表或专用日志文件，**永不删除**，便于合规追溯。
- **运行手册**：
  - `docs/runbook-to-c.md`（待写）：包含「支付回调失败排查」「邮件送达失败排查」「数据库恢复演练」「商户号 / 私钥轮换」等 SOP。

> 当前实现：**Sentry 错误监控**已在 `api/app.py::_init_sentry` 接入（`SENTRY_DSN` 非空时自动初始化，集成 FastAPI + SQLAlchemy，`send_default_pii=False`，`traces_sample_rate` 可配置）。**`app_audit_logs` 表**（`AppAuditLog`，永不删除）已上线，登录/注册/退订/套餐变更/支付回调/管理员操作均写入；`/api/v1/admin/audit-logs` 已暴露查询接口。支付回调 IP 白名单 + 签名失败滑动窗口告警（`src/services/billing/security.py`）已接入，超阈值通过 `ADMIN_ALERT_EMAIL` + `RECONCILE_WEBHOOK_URL` 发出告警。`/health` 已存在用于健康检查。**SQLite 备份脚本** `scripts/backup_db.py` 已上线，支持在线热备份、gzip 压缩、保留份数、上传钩子与 dry-run。未完成：**`docs/runbook-to-c.md`** 尚未撰写；Prometheus 监控看板（Sentry 覆盖错误层，业务层指标从 DB 聚合）；SQLite → PostgreSQL 迁移脚本。

### 5.10 增长、运营与客服

- **客服入口**：
  - 顶栏「帮助」菜单 → FAQ 页 + 「联系我们」（邮箱 / 飞书工单 / 微信客服群三选一，MVP 至少一个）。
  - 配额超额对话框、登录异常等关键错误页都要附客服入口。
- **公告中心**：
  - `/notices` 路由 + 顶栏铃铛；上线、停服、价格调整、安全事件都通过此处通知，并同步发邮件给付费用户。
- **增长埋点**：
  - 注册转化漏斗：访问落地页 → 注册 → 完成首单分析 → 升级 Pro 的 4 步漏斗。
  - 自建事件表 `app_growth_events`（user_id, event, props_json, ts），后端写入 + 前端通过 `/api/v1/usage/events` 上报；GA 仅做兜底。
  - 关注指标见 §9。
- **邀请裂变**：
  - 第一版**仅支持邀请码注册 + 邀请奖励**（被邀请人完成首单付费后赠送邀请人 N 天 Pro）；防止用户单方面刷量。
  - 通过现有 `app_redeem_codes` 表 + 新增 `referrer_id` / `target_user_id` 字段实现，避免新建表。
- **运营素材**：
  - 落地页：核心卖点（AI 自动分析 / 多市场支持 / 决策仪表盘）+ 报告截图 + 价格 + 免责。
  - 小红书 / 公众号 / X：MVP 由运营手工运营，技术侧只提供报告分享链接 + 报告水印。
- **续费提醒**：
  - 到期前 7 天、3 天、1 天分别发邮件 + 站内顶栏提示 + 一键续费按钮（跳 `/billing?renew=1`）。
  - 过期当日发一封「已降级 free」邮件，包含一键续费链接。

> 当前实现：**公告中心**已上线：`AppNotice` 表 + `api/v1/endpoints/notices.py`（公开列表 + 管理员 CRUD）+ 前端 `/notices`（`NoticesPage.tsx`）+ 侧边栏铃铛图标含未读数角标（`SidebarNav.tsx` 通过 `noticesApi.getUnreadCount()` 轮询）。**客服入口 / FAQ** 已上线：侧边栏「帮助」进入站内 `/help` 页面，集中展示常见问题、反馈指引、配置入口与免责声明。**增长埋点**已上线：`AppGrowthEvent` 表（`app_growth_events`）+ `POST /api/v1/usage/events`（白名单事件：`page.view` / `user.register` / `user.first_analysis` / `user.upgrade_click` / `payment.success` 等 11 种），无需登录即可上报，登录态自动关联 `user_id`。**续费提醒**已上线：`app_plan_reminders` + `plan_lifecycle.py` 每日扫描到期前 7/3/1 天发邮件，前端 `RenewalBanner` 置顶展示，`/billing?renew=1` 一键续费入口。未完成：**邀请奖励**（`app_redeem_codes` 尚无 `referrer_id` / `target_user_id` / `discount_type` 字段，邀请裂变逻辑未实现）；公告发出时同步邮件给付费用户的功能。

### 5.11 法务与合规

- **协议三件套**（必须在 Phase 5 前上线）：
  - 《用户服务协议》：定义服务范围、收费、违约、争议解决（约定管辖法院）。
  - 《隐私政策》：遵循 PIPL / GDPR 双口径，列明收集字段、用途、保留期、第三方共享（支付通道 / 邮件服务 / LLM provider）。
  - 《投资风险揭示书》：明确「本服务不构成投资建议、不承诺收益、用户自行决策、投资有风险」。
  - 三份协议作为静态页（`/legal/terms`、`/legal/privacy`、`/legal/risk-disclosure`），版本号 + 生效日期；变更需要全量用户重新勾选。
- **注册同意**：
  - 注册页强制勾选「我已阅读并同意《用户服务协议》《隐私政策》《投资风险揭示书》」，未勾选时禁用提交按钮。
  - 落库时记录用户同意的协议版本号 + IP + UA + 时间，存入 `app_users.terms_version` 与 `app_user_consents`（新表，可选）。
- **免责口径**（所有出口必须遵守）：
  - 文案禁用词：「稳赚」「必涨」「保收益」「推荐买入」「跑赢大盘」「内幕」「绝佳机会」等。
  - 文案推荐词：「AI 辅助分析」「参考观点」「数据驱动复盘」「自动化研究」。
  - 报告输出（`backend/templates/report_*.j2`）末尾固定渲染免责声明；邮件模板同样。
  - 落地页 footer 固定一句：「本产品基于 AI 模型生成观点，不构成投资建议。投资有风险，入市需谨慎。」
- **数据保护**：
  - 注销账号：后端支持用户提交注销 → 7 天冷静期 → 软删（`status='deleted'`）→ 30 天后物理清理个人数据；保留订单与发票按财税法规留存 5 年，普通账户页暂不展示自助注销入口。
  - 数据导出：后端支持申请导出个人数据（注册信息、自选股、历史分析、订单），普通账户页暂不展示自助导出入口。
  - 第三方共享披露：明确写在隐私政策中（支付通道、邮件 SaaS、LLM provider 等）。
- **未成年人保护**：
  - 注册页明示「本服务不向 18 岁以下用户提供」；检测到生日 / 身份信息异常时拒绝注册（实名信息一般不收集，主要靠用户协议约束）。
- **广告法红线**：
  - 落地页 / 推广物料避免极限词与数字承诺；可用「过往复盘准确率 XX%」需附时间窗口与样本量。
  - 任何「7 天体验返本」「无效退款双倍赔付」等承诺均**不**在 MVP 出现。

> 当前实现：**协议三件套**已上线：`/legal/terms`（`TermsPage.tsx`）/ `/legal/privacy`（`PrivacyPage.tsx`）/ `/legal/risk-disclosure`（`RiskDisclosurePage.tsx`）三个静态页面公开可访问（`App.tsx` 已将其从登录重定向中豁免）；注册表单 `UserAuthPage` 已强制勾选三件套，未勾选禁止提交；`app_user_consents`（`AppUserConsent`）表落库协议版本 / IP / UA，`/api/v1/account/status` 返回 `termsVersion` / `needsReacceptTerms`。**账号注销**已上线：`src/users/deletion.py` + `/api/v1/account/deletion` GET/POST/DELETE，实现 7 天冷静期 + 软删 + 30 天物理清理个人数据，保留订单/发票 5 年，但 `AccountPage` 暂不展示自助注销入口。**个人数据导出**已上线：`src/users/data_export.py` + `POST /api/v1/account/data-export`，但 `AccountPage` 暂不展示自助导出入口。报告末尾、邮件模板均附免责声明。未完成：注册页「本服务不向 18 岁以下用户提供」明示文案；落地页 footer 固定免责文本（取决于落地页是否独立建设）。

## 6. 分阶段实施路线

> **MVP 范围定义**：Phase 0–5 都必须在第一版上线前闭环；Phase 6 可与 Phase 5 并行或紧随其后；Phase 7 不在 MVP 范围。

### Phase 0（产品先行，已完成）
- 输出本规划文档、UI 关键页面线框（登录/注册/账户/会员中心/配额提示）。
- 锁定：套餐权益、配额数值、模型分档、第一版支付通道（微信 Native + 支付宝 PC）。

### Phase 1：用户体系 + 隔离（必须）
- DB schema 重置，引入 `users` 等表 + 全表 `user_id`。
- 邮箱注册/登录/重置；前端登录注册改造。
- 仓储/服务统一 `user_id` 过滤（含写入侧 `pipeline.save_analysis_history` 与异步任务队列），关键缺口见 §5.3。
- 单元测试 + 数据隔离回归。

### Phase 2：商业化骨架（必须）
- `plans` / `subscriptions` / `usage_counters` 表 + 配额服务（已落地，详见 §5.4）。
- 顶栏配额展示；超额引导（已落地）。
- 兑换码用户侧兑换已落地；兑换码生成 / 邀请码后台未落地。
- ✅ `src/users/model_router.py` 已接入 `GeminiAnalyzer._call_litellm` 与 `LLMToolAdapter.call_completion`（Phase 4 完成）；平台 Key 配额池统一管理、`allowed_models` 具体配置入库仍为剩余缺口，详见 §5.4.1。

### Phase 3：调度与通知 To C 化（必须）
- ✅ 调度器按用户分桶；通知按用户偏好；邮件模板 HTML 化（`markdown2` 渲染 + 报告卡片 + 免责声明）。
- ✅ Pro Webhook 通知按 `webhook_type` 投递到飞书 / 企业微信 / Discord / Telegram / 通用 JSON，单渠道失败仅日志。
- ✅ 一键退订：HMAC 签名 token + 公开 `GET /api/v1/account/notification-prefs/unsubscribe` endpoint。
- ⬜ 邮件接 SES / 阿里云邮件推送, 自建 SMTP 仅做兜底（运维侧, 取决于商户选型）。
- ⬜ HTML 模板视觉打磨与多用户并发调度（用户量上来后再优化）。

### Phase 4：模型偏好 + Pro 能力（必须）
- ✅ 用户模型偏好已落地；`model_router.py` 已接入 `GeminiAnalyzer._call_litellm` 与 `LLMToolAdapter.call_completion`，平台路由按 `plan.allowed_models` 过滤可用模型并优先使用用户首选模型。
- ✅ Pro 套餐字段 `can_webhook` / `allowed_models` / `max_stocks` 已就位；Webhook 通知（飞书/企业微信/Discord/TG/通用 JSON）和 per-user 自选股上限已在调度与 watchlist 服务中生效。
- ⬜ `allowed_models` 具体配置尚未入库（该字段为空时降级为不限制）；`app_plans` 待运营配置后启用高低档模型分档。

### Phase 5：商业化闭环 + 支付（**第一版必做**）
- ✅ 数据模型 + 服务层：`app_orders` / `app_payment_events` / `app_refunds` / `app_invoices` 四张表 + `src/services/billing/order_service.py`（创建幂等、状态机、fulfill、退款、发票、回调落库）已完成；新增 `app_user_consents` / `app_reconciliation_diffs` / `app_reconciliation_reports` 三张表。
- ✅ 后端 endpoint：用户侧 orders / pay / cancel / refunds / invoices / callbacks 已上线；`PAYMENT_ENABLED=false` 时 503 + 人工收款兜底；`PAYMENT_MOCK_ENABLED=true` 时 `/pay` 返回 mock 二维码 + `/mock-pay` 手动模拟成功，方便前端联调与端到端验证。
- ✅ 前端入口：`/account/orders`（列表 + 取消 + 退款弹窗）、`/account/invoices`（发票申请 + 历史）、`/billing` `PaymentDialog`（二维码 + 轮询 + mock 调试按钮）、`/admin` 运营后台均已上线。
- ✅ 协议三件套：`/legal/terms` `/legal/privacy` `/legal/risk-disclosure` 静态页 + 注册勾选同意 + `app_user_consents` 落库已上线，`/api/v1/account/status` 同步返回 `termsVersion` / `needsReacceptTerms`。
- ✅ 运营后台：`/api/v1/admin/*`（users / orders / refunds / invoices / grant-plan / stats）+ Web `/admin` 页面 + `scripts/grant_admin.py` 已上线；退款审核、发票审核、手动 grant-plan 可在 Web 内完成。
- ✅ 对账脚本骨架：`scripts/reconcile_payments.py` 支持按通道按日跑（`--commit` 写库，默认 dry-run），输出 `channel_only` / `local_only` / `amount_mismatch` / `status_mismatch` 四类差异，落 `app_reconciliation_diffs` / `app_reconciliation_reports`。
- ✅ 支付 SDK 安全闭环：新增 `src/services/billing/gateways/` 抽象层（`WechatGateway` V3 RSA-SHA256 + AES-256-GCM / `AlipayGateway` RSA2 SHA256），两路回调端点接入验签 → 幂等去重 → `fulfill_order`；`approve_refund` 调用 `gateway.refund()` 拿通道退款单号（不配置时回退人工）；`issue_invoice` 审批后自动发送邮件回执；`fetch_channel_settlements` 接入 gateway facade 扩展点；`.env.example` 补充全部 `WECHAT_PAY_*` / `ALIPAY_*` 字段。
- ✅ 下单 / 退款 SDK 接入：`WechatGateway.place_order`（V3 Native，商户私钥 RSA-SHA256 签名 → `POST /v3/pay/transactions/native` → 返回 `code_url`）与 `refund`（`POST /v3/refund/domestic/refunds`，返回 `refund_id`）；`AlipayGateway.place_order`（`alipay.trade.precreate`，RSA2 签名 → 返回 `qr_code`）与 `refund`（`alipay.trade.refund`）；factory 加载 `WECHAT_PAY_PRIVATE_KEY_*` / `WECHAT_PAY_CERT_SERIAL_NO` / `ALIPAY_APP_PRIVATE_KEY_*` 并透传给 gateway；`/pay` 端点在 `PAYMENT_ENABLED=true` 时调用 `gateway.place_order()`，密钥缺失返回 503，通道失败返回 502。
- ✅ 安全收尾（IP 白名单 + 签名失败告警）：`src/services/billing/security.py` 新增 `check_callback_ip`（读 `PAYMENT_CALLBACK_ALLOWED_IPS[_{PROVIDER}]` CIDR，留空放行所有，命中外 IP 返回 200 + 写 `app_audit_logs.callback.ip_blocked`）与 `record_sig_failure`（滑动窗口 `THRESHOLD/WINDOW_SECONDS`，超阈值通过 `ADMIN_ALERT_EMAIL` + `RECONCILE_WEBHOOK_URL` 告警，30 分钟冷却）；两个回调端点均已接入。⬜ 生产小额端对端验证（待商户准入下证后执行）。
- ✅ 对账脚本真实通道账单拉取：`WechatGateway.fetch_settlements`（V3 `GET /v3/bill/tradebill` → gzip CSV 解析）与 `AlipayGateway.fetch_settlements`（`alipay.bill.downloadurl.query` → ZIP 内 GBK CSV 解析）均已实现并对接 gateway facade；失败时静默返回空列表，对账脚本降级为仅 `local_only` 差异。

### Phase 6：可观测、运营、合规（与 Phase 5 并行）
- ✅ Sentry、健康检查、用户操作审计、SQLite 备份脚本已上线；Prometheus 看板与 SQLite → PostgreSQL 迁移脚本仍为后续项。
- ✅ 协议三件套、注册同意、账号注销、个人数据导出已上线。
- ✅ 注册防刷已包含一次性邮箱拦截、IP / 邮箱滚动窗口限频与可选 MX 校验；行为验证码仍为后续项。
- ✅ 客服入口、FAQ、产品公告中心已上线；公告同步邮件给付费用户仍为后续项。
- ✅ 增长埋点、自建事件 API、首次引导、续费提醒已上线；邀请奖励 / 推荐裂变仍为后续项。
- 详见 §5.9 / §5.10 / §5.11。

### Phase 7：移动端 / 小程序（可选/后置）
- 现有 React 站点做 PWA + 移动端样式优化（MVP 已保证响应式可用）。
- 视需要再决定是否做小程序 / 公众号入口；微信支付若叠加 JSAPI 渠道可在此阶段引入。

## 7. 关键风险与注意点

- **平台 Key 成本失控**：免费档调用必须有强配额 + 模型分档 + 全局熔断；上线前估算单用户日成本上限，并对接 §5.4.1 的统一模型路由。
- **多用户调度爆量**：所有自选股 × 所有用户的笛卡尔积可能过大，需要按用户错峰、合并相同标的的行情/新闻请求（已有的全局缓存要保留）。
- **数据隔离漏洞**：多租户最常见 bug；需要在 repository 层强约束 + 集中拦截 + 单测覆盖。
- **投顾合规风险**：A 股 / 港股 / 美股的「证券投资咨询」属于持牌业务。所有产品文案、报告输出口径必须定性为「AI 辅助分析」而非「投资建议」，注册需勾选三件套，每页/每邮件附免责，落地页避免「稳赚 / 必涨 / 推荐买入」等措辞。详见 §5.11。
- **邮件送达率**：自建 SMTP 易进垃圾箱，第一版直接接 SES / 阿里云邮件推送 / SendGrid 之一；通知模板做 SPF / DKIM / DMARC。
- **平台模型成本与权限**：用户只能选择管理员配置且当前套餐允许的模型；套餐降级、模型下架或渠道不可用时必须自动回退到可用平台模型，避免暴露部署级密钥或内部模型配置。
- **支付通道风险**（新）：
  - **商户准入失败**：微信 / 支付宝个体户准入需提供经营范围、对公账户、法人身份证；周期约 1–3 周。**应在 Phase 1 同步推进，避免阻塞 Phase 5**。
  - **资金对账偏差**：必须有**每日对账任务**（拉通道账单 vs 本地订单），偏差落告警；MVP 可手工核单，但脚本必须就绪。
  - **退款滥用**：限制单用户退款次数（如 1 次/月）、退款金额阈值需运营二审、所有退款落审计日志。
  - **回调伪造**：回调入口必须做签名校验 + IP 白名单 + 幂等 token；签名失败的请求落日志但 200 返回（避免被通道反复重试触发 DDoS）。
  - **黑产薅羊毛**：限时折扣 / 兑换码 / 邀请奖励都是高危场景，需要绑定设备指纹 + IP + 手机号实名（或仅限邀请制）。
- **数据备份**：第一版必须做**每日全量备份 + 7 天保留**；备份恢复演练上线前至少跑一次。SQLite 文件单点丢失风险高，建议 Phase 5 前迁 PostgreSQL（详见 §5.9）。
- **桌面端定位**：决定是「Pro 福利」还是「废弃」。MVP 期间不投入即可，桌面端 Release 工作流暂停 main 分支自动触发。
- **存量数据丢弃**：迁移上线前周知现有自部署用户（README + CHANGELOG），并保留旧版本 tag 供回退。
- **ICP 备案 / 经营许可证**：Web 站若部署在境内服务器（阿里云 / 腾讯云），**ICP 备案**强制要求；接入支付通道时部分商户类目还需要 **ICP 经营许可证（增值电信业务）**。提前与服务器供应商确认。

## 8. 待确认事项（不阻塞规划，但实现前需明确）

**业务侧**：

- 各档位最终配额数字与价格（草案 §3，需要运营定）。
- 限时折扣 / 首单价 / 邀请奖励的具体力度与白名单。
- 客服 IM 群（微信 / 飞书 / Discord 二选一）+ 工单系统选型（飞书工单 / Zoho / 自建表单）。
- 落地页文案与素材（中文为主），是否预算投放小红书 / 公众号软文。
- 7 天无理由退款的具体边界：「已消费」如何定义（按调用次数 / 按使用天数）？建议按**已使用天数比例扣除**（含一日不退）。

**合规与主体**：

- 商户主体（个体工商户 / 公司有限责任）；个体户准入快但金额上限较低，公司主体长期更稳。
- 部署机房（境内 / 境外）；境内需要 ICP 备案 + 等保 2.0 三级以下，境外需要遵循 PIPL 的数据出境规则。
- 协议三件套（用户协议 / 隐私政策 / 投资风险揭示书）由谁起草（自行参照同类 / 法律顾问审阅）。
- 是否需要持「证券投资咨询牌照」；MVP 不以「投资建议」定性即可规避，但仍需在所有出口加免责声明。

**工程侧**：

- 邮件发送服务选型（SES / 阿里云邮件推送 / SendGrid，三选一）。
- 平台模型 provider 范围与套餐分档策略（OpenAI / Anthropic / Gemini / Anspire / AIHubMix / 自定义 OpenAI 兼容端点等由管理员配置）。
- 是否在 Phase 5 前把 SQLite 迁到 PostgreSQL（推荐迁，订单 / 退款写入并发风险较高）。
- 监控选型（Sentry 免费版 + Uptime Robot 已足够 MVP；后期再引入 Prometheus / Grafana）。
- 是否上线 PWA 支持（移动端体验决定）。

## 9. 产品指标与运营北极星

第一版上线后必须可量化的核心指标，否则迭代失去方向。

### 9.1 北极星指标（One Metric That Matters）

- **MVP 阶段（前 3 个月）**：**周活跃付费用户（Weekly Paying Users, WPU）**。
  - 理由：DAU 受免费用户波动大，WPU 直接反映「愿意为 AI 分析付费」的真实需求量。
  - 目标：首月 ≥ 50，第 3 月 ≥ 300（保守估计，运营定校准）。
- **PMF 验证后**：切换为 **MRR（月度经常性收入）** 或 **付费用户留存率（Paid M3 Retention）**。

### 9.2 关键指标看板

| 维度 | 指标 | 默认计算口径 | 上线 Phase |
| --- | --- | --- | --- |
| 用户规模 | DAU / WAU / MAU | `app_users` 按 `last_login_at` 聚合 | Phase 1 |
| 注册转化 | 落地页 → 注册 → 首单分析 → 付费 | `app_growth_events` 漏斗 | Phase 6 |
| 付费转化 | 免费 → Pro 转化率 | `paid_users / active_users` | Phase 5 |
| 收入 | DAU ARPU / MRR / ARR | 订单聚合 | Phase 5 |
| 留存 | 注册后 D1 / D7 / D30 留存 | `last_login_at` 时间序列 | Phase 1 |
| 付费留存 | 付费 M1 / M3 续费率 | `app_subscriptions` 续期统计 | Phase 5 |
| 单用户成本 | LLM 调用成本 / 邮件成本 / 服务器成本 | 按 user_id 累加调用次数 × 单价 | Phase 2 |
| 客服 | 工单响应时长 / 满意度 | 工单系统 | Phase 6 |
| 退款 | 退款率 / 退款金额 / 退款 Top 原因 | `app_refunds` 聚合 | Phase 5 |
| 故障 | 5xx 比例 / 支付回调失败率 / 调度失败率 | Sentry + 业务日志 | Phase 6 |

### 9.3 报表 / 看板实现

- MVP：直接在运营后台 (`/admin/stats`) 暴露几张 SQL 聚合视图（不接 BI 工具，节省成本）。
- 二期：按需引入 Metabase / Superset（开源、免费、自部署即可）。
- 不在本次范围：广告归因、A/B 测试框架、推荐算法。

### 9.4 单用户成本红线

为防止平台 Key 失血，定义以下红线：

- **免费档单用户日成本** 不超过 **¥0.5**（按当前 LLM 价格估算）；超过即触发 §5.4.1 的全局熔断。
- **平台 Pro 用户单月成本** 不超过订阅价格的 **40%**（即毛利率 ≥ 60%）；持续两个月跌破即评估涨价、调整配额或收紧高成本模型分档。

## 10. 落地节奏与里程碑

> §6 描述了**模块的工程依赖**，本节给出**按周/双周拆分的实施时间线**，便于对齐进度与资源。
> 时间估算基于 1 名后端 + 0.5 名前端 + 0.5 名运营的人力投入；遇到合规 / 商户准入卡点时整体后延。

### 10.1 时间线（第一版）

| 周 | 主线工作 | 并行工作 | 关键里程碑 |
| --- | --- | --- | --- |
| W1 | Phase 1 收尾：写入侧 `user_id` 透传（pipeline + 异步队列），portfolio_* / alert_* / conversation_messages 加 `user_id` | 启动微信支付商户准入资料、支付宝商户准入资料 | M1：业务表全部 per-user 隔离，回归测试通过 |
| W2 | Phase 2 收尾：模型路由统一入口 `src/users/model_router.py`，平台 Key 池监控 | 协议三件套起草（用户协议 / 隐私政策 / 投资风险） | M2：免费档不会拖垮平台 Key 预算 |
| W3 | Phase 3 启动：调度器按用户分桶 + 通知按用户偏好 | 选定邮件 SaaS（SES / 阿里云 / SendGrid） | M3：单用户失败降级落地 |
| W4 | Phase 3 收尾：邮件模板个性化 + 退订链接 | 接入邮件 SaaS + DKIM/SPF/DMARC 配置 | M4：邮件送达率 ≥ 95% |
| W5 | Phase 4：模型偏好路由层 + Pro Webhook 通知 | 监控接 Sentry + Uptime Robot | M5：Pro 用户首条 webhook 推送 |
| W6 | Phase 5 数据层：`app_orders` / `app_payment_events` / `app_refunds` / `app_invoices` 表 + 服务层 | 商户准入下证（理论上 W4 应已下证） | M6：本地可创建订单（不连真支付） |
| W7 | Phase 5 通道层：微信 Native + 支付宝 PC SDK 接入；下单 / 拉取二维码 / 状态轮询 | `/billing` 前端改造（套餐选择 → 二维码弹窗） | M7：沙箱环境完整跑通下单 → 支付 → 开通 |
| W8 | Phase 5 安全 + 对账：回调签名校验、幂等、状态机；每日对账任务 | `/account/orders` / `/account/invoices` 前端 | M8：生产环境跑通真实小额付款 |
| W9 | Phase 5 退款 + 发票：运营后台审核闭环 | 协议三件套上线 + 注册同意校验 | M9：完整退款 / 发票闭环 |
| W10 | Phase 6：注册防刷 + 客服入口 + FAQ + 公告中心 | 备份脚本 + 恢复演练 | M10：生产环境上线公测 |
| W11 | 公测期 bug fix + 数据观察 + 单用户成本核算 | 落地页文案与素材打磨 | M11：北极星指标基线建立 |
| W12 | 正式发版 + 邀请码内测扩展 | 小红书 / 公众号首批素材投放 | M12：第一版商用上线 |

### 10.2 关键依赖与卡点

- **商户准入是 Phase 5 的硬阻塞**：必须在 W1 启动，最迟 W6 拿证；个体户准入约 1–2 周，公司主体 2–4 周。
- **协议三件套必须在公测前上线**（最迟 W10），否则注册流程不能跑。
- **数据库迁移到 PostgreSQL** 建议在 W6 之前完成（订单写入并发）；如果不迁，W6 之后必须开启 SQLite WAL 模式 + 严格的写串行化。
- **ICP 备案**：境内服务器 + 自有域名，备案周期 2–4 周；建议 W1 同步发起。
- **外网域名 + HTTPS 证书**：支付通道要求合法 ICP + 有效 HTTPS，提前一周申请 Let's Encrypt 或买证书。

### 10.3 风险预案

- **商户准入被拒** → Plan B：先用 §11.10 的「人工汇款 + 收款码 + 工单核单」过渡，保 W12 上线，准入过审后再切。
- **平台 Key 成本超预算** → Plan B：临时下调免费档配额（环境变量 `USER_FREE_DAILY_ANALYSIS=2`），或直接关闭免费档注册（仅邀请制）。
- **首版收入未达预期** → Plan B：暂不接自动续费，把人力投入到落地页 / 增长 / SEO。

## 11. 支付通道接入细则

> 本节是第一版商业化闭环的核心补充。所有提到的接口名 / 字段名为草案，实现时以 `data_provider/`、`src/services/` 与 `api/v1/endpoints/` 的实际命名规范为准。
> 通道选型：**微信支付 Native（PC 扫码）** + **支付宝 PC 网站支付（扫码）**。手机端 H5 网站访问时退化到对应 H5 通道（二期落地）。

### 11.1 通道选型与商户准入

| 通道 | 接口模式 | 适用场景 | 商户准入 |
| --- | --- | --- | --- |
| 微信支付 Native | 后端下单 → 返回 `code_url` → 前端渲染二维码 → 用户微信扫码 → 微信回调 | PC 浏览器 | 微信支付商户平台 + 微信公众平台主体（个体户 / 公司均可） |
| 支付宝 PC 网站支付 | 后端下单 → 返回支付宝跳转 URL（含二维码） → 前端嵌入或新开页 → 支付宝异步通知 | PC 浏览器 | 支付宝商家中心（个体户 / 公司均可） |
| 微信支付 H5 | 后端下单 → 返回 `mweb_url` → 前端跳转 → 用户在微信中确认 | 移动浏览器（非微信内） | 同 Native |
| 微信 JSAPI | 用户在微信内打开 → 后端下单 → 调起 SDK | 微信内（公众号 / 小程序） | 需要绑定公众号 |
| 支付宝手机网站支付 | 后端下单 → 跳转 → 用户在支付宝 App 内付款 | 移动浏览器 | 同 PC |

**第一版范围**：仅 Native（PC 扫码）+ PC 网站支付（扫码）。H5 / JSAPI 留到二期，理由：

- PC 扫码足以覆盖 80% 桌面端用户；移动端用户先用 H5 唤起 Native（部分浏览器支持）或落地页提示「请用 PC 完成下单」。
- H5 的安全约束更复杂（防钓鱼跳转、回退处理），延后能集中精力。

**商户准入清单**（在 Phase 1 启动同步推进）：

- 营业执照（个体工商户 / 公司均可）。
- 法人身份证正反面。
- 对公账户（个体户对公账户即可）。
- 经营范围中**含「软件 / 信息技术服务」类目**（避免归到「证券咨询」等敏感类目）。
- 网站 ICP 备案截图、网站首页截图、价格页截图。
- 客服邮箱 + 客服电话。

### 11.2 数据模型（新增表草案）

> 字段名采用 snake_case，字段类型沿用 `src/storage/` 中 SQLAlchemy ORM 的风格。
> 实现时在 `src/storage/models/` 对应模块追加 ORM 模型，并通过 **Alembic migration**（`alembic revision --autogenerate` + `alembic upgrade head`）落库；禁止直接调用 `Base.metadata.create_all()` 或手写 `ALTER TABLE` 变更生产 schema。

#### 11.2.1 `app_orders`（订单主表）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | Integer PK | 自增 |
| order_no | String(32) UNIQUE | 业务订单号，建议 `DSA{yyyymmdd}{random10}` |
| user_id | FK app_users.id | 必须 |
| plan_code | String(32) | 下单时的套餐 |
| grant_days | Integer | 下单时锁定的赠送天数 |
| amount_cents | Integer | 实付金额（分） |
| original_amount_cents | Integer | 原价（分），便于折扣审计 |
| discount_cents | Integer | 优惠金额（分） |
| coupon_code | String(64) | 使用的优惠码（如有） |
| currency | String(8) | 默认 CNY |
| provider | String(16) | `wechat` / `alipay` |
| provider_trade_no | String(64) | 通道交易号（微信 transaction_id / 支付宝 trade_no） |
| status | String(16) | `created` / `pending` / `paid` / `failed` / `closed` / `refunded` / `partial_refunded` |
| client_ip | String(64) | 下单 IP |
| user_agent | String(255) | 下单 UA |
| quote_snapshot | Text JSON | 下单时的套餐快照（防价格漂移） |
| paid_at | DateTime | 支付完成时间 |
| expires_at | DateTime | 订单超时时间（默认 15min 关单） |
| created_at | DateTime | |
| updated_at | DateTime | |

**唯一约束**：`uix_app_orders_order_no`、`uix_app_orders_provider_trade_no`（允许 NULL）。
**索引**：`(user_id, created_at)`、`(status)`、`(provider, status)`。

#### 11.2.2 `app_payment_events`（通道回调流水）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | Integer PK | |
| order_no | String(32) | 关联订单，**允许重复**（同一订单可能收到多次回调） |
| provider | String(16) | `wechat` / `alipay` |
| event_type | String(32) | `pay.success` / `pay.fail` / `refund.success` / `refund.fail` |
| provider_event_id | String(128) | 通道事件 ID（微信 transaction_id + 状态、支付宝 notify_id） |
| raw_payload | Text | 原始回调 body（脱敏后写入） |
| signature | String(255) | 通道签名 |
| signature_valid | Boolean | 是否通过签名校验 |
| processed | Boolean | 是否已驱动业务（幂等控制） |
| processed_at | DateTime | 业务驱动完成时间 |
| received_at | DateTime | 收到回调时间 |

**唯一约束**：`uix_app_payment_events_provider_event_id`（保证幂等）。
**用途**：所有回调原样落库，便于线下回放、对账、审计；`signature_valid=false` 的事件**仅落库不驱动业务**。

#### 11.2.3 `app_refunds`（退款记录）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | Integer PK | |
| refund_no | String(32) UNIQUE | 退款单号 `RF{yyyymmdd}{random10}` |
| order_no | String(32) | 关联订单 |
| user_id | FK | |
| amount_cents | Integer | 应退金额 |
| reason | String(255) | 用户填写 |
| reviewer_id | FK app_users.id | 运营审核人（管理员） |
| status | String(16) | `pending` / `approved` / `rejected` / `refunded` / `failed` |
| provider_refund_no | String(64) | 通道退款单号 |
| revoke_subscription | Boolean | 是否同时收回 Pro 权益（默认 true） |
| created_at | DateTime | |
| approved_at | DateTime | |
| refunded_at | DateTime | |

#### 11.2.4 `app_invoices`（发票申请）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | Integer PK | |
| invoice_no | String(32) | 系统内部编号 |
| user_id | FK | |
| order_no | String(32) | 关联订单 |
| invoice_type | String(16) | `personal` / `company` |
| title | String(255) | 抬头 |
| tax_id | String(64) | 税号（公司必填） |
| amount_cents | Integer | 开票金额 |
| email | String(255) | 收件邮箱 |
| status | String(16) | `pending` / `issued` / `rejected` |
| issued_url | String(1024) | 电子发票下载 URL |
| reviewer_id | FK | |
| created_at | DateTime | |
| issued_at | DateTime | |

#### 11.2.5 订单状态机（不可回退）

```
created ──下单超时──> closed
   │
   └──发起支付──> pending ──通道回调成功──> paid ──申请退款──> refunded / partial_refunded
                       │
                       └──通道回调失败──> failed (用户可重新下单)
```

**关键规则**：

- `paid` → `refunded` 必须经过 `app_refunds` 流转，不直接改 `app_orders.status`。
- 任何分支都不允许从 `paid` 回到 `pending` / `failed` / `closed`。
- 状态机用 SQLAlchemy `before_update` 钩子或服务层校验强制约束。

### 11.3 下单与扫码流程

```
用户点击「升级 Pro 月付」
    ↓
POST /api/v1/billing/orders         # 创建订单, 返回 order_no
    ↓
POST /api/v1/billing/orders/{order_no}/pay?provider=wechat
    ↓
后端 → 微信 / 支付宝 SDK 下单 → 返回 code_url / qr_url
    ↓
前端渲染二维码 + 倒计时（15min）
    ↓
GET /api/v1/billing/orders/{order_no}    # 前端每 2s 轮询
    ↓
通道异步回调 → POST /api/v1/billing/callbacks/{provider}
    ↓
后端校验签名 → 写 app_payment_events → 驱动 app_orders.status='paid' → grant_plan
    ↓
前端轮询命中 status='paid' → 跳转 /account 显示「升级成功」
```

**幂等关键点**：

- 创建订单：同一 `(user_id, plan_code, status='created'|'pending')` 在 5 分钟内不允许并发创建第二个订单（避免重复扣款）。
- 回调驱动：以 `provider_event_id` 唯一约束保证同一回调只处理一次；处理前用 `SELECT ... FOR UPDATE` 锁定订单行。
- 前端轮询：建议 2s 间隔，超过 60s 改 5s 间隔，超过 5min 提示用户「支付超时」并允许刷新订单。

### 11.4 支付通道回调

#### 11.4.1 微信支付 Native 回调

- 接口：`POST /api/v1/billing/callbacks/wechat`
- 内容：JSON，敏感字段（resource.ciphertext）需要 AES-GCM 解密（密钥即微信支付 APIv3 密钥）。
- 校验：
  - HTTP Header `Wechatpay-Signature` 校验（用微信平台证书公钥验签）。
  - `Wechatpay-Timestamp` 与服务器时间偏差 ≤ 5min。
  - `Wechatpay-Nonce` 在 24h 内不可重复（用 Redis / 内存 LRU 缓存）。
- 返回：成功处理后返回 HTTP 200 + `{"code":"SUCCESS","message":"OK"}`；失败返回非 200 让微信重试。

#### 11.4.2 支付宝 PC 网站支付回调

- 接口：`POST /api/v1/billing/callbacks/alipay`
- 内容：表单（application/x-www-form-urlencoded）。
- 校验：
  - 用支付宝公钥验签 `sign` 字段（`RSA2`）。
  - `app_id` 必须等于本应用 `ALIPAY_APP_ID`。
  - `out_trade_no` 必须能在 `app_orders` 找到对应记录。
- 返回：处理成功返回纯文本 `success`；失败返回 `failure` 或非 200 让支付宝重试。

### 11.5 对账任务

每日凌晨 02:00 跑对账（`scripts/reconcile_payments.py`）：

1. 拉取**昨日通道账单**（微信支付商户平台 / 支付宝商户中心 API）。
2. 与本地 `app_orders` 按 `provider_trade_no` 关联。
3. 输出三类差异并落 `app_reconciliation_diff` 表 + 邮件发送给运营：
   - **通道有 / 本地无**：通道有支付记录但本地没订单，可能是回调丢失，**自动补单**（拉起 grant_plan）。
   - **本地有 / 通道无**：本地标记 paid 但通道没有对应记录，**告警**，可能是伪造回调或测试数据污染。
   - **金额不一致**：通道金额与本地金额不一致，**告警 + 暂停通道**，由运营手动核对。
4. 当日核对完成后写一条 `app_reconciliation_report`（汇总状态：clean / has_diff），便于审计。

### 11.6 退款流程

```
用户在 /account/orders 点击「申请退款」
    ↓
POST /api/v1/billing/refunds         # 创建退款单, status=pending
    ↓
邮件 / 飞书机器人通知运营
    ↓
运营在后台 /admin/refunds 审核
    ├─ 通过 → POST /api/v1/admin/refunds/{id}/approve
    │       → 调用通道退款 API → status=refunded → 收回 Pro 权益（更新 app_users.plan_expires_at）
    └─ 拒绝 → POST /api/v1/admin/refunds/{id}/reject  → status=rejected
    ↓
邮件回执用户
```

**业务规则**：

- 7 天无理由：订单 `paid_at` 之后 7 天内可申请，**未消费**（按 `app_user_usage_counters` 检查注册以来日均使用 = 0）的全额退；**已消费**按已使用天数比例扣除（最少扣 1 天）。
- 单用户退款次数限制：同一 `user_id` 累计 ≥ 2 次退款时强制人工二审，运营拍板。
- 风控：发起退款时记录 `app_audit_logs`，含 IP / UA / 退款原因；同一 IP 单日发起退款 ≥ 3 次时拉黑。

### 11.7 安全（签名 / 幂等 / 状态机）

- **HTTPS 全程强制**：回调入口、下单接口、退款接口必须 HTTPS；HTTP 直接 308 重定向到 HTTPS。
- **回调签名**：见 §11.4.1 / §11.4.2；签名失败的请求落 `app_payment_events.signature_valid=false` + 200 返回，**不驱动业务**。
- **幂等键**：
  - 下单：`(user_id, plan_code, status in (created,pending))` 5 分钟内只允许一条。
  - 支付驱动：`provider_event_id` 唯一约束。
  - 退款：`refund_no` 唯一约束 + 退款时 `SELECT ... FOR UPDATE` 锁单。
- **敏感字段管理**：
  - `WECHAT_PAY_MCH_ID` / `WECHAT_PAY_APIV3_KEY` / `WECHAT_PAY_PRIVATE_KEY_PATH` / `ALIPAY_APP_PRIVATE_KEY_PATH` / `ALIPAY_PUBLIC_KEY_PATH` 全部走环境变量。
  - 私钥文件不入库、不入 Git；仅在部署时通过 secret 注入。
  - 启动时校验密钥可解密一段哨兵字符串，失败 fail-fast。
- **防止重放**：所有回调请求落 `app_payment_events`，`provider_event_id` 唯一约束自动去重。
- **状态机不可回退**：服务层封装 `OrderService.transition(order, new_status)`，校验 `(old, new)` 是否在白名单内，否则抛异常。
- **金额防篡改**：前端**仅用于展示**，最终金额以 `app_orders.amount_cents` 为准；下单接口入参不接受前端传的 `amount`。

### 11.8 发票

- MVP 手工开具：用户提交发票申请 → 运营在第三方电子发票平台（如百望 / 诺诺 / 高灯）开具 → 把下载 URL 写回 `app_invoices.issued_url` → 邮件发送用户。
- 二期对接 SaaS 自动开票（高灯云 / 诺诺云 API），支持小额自动开票。
- 仅开**电子普通发票**（增值税普通发票）；增值税专用发票需要公司主体 + 一般纳税人资格，第一版**不支持**。
- 发票内容：服务名称固定为「信息技术服务费 - DSA AI 分析订阅」，避免敏感词。

### 11.9 价格、活动码与优惠

- **价格**：
  - 走 `app_plans.price_cents`，下单时**冻结到 `app_orders.quote_snapshot`**，后续涨价不影响已下单。
  - 不在前端暴露原始 `price_cents`，只展示「¥39 / 月」格式化字符串，避免人为篡改。
- **优惠码**：
  - 复用 `app_redeem_codes`，新增字段 `discount_type='days_free' | 'price_off'` 与 `discount_value`。
  - 类型 1：`days_free` → 直接赠送天数（线下渠道、邀请奖励）。
  - 类型 2：`price_off` → 在下单时减免金额（限时折扣、首单价）。
- **限时活动**：
  - 直接配置 `app_plans` 新增条目 `pro_first_month_99`，定价 ¥9.9，`grant_days=30`，`is_active=true` + `valid_until` 截止；活动到期后自动下架。
- **风控**：
  - 同一 `user_id` 仅可使用 1 次「首单价」类型的优惠码。
  - 同一 `client_ip` 单日下单 ≥ 3 次触发风控（防止羊毛党）。

### 11.10 Plan B：人工汇款 / 收款码兜底

> 用于商户准入未下证、通道临时故障的过渡方案，**不作为长期路径**。

- 落地页 / `/billing` 在「未配置支付通道」时降级为：
  - 显示运营提供的微信收款码 + 支付宝收款码（图片）。
  - 用户付款后填写「订单备注 + 付款截图 + 邮箱」。
  - 后台收到工单 → 运营核对到账 → `POST /api/v1/admin/grant-plan` 手动开通。
- 该路径必须**仅在准入未下证期间开放**，下证后立即下线，避免合规风险。

### 11.11 前端 / 后端 endpoint 列表（草案）

```
# 用户侧
GET  /api/v1/billing/plans                         # 已上线（仅读）
GET  /api/v1/billing/subscription                  # 已上线（仅读）
POST /api/v1/billing/orders                        # ✅ 已上线：创建订单
GET  /api/v1/billing/orders/{order_no}             # ✅ 已上线：查询订单
POST /api/v1/billing/orders/{order_no}/pay         # ✅ 已上线（PAYMENT_ENABLED=true 时调用 gateway.place_order()）：拉起支付
POST /api/v1/billing/orders/{order_no}/cancel      # ✅ 已上线：用户主动关单
POST /api/v1/billing/refunds                       # ✅ 已上线：申请退款
GET  /api/v1/billing/refunds/{refund_no}           # ✅ 已上线：查询退款
POST /api/v1/billing/invoices                      # ✅ 已上线：申请发票
GET  /api/v1/billing/invoices                      # ✅ 已上线：列出我的发票

# 通道回调
POST /api/v1/billing/callbacks/wechat              # ✅ 已上线（签名校验为骨架）：微信支付回调
POST /api/v1/billing/callbacks/alipay              # ✅ 已上线（签名校验为骨架）：支付宝回调

# 运营后台
GET  /api/v1/admin/orders                          # 新增：订单列表
GET  /api/v1/admin/refunds                         # 新增：退款列表
POST /api/v1/admin/refunds/{id}/approve            # 新增：审核通过
POST /api/v1/admin/refunds/{id}/reject             # 新增：审核拒绝
GET  /api/v1/admin/invoices                        # 新增：发票列表
POST /api/v1/admin/invoices/{id}/issue             # 新增：标记已开具
GET  /api/v1/admin/reconciliation                  # 新增：对账报表
POST /api/v1/admin/grant-plan                      # 新增：手动开通（兜底）
```

### 11.12 监控与告警

- **支付下单成功率**：`paid / created` 比值，<70% 触发告警（可能 SDK 故障 / 网络故障）。
- **回调失败率**：`signature_valid=false` 比例，>0.1% 立即告警（可能伪造攻击）。
- **回调延迟**：从用户支付完成到回调到达本地的时间 P99，>30s 告警。
- **对账差异**：每日跑完后差异 ≥ 1 条即邮件 / 飞书通知运营。
- **退款积压**：`status=pending` 超过 24h 的退款单触发提醒。

### 11.13 环境变量清单（追加到 `.env.example`）

```
# 微信支付
WECHAT_PAY_APP_ID=
WECHAT_PAY_MCH_ID=
WECHAT_PAY_APIV3_KEY=
WECHAT_PAY_PRIVATE_KEY_PATH=/app/secrets/wechat_apiclient_key.pem
WECHAT_PAY_CERT_SERIAL_NO=
WECHAT_PAY_NOTIFY_URL=https://yourdomain.com/api/v1/billing/callbacks/wechat

# 支付宝
ALIPAY_APP_ID=
ALIPAY_APP_PRIVATE_KEY_PATH=/app/secrets/alipay_app_private_key.pem
ALIPAY_PUBLIC_KEY_PATH=/app/secrets/alipay_public_key.pem
ALIPAY_NOTIFY_URL=https://yourdomain.com/api/v1/billing/callbacks/alipay
ALIPAY_RETURN_URL=https://yourdomain.com/billing?from=alipay

# 通用
PAYMENT_ENABLED=false              # 总开关；false 时 /billing 走 §11.10 兜底
PAYMENT_ORDER_EXPIRE_MINUTES=15    # 订单超时关单时长
PAYMENT_RECONCILE_HOUR=2           # 对账任务时刻（UTC+8）
INVOICE_BACKEND=manual             # manual / nuonuo / gaodeng

# 对账告警（Phase 6）
ADMIN_ALERT_EMAIL=                 # 接收对账差异邮件告警的运营邮箱；留空则跳过邮件告警
RECONCILE_WEBHOOK_URL=             # 对账差异 Webhook（飞书/企业微信/自定义 HTTP POST）；留空则跳过
```

### 11.14 落地实现的优先级

按 §10.1 的 W6–W9 推进，但内部排序：

1. ✅ 数据模型 + 状态机 + 服务层（`src/services/billing/`）——已完成。
2. ✅ 后端 endpoint（用户侧 + 回调）——已完成。
3. ✅ 下单 / 退款 SDK（`place_order` + `refund` 纯 `cryptography` 实现，不依赖第三方 SDK）——已完成。
4. ✅ 前端 `/billing` 改造（套餐选择 + 二维码弹窗 + 状态轮询）——已完成。
5. ✅ 运营后台（订单 / 退款 / 发票 / 对账）——已完成。
6. ✅ 对账脚本 + 邮件 / Webhook 告警——已完成；`fetch_settlements` 真实账单 API 接入——已完成。
7. ✅ 协议三件套上线 + 注册同意校验——已完成。
8. 生产小额验证（一笔真实付款 + 一笔退款）——待商户准入下证后执行。

## 12. 不在本次规划范围

- 实际代码改动（本文档为产品规划，代码改动由 Phase 1+ 各阶段单独推进）。
- 移动端原生 App、小程序、公众号开发。
- 复杂的运营后台、A/B 框架、推荐系统。
- 多语言全量铺开（先 zh-CN，后续视需求加 en）。
- 自动续费 / 连续包月 / 苹果内购（推迟到二期）。
- 增值税专用发票（需要公司主体 + 一般纳税人资格）。
- BI 工具（Metabase / Superset）的接入（MVP 用 SQL 视图够用）。

## 13. 相关文档

- [`docs/to-c-mode.md`](./to-c-mode.md)：Phase 1 工程骨架（环境变量、API、表结构、回滚方式）。
- [`docs/to-c-product-wireframes.md`](./to-c-product-wireframes.md)：关键页面线框（登录/注册/账户/会员中心/配额提示）。
- [`docs/notifications.md`](./notifications.md)：通知渠道基线（Phase 3 推送将基于此扩展为按用户偏好）。
- [`docs/LLM_CONFIG_GUIDE.md`](./LLM_CONFIG_GUIDE.md)：LLM 渠道配置（Phase 4 模型偏好路由将基于此扩展）。
- [`docs/CHANGELOG.md`](./CHANGELOG.md)：版本变更记录，To C 化各 Phase 的提交信息会按惯例落到此处。
