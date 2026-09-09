# 文档中心

Daily Stock Analysis 是一个面向 A 股、港股和美股的智能分析系统。它把行情、技术指标、基本面、新闻与大模型分析组合为报告，并提供 Web、桌面端、API、定时任务与消息通知入口。

> 本项目仅用于研究和辅助判断，不构成投资建议。市场数据可能延迟、缺失或来自降级数据源。

## 从这里开始

| 你的目标 | 阅读顺序 |
| --- | --- |
| 第一次运行项目 | [快速开始](getting-started.md) -> [配置说明](configuration.md) |
| 使用 Web、分析、问股或选股 | [功能说明](features.md) -> [配置说明](configuration.md) |
| 部署、定时运行或排障 | [运行与部署](operations.md) -> [常见问题](FAQ.md) |
| 理解或修改代码 | [架构说明](architecture.md) -> [开发指南](development.md) |
| 调用 HTTP API | [API 说明](api.md) -> 服务的 `/docs` |

## 文档地图

### 使用与运行

- [快速开始](getting-started.md)：本地安装、首次配置和常用命令。
- [配置说明](configuration.md)：LLM、数据源、通知、认证与配置优先级。
- [功能说明](features.md)：报告、Agent、情报、组合、告警、选股、回测和 Bot。
- [运行与部署](operations.md)：Docker Compose、生产运行、备份、日志和故障排查。
- [常见问题](FAQ.md)：常见的环境、模型、数据与通知问题。

### 开发与接口

- [架构说明](architecture.md)：系统边界、主流程、模块职责、数据与任务生命周期。
- [开发指南](development.md)：开发环境、测试、迁移和贡献边界。
- [API 说明](api.md)：版本化 API、认证和 OpenAPI 使用方式。
- [贡献指南](CONTRIBUTING.md)：Issue、PR 与协作规范。

### 参考资料

- [环境变量模板](../.env.example)：全部可配置字段及示例，配置的唯一字段真源。
- [变更日志](CHANGELOG.md)：版本与未发布变更。
- [图片识别 Prompt](image-extract-prompt.md)：图片导入使用的完整 Prompt。
- [OpenClaw 集成](openclaw-skill-integration.md)：外部 Skill 集成说明。

## 真源约定

代码行为以 `backend/`、`frontend/` 和测试为准；完整配置字段以 `.env.example` 为准；运行时接口以启动后的 OpenAPI 页面为准。文档提供稳定的概念、流程和操作路径，不重复维护会随代码频繁变化的字段清单或接口清单。
