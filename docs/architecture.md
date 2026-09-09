# 架构说明

## 系统边界

```text
用户 / Bot / 定时器
        |
CLI、Web、Desktop、HTTP API
        |
FastAPI 与任务服务
        |
分析编排、Agent、选股、告警、通知
        |
数据源适配器、LLM/搜索提供商、数据库、文件存储
```

后端位于 `backend/`，Web 位于 `frontend/web/`，Electron 桌面端位于 `frontend/desktop/`。所有入口共享后端服务层和同一套数据模型，不维护平行实现。

## 入口与运行模型

- `backend/main.py`：CLI、单次分析、定时任务与服务启动入口。
- `backend/server.py`：ASGI 入口，供 Uvicorn 等进程服务器使用。
- `backend/api/app.py`：FastAPI 应用工厂、生命周期、中间件和静态资源托管。
- `backend/api/v1/`：版本化 HTTP API；每个业务域各自维护 endpoint 与 schema。

API 请求中耗时的分析操作会进入任务服务。客户端通过任务状态、轮询或流式事件观察进度，而不应把一次分析当作同步、即时完成的调用。

## 单股分析主流程

```text
代码/名称输入
  -> 代码规范化与市场识别
  -> 行情、K 线、基本面、新闻和事件采集
  -> 数据标准化、缓存与后备源降级
  -> 技术分析、风险与市场上下文
  -> LLM 或 Agent 编排
  -> 结构化报告与 Markdown 渲染
  -> 历史存储、Web 展示和通知分发
```

核心编排在 `backend/src/core/`；分析与报告相关服务在 `backend/src/services/`；数据源适配器在 `backend/data_provider/`。数据源失败、超时或字段缺失应形成局部降级，而不是无条件终止全链路。

## Agent

`backend/src/agent/` 管理对话、上下文、技能、策略和工具注册。Agent 可以请求行情、分析、搜索与回测工具，并由执行器控制步数、模型调用和上下文预算。Agent 对话与普通报告共享数据服务和 LLM 路由，但有独立的会话与记忆生命周期。

## 数据、存储与迁移

SQLAlchemy 模型在 `backend/src/storage/models/`，仓储层在 `backend/src/repositories/`，业务服务不应直接扩散数据库访问逻辑。数据库 schema 的新增或变更必须由 `backend/alembic/versions/` 中的新 Alembic migration 完成；不允许在生产路径调用 `create_all()` 或手写 DDL。

文件型运行产物位于 `data/`、`logs/` 和 `reports/`。它们包含缓存、数据库、日志或报告，部署和迁移前应纳入备份计划。

## 前端与认证

React Web 从 `frontend/web/src/` 构建，按路由懒加载页面，并通过 API 客户端访问 `/api/v1`。认证中间件负责把会话和用户状态附加到请求；启用多用户模式后，服务层与仓储层必须根据当前用户隔离可见数据。桌面端打包同一份 Web 产物，并启动或连接本地后端。

## 通知与 Bot

通知服务聚合多个 sender，并负责路由、去重、冷却与错误隔离。Bot 平台接入统一进入 `backend/bot/` 的处理和分发链路，最终调用既有分析或 Agent 服务。外部回调必须经过平台要求的签名校验，并在公网部署时受 HTTPS 与网络边界保护。
