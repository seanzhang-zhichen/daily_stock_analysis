# 快速开始

本指南在本地启动完整的分析服务。首次只需要一个可用的大模型配置；行情与新闻数据源、通知渠道可以后续补充。

## 前置条件

- Python 3.10 或更高版本，以及 [uv](https://docs.astral.sh/uv/)。
- Node.js 20 或更高版本，仅在开发 Web 前端或桌面端时需要。
- 一个可用的 LLM API Key，或本地 Ollama 服务。

## 首次启动

在仓库根目录执行：

```powershell
Copy-Item .env.example .env
uv sync --locked
uv run --locked python backend/main.py --serve-only
```

打开 `http://127.0.0.1:8000/docs` 验证 API 已运行。若已经构建了 Web 静态资源，也可从该服务访问 Web 界面。

在 `.env` 中至少填写一种 LLM 配置后，再运行单次分析：

```powershell
uv run --locked python backend/main.py --stocks 600519,hk00700,AAPL
```

不要把 `.env` 提交到版本库。完整字段与示例见 [`.env.example`](../.env.example)，配置取舍见[配置说明](configuration.md)。

## 常用运行方式

| 目标 | 命令 |
| --- | --- |
| 分析配置中的自选股 | `uv run --locked python backend/main.py` |
| 指定股票 | `uv run --locked python backend/main.py --stocks 600519,hk00700,AAPL` |
| 不发送通知 | `uv run --locked python backend/main.py --no-notify` |
| 大盘复盘 | `uv run --locked python backend/main.py --market-review` |
| 定时任务 | `uv run --locked python backend/main.py --schedule` |
| 仅启动 API | `uv run --locked python backend/main.py --serve-only` |
| API 与一次分析同时运行 | `uv run --locked python backend/main.py --serve` |
| 托管已构建 Web 资源 | `uv run --locked python backend/main.py --webui-only` |

使用 `uv run --locked python backend/main.py --help` 查看当前版本的完整参数。`--webui` 与 `--webui-only` 是兼容入口；新脚本优先使用 `--serve` 或 `--serve-only`，除非需要托管 Web 静态资源。

## Web 开发模式

启动后端：

```powershell
uv run --locked python backend/main.py --serve-only
```

另开一个终端启动 Vite：

```powershell
Set-Location frontend/web
npm ci
npm run dev
```

Vite 默认地址与 API 代理由 `frontend/web` 的配置决定。生产静态文件由 `npm run build` 生成，并由 Docker 构建或 Web 托管入口使用。

## 下一步

1. 在[配置说明](configuration.md)选择 LLM、数据源和通知方式。
2. 阅读[功能说明](features.md)，了解分析结果、Agent、告警和选股的边界。
3. 需要长期运行时，按[运行与部署](operations.md)使用 Docker Compose 或进程管理器。
