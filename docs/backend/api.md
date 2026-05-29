# API 层详细说明

> 基于 `backend/api/` 目录下的真实代码。所有接口统一挂载在 `/api/v1` 前缀下。

---

## 目录

1. [认证与会话](#认证与会话)
2. [依赖注入](#依赖注入)
3. [接口分组详情](#接口分组详情)
   - [Account — 账号管理](#account--账号管理)
   - [Auth — 旧版管理员认证](#auth--旧版管理员认证)
   - [Analysis — 股票分析任务](#analysis--股票分析任务)
   - [Agent — AI 对话与研究](#agent--ai-对话与研究)
   - [History — 分析历史](#history--分析历史)
   - [Stocks — 股票数据](#stocks--股票数据)
   - [Backtest — 回测](#backtest--回测)
   - [Portfolio — 投资组合](#portfolio--投资组合)
   - [Alerts — 价格预警](#alerts--价格预警)
   - [Billing — 计费与支付](#billing--计费与支付)
   - [Admin — 平台管理员](#admin--平台管理员)
   - [Notices — 平台公告](#notices--平台公告)
   - [System — 系统配置](#system--系统配置)
   - [Usage — 使用量](#usage--使用量)
4. [通用响应格式](#通用响应格式)
5. [错误码](#错误码)

---

## 认证与会话

系统使用 **Cookie-based Session** 认证。

### 认证流程

```
POST /api/v1/account/login
→ 后端创建 AppUserSession，生成随机 Token
→ 存储 sha256(token) 到 app_user_sessions 表
→ Set-Cookie: dsa_user_session=<token>; HttpOnly; Path=/; SameSite=Lax
```

后续请求携带该 Cookie，`AuthMiddleware` 自动验证并将用户对象注入 `request.state.user`。

### 豁免路径（无需认证）

以下路径不需要登录即可访问：

```
/api/v1/account/register
/api/v1/account/login
/api/v1/account/logout
/api/v1/account/status
/api/v1/account/verify-email
/api/v1/account/request-password-reset
/api/v1/account/reset-password
/api/v1/account/notification-prefs/unsubscribe
/api/v1/auth/login          # 旧版管理员登录
/api/v1/auth/status
/api/v1/billing/plans       # 套餐目录（落地页展示）
/api/v1/billing/callbacks/wechat   # 支付回调
/api/v1/billing/callbacks/alipay
/api/v1/stocks/search       # 股票搜索（IP 限速）
/api/v1/usage/events        # 增长埋点（可匿名）
/api/v1/notices             # 公告列表（公开）
/api/v1/notices/unread-count
/api/health
/docs  /redoc  /openapi.json
```

### 管理员权限

部分接口需要 `AppUser.is_admin = true`，通过 `get_admin_user` 依赖注入检查。非管理员访问返回 `403 forbidden`。

---

## 依赖注入

**文件：** `backend/api/deps.py`

| 依赖函数 | 返回类型 | 说明 |
|---------|---------|------|
| `get_db()` | `Session` | SQLAlchemy Session，请求结束自动关闭 |
| `get_config_dep()` | `Config` | 全局配置单例 |
| `get_current_user(request)` | `AppUser` | 当前登录用户，未登录抛 `401` |
| `get_optional_current_user(request)` | `AppUser \| None` | 当前登录用户，未登录返回 `None` |
| `get_admin_user(request)` | `AppUser` | 要求已登录且 `is_admin=True` |
| `get_system_config_service(request)` | `SystemConfigService` | app 生命周期内的配置服务单例 |
| `get_database_manager()` | `DatabaseManager` | 数据库管理器单例 |

---

## 接口分组详情

### Account — 账号管理

**前缀：** `/api/v1/account`

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| POST | `/register` | 无 | 注册新用户，需同意协议（`termsAgreed=true`） |
| POST | `/login` | 无 | 密码登录，成功后 Set-Cookie |
| POST | `/logout` | 无 | 清除 Cookie，吊销 Session |
| GET | `/status` | 无 | 检查当前登录状态 + 配额快照 |
| POST | `/verify-email` | 无 | 消费邮箱验证 Token，激活邮箱 |
| POST | `/request-password-reset` | 无 | 发送密码重置邮件 |
| POST | `/reset-password` | 无 | 消费重置 Token，设置新密码 |
| POST | `/change-password` | 需登录 | 修改密码（需提供旧密码） |
| GET | `/me` | 需登录 | 当前用户信息 |
| PATCH | `/me/preferred-model` | 需登录 | 修改偏好 LLM 模型 |
| GET | `/watchlist` | 需登录 | 查看自选股列表 |
| POST | `/watchlist` | 需登录 | 添加自选股 |
| DELETE | `/watchlist/{stock_code}` | 需登录 | 删除自选股 |
| PUT | `/watchlist` | 需登录 | 批量覆盖自选股 |
| GET | `/notification-prefs` | 需登录 | 读取通知偏好 |
| PUT | `/notification-prefs` | 需登录 | 更新通知偏好 |
| GET | `/notification-prefs/unsubscribe` | 无 | 邮件一键退订（Token 认证） |
| POST | `/redeem` | 需登录 | 兑换码升级套餐 |
| POST | `/request-deletion` | 需登录 | 申请注销账号（进入冷静期） |
| POST | `/cancel-deletion` | 需登录 | 取消注销申请 |
| POST | `/data-export` | 需登录 | 申请导出个人数据 |
| POST | `/consents` | 需登录 | 重新接受协议（协议升版时调用） |

**注册请求体示例：**
```json
{
  "email": "user@example.com",
  "password": "Password123!",
  "passwordConfirm": "Password123!",
  "inviteCode": "INVITE_CODE",
  "termsAgreed": true,
  "termsVersion": "2026-01"
}
```

**登录成功响应示例：**
```json
{
  "success": true,
  "user": {
    "id": 1,
    "email": "user@example.com",
    "plan_code": "free",
    "is_admin": false
  }
}
```

---

### Auth — 旧版管理员认证

**前缀：** `/api/v1/auth`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/login` | 单管理员密码登录（兼容旧版 WebUI） |
| POST | `/logout` | 登出 |
| GET | `/status` | 检查认证状态 |

---

### Analysis — 股票分析任务

**前缀：** `/api/v1/analysis`

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| POST | `/tasks` | 需登录 | 提交单只股票异步分析任务 |
| POST | `/tasks/batch` | 需登录 | 批量提交分析任务 |
| GET | `/tasks` | 需登录 | 查看当前用户的任务列表 |
| GET | `/tasks/stream` | 需登录 | SSE 流式接收任务进度事件 |
| GET | `/status/{task_id}` | 需登录 | 查询单个任务状态 |
| POST | `/market-review` | 需登录 | 触发大盘复盘任务 |
| GET | `/history/{query_id}` | 需登录 | 根据 query_id 获取详细报告 |

**提交分析任务请求体：**
```json
{
  "stock_code": "600519",
  "report_type": "detailed",
  "skills": ["technical", "news", "fundamental"]
}
```

**任务状态说明：**

| 状态 | 含义 |
|------|------|
| `pending` | 等待执行 |
| `processing` | 分析中（有进度百分比） |
| `completed` | 完成（含分析结果） |
| `failed` | 失败（含错误信息） |

**SSE 事件格式：**
```
data: {"task_id": "xxx", "status": "processing", "progress": 60, "message": "正在调用 LLM 分析..."}
```

**配额逻辑：**
- 提交时调用 `enforce_quota(KIND_ANALYSIS)` 扣减 1 次
- 后台任务失败时通过 `refund_quota(KIND_ANALYSIS)` 回补
- 超额返回 `429 {"error": "quota_exceeded"}`

---

### Agent — AI 对话与研究

**前缀：** `/api/v1/agent`

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| POST | `/chat` | 需登录 | 单轮对话（非流式，消耗 agent 配额） |
| POST | `/chat/stream` | 需登录 | 流式对话（SSE，消耗 agent 配额） |
| POST | `/research` | 需登录 | 深度研究报告（非流式，消耗 agent 配额） |
| POST | `/chat/send` | 需登录 | 发送消息并推送通知 |
| GET | `/sessions` | 需登录 | 列出当前用户的对话会话 |
| GET | `/sessions/{session_id}` | 需登录 | 获取会话消息历史 |
| DELETE | `/sessions/{session_id}` | 需登录 | 删除会话 |
| GET | `/models` | 需登录 | 可用的 Agent LLM 模型列表 |

**会话 ID 隔离：** 登录用户的 `session_id` 会被加上 `u{user_id}:` 前缀，确保不同用户的对话严格隔离。

**对话请求体示例：**
```json
{
  "message": "分析一下 600519 近期走势",
  "session_id": "my-session-001",
  "model": "gpt-4o-mini",
  "stock_code": "600519"
}
```

---

### History — 分析历史

**前缀：** `/api/v1/history`

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/` | 需登录 | 分页查询历史分析记录（支持按股票代码、日期筛选） |
| GET | `/{id}` | 需登录 | 获取单条历史分析详情 |
| DELETE | `/{id}` | 需登录 | 删除历史分析记录 |
| GET | `/compare` | 需登录 | 横向对比多条分析结果 |

历史详情响应的 `details.price_history` 返回最近已保存的日线行情，包含 `date`、`open`、`high`、`low`、`close`、`volume`、`amount`、`pct_chg`、`ma5`、`ma10`、`ma20`、`volume_ratio` 和 `data_source`，供前端在分析报告中展示历史股价走势。

当 Deep Research 能力可用时，个股分析会在正式报告生成前阻塞生成 `details.stock_profile`，并把该结果作为报告中股票基本情况的权威来源注入 LLM / Agent 上下文。正式报告完成后，后端会继续以 Deep Research 结果回写 `raw_result.stock_profile`，避免被报告模型自行生成的 `stock_profile` 覆盖。Deep Research 会由模型在 `AGENT_DEEP_RESEARCH_MAX_SUB_QUESTIONS` 上限内规划子问题，覆盖充分时可提前收敛；每个子问题的工具循环上限由 `AGENT_DEEP_RESEARCH_SUB_QUESTION_STEPS` 控制，并继续受 token budget 和 timeout 约束。直接调用 `/api/v1/agent/research` 仍按 Agent 配额单独计费。

```json
{
  "stock_profile": {
    "research_report": "Markdown 格式的股票基本情况概览",
    "research_method": "deep_research",
    "research_sources": ["研究子问题 1", "研究子问题 2"],
    "research_token_usage": 1234
  }
}
```

该字段存放在历史记录的 `raw_result` 中，不引入数据库 schema 变更；旧历史记录没有该字段时前端隐藏对应内容块。

---

### Stocks — 股票数据

**前缀：** `/api/v1/stocks`

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/search` | **无（IP 限速）** | 股票搜索（代码/名称/拼音） |
| GET | `/{code}/quote` | 需登录 | 实时行情 |
| GET | `/{code}/daily` | 需登录 | 历史日线数据 |
| GET | `/{code}/fundamental` | 需登录 | 基本面数据 |
| POST | `/extract` | 需登录 | 从图片/截图中识别股票代码（LLM Vision） |
| POST | `/import` | 需登录 | 批量导入股票代码 |

**搜索接口（`/stocks/search`）** 为公开 IP 限速接口，前端股票搜索框使用此接口。返回结果包含 `canonicalCode`、`displayCode`、`nameZh`、`market`。

---

### Backtest — 回测

**前缀：** `/api/v1/backtest`

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/results` | 需登录 | 分页查询回测结果 |
| GET | `/results/{id}` | 需登录 | 单条回测详情 |
| POST | `/evaluate` | 需登录 | 手动触发评估指定分析的回测结果 |
| GET | `/summary` | 需登录 | 回测统计汇总（胜率、平均收益等） |

**回测评估逻辑：** 在分析后 N 个交易日（`eval_window_days`，默认 20）后，对比分析时的点位建议与实际走势，计算方向准确率、收益率等指标。

---

### Portfolio — 投资组合

**前缀：** `/api/v1/portfolio`

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/accounts` | 需登录 | 账户列表 |
| POST | `/accounts` | 需登录 | 创建账户 |
| GET | `/accounts/{id}` | 需登录 | 账户详情（含持仓、现金） |
| DELETE | `/accounts/{id}` | 需登录 | 删除账户 |
| POST | `/accounts/{id}/trades` | 需登录 | 录入交易流水 |
| GET | `/accounts/{id}/trades` | 需登录 | 交易历史 |
| GET | `/accounts/{id}/positions` | 需登录 | 当前持仓 |
| GET | `/accounts/{id}/snapshots` | 需登录 | 每日净值快照 |
| POST | `/accounts/{id}/cash` | 需登录 | 出入金记录 |
| POST | `/accounts/{id}/corporate-actions` | 需登录 | 录入分红/拆股 |
| POST | `/import` | 需登录 | 批量导入交易记录 |

成本计算方法支持 `fifo`（先进先出）和 `avg`（加权平均），创建账户时指定。

---

### Alerts — 价格预警

**前缀：** `/api/v1/alerts`

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/rules` | 需登录 | 查询预警规则列表 |
| POST | `/rules` | 需登录 | 创建预警规则 |
| GET | `/rules/{id}` | 需登录 | 单条规则详情 |
| PATCH | `/rules/{id}` | 需登录 | 更新规则 |
| DELETE | `/rules/{id}` | 需登录 | 删除规则 |
| POST | `/rules/{id}/dry-run` | 需登录 | 用当前价格 dry-run 评估规则 |
| GET | `/triggers` | 需登录 | 预警触发记录 |

**支持的预警类型：**

| 类型 | 说明 |
|------|------|
| `price_cross` | 价格穿越指定价位（上穿/下穿） |
| `price_change_percent` | 涨跌幅超过阈值 |
| `volume_spike` | 成交量突增（量比超阈值） |

---

### Billing — 计费与支付

**前缀：** `/api/v1/billing`

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/plans` | **无** | 套餐目录（落地页展示） |
| GET | `/subscription` | 需登录 | 当前订阅状态 |
| POST | `/orders` | 需登录 | 创建订单 |
| GET | `/orders/{order_no}` | 需登录 | 查询订单状态 |
| POST | `/orders/{order_no}/pay` | 需登录 | 发起支付（返回支付 URL/二维码） |
| POST | `/orders/{order_no}/cancel` | 需登录 | 取消订单 |
| POST | `/callbacks/wechat` | **无（IP 白名单）** | 微信支付回调 |
| POST | `/callbacks/alipay` | **无（签名验证）** | 支付宝回调 |
| POST | `/refunds` | 需登录 | 申请退款 |
| GET | `/refunds/{refund_no}` | 需登录 | 查询退款状态 |
| POST | `/invoices` | 需登录 | 申请开票 |
| GET | `/invoices` | 需登录 | 我的发票列表 |

**注意：** `PAYMENT_ENABLED=false`（默认）时，`/orders/{order_no}/pay` 返回 `503` 并提示联系人工充值。

---

### Admin — 平台管理员

**前缀：** `/api/v1/admin`

**权限要求：** `AppUser.is_admin = true`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/users` | 用户列表（分页、搜索） |
| GET | `/users/{id}` | 用户详情 |
| POST | `/users/{id}/grant-plan` | 手动开通/续期套餐 |
| POST | `/users/{id}/disable` | 禁用账号 |
| POST | `/users/{id}/enable` | 启用账号 |
| GET | `/refunds` | 退款申请列表 |
| POST | `/refunds/{refund_no}/approve` | 审核通过退款 |
| POST | `/refunds/{refund_no}/reject` | 驳回退款 |
| GET | `/invoices` | 发票申请列表 |
| POST | `/invoices/{invoice_no}/issue` | 标记已开票 |
| POST | `/invoices/{invoice_no}/reject` | 驳回发票申请 |
| POST | `/notices` | 创建平台公告 |
| PATCH | `/notices/{id}` | 修改公告 |
| DELETE | `/notices/{id}` | 删除公告 |
| GET | `/growth-events` | 增长埋点事件列表 |
| GET | `/audit-logs` | 操作审计日志 |
| GET | `/redeem-codes` | 兑换码列表 |
| POST | `/redeem-codes` | 批量生成兑换码 |
| GET | `/reconciliation` | 对账报告 |

---

### Notices — 平台公告

**前缀：** `/api/v1/notices`

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/` | **无** | 查看已发布的公告列表（支持 `target_plan` 过滤） |
| GET | `/unread-count` | **无** | 未读公告数量（用于顶栏红点） |
| POST | `/{id}/read` | 需登录 | 标记公告已读 |

---

### System — 系统配置

**前缀：** `/api/v1/system`

**权限要求：** `AppUser.is_admin = true`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/config` | 获取当前系统配置（按分类，含校验状态） |
| PATCH | `/config` | 批量更新配置字段（写入 .env） |
| GET | `/config/schema` | 获取配置字段 Schema（字段类型、枚举、分类等） |
| POST | `/config/test-llm` | 测试 LLM 连通性 |
| POST | `/config/test-notification` | 测试通知渠道 |
| POST | `/config/diagnose` | 完整通知诊断 |
| GET | `/config/export` | 导出配置为 .env 格式 |
| POST | `/config/import` | 导入 .env 配置文件 |
| GET | `/setup-status` | 是否完成初始配置（首次使用引导） |

---

### Usage — 使用量

**前缀：** `/api/v1/usage`

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/quota` | 需登录 | 当日配额使用快照（analysis + agent） |
| POST | `/events` | **无** | 上报增长埋点事件（支持匿名） |
| GET | `/stats` | 需登录（管理员） | 全平台用量统计 |

---

## 通用响应格式

### 成功响应

不同接口各自定义 Pydantic 响应模型，通常包含业务数据字段。

### 错误响应

```json
{
  "error": "error_code",
  "message": "人类可读的错误描述"
}
```

常见 HTTP 状态码：

| 状态码 | 含义 |
|--------|------|
| 200 | 成功 |
| 201 | 创建成功 |
| 400 | 请求参数错误 |
| 401 | 未认证（需登录） |
| 403 | 无权限（非管理员访问管理接口） |
| 404 | 资源不存在 |
| 409 | 冲突（如重复提交分析任务） |
| 422 | 参数校验失败（Pydantic） |
| 429 | 配额超限 |
| 500 | 服务内部错误 |
| 503 | 功能未启用（如支付未开放） |

---

## 错误码

业务错误 `error` 字段常见值：

| 错误码 | 说明 |
|--------|------|
| `unauthorized` | 未登录或 Session 失效 |
| `forbidden` | 无操作权限 |
| `quota_exceeded` | 每日使用次数超出套餐上限 |
| `duplicate_task` | 相同股票已有进行中的任务 |
| `validation_error` | 输入参数校验失败 |
| `invite_code_required` | 注册需要邀请码但未提供 |
| `invite_code_invalid` | 邀请码/兑换码无效 |
| `token_expired` | 验证 Token 已过期 |
| `email_already_exists` | 邮箱已被注册 |
| `rate_limited` | 触发频率限制 |
| `not_found` | 资源不存在 |
| `unsupported_alert_type` | 不支持的预警类型 |
| `payment_disabled` | 支付功能未启用 |
