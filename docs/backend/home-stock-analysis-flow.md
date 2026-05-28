# 首页股票输入到分析任务流程

本文说明用户在 Web 首页输入股票代码或股票名称后，系统如何完成股票搜索补全、提交分析任务、后端异步执行、实时状态回传与报告落库。

## 一句话概览

首页输入框本身不直接执行分析。它先通过股票索引接口做搜索补全；用户按回车、点击补全项或点击「分析」后，前端把股票代码规范化并调用 `POST /api/v1/analysis/analyze`，后端把请求转成异步任务，任务线程再调用 `AnalysisService -> StockAnalysisPipeline` 完成数据抓取、AI 分析、报告保存和可选通知。

```text
HomePage / StockAutocomplete
        ↓
GET /api/v1/stocks/search              POST /api/v1/analysis/analyze
股票搜索补全                              提交异步分析任务
        ↓                                      ↓
StockIndexRepository                  AnalysisTaskQueue
stock_index 表                         线程池 + 防重复 + SSE
                                               ↓
                                      AnalysisService
                                               ↓
                                      StockAnalysisPipeline
                                               ↓
                         数据源 / 技术分析 / 新闻搜索 / LLM 或 Agent / 通知 / 历史报告
```

## 代码入口速查

| 阶段 | 主要代码 | 说明 |
| --- | --- | --- |
| 首页页面 | `frontend/web/src/pages/HomePage.tsx` | 渲染输入框、处理点击「分析」、把精确名称解析为规范代码 |
| 输入框组件 | `frontend/web/src/components/StockAutocomplete/StockAutocomplete.tsx` | 处理输入、回车、键盘选择、点击补全项 |
| 自动补全 Hook | `frontend/web/src/hooks/useAutocomplete.ts` | debounce 搜索、AbortController 取消旧请求、失败后降级普通输入 |
| 股票搜索 API 封装 | `frontend/web/src/api/stocks.ts` | `stocksApi.search()` 调用 `/api/v1/stocks/search` |
| 首页状态 store | `frontend/web/src/stores/stockPoolStore.ts` | `submitAnalysis()` 做校验、规范化、调用分析 API、乐观加入任务队列 |
| 分析 API 封装 | `frontend/web/src/api/analysis.ts` | `analysisApi.analyzeAsync()` 调用 `/api/v1/analysis/analyze` |
| 股票搜索后端 | `backend/api/v1/endpoints/stocks.py` | `search_stock_index()` 公开限流股票搜索接口 |
| 股票索引仓库 | `backend/src/repositories/stock_index_repo.py` | `StockIndexRepository.search()` 查询 `stock_index` 并打分排序 |
| 分析接口后端 | `backend/api/v1/endpoints/analysis.py` | `trigger_analysis()` 校验、规范化、扣配额、提交异步任务 |
| 异步任务队列 | `backend/src/services/task_queue.py` | `AnalysisTaskQueue` 防重复、线程池执行、SSE 广播、失败返还配额 |
| 分析服务层 | `backend/src/services/analysis_service.py` | `AnalysisService.analyze_stock()` 创建并调用分析 pipeline |
| 核心分析 pipeline | `backend/src/core/pipeline.py` | `StockAnalysisPipeline.process_single_stock()` 执行完整分析流程 |
| SSE 前端 Hook | `frontend/web/src/hooks/useTaskStream.ts` | 连接 `/api/v1/analysis/tasks/stream` 并同步任务状态 |

## 1. 用户输入时：股票搜索补全

### 前端行为

1. `HomePage.tsx` 在首页工具栏渲染 `StockAutocomplete`。
2. 用户输入内容时，`StockAutocomplete` 调用父组件传入的 `onChange`，最终更新 `useStockPoolStore` 中的 `query`。
3. `StockAutocomplete` 内部使用 `useAutocomplete()`：
   - 输入长度达到搜索阈值后触发 debounce 搜索。
   - 新搜索发出前会 abort 上一次未完成请求。
   - 请求失败时进入 runtime fallback，变成普通输入框，不阻断手动提交。
4. `useAutocomplete()` 通过 `stocksApi.search(q, limit, signal)` 请求后端：

```text
GET /api/v1/stocks/search?q=<用户输入>&limit=20
```

5. 用户可以：
   - 点击补全项；
   - 用方向键选中补全项后按回车；
   - 不选补全项，直接按回车或点击首页「分析」。

### 后端行为

`backend/api/v1/endpoints/stocks.py` 中的 `search_stock_index()` 处理搜索请求：

1. 按客户端 IP 做简单限流，默认 60 秒最多 60 次。
2. 调用 `StockIndexRepository().search(q, limit=limit)`。
3. `backend/src/repositories/stock_index_repo.py` 在 `stock_index` 表中按代码、中文名、拼音、别名查询候选项。
4. 仓库层对候选项重新打分：
   - 规范代码精确匹配分数最高；
   - 其次是展示代码、中文名、别名、拼音缩写精确匹配；
   - 再往后是前缀匹配和包含匹配。
5. 返回给前端的每一项包含 `canonicalCode`、`displayCode`、`nameZh`、`market`、`matchType`、`matchField`、`score`。

## 2. 用户提交时：从输入值到分析请求

提交入口主要在 `HomePage.tsx` 的 `handleSubmitAnalysis()`。

### 选择补全项提交

如果用户点击补全项或按回车选中补全项：

1. `StockAutocomplete.tsx` 把输入框显示值更新为 `displayCode`。
2. 调用 `onSubmit(canonicalCode, nameZh, 'autocomplete')`。
3. `HomePage.tsx` 把规范代码和股票名称传给 `submitAnalysis()`。
4. `selectionSource` 记录为 `autocomplete`。

### 直接输入后点击「分析」

如果用户没有选择补全项，而是直接输入 `600519`、`AAPL`、`hk00700` 或股票中文名后点击「分析」：

1. `HomePage.tsx` 会先调用 `resolveExactStockInput(query)`。
2. 该函数用前端加载到的股票索引做精确匹配。
3. 如果只有一个精确匹配项，就把输入解析为 `canonicalCode` 和 `nameZh` 后再提交。
4. 如果没有唯一精确匹配，就把原始输入交给 store 的 `submitAnalysis()`，由前端校验和后端兜底解析继续处理。

这条逻辑避免用户输入精确中文名时，把中文名直接作为 `stock_code` 提交给后端，从而减少后端在线名称解析带来的超时风险。

## 3. 前端 store：校验、规范化和调用分析 API

`frontend/web/src/stores/stockPoolStore.ts` 中的 `submitAnalysis()` 是首页提交分析的核心状态逻辑。

它会依次做这些事：

1. 读取 `options.stockCode`；没有则使用当前 `query`。
2. 去除首尾空白，空值时设置 `inputError`。
3. 如果不是 autocomplete 来源，先用 `isObviouslyInvalidStockQuery()` 拦截明显无效输入。
4. 如果来源是 autocomplete，或输入看起来像股票代码，则通过 `validateStockCode()` 校验并规范化。
5. 设置 `isAnalyzing=true`，清理旧错误。
6. 调用 `analysisApi.analyzeAsync()`，固定以异步方式提交：

```text
POST /api/v1/analysis/analyze
```

请求体字段由前端 camelCase 转成后端 snake_case，典型单股请求如下：

```json
{
  "stock_code": "600519",
  "report_type": "detailed",
  "force_refresh": false,
  "async_mode": true,
  "stock_name": "贵州茅台",
  "original_query": "600519",
  "selection_source": "autocomplete",
  "skills": ["可选技能"],
  "notify": true
}
```

7. 后端返回 `202` 后，前端用返回的 `task_id` 构造乐观任务，立即展示在首页任务面板。
8. 如果后端返回 `409`，`analysisApi.analyzeAsync()` 抛出 `DuplicateTaskError`，首页展示“股票正在分析中”。

## 4. 后端分析接口：校验、规范化、配额和入队

`backend/api/v1/endpoints/analysis.py` 中的 `trigger_analysis()` 处理 `POST /api/v1/analysis/analyze`。

主要步骤如下：

1. 收集 `stock_code` 和 `stock_codes`。
2. 调用 `_resolve_and_normalize_input()` 对每个输入做解析和规范化：
   - 看起来像代码的输入走 `canonical_stock_code()`；
   - 明显非法的自由文本直接返回 `400`；
   - 非代码输入调用 `resolve_name_to_code()` 兜底解析为股票代码；
   - 无法解析则返回 `400`。
3. 对规范化后的股票去重，避免 `600519` 和 `600519.SH` 被当成两只股票。
4. 限制单次最多 50 只股票。
5. 首页请求使用 `async_mode=true`，因此走异步分支。
6. To C 用户模式下，按股票数量调用 `enforce_quota()` 扣减分析配额；如果配额不足返回 `402`。
7. 调用 `_handle_async_analysis_batch()` 提交任务队列。

异步单股成功时，接口返回 `202`：

```json
{
  "task_id": "<任务ID>",
  "status": "pending",
  "message": "分析任务已加入队列: 600519"
}
```

如果同一用户已经有同一股票正在分析，单股请求返回 `409`，并带上已有任务 ID。

## 5. 异步任务队列：防重复、线程池和 SSE

`backend/src/services/task_queue.py` 中的 `AnalysisTaskQueue` 是进程内单例队列。

### 提交任务

`submit_tasks_batch()` 负责创建任务：

1. 通过 `_dedupe_task_key(stock_code, user_id)` 生成防重复 key。
2. 防重复维度包含当前用户：不同用户可以提交同一股票；同一用户不能重复提交同一只正在分析的股票。
3. 为每只股票生成 `task_id`。
4. 创建 `TaskInfo`，初始状态为 `pending`。
5. 把任务交给 `ThreadPoolExecutor` 执行 `_execute_task()`。
6. 广播 `task_created` SSE 事件。

### 执行任务

`_execute_task()` 在线程池中运行：

1. 将任务状态改为 `processing`，进度设为 10，并广播 `task_started`。
2. 创建 `AnalysisService()`。
3. 调用 `service.analyze_stock()`，同时传入进度回调。
4. 分析成功后：
   - 状态改为 `completed`；
   - 进度改为 100；
   - 保存 `result`；
   - 从防重复集合移除；
   - 广播 `task_completed`。
5. 分析失败后：
   - 状态改为 `failed`；
   - 记录错误信息；
   - 从防重复集合移除；
   - 广播 `task_failed`；
   - 如果前面扣过 To C 分析配额，则用独立 DB session 返还配额。

## 6. 核心分析链路：AnalysisService 到 StockAnalysisPipeline

`backend/src/services/analysis_service.py` 的 `AnalysisService.analyze_stock()` 是 API/任务队列和核心 pipeline 之间的服务层。

它会：

1. 读取全局配置。
2. 创建 `StockAnalysisPipeline`，设置：
   - `query_id`：异步任务中通常等于 `task_id`；
   - `query_source="api"`；
   - `progress_callback`：用于把 pipeline 阶段进度同步到任务队列；
   - `analysis_skills`：首页选择的分析技能；
   - `user_id`：To C 用户归属。
3. 把 `report_type` 转成 `ReportType`。
4. 调用 `pipeline.process_single_stock()`。
5. 把 `AnalysisResult` 转换为 API 需要的 `stock_code`、`stock_name`、`report` 字典。

`backend/src/core/pipeline.py` 中的 `StockAnalysisPipeline.process_single_stock()` 才是真正的单股分析主流程：

1. 冻结本轮目标交易日，保证同一次分析中的日期判断一致。
2. 调用 `fetch_and_save_stock_data()` 获取并保存行情数据。
3. 调用 `analyze_stock()` 执行增强分析：
   - 获取股票名称；
   - 获取实时行情，失败时降级为历史收盘价；
   - 获取筹码分布，失败不阻断主流程；
   - 聚合基本面和技术面数据；
   - 可选检索新闻、舆情和风险信息；
   - 根据配置选择传统 LLM 分析或 Agent 分析；
   - 保存分析历史和上下文快照；
   - 单股通知开启时发送通知。
4. 返回分析结果给 `AnalysisService`。

## 7. 任务状态如何回到首页

首页任务状态主要通过 SSE 实时同步。

### 后端 SSE

`backend/api/v1/endpoints/analysis.py` 提供：

```text
GET /api/v1/analysis/tasks/stream
```

`task_stream()` 会：

1. 建立 SSE 连接后先发送 `connected`。
2. 把当前用户正在进行的任务以 `task_created` 形式补发给前端。
3. 订阅 `AnalysisTaskQueue` 的事件队列。
4. 持续推送：
   - `task_created`
   - `task_started`
   - `task_progress`
   - `task_completed`
   - `task_failed`
   - `heartbeat`

### 前端接收

`frontend/web/src/hooks/useTaskStream.ts` 会创建 `EventSource`：

```text
/api/v1/analysis/tasks/stream
```

它把后端 snake_case 事件数据转成前端 `TaskInfo` 的 camelCase 结构，然后调用首页传入的回调。

`HomePage.tsx` 把这些回调接到 `useStockPoolStore`：

- `syncTaskCreated()`：新增任务卡片。
- `syncTaskUpdated()`：更新进度、状态、消息。
- `syncTaskFailed()`：更新任务并显示错误。

因此用户提交后通常会看到：

1. 前端乐观任务立即出现。
2. 后端 SSE 推送任务开始。
3. pipeline 阶段进度持续更新。
4. 完成后任务变成 `completed`。
5. 历史报告列表可刷新或通过状态查询拿到最终报告。

## 8. 单任务状态查询和历史兜底

除了 SSE，后端还提供：

```text
GET /api/v1/analysis/status/{task_id}
GET /api/v1/analysis/tasks
```

`get_analysis_status()` 会优先从内存任务队列查任务；如果任务已被清理，则用 `query_id=task_id` 从数据库分析历史中查询已完成报告。

这就是为什么异步任务的 `task_id` 同时也是分析链路的 `query_id`：它可以把任务状态、pipeline 运行、历史报告串在同一条链路上。

## 9. 常见输入路径示例

### 输入 `600519` 并点击「分析」

```text
HomePage query=600519
  -> stockPoolStore.submitAnalysis()
  -> validateStockCode() 规范化
  -> POST /api/v1/analysis/analyze async_mode=true
  -> trigger_analysis() canonical_stock_code()
  -> AnalysisTaskQueue.submit_tasks_batch()
  -> AnalysisService.analyze_stock()
  -> StockAnalysisPipeline.process_single_stock()
```

### 输入 `贵州茅台` 并点击「分析」

```text
HomePage query=贵州茅台
  -> resolveExactStockInput() 在前端股票索引中找到唯一精确匹配
  -> stockCode=600519, stockName=贵州茅台
  -> 后续与代码输入路径相同
```

如果前端索引没有唯一精确匹配，后端 `_resolve_and_normalize_input()` 仍会尝试 `resolve_name_to_code()` 兜底；兜底失败则返回 `400`。

### 点击补全项 `腾讯控股 hk00700`

```text
StockAutocomplete suggestion
  -> onSubmit(canonicalCode, nameZh, 'autocomplete')
  -> stockPoolStore.submitAnalysis(selectionSource='autocomplete')
  -> POST /api/v1/analysis/analyze
  -> 后端异步任务队列
```

## 10. 排查时优先看的日志和位置

| 现象 | 优先检查 |
| --- | --- |
| 输入框没有补全 | `/api/v1/stocks/search`、`StockIndexRepository.search()`、`stock_index` 表是否有数据 |
| 点分析没有发请求 | `HomePage.tsx` 的 `handleSubmitAnalysis()`、`stockPoolStore.submitAnalysis()` 的前端校验 |
| 后端返回 `400` | `analysis.py` 的 `_resolve_and_normalize_input()` 和请求体中的 `stock_code` |
| 后端返回 `402` | `trigger_analysis()` 中的 `enforce_quota()` 配额逻辑 |
| 后端返回 `409` | `AnalysisTaskQueue._analyzing_stocks` 中同用户同股票已有任务 |
| 任务不更新进度 | `/api/v1/analysis/tasks/stream`、`useTaskStream.ts`、`AnalysisTaskQueue._broadcast_event()` |
| 任务失败但接口提交成功 | `task_queue.py` 的 `_execute_task()` 日志和 `AnalysisService.last_error` |
| 报告没出现在历史中 | `StockAnalysisPipeline.analyze_stock()` 中保存历史的逻辑和 `query_id/task_id` 是否一致 |
