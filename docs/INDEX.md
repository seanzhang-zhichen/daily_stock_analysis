# 文档中心

这里保留项目当前维护中的中文文档入口。README 负责项目概览和快速开始；配置、部署、专题说明和开发资料从这里进入。

## 常用入口

| 我想要 | 先看 | 继续看 |
| --- | --- | --- |
| 快速了解项目和启动方式 | [README](../README.md) | [完整配置与部署指南](full-guide.md) |
| 本地开发和 WebUI 启动 | [本地启动指南](local-dev.md) | [后端理解指南](backend/) |
| 给非开发用户安装配置 | [小白客户端安装与配置](beginner-client-setup.md) | [FAQ](FAQ.md) |
| 配置大模型渠道 | [LLM 配置指南](LLM_CONFIG_GUIDE.md) | [LLM 服务商配置指南](llm-providers.md) |
| 配置通知推送 | [通知能力基线](notifications.md) | [Bot 命令与接入](bot-command.md) |
| 部署到服务器或云平台 | [部署指南](DEPLOY.md) | [云端 WebUI 部署](deploy-webui-cloud.md) |
| 排查问题 | [FAQ](FAQ.md) | [更新日志](CHANGELOG.md) |
| 参与开发 | [贡献指南](CONTRIBUTING.md) | [后端理解指南](backend/) |

## 核心文档

| 文档 | 内容 |
| --- | --- |
| [完整配置与部署指南](full-guide.md) | 环境准备、运行方式、配置说明、部署路径和常见问题。 |
| [FAQ](FAQ.md) | 常见配置、模型、通知、部署和运行问题。 |
| [更新日志](CHANGELOG.md) | 版本变化、能力调整和迁移说明。 |
| [产品路线与维护边界](product-roadmap.md) | 当前产品方向、已落地能力、近期维护重点和文档边界。 |

## 配置与集成

| 文档 | 内容 |
| --- | --- |
| [LLM 配置指南](LLM_CONFIG_GUIDE.md) | 大模型渠道、三层配置、Web 设置页和常见模型配置。 |
| [LLM 服务商配置指南](llm-providers.md) | Provider 预设、服务器环境配置、错误分类和诊断建议。 |
| [LiteLLM YAML 示例](examples/litellm_config.example.yaml) | LiteLLM 多渠道配置示例。 |
| [通知能力基线](notifications.md) | 企业微信、飞书、Telegram、Discord、Slack、邮件等通知渠道配置。 |
| [Bot 命令与接入](bot-command.md) | Bot 命令、Webhook、平台接入和回调说明。 |
| [Bot 平台配置](bot/) | 飞书、钉钉、Discord 等 Bot 配置截图和补充说明。 |
| [Tushare 股票列表指南](TUSHARE_STOCK_LIST_GUIDE.md) | Tushare 股票列表相关配置和使用说明。 |

## 部署与客户端

| 文档 | 内容 |
| --- | --- |
| [本地启动指南](local-dev.md) | 本地前后端启动、常用命令和常见问题。 |
| [小白客户端安装与配置](beginner-client-setup.md) | 面向不会代码用户的客户端安装、模型和新闻源配置。 |
| [部署指南](DEPLOY.md) | 服务器部署、Docker、systemd、Supervisor 等部署方式。 |
| [云端 WebUI 部署](deploy-webui-cloud.md) | 云服务器访问 WebUI 的部署说明。 |
| [Zeabur 部署](docker/zeabur-deployment.md) | Zeabur 平台部署说明。 |
| [桌面端打包说明](desktop-package.md) | Electron 桌面端和 Web 构建产物打包说明。 |

## 功能专题

| 文档 | 内容 |
| --- | --- |
| [To C 多用户模式](to-c-mode.md) | 多用户认证、API、表结构、配额与数据隔离边界。 |
| [实时告警中心](alerts.md) | 告警规则、技术指标、持仓联动、Market Light、通知冷却与部署边界。 |
| [AI 建议](decision-signals.md) | 分析建议的结构化提取、查询、生命周期、用户隔离与回滚。 |
| [图片识别 Prompt](image-extract-prompt.md) | 图片识别股票信息的 Prompt 与使用边界。 |
| [OpenClaw Skill 集成](openclaw-skill-integration.md) | OpenClaw / Skill 外部集成说明。 |

## 开发参考

| 文档 | 内容 |
| --- | --- |
| [后端理解指南](backend/) | 后端架构、API、数据管道、存储模型、To C 用户体系与计费说明。 |
| [后端架构总览](backend/overview.md) | 后端目录、启动入口、FastAPI、分析管道、Agent、数据源、存储和通知总览。 |
| [后端 API 层](backend/api.md) | 认证、依赖注入、接口分组、错误响应和主要 API 行为。 |
| [后端数据管道](backend/data-pipeline.md) | 股票代码规范化、数据源 fallback、LLM/Agent 分析、异步任务和通知链路。 |
| [首页股票输入到分析任务流程](backend/home-stock-analysis-flow.md) | 首页输入股票代码或名称后，前端补全、分析 API、异步任务队列、SSE 和 pipeline 如何串联。 |
| [后端存储层](backend/storage.md) | SQLAlchemy 模型、Repository、Alembic migration 与用户数据隔离边界。 |
| [后端用户体系与计费](backend/user-system.md) | 注册登录、Session、套餐、配额、订单支付、退款发票和管理员能力。 |
| [API 规格](architecture/api_spec.json) | FastAPI OpenAPI 规格产物。 |
| [贡献指南](CONTRIBUTING.md) | Issue、PR、测试和文档协作要求。 |
