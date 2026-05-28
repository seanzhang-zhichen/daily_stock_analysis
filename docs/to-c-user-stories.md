# DSA To C 用户故事

本文档基于当前前后端代码与 To C 多用户文档，整理面向个人投资者的核心用户故事、验收口径与已落地实现，作为产品规划、页面线框和研发验收之间的连接层。

本文聚焦账户、配额、分析、问股、推送、支付、合规与运营主链路；持仓、回测、告警等既有业务能力仅在关键边界中说明多用户隔离要求，后续可按需要拆成独立用户故事。

相关文档：

- [To C 产品规划](./to-c-product-plan.md)
- [To C 关键页面线框](./to-c-product-wireframes.md)
- [To C 多用户模式](./to-c-mode.md)

## 1. 角色定义

| 角色 | 说明 | 当前入口 |
| --- | --- | --- |
| 游客 | 未登录访问者，可查看公开公告、法律协议，并进入登录 / 注册流程。 | `/login`、`/register`、`/notices`、`/legal/*` |
| 免费会员 | 已注册并登录的普通用户，可管理少量自选股、执行有限次数分析和 Agent 问股。 | `/`、`/chat`、`/watchlist`、`/account`、`/billing` |
| Pro 会员 | 已开通付费套餐的用户，可获得更高配额、更多自选股、按套餐配置解锁的模型选项、Webhook 和报告推送能力。 | `/billing`、`/account`、`/account/orders`、`/account/invoices` |
| 平台管理员 | 具备 `is_admin=True` 的运营人员，处理用户、套餐用量、订单、退款、发票、公告和手动开通。 | `/admin` |

## 2. 用户旅程总览

| 阶段 | 用户目标 | 关键系统能力 | 当前状态 |
| --- | --- | --- | --- |
| 发现与信任 | 了解产品边界、风险声明和公告。 | 公开公告中心、协议三件套、登录后帮助中心。 | ✅ 已落地 |
| 注册与登录 | 用邮箱创建账户并恢复访问。 | 邮箱密码注册、登录、邮箱验证、找回密码、session cookie。 | ✅ 已落地 |
| 首次配置 | 选择关注股票，形成个人工作区。 | 首次登录引导、自选股 CRUD、套餐上限校验。 | ✅ 已落地 |
| 日常分析 | 获取个股分析、历史报告和大盘复盘。 | 分析 API、异步任务、历史记录用户隔离、配额扣减。 | ✅ 已落地 |
| AI 问股 | 围绕股票进行多轮问答。 | Agent chat / stream / research、技能列表、会话隔离、生成请求配额扣减与失败返还。 | ✅ 已落地 |
| 订阅推送 | 自动收到个人报告。 | 通知偏好、每日调度、邮件、一键退订、Webhook。 | ✅ 主要落地 |
| 升级付费 | 解锁更高配额和高级能力。 | 套餐页、订单、微信 / 支付宝扫码、兑换码、续费提示、本地 mock / 人工兜底。 | 🟡 待生产小额验证 |
| 售后与合规 | 申请退款、发票、数据导出或注销。 | 订单页、退款申请、发票申请、管理员审核、导出 / 注销 API。 | 🟡 数据导出 / 注销显式入口待运营确认 |
| 运营管理 | 处理用户与商业化后台事务。 | Admin 后台、套餐与每日用量配置、审计日志、公告管理、手动 grant-plan。 | ✅ 主要落地 |

## 3. 核心用户故事

### US-001：游客注册成为会员

**作为** 一个第一次访问 DSA 的个人投资者，**我希望** 用邮箱和密码注册账号，并明确同意服务条款、隐私政策和投资风险免责声明，**以便** 开始使用个人化股票分析能力。

**验收标准**：

- 用户可以从 `/register` 输入邮箱、密码、确认密码和可选邀请码。
- 注册表单必须勾选协议三件套后才能提交。
- 若 `USER_REQUIRE_EMAIL_VERIFICATION=true`，注册后不直接登录，提示用户去邮箱验证。
- 若未要求邮箱验证，注册成功后仍不会自动创建登录 session，用户需返回登录后进入首次引导。
- 若公开注册关闭，`/register` 显示“暂未开放注册”，后端仍以 `registration_disabled` 拒绝普通公开注册路径。
- 注册成功后协议同意记录写入 `app_user_consents`。

**当前实现依据**：

- 前端：`UserAuthPage`、`/register`，包含公开注册关闭时的阻止页。
- API：`POST /api/v1/account/register`。
- 数据：`app_users`、`app_user_consents`、`app_audit_logs`。

### US-002：会员登录、邮箱验证与找回密码

**作为** 已注册会员，**我希望** 能安全登录、完成邮箱验证，并在忘记密码时通过邮箱重置，**以便** 稳定恢复账号访问。

**验收标准**：

- 用户可在 `/login` 使用邮箱和密码登录，成功后写入 `dsa_user_session` httpOnly cookie。
- 用户点击验证邮件链接后进入 `/verify-email?token=...`，页面展示验证中、成功或失败状态。
- 用户可在 `/forgot-password` 请求重置邮件；无论邮箱是否存在，前端均展示一致提示。
- 使用重置 token 设置新密码后，后端吊销该用户旧 session。

**当前实现依据**：

- 前端：`UserAuthPage`、`VerifyEmailPage`、`ForgotPasswordPage`。
- API：`POST /api/v1/account/login`、`verify-email`、`request-password-reset`、`reset-password`、`logout`。
- 数据：`app_user_sessions`、`app_user_email_verifications`。

### US-003：新用户完成首次自选股引导

**作为** 新注册会员，**我希望** 在首次进入产品时可添加 1–3 只关注股票，**以便** 首页和后续推送围绕我的关注列表工作。

**验收标准**：

- 注册页成功提示不会自动登录；用户在首次登录且无显式 `redirect` 时如果自选股为空会进入 `/onboarding`；邮箱验证成功页的登录入口会携带 `/onboarding` 跳转。
- 引导页使用股票搜索添加股票，并展示完成进度。
- 免费用户最多添加 `min(plan.maxStocks, 3)` 只股票；Pro 用户最终上限由 `plan.maxStocks` 决定。
- 引导页支持跳过或完成后进入首页，用户也可之后在 `/watchlist` 继续管理。

**当前实现依据**：

- 前端：`OnboardingPage`、`WatchlistPage`。
- API：`GET/POST/PUT/DELETE /api/v1/account/watchlist`。
- 数据：`app_user_watchlists`。

### US-004：会员手动发起个股 AI 分析

**作为** 登录会员，**我希望** 在首页输入股票并点击分析，**以便** 获取 AI 生成的个股分析报告。

**验收标准**：

- 未登录用户访问首页会跳转到 `/login?redirect=...`。
- 登录用户调用 `POST /api/v1/analysis/analyze` 时必须携带当前 session。
- 同步或异步分析都写入当前用户上下文，历史数据、任务列表、任务状态和 SSE 事件仅当前用户可见；异步队列的重复检测也按当前用户隔离。
- 分析前按套餐配额扣减；同步异常、异步提交异常、重复任务或异步后台业务失败时按已扣次数返还配额。
- 配额不足时返回 `quota_exceeded`，前端弹出升级引导。

**当前实现依据**：

- 前端：`HomePage`、`QuotaExceededDialog`、任务队列与 SSE 状态流。
- API：`POST /api/v1/analysis/analyze`、`GET /api/v1/analysis/tasks`、`GET /api/v1/analysis/status/{task_id}`、`GET /api/v1/analysis/tasks/stream`。
- 服务：`src/users/quota_guard.py`、`src/services/task_queue.py` 中的 `user_id` 透传、按用户过滤与失败返还。

### US-005：会员查看自己的历史报告

**作为** 登录会员，**我希望** 查看自己过去生成的分析报告，**以便** 复盘同一股票在不同时间的变化。

**验收标准**：

- 历史报告查询必须按当前 `AppUser.id` 过滤。
- 用户不能通过 URL、筛选条件或 API 参数读取他人的历史报告。
- 首页历史列表支持选中、筛选、批量删除和打开报告详情；删除、新闻和 Markdown 报告查询同样按当前用户过滤。

**当前实现依据**：

- 前端：首页历史分析区域。
- API：`GET/DELETE /api/v1/history`、`GET /api/v1/history/{record_id}`、`news`、`markdown`。
- 数据：`analysis_history.user_id`。

### US-006：会员使用 Agent 问股

**作为** 登录会员，**我希望** 在 `/chat` 与 AI 多轮对话并选择分析技能，**以便** 对某只股票或投资问题进行延展追问。

**验收标准**：

- `/chat` 只对登录用户可用。
- 每次触发模型生成的 Agent chat / chat stream / research 请求按套餐 Agent 配额扣减，失败或流式异常时返还；技能列表、会话读取 / 删除和会话发送通知不扣 Agent 配额。
- 会话列表、会话消息读取和删除均按当前用户隔离。
- Agent 实际调用使用当前用户的模型路由；首选模型在账户页配置，若不可用则回退到平台可用模型。
- `/api/v1/agent/chat/send` 当前用于把会话内容发送到通知渠道，走全局通知链路；若面向普通 C 端用户开放为个人通知能力，需改为按当前用户通知偏好与套餐权益投递。

**当前实现依据**：

- 前端：`ChatPage`、`agentApi.chatStream`、会话列表。
- API：`GET /api/v1/agent/skills`、`POST /api/v1/agent/chat`、`chat/stream`、`research`、`chat/send`、`GET/DELETE /api/v1/agent/chat/sessions`。
- 服务：`src/users/quota_guard.py`、`src/users/model_router.py`。

### US-007：会员随时理解自己的配额状态

**作为** 免费或 Pro 会员，**我希望** 在页面中看到今日剩余分析和问股次数，**以便** 判断是否继续使用或升级。

**验收标准**：

- 登录后全局导航展示今日分析 / Agent 配额状态。
- 分析、Agent、支付成功、兑换码使用后应刷新用户状态。
- 配额用完后，引导用户升级 Pro。

**当前实现依据**：

- 前端：`QuotaIndicator`、`QuotaExceededDialog`、`AuthContext.refreshStatus`。
- API：`GET /api/v1/account/status`。
- 服务：`src/users/quota.py`。

### US-008：Pro 会员开启每日报告推送

**作为** Pro 会员，**我希望** 开启每日推送和邮件通知，**以便** 在交易日自动收到自选股分析结果。

**验收标准**：

- 免费用户不能开启 AI 分析报告邮件推送或每日推送。
- Pro 用户可在账户页开启 `dailyPushEnabled` 与 `emailEnabled`；`plan.canWebhook=true` 时可配置飞书 / 企业微信 / 钉钉 / Discord / Telegram / 自定义 Webhook。
- 调度器先按 `dailyPushEnabled` 收集用户，再校验当前套餐、自选股和交易日过滤；Pro 到期降级后不再触发每日自动分析。
- 单用户或单渠道失败不影响其它用户。
- 邮件中包含一键退订链接，用户无需登录即可关闭对应推送。

**当前实现依据**：

- 前端：`AccountPage` 通知偏好卡片。
- API：`GET/PATCH /api/v1/account/notification-prefs`、`GET /api/v1/account/notification-prefs/unsubscribe`。
- 服务：`run_per_user_scheduled_analysis`、`src/users/notification_prefs.py`、`src/users/notification_delivery.py`、`src/users/unsubscribe.py`。

### US-009：会员选择模型偏好

**作为** 登录会员，**我希望** 从管理员配置且当前套餐未限制或已允许的模型中选择首选模型，**以便** 在平台模型池内使用更符合需求的模型能力。

**验收标准**：

- 模型列表来自管理员配置的模型；当当前套餐 `allowed_models` 非空时再取交集，`allowed_models` 为空表示不额外限制平台可用模型。
- 用户可在 `/account` 清空首选模型，恢复平台默认模型顺序。
- 如果套餐降级或模型下架，后端应忽略不可用的首选模型并回退到可用平台模型。
- 普通用户不能访问部署级模型渠道、API Key 或系统设置；`/api/v1/system/config*` 仅平台管理员可访问。

**当前实现依据**：

- 前端：`AccountPage` 模型偏好卡片。
- API：`GET/PATCH /api/v1/account/model-preference`。
- 服务：`src/users/model_router.py`。
- 数据：`app_users.preferred_model`、`app_plans.allowed_models`。

### US-010：会员升级、续费或使用兑换码

**作为** 免费会员，**我希望** 通过套餐页升级 Pro，或使用兑换码获得权益，**以便** 获得更高配额和高级能力。

**验收标准**：

- `/billing` 展示套餐、当前订阅、订单和发票入口。
- 用户可使用兑换码升级套餐。
- 用户选择已配置价格的套餐后创建订单；`PAYMENT_ENABLED=true` 且通道配置完整时拉起微信或支付宝扫码支付。
- 本地或沙箱联调可通过 `PAYMENT_MOCK_ENABLED=true` 走 mock 支付；支付未启用且未开启 mock 时返回人工收款兜底提示。
- 支付弹窗轮询订单状态，支付成功后刷新订阅和配额。
- 微信 / 支付宝回调不需要登录，但会经过签名校验、金额校验和可选 IP 白名单检查后才驱动订单履约。
- Pro 到期前展示续费提示；到期未续费自动降级 free。

**当前实现依据**：

- 前端：`BillingPage`、`PaymentDialog`、`RenewalBanner`。
- API：`GET /api/v1/billing/plans`、`subscription`、`POST /api/v1/account/redeem`、`POST /api/v1/billing/orders`、`POST /api/v1/billing/orders/{order_no}/pay|mock-pay`、`POST /api/v1/billing/callbacks/wechat|alipay`。
- 服务：`src/users/plans.py`、`src/users/plan_lifecycle.py`、`src/services/billing/order_service.py`。

### US-011：付费用户处理订单、退款与发票

**作为** 付费用户，**我希望** 查看订单、申请退款和申请发票，**以便** 完成付费后的售后与财务流程。

**验收标准**：

- 用户可在 `/account/orders` 查看自己的订单列表。
- 未支付订单可取消，已支付订单可提交退款原因。
- 用户可在 `/account/invoices` 为已支付订单提交发票申请并查看处理状态。
- 退款与发票由管理员审核；退款审核结果通过邮件和状态反馈，发票处理结果当前通过状态与开票链接反馈。
- 用户只能访问自己的订单、退款和发票记录。

**当前实现依据**：

- 前端：`OrdersPage`、`InvoicesPage`。
- API：`GET /api/v1/billing/orders`、`GET /orders/{order_no}`、`POST /orders/{order_no}/cancel`、`POST /refunds`、`GET /refunds/{refund_no}`、`POST/GET /invoices`。
- 管理端：`/api/v1/admin/refunds/*`、`/api/v1/admin/invoices/*`。

### US-012：用户获取帮助、公告和风险提示

**作为** 个人投资者，**我希望** 在产品内看到公告、帮助和风险提示，**以便** 了解服务变更、排障方式和投资风险边界。

**验收标准**：

- 未登录用户可访问 `/notices` 和 `/legal/*`；`/help` 当前需要登录后访问。
- 登录用户可从侧边栏进入公告中心与帮助中心。
- 公告铃铛展示近期公告数量。
- 帮助页提供 FAQ、反馈指引和免责声明；系统设置入口只对管理员或非 To C 内部部署可见。
- 产品文案保持“AI 分析助手”定位，不承诺收益，不给出荐股保证。

**当前实现依据**：

- 前端：`NoticesPage`、`HelpPage`、`LegalPageLayout`。
- API：`GET /api/v1/notices`、`GET /api/v1/notices/unread-count`。
- 管理端：公告 CRUD 与发布 / 下架接口。

### US-013：用户处理数据导出与账号注销

**作为** 注册用户，**我希望** 可以申请导出个人数据或注销账号，**以便** 满足个人信息保护与账户退出诉求。

**验收标准**：

- 后端提供登录态 API 申请个人数据导出，导出结果发送到注册邮箱。
- 后端提供登录态 API 申请账号注销，进入 7 天冷静期并撤销当前 session。
- 冷静期内用户可通过 API 取消注销申请。
- 到期后系统软删账号，并按规则清理个人数据；订单 / 发票按合规要求保留。
- 相关操作写入审计日志；当前普通账户页不展示显式入口，后续开放 Web 入口时需补充二次确认与风险提示。

**当前实现依据**：

- API：`POST /api/v1/account/data-export`、`GET/POST/DELETE /api/v1/account/deletion`。
- 服务：`src/users/data_export.py`、`src/users/deletion.py`。
- 前端：`src/api/account.ts` 已封装 `requestDataExport`、`requestDeletion`、`cancelDeletion`、`getDeletionStatus`，普通账户页暂不展示数据导出和注销区块。

### US-014：管理员处理运营后台事务

**作为** 平台管理员，**我希望** 在后台查看用户、订单、退款、发票、公告和审计日志，**以便** 支撑 To C 商业化运营。

**验收标准**：

- 只有 `is_admin=True` 的登录用户能正常使用 `/admin` 和 `/api/v1/admin/*`。
- 管理员可筛选用户、配置免费档和会员套餐每日用量、查看订单、审核退款、处理发票、手动开通套餐和查看审计 / 统计数据。
- 管理员可创建、发布、下架和删除公告。
- 管理员操作写入 `app_audit_logs`。
- 普通 C 端用户看不到 `/settings` 与 `/admin` 入口，直接访问 `/settings` 会回到 `/account`，访问管理员 API 返回 403；直接访问 `/admin` 会因管理员 API 权限失败而不可用。

**当前实现依据**：

- 前端：`AdminPage`、`SidebarNav` 权限控制。
- API：`/api/v1/admin/me|users|plans|orders|refunds|invoices|grant-plan|audit-logs|stats`，系统配置 API 也依赖管理员权限。
- 权限：`api.deps.get_admin_user`、`app_users.is_admin`、`api/v1/endpoints/system_config.py` 的 router 级管理员依赖。

## 4. 关键验收边界

| 边界 | 验收口径 |
| --- | --- |
| 数据隔离 | 分析历史、异步分析任务 / SSE、投资组合、告警、Agent 会话、自选股、通知偏好、订单、退款和发票都必须按当前用户隔离。 |
| 普通用户权限 | 普通 C 端用户不能访问部署级系统设置和运营后台。 |
| 免费 / Pro 权益 | 免费用户可手动分析和问股但受低配额限制；AI 分析报告自动推送、Webhook 属于 Pro 能力；模型分档依赖运营配置，套餐 `allowed_models` 非空时才限制可选模型集合。 |
| 支付金额 | 前端不能决定开通金额和时长；以服务端订单快照为准，支付回调需经签名 / 金额校验后才可履约。 |
| 通知可靠性 | 单个用户、单个股票或单个通知渠道失败不应拖垮其它用户调度。 |
| 投资合规 | 所有报告与页面应保持辅助分析定位，不承诺收益，不构成投资建议。 |
| 模型权限 | 普通用户只能选择管理员配置且当前套餐未限制或已允许的模型；套餐 `allowed_models` 为空表示不额外限制平台可用模型，普通用户不能访问部署级模型渠道或密钥。 |
| 审计 | 登录、注册、协议同意、套餐变更、支付回调、退款、发票、退订、管理员操作等关键动作需要可追溯。 |

## 5. 当前缺口与后续故事池

| 编号 | 用户故事方向 | 当前缺口 |
| --- | --- | --- |
| FUT-001 | 用户选择个人语言和报告偏好。 | 语言与报告偏好仍依赖全局 `SystemConfig`，尚未 per-user 化。 |
| FUT-002 | Pro 用户查看平台模型池与模型可用性。 | 用户侧已能选择可用模型名称，但 `allowed_models` 运营分档、模型健康度和平台 Key 池用量看板待完善。 |
| FUT-003 | 用户邀请好友并获得 Pro 奖励。 | 邀请奖励 / 推荐裂变尚未落地。 |
| FUT-004 | 用户收到更精美的日报邮件与续费邮件。 | HTML 邮件模板视觉仍待运营素材定稿后打磨。 |
| FUT-005 | 用户在生产支付链路中完成小额端到端验证。 | 微信 / 支付宝生产商户准入与证书下发后再验证。 |
| FUT-006 | 运营从后台查看对账报表和平台 Key 用量。 | 对账脚本已有，Web 视图与用量看板待接入。 |
| FUT-007 | 用户在账户页自助导出数据或注销账号。 | 后端 API 与前端 API client 已具备，普通账户页显式入口、二次确认和运营策略待确认后开放。 |
| FUT-008 | 用户将问股会话发送到自己的通知渠道。 | 当前 `/api/v1/agent/chat/send` 走全局通知链路，若作为 C 端个人能力开放，需改为按当前用户通知偏好、套餐权益和审计链路投递。 |
| FUT-009 | 持仓、回测、告警形成完整 To C 用户故事。 | 相关能力已有页面或多用户隔离要求，但本文当前仅覆盖账户、配额、分析、问股、推送、支付与合规主链路。 |
