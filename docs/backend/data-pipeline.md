# 数据管道与分析流程

> 本文档解释后端从“接收股票代码”到“生成 AI 分析报告并通知用户”的完整链路，重点覆盖 `StockAnalysisPipeline`、数据源层、新闻搜索、LLM/Agent 分析、异步任务队列和通知发送。

---

## 目录

1. [整体链路](#整体链路)
2. [入口来源](#入口来源)
3. [股票代码规范化](#股票代码规范化)
4. [数据源管理](#数据源管理)
5. [行情与基本面数据](#行情与基本面数据)
6. [新闻与情报检索](#新闻与情报检索)
7. [核心分析管道](#核心分析管道)
8. [LLM 分析器](#llm-分析器)
9. [Agent 多智能体分析](#agent-多智能体分析)
10. [异步任务队列](#异步任务队列)
11. [大盘复盘](#大盘复盘)
12. [报告落库](#报告落库)
13. [通知推送](#通知推送)
14. [失败处理与降级](#失败处理与降级)
15. [关键配置](#关键配置)

---

## 整体链路

```text
用户 / 定时器 / Bot / CLI
        │
        ▼
股票代码输入
        │
        ▼
normalize_stock_code / canonical_stock_code
        │
        ▼
DataFetcherManager
  ├─ EfinanceFetcher
  ├─ AkshareFetcher
  ├─ TushareFetcher
  ├─ PytdxFetcher
  ├─ BaostockFetcher
  ├─ YfinanceFetcher
  └─ LongbridgeFetcher
        │
        ▼
行情 + 基本面 + 筹码 + 实时价格
        │
        ├─────────────┐
        ▼             ▼
SearchService     StockTrendAnalyzer
新闻/风险/行业       技术趋势计算
        │             │
        └──────┬──────┘
               ▼
Deep Research 生成 stock_profile
               │
               ▼
GeminiAnalyzer / AgentOrchestrator
               │
               ▼
AnalysisResult / Dashboard JSON
               │
        ┌──────┴──────┐
        ▼             ▼
AnalysisHistory   NotificationService
历史记录落库        多渠道推送
```

---

## 入口来源

同一条分析管道可被多个入口调用。

| 入口 | 文件 | 说明 |
|------|------|------|
| CLI | `backend/main.py` | `python backend/main.py --stocks 600519` |
| FastAPI | `backend/api/v1/endpoints/analysis.py` | Web 端提交异步分析任务 |
| 定时器 | `backend/src/scheduler.py` | 每日固定时间执行自选股分析 |
| Bot | `backend/bot/handler.py` | IM Webhook 触发分析 |
| Agent 工具 | `backend/src/agent/tools/analysis_tools.py` | 对话中调用分析工具 |

### Web 分析入口

Web 端通常调用：

```text
POST /api/v1/analysis/tasks
```

后端不会同步阻塞到分析完成，而是：

1. 检查登录用户与配额
2. 规范化股票代码
3. 写入内存任务队列
4. 返回 `task_id`
5. 后台线程池运行分析
6. 前端通过 SSE 监听进度

---

## 股票代码规范化

核心函数位于 `backend/data_provider/base.py`。

| 函数 | 作用 |
|------|------|
| `normalize_stock_code(code)` | 将用户输入转成数据源可识别的代码形态 |
| `canonical_stock_code(code)` | 生成去后缀的 canonical key，用于去重和查询 |
| `is_us_stock_code(code)` | 判断是否美股代码 |
| `is_hk_stock_code(code)` | 判断是否港股代码 |

### 典型转换

| 用户输入 | 规范化形态 | Canonical |
|----------|------------|-----------|
| `600519` | `600519.SH` | `600519` |
| `000001` | `000001.SZ` | `000001` |
| `hk00700` | `00700.HK` 或数据源内部映射 | `00700` |
| `AAPL` | `AAPL` | `AAPL` |

### 重复任务检测

异步队列使用：

```python
_dedupe_task_key(stock_code, user_id)
```

生成：

```text
user:{user_id}:{canonical_stock_code}
```

因此同一用户对同一股票不会重复提交，但不同用户之间互不影响。

---

## 数据源管理

**目录：** `backend/data_provider/`

数据源层使用策略模式：每个数据源实现统一接口，`DataFetcherManager` 按优先级尝试，失败后自动 fallback。

### 主数据源优先级

| 优先级 | Fetcher | 主要用途 |
|--------|---------|----------|
| 0 | `TushareFetcher` | 配置 `TUSHARE_TOKEN` 后优先，A 股数据质量较好 |
| 0 | `EfinanceFetcher` | A 股免费行情，默认高优先级 |
| 1 | `AkshareFetcher` | A 股、港股、美股，多功能兜底 |
| 2 | `PytdxFetcher` | 通达信协议，A 股行情 |
| 3 | `BaostockFetcher` | baostock 数据源 |
| 4 | `YfinanceFetcher` | 美股/港股常用 fallback |
| 5 | `LongbridgeFetcher` | 长桥 OpenAPI，港股/美股兜底 |

### 辅助数据源

| Fetcher | 说明 |
|---------|------|
| `FinnhubFetcher` | 美股基本面、新闻等辅助数据 |
| `AlphaVantageFetcher` | 美股行情与指标辅助数据 |
| `FundamentalAdapter` | 对多个来源的基本面字段做标准化 |

### Fallback 原则

1. 单个数据源失败不应拖垮主流程
2. 数据源异常记录日志，继续尝试下一个可用数据源
3. 不同市场优先使用更匹配的数据源
4. 最终结果尽量标准化为统一字段，避免上层感知来源差异

---

## 行情与基本面数据

核心数据包括：

| 数据类别 | 来源 | 用途 |
|----------|------|------|
| 日线 OHLCV | DataFetcherManager | 均线、趋势、回测 |
| 实时行情 | DataFetcherManager | 当前价格、涨跌幅、预警 |
| 基本面 | FundamentalAdapter / Fetcher | PE/PB/ROE/营收利润等 |
| 筹码分布 | 支持的 Fetcher | 判断筹码集中度、成本区间 |
| 指数行情 | market tools / fetcher | 大盘环境判断 |

### 日线数据标准字段

通常会包含：

```text
日期、开盘、最高、最低、收盘、成交量、成交额、涨跌幅、MA5、MA10、MA20、量比
```

落库模型为 `StockDaily`，唯一键为：

```text
(code, date)
```

### 基本面快照

`FundamentalSnapshot` 目前主要是 write-only，用于把分析时的基本面上下文保存下来，方便后续回测、画像、数据审计扩展。

---

## 新闻与情报检索

**文件：** `backend/src/search_service.py`

`SearchService` 为分析流程提供新闻、风险、财报、行业等情报。

### 情报维度

| 维度 | 说明 |
|------|------|
| `latest_news` | 最新新闻 |
| `risk_check` | 风险排查（处罚、退市、诉讼、业绩暴雷等） |
| `earnings` | 财报、业绩预告、业绩快报 |
| `market_analysis` | 大盘和宏观环境 |
| `industry` | 行业趋势、板块表现 |

### 情报落库

检索结果保存到 `NewsIntel` 表，关键字段包括：

- `query_id`：一次分析链路的唯一 ID
- `code` / `name`：股票信息
- `dimension`：检索维度
- `provider`：搜索提供商
- `title` / `snippet` / `url` / `source`
- `published_date`
- `query_source`：来源（web、bot、cli、system）

---

## 核心分析管道

**文件：** `backend/src/core/pipeline.py`

核心类：

```python
StockAnalysisPipeline
```

### 初始化参数

| 参数 | 说明 |
|------|------|
| `config` | 配置对象，不传则使用 `get_config()` |
| `max_workers` | 并发分析线程数 |
| `source_message` | Bot 消息上下文 |
| `query_id` | 本次分析链路 ID，不传则生成 UUID |
| `query_source` | 来源：web / bot / cli / system |
| `save_context_snapshot` | 是否保存上下文快照 |
| `progress_callback` | 进度回调（异步任务队列用于 SSE 推送） |
| `analysis_skills` | 分析技能集 |
| `user_id` | To C 用户 ID，落库隔离用 |

### 单股分析主要步骤

```text
1. 规范化股票代码
2. 拉取日线数据
3. 计算趋势指标
4. 拉取实时行情
5. 拉取基本面与筹码分布
6. 搜索新闻/风险/行业情报
7. 阻塞执行 Deep Research 生成权威 stock_profile
8. 构造 LLM Prompt / Agent Context 并注入 stock_profile
9. 调用 LLM 或 Agent 生成结论
10. 标准化报告语言和决策字段，并以 Deep Research 结果回写 AnalysisResult.stock_profile
11. 保存 AnalysisHistory
12. 返回 AnalysisResult
```

### 批量分析

批量分析使用线程池并发执行单股流程。每只股票独立捕获异常，避免一只股票失败影响整批。

---

## LLM 分析器

**文件：** `backend/src/analyzer.py`

核心类：

| 类 | 说明 |
|----|------|
| `GeminiAnalyzer` | 历史命名保留，实际可通过 LiteLLM 对接多供应商 |
| `AnalysisResult` | 标准分析结果结构 |

### AnalysisResult 关键字段

| 字段 | 说明 |
|------|------|
| `stock_code` | 股票代码 |
| `stock_name` | 股票名称 |
| `sentiment_score` | 综合情绪/信心评分 |
| `operation_advice` | 操作建议（买入、持有、卖出、观望等） |
| `trend_prediction` | 趋势判断 |
| `analysis_summary` | 总结文本 |
| `ideal_buy` | 理想买点 |
| `secondary_buy` | 次级买点 |
| `stop_loss` | 止损位 |
| `take_profit` | 止盈位 |
| `raw_result` | 原始 LLM 输出 |

### 决策稳定化

分析器中包含一些结构化修正逻辑，例如：

- `fill_chip_structure_if_needed`
- `fill_price_position_if_needed`
- `stabilize_decision_with_structure`

作用是让 LLM 输出与技术结构一致，降低“文本建议”和“价格结构”矛盾的概率。

---

## Agent 多智能体分析

**目录：** `backend/src/agent/`

当使用 Agent 架构时，系统不再只用单个 Prompt 直接出报告，而是多阶段协作：

```text
AgentContext
    │
    ├─ TechnicalAgent：技术分析
    ├─ IntelAgent：新闻与情报
    ├─ RiskAgent：风险排查
    ├─ SpecialistAgent：专项评估（specialist 模式）
    └─ DecisionAgent：最终决策
```

### Orchestrator 模式

| 模式 | 阶段 | 适用场景 |
|------|------|----------|
| `quick` | Technical → Decision | 低成本快速判断 |
| `standard` | Technical → Intel → Decision | 默认分析 |
| `full` | Technical → Intel → Risk → Decision | 更完整的风险评估 |
| `specialist` | Technical → Intel → Risk → Specialist → Decision | 深度研究 |

### 工具注册

`ToolRegistry` 统一管理工具函数。Agent 可调用：

- 行情工具
- 新闻搜索工具
- 技术分析工具
- 回测工具
- 大盘工具

---

## 异步任务队列

**文件：** `backend/src/services/task_queue.py`

### 核心对象

| 对象 | 说明 |
|------|------|
| `TaskInfo` | 单个任务的内存态信息 |
| `TaskStatus` | `pending` / `processing` / `completed` / `failed` |
| `AnalysisTaskQueue` | 全局任务队列单例 |
| `DuplicateTaskError` | 重复任务异常 |

### 状态流转

```text
pending → processing → completed
                    └→ failed
```

### SSE 广播

任务队列内部维护订阅者队列。任务状态变化时向订阅者广播 JSON 事件。

前端连接：

```text
GET /api/v1/analysis/tasks/stream
```

### 多用户隔离

`TaskInfo.user_id` 记录任务归属。

- 查询任务列表时按 `user_id` 过滤
- 查询单个任务状态时校验归属
- SSE 只推送当前用户任务
- 重复任务检测 key 包含 `user_id`

---

## 大盘复盘

相关文件：

| 文件 | 说明 |
|------|------|
| `backend/src/core/market_review.py` | 大盘复盘主逻辑 |
| `backend/src/core/market_review_lock.py` | 跨进程锁，防重复执行 |
| `backend/src/core/market_review_runtime.py` | 构建运行时配置 |
| `backend/src/market_analyzer.py` | 大盘分析器 |
| `backend/src/market_context.py` | 大盘上下文 |

### 执行特点

- 支持 API 手动触发和定时触发
- 使用锁文件避免同一时间多次复盘
- 输出整体市场判断、指数表现、板块信息、风险提示等

---

## 报告落库

分析结果保存到 `analysis_history` 表。

### 关键字段

| 字段 | 说明 |
|------|------|
| `user_id` | To C 用户 ID，CLI/Bot 旧路径可为 NULL |
| `query_id` | 一次分析请求的链路 ID |
| `code` / `name` | 股票信息 |
| `report_type` | 报告类型 |
| `sentiment_score` | 情绪评分 |
| `operation_advice` | 操作建议 |
| `trend_prediction` | 趋势预测 |
| `analysis_summary` | 摘要 |
| `raw_result` | 原始报告 JSON / 文本 |
| `news_content` | 新闻内容快照 |
| `context_snapshot` | 分析上下文快照 |
| `ideal_buy` / `secondary_buy` | 建议买点 |
| `stop_loss` / `take_profit` | 风控点位 |

### 查询入口

- `/api/v1/history/*`
- `/api/v1/analysis/history/{query_id}`
- 回测服务也会读取历史分析中的点位建议

---

## 通知推送

**文件：** `backend/src/notification.py`

分析完成后，`NotificationService` 负责将结果整理为 Markdown/图片/HTML 等形式并发送。

### 支持渠道

- 企业微信
- 飞书
- Telegram
- 邮件 SMTP
- Pushover
- ntfy
- Gotify
- PushPlus
- Server 酱 3
- Discord
- Slack
- AstrBot
- 自定义 Webhook

### 报告类型

`ReportType` 控制报告渲染形式，例如：

- 单股详细报告
- 批量报告
- 大盘复盘报告
- 预警通知

### 单股邮件主题

单只股票分析邮件主题包含股票名、代码和日期，批量报告保持日期维度主题。

---

## 失败处理与降级

### 数据源失败

- 单数据源失败：尝试下一个数据源
- 所有数据源失败：当前股票分析失败，但批量任务继续

### 新闻搜索失败

- 记录日志
- LLM 仍可基于技术数据和基本面继续分析

### LLM 失败

- 当前股票标记失败
- Web 异步任务进入 `failed`
- 已扣配额会按失败路径 refund

### 通知失败

- 单一通知渠道失败不应拖垮分析主流程
- 失败记录日志，其他渠道继续发送

### 配额失败

- 提交前扣减失败直接返回 `429 quota_exceeded`
- 扣减成功但后台业务失败则回补

---

## 关键配置

| 配置 | 说明 |
|------|------|
| `STOCK_CODES` | 默认自选股列表 |
| `MAX_WORKERS` | 批量分析线程数 |
| `LLM_MODEL` | 默认 LLM 模型 |
| `AGENT_ARCH` | Agent 架构选择 |
| `AGENT_MAX_STEPS` | ReAct Agent 最大步数 |
| `AGENT_DEEP_RESEARCH_TIMEOUT` | Deep Research 总超时秒数，默认 600 秒 |
| `AGENT_DEEP_RESEARCH_MAX_SUB_QUESTIONS` | Deep Research 模型规划子问题上限 |
| `AGENT_DEEP_RESEARCH_SUB_QUESTION_STEPS` | Deep Research 单个子问题最大 ReAct/tool 步数 |
| `TUSHARE_TOKEN` | Tushare 数据源 Token |
| `LONGBRIDGE_APP_KEY` / `LONGBRIDGE_APP_SECRET` | 长桥 OpenAPI |
| `SEARCH_PROVIDER` | 搜索服务提供商 |
| `SCHEDULE_TIME` | 每日调度时间 |
| `NOTIFICATION_CHANNELS` | 通知渠道路由 |

---

## 推荐阅读顺序

1. `backend/api/v1/endpoints/analysis.py` — 理解 Web 分析入口
2. `backend/src/services/task_queue.py` — 理解任务状态与 SSE
3. `backend/src/core/pipeline.py` — 理解主分析流程
4. `backend/data_provider/base.py` — 理解数据源策略与代码规范化
5. `backend/src/analyzer.py` — 理解 LLM 输出结构
6. `backend/src/notification.py` — 理解报告发送
