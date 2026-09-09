# API 说明

## 入口

启动服务：

```bash
uv run --locked python backend/main.py --serve-only
```

开发环境默认在 `http://127.0.0.1:8000` 提供服务。实时 OpenAPI 文档位于：

- `/docs`：Swagger UI
- `/redoc`：ReDoc
- `/openapi.json`：机器可读规范

运行时 OpenAPI 是接口字段、认证要求和响应模型的权威来源。不要依赖仓库中历史导出的 JSON 作为当前契约。

## 版本与资源组

业务 API 使用 `/api/v1` 前缀，主要资源组包括：

| 资源组 | 责任 |
| --- | --- |
| `auth`、`account` | 管理员认证与多用户账户。 |
| `analysis`、`history`、`stocks` | 分析发起、任务/历史查询与股票信息。 |
| `agent`、`intelligence` | 多轮问股、智能情报和运行上下文。 |
| `decision-signals` | 决策信号、反馈、复评与跟踪结果。 |
| `portfolio`、`alerts` | 组合、持仓和告警规则。 |
| `screening`、`stock-selection`、`backtest` | 选股、候选分析和策略回测。 |
| `system`、`usage`、`admin` | 配置、用量与管理能力。 |
| `billing`、`credits`、`research-reports` | 计费、额度和研究报告。 |

各组的实际 path、HTTP 方法和 schema 以 `/docs` 为准。

## 认证与兼容性

认证由后端中间件统一处理。未启用用户模式时，部分本地管理路径可按当前服务配置访问；启用认证后，客户端应保留服务设置的安全 Cookie，并处理未认证、无权限和配额用尽响应。不要在浏览器或脚本中硬编码会话令牌。

新增 API 优先追加字段和路径，避免静默破坏已发布客户端。修改枚举、认证、任务状态或报告载荷时，必须同步更新 Web、桌面端和相关契约测试。
