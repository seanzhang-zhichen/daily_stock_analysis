# To C 用户体系与计费

> 本文档基于 `backend/src/users/`、`backend/api/v1/endpoints/account.py`、`billing.py`、`admin.py` 和 `backend/src/storage/models/app.py` 当前代码，解释用户注册登录、Session、套餐、配额、订单支付、通知偏好、合规与管理员能力。

---

## 目录

1. [整体定位](#整体定位)
2. [模块结构](#模块结构)
3. [核心数据模型](#核心数据模型)
4. [注册流程](#注册流程)
5. [登录与 Session](#登录与-session)
6. [邮箱验证与密码重置](#邮箱验证与密码重置)
7. [套餐解析](#套餐解析)
8. [配额扣减与回补](#配额扣减与回补)
9. [自选股与通知偏好](#自选股与通知偏好)
10. [计费与订单](#计费与订单)
11. [支付回调](#支付回调)
12. [退款与发票](#退款与发票)
13. [管理员能力](#管理员能力)
14. [合规与审计](#合规与审计)
15. [关键配置](#关键配置)
16. [常见业务链路](#常见业务链路)
17. [开发注意事项](#开发注意事项)

---

## 整体定位

后端已经内置 To C 多用户体系。它解决以下问题：

- 用户注册、登录、退出
- 邮箱验证、密码重置
- 服务端 Session 管理
- 免费/付费套餐权益
- 每日分析次数与 Agent 次数配额
- 用户自选股与每日推送偏好
- 订单、支付、退款、发票
- 平台管理员运营能力
- 用户协议、审计日志、账号注销与数据导出

### 与旧单管理员模式的关系

项目早期有单管理员 `.admin_password_hash` 体系，当前 To C 用户体系与其解耦。

- C 端用户：`app_users`
- 平台管理员：`app_users.is_admin = true`
- 旧版管理员接口：`/api/v1/auth/*` 仍保留兼容
- 系统配置接口：仅平台管理员可访问

---

## 模块结构

```text
backend/src/users/
├── config.py                # USER_* 环境变量解析
├── service.py               # 注册、登录、改密、重置密码
├── sessions.py              # Session 创建、解析、吊销
├── passwords.py             # 密码哈希 pbkdf2_sha256
├── registration_guard.py    # 注册频率限制、一次性邮箱、MX 检查
├── email.py                 # 邮箱验证/密码重置邮件
├── unsubscribe.py           # 退订链接与前端公共 URL
├── plans.py                 # 套餐解析、开通、兑换码
├── quota.py                 # 用量计数、配额扣减、回补
├── quota_guard.py           # endpoint 层配额封装
├── watchlist.py             # 用户自选股
├── notification_prefs.py    # 用户通知偏好
├── model_router.py          # 用户可用模型选择
├── plan_lifecycle.py        # 套餐到期提醒与降级
├── audit.py                 # 审计日志
├── consents.py              # 协议版本与同意记录
├── deletion.py              # 账号注销冷静期
├── data_export.py           # 用户数据导出
├── notification_delivery.py # 用户级通知投递辅助
└── errors.py                # 用户体系业务错误码
```

API endpoint：

| 模块 | 路径 | 说明 |
|------|------|------|
| `account.py` | `/api/v1/account/*` | 账号、登录、自选股、通知偏好、注销 |
| `billing.py` | `/api/v1/billing/*` | 套餐目录、订单、支付、退款、发票 |
| `admin.py` | `/api/v1/admin/*` | 管理员运营后台 |
| `usage.py` | `/api/v1/usage/*` | 配额快照、增长事件 |
| `notices.py` | `/api/v1/notices/*` | 平台公告 |

---

## 核心数据模型

**文件：** `backend/src/storage/models/app.py`

### 用户主表：`app_users`

| 字段 | 说明 |
|------|------|
| `email` | 登录邮箱，唯一 |
| `password_hash` | PBKDF2-SHA256 哈希 |
| `status` | `active` / `disabled` |
| `plan_code` | 当前套餐代码，默认 `free` |
| `plan_expires_at` | 套餐过期时间，NULL 表示不过期/免费档 |
| `preferred_model` | 用户偏好的 Agent/LLM 模型 |
| `email_verified_at` | 邮箱验证时间 |
| `last_login_at` | 最近登录时间 |
| `is_admin` | 平台管理员标记 |
| `terms_version` | 最近一次接受的协议版本 |
| `deletion_requested_at` | 账号注销冷静期开始时间 |

### Session：`app_user_sessions`

服务端 Session 表。Cookie 只保存随机 Token，数据库保存 `sha256(token)`。

| 字段 | 说明 |
|------|------|
| `user_id` | 归属用户 |
| `token_hash` | token 的 SHA256 哈希 |
| `issued_at` | 签发时间 |
| `expires_at` | 过期时间 |
| `revoked_at` | 吊销时间 |
| `user_agent` / `ip` | 登录环境 |

### 配额：`app_user_usage_counters`

按用户、UTC 日期、类型记录当日用量。

唯一约束：

```text
(user_id, counter_date, kind)
```

`kind` 取值：

- `analysis`
- `agent`
- `notify`

### 套餐：`app_plans`

| 字段 | 说明 |
|------|------|
| `code` | 套餐代码，如 `free` / `pro` / `pro_yearly` |
| `name` | 展示名称 |
| `daily_analysis_limit` | 每日分析次数 |
| `daily_agent_limit` | 每日 Agent 次数 |
| `max_stocks` | 自选股上限 |
| `allowed_models` | JSON 字符串；为空表示不额外限制 |
| `can_webhook` | 是否允许自定义 Webhook |
| `price_cents` / `currency` | 价格 |
| `is_active` | 是否上架 |

### 订单与支付

| 表 | 说明 |
|----|------|
| `app_orders` | 订单主表 |
| `app_payment_events` | 支付回调流水 |
| `app_refunds` | 退款申请 |
| `app_invoices` | 发票申请 |
| `app_reconciliation_diffs` | 对账差异 |
| `app_reconciliation_reports` | 对账汇总 |

---

## 注册流程

入口：

```text
POST /api/v1/account/register
```

### 请求字段

```json
{
  "email": "user@example.com",
  "password": "Password123!",
  "passwordConfirm": "Password123!",
  "inviteCode": "optional",
  "termsAgreed": true,
  "termsVersion": "2026-01"
}
```

### 流程

```text
1. 读取 UserModeSettings
2. 校验公开注册是否开启
3. 校验邮箱格式、密码强度、二次密码一致
4. 校验是否显式同意协议
5. 执行注册保护：
   ├─ IP 频率限制
   ├─ 邮箱维度频率限制
   ├─ 一次性邮箱拦截
   └─ 可选 MX 记录检查
6. 如配置邀请码，则校验 inviteCode
7. 写 app_users
8. 写 app_user_consents
9. 生成邮箱验证 token（app_user_email_verifications）
10. 发送验证邮件
11. 返回注册成功
```

### 注册保护配置

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `USER_PUBLIC_REGISTRATION_ENABLED` | true | 是否允许公开注册 |
| `USER_INVITE_CODES` | 空 | 邀请码列表，逗号分隔 |
| `USER_REGISTER_DISPOSABLE_BLOCK` | true | 是否拦截一次性邮箱 |
| `USER_REGISTER_IP_DAILY_MAX` | 10 | 每 IP 每日注册上限 |
| `USER_REGISTER_EMAIL_DAILY_MAX` | 3 | 单邮箱每日尝试上限 |
| `USER_REGISTER_RATE_WINDOW_HOURS` | 24 | 限流窗口 |
| `USER_EMAIL_MX_CHECK_ENABLED` | false | 是否校验邮箱 MX 记录 |

---

## 登录与 Session

入口：

```text
POST /api/v1/account/login
```

### 登录流程

```text
1. 查询 app_users.email
2. 校验 status=active
3. 校验密码 hash
4. 更新 last_login_at
5. 生成随机 session token
6. 写 app_user_sessions(token_hash=sha256(token))
7. Set-Cookie: dsa_user_session=<token>
8. 返回用户信息
```

### Cookie

| 属性 | 说明 |
|------|------|
| 名称 | `dsa_user_session` |
| HttpOnly | true |
| SameSite | Lax |
| Path | `/` |
| TTL | `USER_SESSION_TTL_HOURS`，默认 14 天 |

### 请求认证

每次请求到 `/api/v1/*` 时：

```text
AuthMiddleware
  ├─ 检查是否 exempt path
  ├─ 读取 Cookie
  ├─ 查询 app_user_sessions
  ├─ 检查 expires_at / revoked_at
  └─ request.state.user = AppUser
```

---

## 邮箱验证与密码重置

### 邮箱验证

1. 注册后生成 `purpose=verify` 的一次性 token
2. 邮件链接指向前端公开地址
3. 前端调用：

```text
POST /api/v1/account/verify-email
```

4. 后端设置 `email_verified_at`

### 密码重置

```text
POST /api/v1/account/request-password-reset
POST /api/v1/account/reset-password
```

流程：

1. 用户提交邮箱
2. 生成 `purpose=reset` token
3. 邮件发送重置链接
4. 用户提交 token + 新密码
5. 后端重置密码并消费 token

### 前端公共 URL

注册验证邮件使用 `get_frontend_public_base_url()`，环境变量优先级：

1. `USER_FRONTEND_BASE_URL`
2. `USER_PUBLIC_BASE_URL`
3. `PUBLIC_BASE_URL`
4. `APP_BASE_URL`
5. 默认 `http://localhost:5200`

退订链接继续使用 API 公共地址，默认 `http://localhost:8000`。

---

## 套餐解析

**文件：** `backend/src/users/plans.py`

核心函数：

```python
resolve_user_plan(db, user)
```

### 解析规则

1. 用户 `plan_code` 为空或 `free` → 返回免费档
2. `plan_expires_at` 已过期 → 返回免费档（不立即写库）
3. `app_plans` 找不到对应套餐 → 返回免费档
4. 找到有效套餐 → 返回 `ResolvedPlan`

### 免费档默认值

来自 `UserModeSettings`：

| 权益 | 环境变量 | 默认 |
|------|----------|------|
| 每日分析次数 | `USER_FREE_DAILY_ANALYSIS` | 5 |
| 每日 Agent 次数 | `USER_FREE_DAILY_AGENT` | 5 |
| 自选股上限 | `USER_FREE_MAX_STOCKS` | 3 |

### allowed_models 语义

`allowed_models=[]` 表示 **不额外限制模型**。如果非空，则用户可用模型需要落在该列表内。

---

## 配额扣减与回补

**文件：** `backend/src/users/quota.py`、`quota_guard.py`

### 配额类型

| kind | 用途 |
|------|------|
| `analysis` | 股票分析任务 |
| `agent` | Agent 对话/研究 |
| `notify` | 用户级通知 |

### 核心函数

| 函数 | 说明 |
|------|------|
| `get_used()` | 查询当日已用次数 |
| `get_remaining()` | 查询剩余次数，`None` 表示不限额 |
| `try_consume()` | 原子扣减 1 次 |
| `refund()` | 失败时回补 1 次 |
| `get_quota_snapshot()` | 返回 analysis + agent 快照 |

### 扣减链路：分析任务

```text
POST /api/v1/analysis/tasks
  ├─ get_current_user
  ├─ resolve_user_plan
  ├─ enforce_quota(KIND_ANALYSIS)
  ├─ 入队后台任务
  └─ 立即返回 task_id

后台任务执行
  ├─ 成功：保持扣减
  └─ 失败：refund_quota(KIND_ANALYSIS)
```

### 扣减链路：Agent

```text
POST /api/v1/agent/chat
POST /api/v1/agent/chat/stream
POST /api/v1/agent/research
  ├─ enforce_quota(KIND_AGENT)
  ├─ 调用 Agent
  ├─ 成功：保持扣减
  └─ 明确业务失败：refund_quota(KIND_AGENT)
```

### 超额响应

```json
{
  "error": "quota_exceeded",
  "message": "今日使用次数已达上限",
  "quota": {
    "kind": "analysis",
    "limit": 5,
    "used": 5,
    "remaining": 0
  }
}
```

---

## 自选股与通知偏好

### 自选股

表：`app_user_watchlists`

接口：

```text
GET    /api/v1/account/watchlist
POST   /api/v1/account/watchlist
PUT    /api/v1/account/watchlist
DELETE /api/v1/account/watchlist/{stock_code}
```

约束：

```text
UNIQUE(user_id, stock_code)
```

上限由套餐 `max_stocks` 控制。

### 通知偏好

表：`app_user_notification_prefs`

字段：

| 字段 | 说明 |
|------|------|
| `daily_push_enabled` | 是否启用每日自动推送 |
| `email_enabled` | 是否启用邮件 |
| `webhook_url` | 用户自定义 Webhook（Pro 权益） |
| `webhook_type` | feishu / wecom / discord / telegram / generic |

接口：

```text
GET /api/v1/account/notification-prefs
PUT /api/v1/account/notification-prefs
GET /api/v1/account/notification-prefs/unsubscribe
```

Pro 用户可开启每日推送和自定义 Webhook，免费用户通常只能使用基础能力。

---

## 计费与订单

**文件：** `backend/src/services/billing/order_service.py`

入口：

```text
GET  /api/v1/billing/plans
GET  /api/v1/billing/subscription
POST /api/v1/billing/orders
GET  /api/v1/billing/orders/{order_no}
POST /api/v1/billing/orders/{order_no}/pay
POST /api/v1/billing/orders/{order_no}/cancel
```

### 订单状态机

```text
created → pending → paid → refunded / partial_refunded
                 └→ failed
created → closed
```

### 创建订单

订单写入 `app_orders`：

| 字段 | 说明 |
|------|------|
| `order_no` | 订单号，如 `DSA{yyyymmdd}{random}` |
| `user_id` | 下单用户 |
| `plan_code` | 购买套餐 |
| `grant_days` | 开通天数 |
| `amount_cents` | 实付金额 |
| `provider` | `wechat` / `alipay` / `manual` |
| `quote_snapshot` | 下单时套餐快照，防止价格漂移 |
| `expires_at` | 订单超时时间 |

### 支付开关

默认：

```text
PAYMENT_ENABLED=false
```

此时 `/pay` 返回 `503`，提示人工兜底。

---

## 支付回调

支付回调接口无需登录，但会做签名和来源验证：

```text
POST /api/v1/billing/callbacks/wechat
POST /api/v1/billing/callbacks/alipay
```

### 回调处理原则

1. 记录原始回调到 `app_payment_events`
2. 验证签名
3. 验证 IP 白名单（如配置）
4. 根据 `provider_event_id` 幂等去重
5. 签名无效只落库，不驱动业务
6. 支付成功后将订单置为 `paid`
7. 调用 `grant_plan()` 给用户开通/续期套餐
8. 写审计日志

### 幂等性

`app_payment_events.provider_event_id` 有唯一约束，防止支付平台重复回调导致重复开通。

---

## 退款与发票

### 退款

接口：

```text
POST /api/v1/billing/refunds
GET  /api/v1/billing/refunds/{refund_no}
```

管理员审核：

```text
POST /api/v1/admin/refunds/{refund_no}/approve
POST /api/v1/admin/refunds/{refund_no}/reject
```

状态：

```text
pending → approved → refunded
        └→ rejected
        └→ failed
```

### 发票

接口：

```text
POST /api/v1/billing/invoices
GET  /api/v1/billing/invoices
```

管理员处理：

```text
POST /api/v1/admin/invoices/{invoice_no}/issue
POST /api/v1/admin/invoices/{invoice_no}/reject
```

状态：

```text
pending → issued
        └→ rejected
```

---

## 管理员能力

管理员是 `app_users.is_admin = true` 的普通用户，使用同一套 Session。

### 管理员可操作

- 用户列表与详情
- 手动开通/续期套餐
- 禁用/启用用户
- 审核退款
- 处理发票
- 查看订单与对账
- 创建/编辑/发布公告
- 查看增长事件
- 查看审计日志
- 生成兑换码
- 访问系统配置

### 权限边界

普通用户访问 `/api/v1/admin/*` 返回 `403`。

普通用户不应访问系统配置接口：

```text
/api/v1/system/config*
```

系统配置涉及部署级密钥和运行参数，只允许平台管理员操作。

---

## 合规与审计

### 用户协议

表：`app_user_consents`

注册时必须同意协议，协议升版后可要求用户重新接受。

当前协议版本由：

```python
CURRENT_TERMS_VERSION
```

控制。

### 审计日志

表：`app_audit_logs`

记录关键操作，例如：

- `auth.login`
- `auth.register`
- `auth.change_password`
- `plan.redeem`
- `plan.grant`
- `order.create`
- `refund.create`
- `admin.grant_plan`
- `admin.approve_refund`
- `invoice.issue`

### 账号注销

字段：`AppUser.deletion_requested_at`

流程：

```text
用户申请注销 → 进入冷静期 → 冷静期内可取消 → 到期后由后续清理任务处理
```

### 数据导出

`data_export.py` 提供用户数据导出申请能力，满足个人信息可携带性要求。

---

## 关键配置

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `USER_PUBLIC_REGISTRATION_ENABLED` | true | 是否允许公开注册 |
| `USER_INVITE_CODES` | 空 | 注册邀请码列表 |
| `USER_SESSION_TTL_HOURS` | 336 | Session 有效期（14 天） |
| `USER_VERIFICATION_TTL_HOURS` | 24 | 邮箱验证 Token 有效期 |
| `USER_RESET_TTL_HOURS` | 2 | 密码重置 Token 有效期 |
| `USER_FREE_DAILY_ANALYSIS` | 5 | 免费用户每日分析次数 |
| `USER_FREE_DAILY_AGENT` | 5 | 免费用户每日 Agent 次数 |
| `USER_FREE_MAX_STOCKS` | 3 | 免费用户自选股上限 |
| `USER_REGISTER_DISPOSABLE_BLOCK` | true | 是否拦截一次性邮箱 |
| `USER_REGISTER_IP_DAILY_MAX` | 10 | IP 注册频率限制 |
| `USER_REGISTER_EMAIL_DAILY_MAX` | 3 | 邮箱注册频率限制 |
| `USER_EMAIL_MX_CHECK_ENABLED` | false | 是否检查邮箱 MX |
| `USER_FRONTEND_BASE_URL` | `http://localhost:5200` | 注册验证邮件前端链接 |
| `PAYMENT_ENABLED` | false | 是否启用真实支付 |
| `WECHAT_PAY_*` | 空 | 微信支付配置 |
| `ALIPAY_*` | 空 | 支付宝配置 |

新增配置项时必须同步更新 `.env.example` 和相关文档。

---

## 常见业务链路

### 链路一：新用户注册并完成首次分析

```text
POST /account/register
  ↓
发送邮箱验证邮件
  ↓
POST /account/verify-email
  ↓
POST /account/login
  ↓
GET /account/status 获取 free 套餐和配额
  ↓
POST /analysis/tasks 扣减 analysis 配额
  ↓
SSE /analysis/tasks/stream 等待完成
  ↓
AnalysisHistory 落库
```

### 链路二：用户购买 Pro

```text
GET /billing/plans
  ↓
POST /billing/orders
  ↓
POST /billing/orders/{order_no}/pay
  ↓
支付平台回调 /billing/callbacks/*
  ↓
签名验证 + 幂等处理
  ↓
订单置为 paid
  ↓
grant_plan(user, plan_code, grant_days)
  ↓
AppUser.plan_code / plan_expires_at 更新
```

### 链路三：Agent 对话消耗配额

```text
POST /agent/chat
  ↓
resolve_user_plan
  ↓
enforce_quota(KIND_AGENT)
  ↓
AgentExecutor / AgentOrchestrator
  ↓
写 ConversationMessage + LLMUsage
  ↓
返回回答
```

### 链路四：每日 Pro 用户自动推送

```text
Scheduler 到点
  ↓
查询 daily_push_enabled=true 用户
  ↓
resolve_user_plan
  ↓
跳过非 Pro 或已过期用户
  ↓
读取用户 watchlist
  ↓
执行 StockAnalysisPipeline(user_id=...)
  ↓
按用户通知偏好发送邮件/Webhook
```

---

## 开发注意事项

### 用户隔离

任何新增用户数据都应显式绑定 `user_id`，并在查询时过滤。

高风险场景：

- 历史记录列表
- Agent 会话列表
- 分析任务状态查询
- 投资组合账户
- 预警规则
- 通知偏好
- 发票/退款/订单

### 配额一致性

如果接口在业务开始前扣减配额，必须定义失败回补条件。

推荐模式：

```text
try_consume 成功
  ↓
执行业务
  ├─ 业务成功：保留扣减
  └─ 业务失败：refund
```

### 支付安全

支付回调必须：

- 免登录，但做签名验证
- 记录原始事件
- 幂等处理
- 签名无效不能驱动业务
- 不在日志中打印敏感密钥

### 管理员接口

管理员接口必须使用：

```python
Depends(get_admin_user)
```

不能只依赖前端隐藏入口。

### 系统配置

`SystemConfigService` 操作部署级 `.env`，普通 C 端用户不应调用。

### 文档同步

如果改动以下内容，需要同步文档与 `docs/CHANGELOG.md`：

- 注册/登录流程
- API 字段
- 套餐权益
- 配额语义
- 支付/退款/发票流程
- 管理员能力
- 用户通知能力
- 合规/注销/数据导出能力

---

## 推荐阅读顺序

1. `backend/api/v1/endpoints/account.py`
2. `backend/src/users/service.py`
3. `backend/src/users/sessions.py`
4. `backend/src/users/plans.py`
5. `backend/src/users/quota.py`
6. `backend/api/v1/endpoints/billing.py`
7. `backend/src/services/billing/order_service.py`
8. `backend/api/v1/endpoints/admin.py`
9. `backend/src/storage/models/app.py`
