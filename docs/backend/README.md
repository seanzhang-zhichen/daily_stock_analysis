# 后端理解指南

> 本目录面向希望系统理解后端实现的开发者，按“先整体、再链路、再数据和用户体系”的顺序组织。

## 推荐阅读顺序

| 顺序 | 文档 | 适合解决的问题 |
| --- | --- | --- |
| 1 | [后端架构总览](overview.md) | 后端有哪些模块、各自职责是什么、从哪里启动 |
| 2 | [API 层详细说明](api.md) | FastAPI 路由、认证、依赖注入和主要接口如何组织 |
| 3 | [数据管道与分析流程](data-pipeline.md) | 股票分析从输入到报告生成经历哪些步骤 |
| 4 | [存储层与数据模型](storage.md) | 数据库表、ORM 模型、Repository 和 migration 如何设计 |
| 5 | [To C 用户体系与计费](user-system.md) | 注册登录、Session、套餐、配额、支付、管理员能力如何工作 |

## 核心心智模型

```text
入口层（CLI / API / Bot / Scheduler）
        ↓
API 与服务层（endpoints / services / users）
        ↓
核心业务编排（StockAnalysisPipeline / Agent / Backtest / Portfolio）
        ↓
外部能力（数据源 / LLM / 搜索 / 通知 / 支付）
        ↓
存储层（SQLAlchemy ORM / Repository / Alembic）
```

## 关键代码入口

| 代码 | 说明 |
| --- | --- |
| `backend/main.py` | CLI、调度、服务启动入口 |
| `backend/api/app.py` | FastAPI 应用工厂 |
| `backend/api/v1/router.py` | API v1 路由聚合 |
| `backend/src/core/pipeline.py` | 股票分析主流程 |
| `backend/src/services/task_queue.py` | 异步分析任务队列和 SSE |
| `backend/data_provider/base.py` | 数据源统一接口和管理器 |
| `backend/src/agent/orchestrator.py` | 多智能体分析编排 |
| `backend/src/storage/` | ORM 模型和数据库管理 |
| `backend/src/users/` | To C 用户体系 |

## 维护说明

- 后端真实代码以 `backend/` 为准。
- 数据库 schema 变更必须通过 Alembic migration。
- API、用户可见能力、部署方式、通知、报告结构发生变化时，请同步更新本目录相关文档与 `docs/CHANGELOG.md`。
