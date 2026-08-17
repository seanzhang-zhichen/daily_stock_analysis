# AI 建议（DecisionSignal）

AI 建议模块把个股分析历史中的操作建议、评分、价格计划与风险条件提取成结构化信号，提供独立的查询和生命周期管理页面。它不会执行下单，也不替代原始分析报告。

## 使用入口

- Web：侧边栏「AI 建议」，路由 `/decision-signals`
- API 前缀：`/api/v1/decision-signals`
- 数据表：`decision_signals`

页面默认显示当前用户的有效建议，支持按股票、市场、动作和状态筛选。点击一条建议可查看参考区间、止损位、目标价、观察条件、风险摘要、潜在催化和来源报告编号。

## 数据来源与同步

当前模块从已持久化的个股 `analysis_history` 提取信号：

1. 进入 AI 建议页或调用列表/最新接口时，后端幂等检查当前用户最近 500 条个股分析历史。
2. 尚未提取的历史记录按报告时间正序写入 `decision_signals`。
3. 大盘复盘（`report_type=market_review` 或 `code=MARKET`）不生成个股建议。
4. 同一用户、来源类型和来源报告只保存一条信号；重复同步不会重复写入。

页面上的「同步历史分析」可显式触发同一流程。信号写入失败不会修改或删除原分析历史。

## 动作与状态

动作值保持稳定的 API 枚举：

| 值 | 含义 |
| --- | --- |
| `buy` / `add` | 买入 / 加仓 |
| `hold` / `watch` | 持有 / 观望 |
| `reduce` / `sell` | 减仓 / 卖出 |
| `avoid` / `alert` | 回避 / 风险提醒 |

状态值包括 `active`、`expired`、`invalidated`、`closed`、`archived`。有效建议到达 `expires_at` 后会在查询时标记为过期；同一股票出现方向相反的新建议时，较早的有效建议会标记为失效。关闭、失效、归档或过期的信号不能通过当前 API 重新激活。

默认有效期由报告的时间敏感度映射：今日/立即行动使用 `intraday`，本周内使用 `5d`，其余使用 `3d`。这是建议展示周期，不是收益承诺或订单有效期。

## API

所有接口要求当前用户登录，并按 `user_id` 隔离：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/v1/decision-signals` | 分页查询；支持 `market`、`stock_code`、`action`、`status`、时间范围 |
| `POST` | `/api/v1/decision-signals/sync` | 从当前用户分析历史执行幂等同步 |
| `GET` | `/api/v1/decision-signals/latest/{stock_code}` | 查询单只股票最新的有效建议 |
| `GET` | `/api/v1/decision-signals/{signal_id}` | 查询详情 |
| `PATCH` | `/api/v1/decision-signals/{signal_id}/status` | 更新为终态（关闭、失效、归档或过期） |

## 数据与兼容边界

- `source_report_id` 指向来源分析历史的主键，但数据库不设置外键；历史删除策略不会隐式级联删除已沉淀建议。
- `evidence`、`data_quality_summary` 和 `metadata` 是只读展示元数据；客户端不应依赖其内部键作为稳定 API 契约。
- 当前移植范围不包含来源项目的后验收益统计、用户反馈和决策风格重评估。这些能力依赖尚未移植的 outcome engine 与 profile policy，不提供空接口。
- 本模块不新增环境变量，不需要更新 `.env.example`。

## 迁移与回滚

升级时执行：

```bash
uv run --locked alembic upgrade head
```

对应迁移为 `backend/alembic/versions/20260814_add_decision_signals.py`。回滚一步会删除 `decision_signals` 表及其中已同步信号，但不会删除 `analysis_history`；再次升级并同步可从仍保留的最近分析历史重建信号。

> 本模块仅用于研究与辅助判断，不构成投资建议，不保证收益或数据完整性。
