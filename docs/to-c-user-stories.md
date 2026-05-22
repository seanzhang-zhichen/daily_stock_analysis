# DSA To C 用户故事

本文档基于当前代码与 To C 多用户文档，整理面向个人投资者的核心用户故事、验收口径与已落地实现，作为产品规划、页面线框和研发验收之间的连接层。

相关文档：

- [To C 产品规划](./to-c-product-plan.md)
- [To C 关键页面线框](./to-c-product-wireframes.md)
- [To C 多用户模式](./to-c-mode.md)

## 1. 角色定义

| 角色 | 说明 | 当前入口 |
| --- | --- | --- |
| 游客 | 未登录访问者，可查看公开公告、法律协议，并进入登录 / 注册流程。 | `/login`、`/register`、`/notices`、`/legal/*` |
| 免费会员 | 已注册并登录的普通用户，可管理少量自选股、执行有限次数分析和 Agent 问股。 | `/`、`/chat`、`/watchlist`、`/account`、`/billing` |
| Pro 会员 | 已开通付费套餐的用户，可获得更高配额、更多自选股、BYOK、Webhook 和报告推送能力。 | `/billing`、`/account/api-keys`、`/account/orders`、`/account/invoices` |
| BYOK 用户 | Pro 会员中配置了自有模型 API Key 的用户，LLM 调用优先走用户自己的 Key。 | `/account/api-keys` |
| 平台管理员 | 具备 `is_admin=True` 的运营人员，处理用户、订单、退款、发票、公告和手动开通。 | `/admin` |

## 2. 用户旅程总览

| 阶段 | 用户目标 | 关键系统能力 | 当前状态 |
| --- | --- | --- | --- |
| 发现与信任 | 了解产品边界、风险声明和公告。 | 公告中心、协议三件套、帮助中心。 | ✅ 已落地 |
| 注册与登录 | 用邮箱创建账户并恢复访问。 | 邮箱密码注册、登录、邮箱验证、找回密码、session cookie。 | ✅ 已落地 |
| 首次配置 | 选择关注股票，形成个人工作区。 | 首次引导、自选股 CRUD、套餐上限校验。 | ✅ 已落地 |
| 日常分析 | 获取个股分析、历史报告和大盘复盘。 | 分析 API、异步任务、历史记录用户隔离、配额扣减。 | ✅ 已落地 |
| AI 问股 | 围绕股票进行多轮问答。 | Agent chat / stream、技能列表、会话隔离、配额扣减与失败返还。 | ✅ 已落地 |
| 订阅推送 | 自动收到个人报告。 | 通知偏好、每日调度、邮件、一键退订、Webhook。 | ✅ 主要落地 |
| 升级付费 | 解锁更高配额和高级能力。 | 套餐页、订单、微信 / 支付宝扫码、兑换码、续费提示。 | 🟡 待生产小额验证 |
| 售后与合规 | 申请退款、发票、数据导出或注销。 | 订单页、退款申请、发票申请、管理员审核、导出 / 注销 API。 | 🟡 部分入口待运营确认 |
| 运营管理 | 处理用户与商业化后台事务。 | Admin 后台、审计日志、公告管理、手动 grant-plan。 | ✅ 主要落地 |

## 3. 核心用户故事

### US-001：游客注册成为会员

**作为** 一个第一次访问 DSA 的个人投资者，**我希望** 用邮箱和密码注册账号，并明确同意服务条款、隐私政策和投资风险免责声明，**以便** 开始使用个人化股票分析能力。

**验收标准**：

- 用户可以从 `/register` 输入邮箱、密码、确认密码和可选邀请码。
- 注册表单必须勾选协议三件套后才能提交。
- 若 `USER_REQUIRE_EMAIL_VERIFICATION=true`，注册后不直接登录，提示用户去邮箱验证。
- 若公开注册关闭，前端应阻止普通公开注册路径，运营可通过其它方式建号或发放邀请。
- 注册成功后协议同意记录写入 `app_user_consents`。

**当前实现依据**：

- 前端：`UserAuthPage`、`/register`。
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

**作为** 新注册会员，**我希望** 在首次进入产品时添加 1–3 只关注股票，**以便** 首页和后续推送围绕我的关注列表工作。

**验收标准**：

- 注册成功后可进入 `/onboarding`。
- 引导页使用股票搜索添加股票，并展示完成进度。
- 免费用户最多添加 `min(plan.maxStocks, 3)` 只股票；Pro 用户最终上限由 `plan.maxStocks` 决定。
- 完成引导后进入首页，用户也可之后在 `/watchlist` 或账户页继续管理。

**当前实现依据**：

- 前端：`OnboardingPage`、`WatchlistPage`、`AccountPage` 中的自选股卡片。
- API：`GET/POST/PUT/DELETE /api/v1/account/watchlist`。
- 数据：`app_user_watchlists`。

### US-004：会员手动发起个股 AI 分析

**作为** 登录会员，**我希望** 在首页输入股票并点击分析，**以便** 获取 AI 生成的个股分析报告。

**验收标准**：

- 未登录用户访问首页会跳转到 `/login?redirect=...`。
- 登录用户调用 `POST /api/v1/analysis/analyze` 时必须携带当前 session。
- 同步或异步分析都写入当前用户上下文，历史数据仅当前用户可见。
- 分析前按套餐配额扣减；队列拒绝、重复任务或业务失败时按已有逻辑返还配额。
- 配额不足时返回 `quota_exceeded`，前端弹出升级 / BYOK 引导。

**当前实现依据**：

- 前端：`HomePage`、`QuotaExceededDialog`、任务队列与 SSE 状态流。
- API：`POST /api/v1/analysis/analyze`、`GET /api/v1/analysis/tasks`、`GET /api/v1/analysis/status/{task_id}`、`GET /api/v1/analysis/tasks/stream`。
- 服务：`src/users/quota_guard.py`、分析任务中的 `user_id` 透传。

### US-005：会员查看自己的历史报告

**作为** 登录会员，**我希望** 查看自己过去生成的分析报告，**以便** 复盘同一股票在不同时间的变化。

**验收标准**：

- 历史报告查询必须按当前 `AppUser.id` 过滤。
- 用户不能通过 URL、筛选条件或 API 参数读取他人的历史报告。
- 首页历史列表支持选中、筛选和打开报告详情。

**当前实现依据**：

- 前端：首页历史分析区域。
- API：`/api/v1/history/*`。
- 数据：`analysis_history.user_id`。

### US-006：会员使用 Agent 问股

**作为** 登录会员，**我希望** 在 `/chat` 与 AI 多轮对话并选择分析技能，**以便** 对某只股票或投资问题进行延展追问。

**验收标准**：

- `/chat` 只对登录用户可用。
- 每次 Agent 请求按套餐 Agent 配额扣减，失败或流式异常时返还。
- 会话列表、会话消息读取和删除均按当前用户隔离。
- Pro 且配置 BYOK 的用户优先使用自己的 Key；否则按套餐模型策略走平台模型。

**当前实现依据**：

- 前端：`ChatPage`、`agentApi.chatStream`、会话列表。
- API：`POST /api/v1/agent/chat`、`chat/stream`、`research`、`GET /api/v1/agent/chat/sessions`。
- 服务：`src/users/quota_guard.py`、`src/users/model_router.py`。

### US-007：会员随时理解自己的配额状态

**作为** 免费或 Pro 会员，**我希望** 在页面中看到今日剩余分析和问股次数，**以便** 判断是否继续使用、升级或配置 BYOK。

**验收标准**：

- 登录后全局导航展示今日分析 / Agent 配额状态。
- 分析、Agent、支付成功、兑换码使用后应刷新用户状态。
- 配额用完后，引导用户升级 Pro 或配置 BYOK。
- BYOK 启用时，前端应表达“不占用平台配额”的语义。

**当前实现依据**：

- 前端：`QuotaIndicator`、`QuotaExceededDialog`、`AuthContext.refreshStatus`。
- API：`GET /api/v1/account/status`。
- 服务：`src/users/quota.py`。

### US-008：Pro 会员开启每日报告推送

**作为** Pro 会员，**我希望** 开启每日推送和邮件通知，**以便** 在交易日自动收到自选股分析结果。

**验收标准**：

- 免费用户不能开启 AI 分析报告邮件推送或每日推送。
- Pro 用户可在账户页开启 `dailyPushEnabled` 与 `emailEnabled`。
- 调度器按用户自选股与通知偏好分桶执行分析。
- 单用户或单渠道失败不影响其它用户。
- 邮件中包含一键退订链接，用户无需登录即可关闭对应推送。

**当前实现依据**：

- 前端：`AccountPage` 通知偏好卡片。
- API：`GET/PATCH /api/v1/account/notification-prefs`、`GET /api/v1/account/notification-prefs/unsubscribe`。
- 服务：`run_per_user_scheduled_analysis`、`src/users/notification_delivery.py`、`src/users/unsubscribe.py`。

### US-009：Pro / BYOK 用户配置自己的模型 Key

**作为** Pro 会员，**我希望** 配置自己的模型 API Key，**以便** 在平台配额之外自费使用更灵活的模型能力。

**验收标准**：

- 免费用户访问 BYOK 页面时应被引导升级。
- Pro 用户可新增、覆盖或删除 provider 级 API Key。
- API Key 写入后只展示脱敏预览，不回显原文。
- 实际 LLM 调用时，BYOK 路由优先于平台模型路由。

**当前实现依据**：

- 前端：`ApiKeysPage`。
- API：`GET/POST/DELETE /api/v1/account/api-keys`。
- 服务：`src/users/byok.py`、`src/users/model_router.py`。
- 数据：`app_user_byok_credentials`。

### US-010：会员升级、续费或使用兑换码

**作为** 免费会员，**我希望** 通过套餐页升级 Pro，或使用兑换码获得权益，**以便** 获得更高配额和高级能力。

**验收标准**：

- `/billing` 展示套餐、当前订阅、订单和发票入口。
- 用户可使用兑换码升级套餐。
- 用户选择套餐后创建订单，拉起微信或支付宝扫码支付。
- 支付弹窗轮询订单状态，支付成功后刷新订阅和配额。
- Pro 到期前展示续费提示；到期未续费自动降级 free。

**当前实现依据**：

- 前端：`BillingPage`、`PaymentDialog`、`RenewalBanner`。
- API：`GET /api/v1/billing/plans`、`subscription`、`POST /api/v1/account/redeem`、`POST /api/v1/billing/orders`、`POST /api/v1/billing/orders/{order_no}/pay`。
- 服务：`src/users/plans.py`、`src/users/plan_lifecycle.py`、`src/services/billing/order_service.py`。

### US-011：付费用户处理订单、退款与发票

**作为** 付费用户，**我希望** 查看订单、申请退款和申请发票，**以便** 完成付费后的售后与财务流程。

**验收标准**：

- 用户可在 `/account/orders` 查看自己的订单列表。
- 未支付订单可取消，已支付订单可提交退款原因。
- 用户可在 `/account/invoices` 为已支付订单提交发票申请并查看处理状态。
- 退款与发票由管理员审核，结果通过邮件通知或状态更新反馈。
- 用户只能访问自己的订单、退款和发票记录。

**当前实现依据**：

- 前端：`OrdersPage`、`InvoicesPage`。
- API：`GET /api/v1/billing/orders`、`POST /orders/{order_no}/cancel`、`POST /refunds`、`POST /invoices`。
- 管理端：`/api/v1/admin/refunds/*`、`/api/v1/admin/invoices/*`。

### US-012：用户获取帮助、公告和风险提示

**作为** 个人投资者，**我希望** 在产品内看到公告、帮助和风险提示，**以便** 了解服务变更、排障方式和投资风险边界。

**验收标准**：

- 未登录用户可访问 `/notices` 和 `/legal/*`。
- 登录用户可从侧边栏进入公告中心与帮助中心。
- 公告铃铛展示近期公告数量。
- 帮助页提供 FAQ、反馈指引、配置入口和免责声明。
- 产品文案保持“AI 分析助手”定位，不承诺收益，不给出荐股保证。

**当前实现依据**：

- 前端：`NoticesPage`、`HelpPage`、`LegalPageLayout`。
- API：`GET /api/v1/notices`、`GET /api/v1/notices/unread-count`。
- 管理端：公告 CRUD 与发布 / 下架接口。

### US-013：用户处理数据导出与账号注销

**作为** 注册用户，**我希望** 可以申请导出个人数据或注销账号，**以便** 满足个人信息保护与账户退出诉求。

**验收标准**：

- 用户可申请个人数据导出，导出结果发送到注册邮箱。
- 用户可申请账号注销，进入 7 天冷静期并撤销当前 session。
- 冷静期内用户可取消注销申请。
- 到期后系统软删账号，并按规则清理个人数据；订单 / 发票按合规要求保留。
- 相关操作写入审计日志。

**当前实现依据**：

- API：`POST /api/v1/account/data-export`、`GET/POST/DELETE /api/v1/account/deletion`。
- 服务：`src/users/data_export.py`、`src/users/deletion.py`。
- 当前产品入口：普通账户页暂不展示数据导出和注销区块，后续可由运营策略决定是否开放显式入口。

### US-014：管理员处理运营后台事务

**作为** 平台管理员，**我希望** 在后台查看用户、订单、退款、发票、公告和审计日志，**以便** 支撑 To C 商业化运营。

**验收标准**：

- 只有 `is_admin=True` 的登录用户能访问 `/admin` 和 `/api/v1/admin/*`。
- 管理员可筛选用户、查看订单、审核退款、处理发票、手动开通套餐。
- 管理员可创建、发布、下架和删除公告。
- 管理员操作写入 `app_audit_logs`。
- 普通 C 端用户看不到 `/settings` 与 `/admin` 入口，直接访问 `/settings` 会回到 `/account`。

**当前实现依据**：

- 前端：`AdminPage`、`SidebarNav` 权限控制。
- API：`/api/v1/admin/me|users|orders|refunds|invoices|grant-plan|audit-logs|stats`。
- 权限：`api.deps.get_admin_user`、`app_users.is_admin`。

## 4. 关键验收边界

| 边界 | 验收口径 |
| --- | --- |
| 数据隔离 | 分析历史、投资组合、告警、Agent 会话、自选股、通知偏好、订单、退款和发票都必须按当前用户隔离。 |
| 普通用户权限 | 普通 C 端用户不能访问部署级系统设置和运营后台。 |
| 免费 / Pro 权益 | 免费用户可手动分析和问股但受低配额限制；AI 分析报告自动推送、Webhook 和 BYOK 属于 Pro 能力。 |
| 支付金额 | 前端不能决定开通金额和时长；以服务端订单快照为准。 |
| 通知可靠性 | 单个用户、单个股票或单个通知渠道失败不应拖垮其它用户调度。 |
| 投资合规 | 所有报告与页面应保持辅助分析定位，不承诺收益，不构成投资建议。 |
| 密钥安全 | BYOK Key 不回显原文；生产环境必须配置强加密密钥。 |
| 审计 | 登录、注册、协议同意、套餐变更、支付回调、退款、发票、退订、管理员操作等关键动作需要可追溯。 |

## 5. 当前缺口与后续故事池

| 编号 | 用户故事方向 | 当前缺口 |
| --- | --- | --- |
| FUT-001 | 用户选择个人语言、模型偏好和报告偏好。 | 语言、模型偏好仍依赖全局 `SystemConfig`，尚未 per-user 化。 |
| FUT-002 | Pro 用户查看平台模型池、BYOK 状态与模型可用性。 | `allowed_models` 具体分档配置和平台 Key 池用量看板待落地。 |
| FUT-003 | 用户邀请好友并获得 Pro 奖励。 | 邀请奖励 / 推荐裂变尚未落地。 |
| FUT-004 | 用户收到更精美的日报邮件与续费邮件。 | HTML 邮件模板视觉仍待运营素材定稿后打磨。 |
| FUT-005 | 用户在生产支付链路中完成小额端到端验证。 | 微信 / 支付宝生产商户准入与证书下发后再验证。 |
| FUT-006 | 运营从后台查看对账报表和平台 Key 用量。 | 对账脚本已有，Web 视图与用量看板待接入。 |
