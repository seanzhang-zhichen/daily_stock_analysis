# 后端架构总览

> 本文档基于 `backend/` 目录下的真实代码撰写，涵盖整体模块划分、调用链路、关键设计决策及配置入口。

---

## 目录

1. [整体目录结构](#整体目录结构)
2. [启动入口](#启动入口)
3. [应用工厂与生命周期](#应用工厂与生命周期)
4. [API 层](#api-层)
5. [核心分析管道](#核心分析管道)
6. [Agent 智能体系统](#agent-智能体系统)
7. [数据源层](#数据源层)
8. [存储层](#存储层)
9. [服务层](#服务层)
10. [用户体系](#用户体系)
11. [通知系统](#通知系统)
12. [Bot 接入](#bot-接入)
13. [定时调度](#定时调度)
14. [配置体系](#配置体系)
15. [数据库迁移](#数据库迁移)

---

## 整体目录结构

```
backend/
├── main.py                    # CLI / 调度入口，懒加载 StockAnalysisPipeline
├── server.py                  # 兼容 shim，创建 FastAPI app 供 uvicorn 使用
├── webui.py                   # WebUI 快捷入口
├── requirements.txt           # 后端 Python 依赖
├── alembic/                   # 数据库迁移管理
│   ├── env.py
│   ├── versions/              # 每次 schema 变更的迁移脚本
│   └── script.py.mako
├── api/                       # FastAPI 应用工厂 + 路由注册
│   ├── app.py                 # create_app() 工厂函数
│   ├── deps.py                # FastAPI 依赖注入 (get_current_user 等)
│   ├── middlewares/
│   │   ├── auth.py            # AuthMiddleware（基于 Cookie Session）
│   │   └── error_handler.py   # 全局异常处理
│   └── v1/
│       ├── router.py          # 汇聚所有 /api/v1/* 路由
│       ├── endpoints/         # 16 个业务端点模块
│       └── schemas/           # Pydantic 请求/响应模型
├── bot/                       # IM 机器人接入（飞书、企业微信、Telegram 等）
│   ├── dispatcher.py
│   ├── handler.py
│   └── platforms/             # 各平台适配器
├── data_provider/             # 多数据源策略层
│   ├── base.py                # BaseFetcher + DataFetcherManager
│   ├── akshare_fetcher.py
│   ├── tushare_fetcher.py
│   ├── efinance_fetcher.py
│   ├── yfinance_fetcher.py
│   ├── longbridge_fetcher.py
│   └── ...                    # 其他数据源适配器
├── strategies/                # 自定义交易策略脚本目录
└── src/                       # 核心业务逻辑
    ├── config.py              # 全局配置解析（单例）
    ├── analyzer.py            # LLM 分析器（GeminiAnalyzer / AnalysisResult）
    ├── scheduler.py           # 定时任务调度
    ├── notification.py        # 通知层（多渠道发送）
    ├── search_service.py      # 搜索服务（新闻/情报检索）
    ├── agent/                 # Multi-agent 智能分析系统
    ├── core/                  # 核心流程编排
    │   ├── pipeline.py        # StockAnalysisPipeline（分析主流程）
    │   ├── backtest_engine.py # 回测评估引擎
    │   ├── market_review.py   # 大盘复盘
    │   └── trading_calendar.py
    ├── repositories/          # 数据访问层（Repository 模式）
    ├── schemas/               # 跨模块数据契约
    ├── services/              # 业务服务层
    ├── storage/               # ORM 模型 + DatabaseManager
    └── users/                 # To C 用户体系（认证、配额、套餐）
```

---

## 启动入口

### 方式一：CLI 分析任务

```bash
python backend/main.py                          # 执行当日自选股分析
python backend/main.py --debug                 # 调试模式（更详细日志）
python backend/main.py --dry-run               # 仅拉数据，不调 LLM
python backend/main.py --stocks 600519,hk00700 # 指定股票
python backend/main.py --market-review         # 大盘复盘模式
python backend/main.py --schedule              # 启动定时调度（每日 18:00）
python backend/main.py --serve                 # 启动 API 服务 + 定时任务
python backend/main.py --serve-only            # 仅启动 API 服务
```

`main.py` 对 `StockAnalysisPipeline` 使用懒加载描述符（`_LazyPipelineDescriptor`），保证在 API / bot 路径下不触发不必要的初始化。

### 方式二：直接用 uvicorn

```bash
uvicorn backend.backend.server:app --reload --host 0.0.0.0 --port 8000
```

`server.py` 调用 `api.app.create_app(serve_frontend=False)` 获得不托管前端的纯 API 实例。

### 方式三：托管前端的一体化模式

`main.py --serve` 内部使用 `create_app(serve_frontend=True, static_dir=<build_dir>)`，同时在 uvicorn 内启动；路由优先匹配 `/api/v1/*`，其余路径回退到 SPA `index.html`。

---

## 应用工厂与生命周期

**文件：** `backend/api/app.py`

`create_app(static_dir, serve_frontend)` 是唯一的工厂函数：

1. **初始化可观测性**：`_init_sentry()` + `_init_llm_observability()`（均为可选，未配置时静默跳过）
2. **CORS 配置**：默认放行 localhost:5173/5200/3000；环境变量 `CORS_ORIGINS` 可追加；`CORS_ALLOW_ALL=true` 放行所有来源
3. **注册中间件**：`AuthMiddleware`（Cookie Session 认证）+ 全局错误处理器
4. **挂载路由**：`api_v1_router`（前缀 `/api/v1`）
5. **前端静态托管**（可选）：`/assets/*` 精确匹配 + SPA 路由回退，含资产一致性检查（`_check_frontend_assets_consistency`，防 GitHub #1064 类型空白页问题）

**生命周期（`app_lifespan`）：**

- 启动：实例化 `SystemConfigService` 并存入 `app.state`；调用 `ensure_stock_index_seeded()` 将股票索引从 JSON 资源文件种入数据库（仅在表为空时执行）
- 关闭：清理 `app.state.system_config_service`

---

## API 层

### 路由总表

所有路由挂载在 `/api/v1` 前缀下：

| 前缀 | 模块文件 | 主要职责 |
|------|----------|----------|
| `/auth` | `auth.py` | 单管理员登录（兼容旧版） |
| `/account` | `account.py` | 注册、登录、邮箱验证、密码重置、通知偏好、自选股、账号注销 |
| `/agent` | `agent.py` | Agent 对话（chat / stream）、研究报告、会话管理 |
| `/analysis` | `analysis.py` | 异步分析任务提交、状态查询、SSE 流式进度、历史分析列表 |
| `/history` | `history.py` | 历史分析报告查询、对比、删除 |
| `/stocks` | `stocks.py` | 股票搜索（公开 IP 限速）、行情数据 |
| `/backtest` | `backtest.py` | 回测结果查询、评估触发 |
| `/system` | `system_config.py` | 系统配置读写（仅管理员） |
| `/usage` | `usage.py` | 配额快照查询、增长埋点上报 |
| `/portfolio` | `portfolio.py` | 投资组合账户 CRUD、交易流水、持仓快照 |
| `/alerts` | `alerts.py` | 价格/涨跌幅/量能预警规则 CRUD |
| `/billing` | `billing.py` | 套餐目录、下单、支付回调、退款、发票 |
| `/admin` | `admin.py` | 平台管理员操作（套餐开通、退款审核等） |
| `/notices` | `notices.py` | 平台公告 CRUD（管理员）+ 公开查询 |

另有：
- `GET /api/health` — 健康检查（免认证）
- `GET /` — 根路由（API-only 模式返回服务信息；前端模式返回 index.html）

### 认证中间件

**文件：** `backend/api/middlewares/auth.py`

`AuthMiddleware` 对所有 `/api/v1/*` 路径进行 Cookie Session 认证：

1. 从 Cookie `dsa_user_session` 读取 Token
2. 查数据库 `app_user_sessions` 验证 Token 是否有效（未过期、未吊销）
3. 将解析到的 `AppUser` 对象存入 `request.state.user`
4. 未通过时返回 `401 {"error": "unauthorized"}`

**豁免路径（`EXEMPT_PATHS`）：** 注册、登录、邮箱验证、密码重置、套餐目录、支付回调、股票搜索、公告公开接口、增长埋点、健康检查、Swagger 文档等。

### 依赖注入

**文件：** `backend/api/deps.py`

- `get_current_user` — 从 `request.state.user` 取已认证用户，未认证时抛 `401`
- `get_db_session` — 获取 SQLAlchemy Session，请求结束后自动关闭
- `get_system_config_service` — 从 `app.state` 取 `SystemConfigService` 单例

---

## 核心分析管道

**文件：** `backend/src/core/pipeline.py` — `StockAnalysisPipeline`

分析管道是整个系统的主业务编排层，负责将「一批股票代码」转变为「分析报告 + 通知」。

### 流程总览

```
[股票代码列表]
      │
      ▼
1. 数据拉取（DataFetcherManager）
   ├── 日线 K 线数据
   ├── 基本面数据（PE/PB/ROE 等）
   ├── 筹码分布（ChipDistribution）
   └── 实时行情（当日价格）
      │
      ▼
2. 新闻/情报搜索（SearchService）
   ├── 多维度查询（最新消息、风险排查、财报、行业分析）
   └── 落库 NewsIntel
      │
      ▼
3. LLM 分析（GeminiAnalyzer / AgentOrchestrator）
   ├── 技术分析（均线、量比、筹码位置）
   ├── 基本面分析
   ├── 情绪分析
   └── 综合决策（买入/持有/卖出/观望 + 点位建议）
      │
      ▼
4. 结果持久化（AnalysisHistory）
      │
      ▼
5. 通知推送（NotificationService）
   └── 按配置发送到企业微信/飞书/Telegram/邮件等渠道
```

### 并发控制

- 使用 `ThreadPoolExecutor` 并行分析多只股票
- `max_workers` 默认从 `Config.max_workers` 读取
- 单股失败不影响整体流程（内部 try/except 捕获并记录）

### 异步任务队列

**文件：** `backend/src/services/task_queue.py`

Web API 调用路径下，分析任务不阻塞 HTTP 请求：

1. `POST /api/v1/analysis/tasks` 提交任务 → 进入内存队列（`AnalysisTaskQueue`）
2. `ThreadPoolExecutor` 后台执行 `StockAnalysisPipeline.run_single()`
3. 通过 SSE（Server-Sent Events）向客户端推送进度：`GET /api/v1/analysis/tasks/stream`
4. 完成后落库 `AnalysisHistory`，客户端可查询最终状态

**防重复提交：** 同一用户（`user_id`）对同一股票代码（规范化后）只能有一个 PENDING/PROCESSING 任务。

---

## Agent 智能体系统

**目录：** `backend/src/agent/`

系统支持两种分析后端，通过环境变量 `AGENT_ARCH` 切换（工厂函数在 `factory.py`）：

### AgentOrchestrator（多智能体流水线）

**文件：** `backend/src/agent/orchestrator.py`

按深度分为四种模式（`AGENT_ARCH=orchestrator`）：

| 模式 | 调用的 Agents | 说明 |
|------|---------------|------|
| `quick` | Technical → Decision | 最快，~2 次 LLM 调用 |
| `standard` | Technical → Intel → Decision | 默认 |
| `full` | Technical → Intel → Risk → Decision | 完整 |
| `specialist` | Technical → Intel → Risk → Specialist → Decision | 最深入 |

**Agents 定义（`src/agent/agents/`）：**

- `TechnicalAgent` — 技术指标分析（均线、量比、支撑压力）
- `IntelAgent` — 情报/新闻分析
- `RiskAgent` — 风险评估
- `DecisionAgent` — 综合决策，输出标准化 Dashboard JSON
- `PortfolioAgent` — 投资组合相关分析

**工具集（`src/agent/tools/`）：**

| 文件 | 工具能力 |
|------|----------|
| `data_tools.py` | 拉取日线、实时行情、基本面 |
| `analysis_tools.py` | 技术分析指标计算 |
| `search_tools.py` | 新闻/情报检索 |
| `backtest_tools.py` | 历史回测查询 |
| `market_tools.py` | 大盘指数 |

### AgentExecutor（单 Agent ReAct 循环）

**文件：** `backend/src/agent/executor.py`

ReAct 循环（Reasoning + Acting），最多 `AGENT_MAX_STEPS_DEFAULT`（默认 10）步，每步选择工具调用或直接输出。

### 对话管理

**文件：** `backend/src/agent/conversation.py`、`backend/src/agent/memory.py`

- 会话记录存入 `ConversationMessage` 表
- 支持多轮对话上下文（含历史消息裁剪）
- `/api/v1/agent/chat` (非流式) 和 `/api/v1/agent/chat/stream` (SSE 流式)

---

## 数据源层

**目录：** `backend/data_provider/`

### 多数据源策略

`DataFetcherManager` 实现了优先级驱动的自动 fallback 策略：

| 优先级 | 数据源 | 说明 |
|--------|--------|------|
| 0（配置了 Token 时） | `TushareFetcher` | Tushare Pro，数据质量高 |
| 0 | `EfinanceFetcher` | efinance 库，A 股免费 |
| 1 | `AkshareFetcher` | akshare 库，A 股/港股/美股 |
| 2 | `PytdxFetcher` | 通达信协议，A 股 |
| 3 | `BaostockFetcher` | baostock 库 |
| 4 | `YfinanceFetcher` | yfinance，主要用于美股/港股 |
| 5 | `LongbridgeFetcher` | 长桥 OpenAPI，美股/港股兜底 |

辅助数据源（不在主 fallback 链，按需调用）：
- `FinnhubFetcher` — 美股基本面
- `AlphaVantageFetcher` — 美股额外数据

### 股票代码规范化

- `normalize_stock_code(code)` — 统一格式（如 `600519` → `600519.SH`）
- `canonical_stock_code(code)` — 去除交易所后缀，回归纯代码
- `is_hk_stock_code(code)` / `is_us_stock_code(code)` — 市场识别
- A 股代码：纯数字或带 `.SH`/`.SZ`/`.BJ`
- 港股代码：`hk` 前缀或 5 位数字
- 美股代码：字母组合

### 数据类型

`BaseFetcher` 定义了统一接口，子类实现：
- `get_daily_data(code, start_date, end_date)` → `pd.DataFrame`（OHLCV + 均线）
- `get_fundamental_data(code)` → Dict（PE、PB、ROE、营收等）
- `get_realtime_quote(code)` → `RealtimeQuote`
- `get_chip_distribution(code)` → `ChipDistribution`（筹码分布）

---

## 存储层

**目录：** `backend/src/storage/`

### DatabaseManager

**文件：** `backend/src/storage/manager/`

单例模式，封装 SQLAlchemy Engine + Session 工厂：
- 默认使用 SQLite（路径由 `DB_PATH` 环境变量或配置决定）
- 支持 PostgreSQL（通过 `DATABASE_URL` 环境变量）
- `get_db()` — 获取 Session 上下文管理器（供 FastAPI 依赖注入使用）

### ORM 模型总览

#### 核心业务表（`models/core.py`）

| 表名 | 模型类 | 用途 |
|------|--------|------|
| `stock_daily` | `StockDaily` | 日线 OHLCV + 均线（MA5/10/20）+ 量比 |
| `news_intel` | `NewsIntel` | 检索到的新闻/情报条目 |
| `fundamental_snapshot` | `FundamentalSnapshot` | 基本面快照（write-only，供回测/画像扩展） |
| `analysis_history` | `AnalysisHistory` | 每次分析结果（含 LLM 结论、点位建议、原始报文） |
| `stock_index` | `StockIndexEntry` | 股票搜索索引（代码、名称、拼音） |
| `stock_index_meta` | `StockIndexMeta` | 股票索引元数据（版本、总条数） |

#### 回测表（`models/backtest.py`）

| 表名 | 说明 |
|------|------|
| `backtest_results` | 单次回测结果（含 eval_status、方向判断、收益率） |
| `backtest_summaries` | 批量回测汇总统计 |

#### 投资组合表（`models/portfolio.py`）

| 表名 | 说明 |
|------|------|
| `portfolio_accounts` | 投资账户（名称、市场、成本方法 fifo/avg） |
| `portfolio_trades` | 交易流水（买/卖 + 数量 + 价格） |
| `portfolio_positions` | 持仓快照 |
| `portfolio_position_lots` | 持仓批次（FIFO 用） |
| `portfolio_cash_ledger` | 现金流水 |
| `portfolio_corporate_actions` | 分红/拆股事件 |
| `portfolio_daily_snapshots` | 每日净值快照 |
| `portfolio_fx_rates` | 汇率缓存 |

#### 预警表（`models/alert.py`）

| 表名 | 说明 |
|------|------|
| `alert_rule_records` | 预警规则（价格穿越/涨跌幅/量能） |
| `alert_trigger_records` | 触发记录 |
| `alert_notification_records` | 通知发送记录 |

#### 会话与 LLM 表（`models/conversation.py`）

| 表名 | 说明 |
|------|------|
| `conversation_messages` | Agent 对话历史 |
| `llm_usage` | LLM token 使用量记录 |

#### To C 用户体系表（`models/app.py`）

详见 [用户体系](#用户体系)。

---

## 服务层

**目录：** `backend/src/services/`

| 服务文件 | 类/函数 | 职责 |
|----------|---------|------|
| `system_config_service.py` | `SystemConfigService` | `.env` 配置读写、LLM 连通性测试、通知诊断 |
| `task_queue.py` | `AnalysisTaskQueue` | 异步分析任务队列 + SSE 广播 |
| `task_service.py` | `TaskService` | 任务 CRUD 封装（供 endpoint 调用） |
| `history_service.py` | `HistoryService` | 分析历史查询、分页、详情 |
| `alert_service.py` | `AlertService` | 预警规则 CRUD + dry-run 评估 |
| `alert_worker.py` | `AlertWorker` | 后台实时价格轮询、触发预警、发送通知 |
| `portfolio_service.py` | `PortfolioService` | 投资组合 CRUD + 持仓计算 + PnL |
| `portfolio_risk_service.py` | `PortfolioRiskService` | 持仓风险评估 |
| `backtest_service.py` | `BacktestService` | 回测评估执行、统计汇总 |
| `stock_service.py` | `StockService` | 股票行情数据 API 封装 |
| `analysis_service.py` | `AnalysisService` | 分析结果归档 |
| `billing/order_service.py` | `OrderService` | 订单创建、关单、支付完成开通套餐 |
| `billing/security.py` | 签名验证 | 微信/支付宝回调签名校验 |
| `image_stock_extractor.py` | `ImageStockExtractor` | 截图识别股票代码（LLM Vision） |
| `social_sentiment_service.py` | `SocialSentimentService` | 社交媒体情绪数据聚合 |
| `name_to_code_resolver.py` | `NameToCodeResolver` | 股票名称 → 代码解析 |

### SystemConfigService

是系统最重要的运维服务，提供：
- 读取当前 `.env` 配置并以结构化 Schema 返回（含字段分类、类型、校验规则）
- 批量写入配置字段到 `.env` 文件（含版本号冲突检测防并发写坏）
- LLM 连通性测试（`test_llm_connection`）
- 通知渠道诊断（`run_notification_diagnostics`）
- 配置导入/导出（`.env` 格式）

---

## 用户体系

**目录：** `backend/src/users/`

系统内建 To C 多用户体系，所有用户数据与系统管理员账户解耦。

### 认证流程

```
注册（/account/register）
  ├── 邮箱格式验证 + 可选 MX 记录检查
  ├── 注册频率限制（IP + 邮箱维度）
  ├── 写入 AppUser（status=active）
  ├── 写入 AppUserConsent（协议同意记录）
  └── 发送邮箱验证邮件（AppUserEmailVerification）

登录（/account/login）
  ├── 校验 email + password（pbkdf2_sha256）
  ├── 创建 AppUserSession（token_hash=sha256(随机 token)）
  └── Set-Cookie: dsa_user_session=<token>; HttpOnly; SameSite=Lax

请求认证（AuthMiddleware）
  └── 读 Cookie → 查 app_user_sessions → 写 request.state.user
```

**关键模块：**
- `backend/src/users/service.py` — 注册、登录、修改密码
- `backend/src/users/sessions.py` — Session 创建、解析、吊销
- `backend/src/users/passwords.py` — pbkdf2_sha256 哈希与验证
- `backend/src/users/registration_guard.py` — 注册频率限制、一次性邮箱检测
- `backend/src/users/email.py` — 邮件验证、密码重置邮件发送

### 套餐体系

**文件：** `backend/src/users/plans.py`

| 档位 | 每日分析次数 | 每日 Agent 次数 | 自选股上限 |
|------|-----------------|---------------------|------------|
| free | 由 `AppPlan` 表配置 | 同上 | 同上 |
| pro/pro_yearly | 由 `AppPlan` 表配置 | 同上 | 同上 |

`resolve_user_plan(db, user)` — 核心函数，读取 `AppUser.plan_code + plan_expires_at`，套餐过期自动降级为 free（内存态，不写库）。

### 配额机制

**文件：** `backend/src/users/quota.py`

三种计费类型（`kind`）：`analysis`、`agent`、`notify`

- `try_consume()` — 原子扣减，用量满则返回 `False`
- `refund()` — 业务失败时回补（如异步任务后台失败）
- `get_quota_snapshot()` — 一次性返回 analysis + agent 当日已用/上限，供前端顶栏渲染

### 计费与支付

**目录：** `backend/src/services/billing/`

订单状态机：
```
created → pending → paid → refunded / partial_refunded
                 └→ failed
created → closed（超时或用户取消）
```

- 支持微信支付、支付宝、手动开通
- 回调签名验证（`billing/security.py`）
- 支付成功后调用 `grant_plan()` 开通订阅

### 合规与审计

- `AppUserConsent` — 用户协议同意历史（PIPL 合规）
- `AppAuditLog` — 关键操作审计日志（永不删除）
- `AppUser.deletion_requested_at` — 账号注销冷静期字段

---

## 通知系统

**文件：** `backend/src/notification.py`

`NotificationService` 聚合多渠道推送。

### 支持的推送渠道

| 渠道 | 类 | 配置字段 |
|------|-----|---------|
| 企业微信 Webhook | `WechatSender` | `WECHAT_WEBHOOK_URL` |
| 飞书 Webhook | `FeishuSender` | `FEISHU_WEBHOOK_URL` |
| Telegram Bot | `TelegramSender` | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` |
| 邮件 SMTP | `EmailSender` | `EMAIL_*` 系列 |
| Pushover | `PushoverSender` | `PUSHOVER_USER_KEY` + `PUSHOVER_API_TOKEN` |
| ntfy | `NtfySender` | `NTFY_URL` |
| Gotify | `GotifySender` | `GOTIFY_URL` + `GOTIFY_TOKEN` |
| PushPlus | `PushplusSender` | `PUSHPLUS_TOKEN` |
| Server酱3 | `Serverchan3Sender` | `SERVERCHAN3_TOKEN` |
| Discord Bot | `DiscordSender` | `DISCORD_BOT_TOKEN` + `DISCORD_CHANNEL_ID` |
| Slack | `SlackSender` | `SLACK_WEBHOOK_URL` |
| AstrBot | `AstrbotSender` | `ASTRBOT_*` |
| 自定义 Webhook | `CustomWebhookSender` | `CUSTOM_WEBHOOK_URL` |

### 通知路由

**文件：** `backend/src/notification_routing.py`

通过 `NOTIFICATION_CHANNELS` 环境变量控制路由策略，支持按 `report_type` 分发到不同渠道。

### 噪音控制

**文件：** `backend/src/notification_noise.py`

- 静默时段（`NOTIFICATION_QUIET_HOURS`）：指定时间段内不发送通知
- 严重度过滤（`NOTIFICATION_MIN_SEVERITY`）：只推送达到阈值的信号
- 重复抑制：同一信号短时间内不重复推送

---

## Bot 接入

**目录：** `backend/bot/`

支持多平台 IM 机器人，接收用户指令触发股票分析：

- **飞书（Lark）**
- **企业微信**
- **Telegram**
- **DingTalk（钉钉）**

Bot 请求通过 Webhook 回调到 `/api/v1/bot/*` 端点，经 `BotHandler` 分发到 `BotDispatcher`，最终调用 `StockAnalysisPipeline.run_single()`。

结果通过平台 API 或 `NotificationService` 回复给用户。

---

## 定时调度

**文件：** `backend/src/scheduler.py`

基于 `schedule` 库，支持：

- 每日指定时间执行自选股分析（默认 18:00）
- 每日大盘复盘（可选）
- `GracefulShutdown` 捕获 SIGTERM/SIGINT，等待当前任务完成后退出
- 时间可在运行期通过 `SystemConfigService` 动态修改（`SCHEDULE_TIME` 环境变量）

To C 模式下，调度器还会对所有 Pro 用户执行每日推送分析（`daily_push_enabled=true`）。

---

## 配置体系

**文件：** `backend/src/config.py`

`Config` 对象通过 `get_config()` 获取全局单例，从 `.env` 文件和环境变量加载。

**核心配置分类：**

| 类别 | 关键环境变量 | 说明 |
|------|-------------|------|
| LLM | `LLM_MODEL`, `OPENAI_API_KEY`, `GEMINI_API_KEY` | LLM 提供商和模型选择 |
| 数据库 | `DATABASE_URL`, `DB_PATH` | 数据库连接字符串 |
| 股票列表 | `STOCK_CODES` | 逗号分隔的自选股代码 |
| 调度 | `SCHEDULE_TIME` | 每日执行时间（HH:MM） |
| 通知 | `WECHAT_WEBHOOK_URL`, `EMAIL_*` 等 | 各渠道配置 |
| 数据源 | `TUSHARE_TOKEN`, `LONGBRIDGE_*` | 数据源 Token |
| To C | `USER_*` 系列 | 用户注册、配额、会话设置 |
| 代理 | `USE_PROXY`, `PROXY_HOST`, `PROXY_PORT` | 本地开发代理 |
| 可观测 | `SENTRY_DSN`, `LANGFUSE_*` | 错误监控和 LLM 可观测 |

**配置 Schema 注册：** `backend/src/core/config_registry.py` 中注册了所有前端可编辑的配置字段（含类型、校验规则、分类、帮助文本），`SystemConfigService` 基于此 Schema 提供结构化配置 API。

---

## 数据库迁移

所有 schema 变更必须通过 Alembic 完成，**禁止**直接调用 `Base.metadata.create_all()` 或手写 `ALTER TABLE`。

```bash
# 生成新的迁移脚本
alembic revision --autogenerate -m "描述"

# 应用迁移
alembic upgrade head

# 查看当前版本
alembic current
```

**已有迁移版本：**

| 文件 | 内容 |
|------|------|
| `20260519_b0bc3c721ef0_initial_schema.py` | 初始全量 Schema（含所有 To C 表） |
| `20260522_add_user_preferred_model.py` | AppUser 新增 `preferred_model` 字段 |
| `20260523_add_stock_index_tables.py` | 新增 `stock_index` / `stock_index_meta` 表 |
| `20260527_widen_eval_status.py` | 回测 `eval_status` 字段扩展 |

---

## 相关文档

- [API 层详细说明](./api.md)
- [数据管道与分析流程](./data-pipeline.md)
- [存储层与数据模型](./storage.md)
- [用户体系与计费](./user-system.md)
- [本地开发指引](../local-dev.md)
- [部署说明](../DEPLOY.md)
- [LLM 配置指引](../LLM_CONFIG_GUIDE.md)
