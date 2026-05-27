# 存储层与数据模型

> 本文档基于 `backend/src/storage/`、`backend/src/repositories/` 和 `backend/alembic/` 当前代码，解释数据库连接、ORM 模型、Repository 模式和迁移规则。

---

## 目录

1. [存储层定位](#存储层定位)
2. [目录结构](#目录结构)
3. [DatabaseManager](#databasemanager)
4. [Session 生命周期](#session-生命周期)
5. [ORM 模型分组](#orm-模型分组)
6. [核心业务表](#核心业务表)
7. [用户体系表](#用户体系表)
8. [投资组合表](#投资组合表)
9. [回测表](#回测表)
10. [预警表](#预警表)
11. [会话与 LLM 用量表](#会话与-llm-用量表)
12. [Repository 层](#repository-层)
13. [Alembic 迁移](#alembic-迁移)
14. [数据隔离与安全边界](#数据隔离与安全边界)
15. [开发建议](#开发建议)

---

## 存储层定位

后端存储层负责：

- 管理数据库连接和 Session
- 定义 SQLAlchemy ORM 模型
- 提供部分历史兼容的数据读写方法
- 支撑 Repository 层和 Service 层的数据访问
- 通过 Alembic 迁移维护 schema 演进

当前项目同时支持：

- **SQLite**：默认本地开发/轻量部署
- **PostgreSQL**：通过 `DATABASE_URL` 配置，用于生产部署

---

## 目录结构

```text
backend/src/storage/
├── __init__.py                  # 统一 re-export ORM 模型与 DatabaseManager
├── base.py                      # SQLAlchemy Base
├── models/
│   ├── __init__.py              # 汇总所有模型导出
│   ├── core.py                  # 股票日线、新闻、基本面、分析历史、股票索引
│   ├── app.py                   # To C 用户、套餐、订单、审计、公告等
│   ├── portfolio.py             # 投资组合账户、交易、持仓、现金、汇率
│   ├── backtest.py              # 回测结果与汇总
│   ├── alert.py                 # 预警规则、触发、通知记录
│   └── conversation.py          # Agent 对话与 LLM 用量
└── manager/
    ├── _base.py                 # DatabaseManager 基础设施
    ├── manager.py               # 最终装配类
    ├── analysis_history.py      # 历史分析相关 Mixin
    ├── stock_data.py            # 股票日线相关 Mixin
    ├── news.py                  # 新闻情报相关 Mixin
    ├── conversation.py          # 会话相关 Mixin
    └── llm_usage.py             # LLM 用量记录 Mixin
```

`backend/src/storage/__init__.py` 统一 re-export，外部代码通常通过：

```python
from src.storage import DatabaseManager, AnalysisHistory, AppUser
```

---

## DatabaseManager

**文件：** `backend/src/storage/manager/_base.py` 与 `manager.py`

`DatabaseManager` 是数据库访问基础设施的单例入口。

### 主要职责

1. 读取数据库配置
2. 初始化 SQLAlchemy Engine
3. 创建 Session 工厂
4. 提供 `get_session()`
5. 装配各业务 Mixin 方法

### 获取实例

```python
manager = DatabaseManager.get_instance()
session = manager.get_session()
```

### FastAPI 中使用

FastAPI 使用依赖注入：

```python
def get_db():
    db_manager = DatabaseManager.get_instance()
    session = db_manager.get_session()
    try:
        yield session
    finally:
        session.close()
```

---

## Session 生命周期

### API 请求

- 每个请求创建一个 Session
- endpoint / service 使用该 Session 查询和写入
- 请求结束后关闭 Session

### 后台任务

异步分析任务、定时任务、支付回调等后台路径应自行创建并关闭 Session。

例如配额 refund 在后台失败路径中会使用独立 DB Session，避免 HTTP 请求 Session 已关闭导致无法回补。

### 注意事项

- 不要跨线程共享同一个 Session
- 不要把 ORM 对象长期缓存到全局变量中
- Service 层应尽量接受 `Session` 或 `DatabaseManager`，明确事务边界
- 写操作后由调用方决定 `commit()`，避免 service 内部过早提交破坏事务一致性

---

## ORM 模型分组

| 文件 | 业务域 | 主要模型 |
|------|--------|----------|
| `core.py` | 核心分析 | `StockDaily`, `NewsIntel`, `FundamentalSnapshot`, `AnalysisHistory`, `StockIndexEntry`, `StockIndexMeta` |
| `app.py` | To C 用户体系 | `AppUser`, `AppPlan`, `AppOrder`, `AppAuditLog`, `AppNotice` 等 |
| `portfolio.py` | 投资组合 | `PortfolioAccount`, `PortfolioTrade`, `PortfolioPosition` 等 |
| `backtest.py` | 回测 | `BacktestResult`, `BacktestSummary` |
| `alert.py` | 预警 | `AlertRuleRecord`, `AlertTriggerRecord`, `AlertNotificationRecord` |
| `conversation.py` | Agent 会话 | `ConversationMessage`, `LLMUsage` |

---

## 核心业务表

### `stock_daily` — 股票日线数据

模型：`StockDaily`

| 字段 | 说明 |
|------|------|
| `code` | 股票代码 |
| `date` | 交易日期 |
| `open/high/low/close` | OHLC 价格 |
| `volume` | 成交量 |
| `amount` | 成交额 |
| `pct_chg` | 涨跌幅 |
| `ma5/ma10/ma20` | 均线 |
| `volume_ratio` | 量比 |
| `data_source` | 数据来源 |

约束：

```text
UNIQUE(code, date)
INDEX(code, date)
```

### `news_intel` — 新闻情报

模型：`NewsIntel`

| 字段 | 说明 |
|------|------|
| `query_id` | 分析链路 ID |
| `code` / `name` | 股票代码与名称 |
| `dimension` | 检索维度 |
| `query` | 原始查询词 |
| `provider` | 搜索提供商 |
| `title` / `snippet` / `url` | 新闻内容 |
| `source` | 来源媒体 |
| `published_date` | 发布时间 |
| `query_source` | web / bot / cli / system |
| `requester_*` | Bot 或用户上下文 |

约束：

```text
UNIQUE(url)
INDEX(code, published_date)
```

### `fundamental_snapshot` — 基本面快照

模型：`FundamentalSnapshot`

保存分析时拿到的基本面 JSON，上层主流程暂不依赖读取，主要用于：

- 复盘时还原当时上下文
- 后续回测特征工程
- 数据质量审计

### `analysis_history` — 分析历史

模型：`AnalysisHistory`

| 字段 | 说明 |
|------|------|
| `user_id` | To C 用户 ID；CLI/Bot 兼容路径可为 NULL |
| `query_id` | 一次请求/分析链路 ID |
| `code` / `name` | 股票信息 |
| `report_type` | 报告类型 |
| `sentiment_score` | 情绪评分 |
| `operation_advice` | 操作建议 |
| `trend_prediction` | 趋势判断 |
| `analysis_summary` | 摘要 |
| `raw_result` | 原始分析结果 |
| `news_content` | 新闻快照 |
| `context_snapshot` | 分析上下文快照 |
| `ideal_buy` | 理想买点 |
| `secondary_buy` | 次级买点 |
| `stop_loss` | 止损位 |
| `take_profit` | 止盈位 |
| `created_at` | 创建时间 |

索引：

```text
INDEX(code, created_at)
```

### `stock_index` / `stock_index_meta` — 股票搜索索引

模型：`StockIndexEntry`, `StockIndexMeta`

股票搜索索引不再放在前端静态目录，而是由后端资源文件种入数据库。

启动时：

```text
FastAPI lifespan → ensure_stock_index_seeded() → stock_index 表为空时写入
```

`stock_index` 字段包括：

- `canonical_code`
- `display_code`
- `name_zh`
- `pinyin_full`
- `pinyin_abbr`
- `aliases`
- `market`
- `asset_type`
- `active`
- `popularity`

公开搜索接口：

```text
GET /api/v1/stocks/search?q=茅台
```

---

## 用户体系表

**文件：** `models/app.py`

### 用户与会话

| 表 | 模型 | 说明 |
|----|------|------|
| `app_users` | `AppUser` | 用户主表，含邮箱、密码哈希、套餐、管理员标记 |
| `app_user_sessions` | `AppUserSession` | 服务端 Session 表，Cookie 只承载随机 token |
| `app_user_email_verifications` | `AppUserEmailVerification` | 邮箱验证与密码重置 Token |

### 配额与套餐

| 表 | 模型 | 说明 |
|----|------|------|
| `app_user_usage_counters` | `AppUserUsageCounter` | 按 user/date/kind 记录每日用量 |
| `app_plans` | `AppPlan` | 套餐定义 |
| `app_subscriptions` | `AppSubscription` | 用户订阅历史 |
| `app_redeem_codes` | `AppRedeemCode` | 兑换码 |

### 用户业务配置

| 表 | 模型 | 说明 |
|----|------|------|
| `app_user_watchlists` | `AppUserWatchlist` | 用户自选股 |
| `app_user_notification_prefs` | `AppUserNotificationPref` | 用户通知偏好（每日推送、邮件、Webhook） |

### 订单与支付

| 表 | 模型 | 说明 |
|----|------|------|
| `app_orders` | `AppOrder` | 订单主表 |
| `app_payment_events` | `AppPaymentEvent` | 支付回调事件原始记录 |
| `app_refunds` | `AppRefund` | 退款申请/处理记录 |
| `app_invoices` | `AppInvoice` | 发票申请 |
| `app_reconciliation_diffs` | `AppReconciliationDiff` | 对账差异 |
| `app_reconciliation_reports` | `AppReconciliationReport` | 每日对账报告 |

### 合规、运营、增长

| 表 | 模型 | 说明 |
|----|------|------|
| `app_user_consents` | `AppUserConsent` | 用户协议同意历史 |
| `app_audit_logs` | `AppAuditLog` | 关键操作审计日志 |
| `app_growth_events` | `AppGrowthEvent` | 增长埋点事件 |
| `app_plan_reminders` | `AppPlanReminder` | 套餐到期提醒去重记录 |
| `app_notices` | `AppNotice` | 平台公告 |

---

## 投资组合表

**文件：** `models/portfolio.py`

| 表 | 说明 |
|----|------|
| `portfolio_accounts` | 投资账户，支持市场、币种、成本方法 |
| `portfolio_trades` | 买入/卖出流水 |
| `portfolio_cash_ledger` | 出入金与现金流水 |
| `portfolio_corporate_actions` | 分红、拆股等公司行为 |
| `portfolio_positions` | 当前持仓汇总 |
| `portfolio_position_lots` | 持仓批次（FIFO 成本计算） |
| `portfolio_daily_snapshots` | 每日资产快照 |
| `portfolio_fx_rates` | 汇率缓存 |

### 典型计算链路

```text
交易流水 + 现金流水 + 公司行为
        │
        ▼
PortfolioService.rebuild_positions()
        │
        ├─ FIFO / AVG 成本计算
        ├─ realized / unrealized PnL
        └─ 持仓与账户净值快照
```

---

## 回测表

**文件：** `models/backtest.py`

| 表 | 模型 | 说明 |
|----|------|------|
| `backtest_results` | `BacktestResult` | 单条分析建议的回测评估结果 |
| `backtest_summaries` | `BacktestSummary` | 聚合统计结果 |

### 回测结果常见字段

- `eval_status`：评估状态
- `position_recommendation`：分析时建议
- `outcome`：实际结果
- `direction_correct`：方向是否正确
- `stock_return_pct`：股票实际收益
- `simulated_return_pct`：模拟策略收益
- `hit_stop_loss` / `hit_take_profit`
- `first_hit` / `first_hit_trading_days`

核心评估逻辑在：

```text
backend/src/core/backtest_engine.py
```

---

## 预警表

**文件：** `models/alert.py`

| 表 | 模型 | 说明 |
|----|------|------|
| `alert_rule_records` | `AlertRuleRecord` | 用户配置的预警规则 |
| `alert_trigger_records` | `AlertTriggerRecord` | 每次触发记录 |
| `alert_notification_records` | `AlertNotificationRecord` | 通知发送记录 |

支持的规则类型：

- `price_cross`
- `price_change_percent`
- `volume_spike`

预警服务位于：

- `backend/src/services/alert_service.py`
- `backend/src/services/alert_worker.py`

---

## 会话与 LLM 用量表

**文件：** `models/conversation.py`

### `conversation_messages`

保存 Agent 对话历史，支持多轮上下文。

关键字段通常包括：

- `session_id`
- `user_id`
- `role`
- `content`
- `metadata`
- `created_at`

To C 场景中，session_id 会被加 `u{user_id}:` 前缀，并在查询时按 user_id 过滤。

### `llm_usage`

保存 LLM token 使用量，用于：

- 成本核算
- 用量统计
- 模型效果/成本对比

---

## Repository 层

**目录：** `backend/src/repositories/`

Repository 是 Service 与 ORM 之间的数据访问层。

| 文件 | 说明 |
|------|------|
| `analysis_repo.py` | 分析历史读写 |
| `alert_repo.py` | 预警规则、触发记录、通知记录 |
| `backtest_repo.py` | 回测结果读写 |
| `portfolio_repo.py` | 投资组合账户、交易、持仓读写 |
| `stock_repo.py` | 股票日线数据读写 |
| `stock_index_repo.py` | 股票搜索索引查询与同步 |

### Repository 使用原则

- 复杂查询集中在 Repository，避免散落在 endpoint 中
- Service 层负责业务规则，Repository 只处理数据访问
- Repository 不应处理 HTTP 语义（状态码、响应体等）
- 尽量把用户隔离条件（`user_id`）放入查询条件中，避免漏查/串数据

---

## Alembic 迁移

**目录：** `backend/alembic/`

### 硬性规则

所有数据库 schema 变更必须通过 Alembic migration 完成，禁止：

- 直接调用 `Base.metadata.create_all()` 变更生产 schema
- 在业务代码里手写 `ALTER TABLE`
- 只改 ORM 模型不生成迁移脚本

### 常用命令

```bash
# 查看当前版本
alembic current

# 生成迁移
alembic revision --autogenerate -m "add xxx table"

# 应用到最新
alembic upgrade head

# 回退一个版本
alembic downgrade -1
```

### 当前迁移文件

| 文件 | 内容 |
|------|------|
| `20260519_b0bc3c721ef0_initial_schema.py` | 初始全量 schema |
| `20260522_add_user_preferred_model.py` | `app_users.preferred_model` |
| `20260523_add_stock_index_tables.py` | 股票搜索索引表 |
| `20260527_widen_eval_status.py` | 扩展回测状态字段 |

### 新增字段流程

```text
1. 修改 ORM 模型
2. 运行 alembic revision --autogenerate
3. 检查生成脚本是否正确
4. 补充 downgrade 逻辑
5. 运行 alembic upgrade head
6. 补充测试
7. 更新文档和 docs/CHANGELOG.md
```

---

## 数据隔离与安全边界

### 用户隔离

To C 用户相关数据必须按 `user_id` 隔离：

- 分析历史：`AnalysisHistory.user_id`
- 任务队列：`TaskInfo.user_id`
- 自选股：`AppUserWatchlist.user_id`
- 投资组合：账户/交易/持仓均应绑定用户
- Agent 会话：`session_id` 前缀 + `user_id`
- 预警规则：`AlertRuleRecord.user_id`

### 管理员边界

`SystemConfigService` 操作 `.env`，属于部署级系统设置，只允许平台管理员访问。

普通用户不应访问：

- `/api/v1/system/config*`
- `/api/v1/admin/*`
- 任何平台级配置导入/导出接口

### 敏感字段

以下字段不能返回给前端：

- `password_hash`
- `token_hash`
- 邮件验证 token hash
- 支付回调签名密钥
- `.env` 中的 API Key 明文（除非系统配置 API 明确以脱敏字段展示）

---

## 开发建议

### 新增表

1. 放到对应 `models/*.py` 文件
2. 在 `models/__init__.py` 和 `storage/__init__.py` 中导出
3. 新增 Alembic migration
4. 如有复杂查询，新增 Repository
5. 补充测试

### 新增查询

优先放在 Repository：

```text
endpoint → service → repository → ORM
```

避免 endpoint 直接堆复杂 SQL。

### 新增用户数据

必须考虑：

- 是否需要 `user_id`
- 是否需要管理员查看能力
- 是否需要审计日志
- 是否涉及个人信息导出/注销
- 是否需要迁移旧数据

### 大批量写入

- 使用批量插入/更新
- 保持事务短小
- 注意 SQLite 写锁限制
- 对用户可见操作提供进度或异步任务

---

## 推荐阅读顺序

1. `backend/src/storage/__init__.py`
2. `backend/src/storage/models/core.py`
3. `backend/src/storage/models/app.py`
4. `backend/src/storage/manager/_base.py`
5. `backend/src/repositories/*.py`
6. `backend/alembic/versions/*.py`
