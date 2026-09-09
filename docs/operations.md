# 运行与部署

## 推荐方式：Docker Compose

Docker 配置位于 `docker/`。首次部署：

```bash
cp .env.example .env
# 编辑 .env，至少配置 LLM 和所需的股票范围
docker compose -f docker/docker-compose.yml up -d --build
docker compose -f docker/docker-compose.yml ps
```

Compose 定义两个服务：`analyzer` 用于默认的定时分析，`server` 用于 API 和 Web UI。按需启动：

```bash
docker compose -f docker/docker-compose.yml up -d analyzer
docker compose -f docker/docker-compose.yml up -d server
docker compose -f docker/docker-compose.yml logs -f server
```

服务端口由 `API_PORT` 决定。容器内服务必须监听 `0.0.0.0`；公网访问时由反向代理提供 TLS、域名、访问控制和限流，不要直接暴露管理接口与未加固的数据库。

## 非容器运行

使用虚拟环境安装后，以进程管理器运行：

```bash
uv sync --locked
uv run --locked python backend/main.py --serve-only
```

长期运行的定时任务使用：

```bash
uv run --locked python backend/main.py --schedule
```

生产进程应由 systemd、Supervisor、容器编排器或等价工具托管，并具备重启策略、日志轮转和独立的运行用户。不要把开发环境的自动重载模式用于生产。

## 升级与回滚

1. 备份 `.env`、`data/`、`reports/` 和必要日志。
2. 更新代码或镜像，并用锁文件重新构建依赖。
3. 在发布前运行数据库迁移：`uv run --locked alembic upgrade head`。
4. 重启服务，检查 `/api/health` 和 `/docs`。
5. 通过一次小范围、不通知的分析验证数据源和 LLM。

回滚应用版本前先确认新 migration 是否可安全降级。业务数据和 schema 并不总能无损回退；必要时从部署前备份恢复，而不是删除生产目录。

## 日常检查

| 检查项 | 方法 |
| --- | --- |
| 服务可达性 | `GET /api/health`，或访问 `/docs`。 |
| 配置问题 | `uv run --locked python backend/main.py --check-notify`，并检查启动日志。 |
| 数据源与模型 | 运行一只股票的 `--no-notify` 分析，检查报告中的数据与降级信息。 |
| 容器状态 | `docker compose -f docker/docker-compose.yml ps` 和 `logs`。 |
| 数据增长 | 监控 `data/`、`logs/`、`reports/` 的容量与备份是否成功。 |

## 常见故障定位

- 服务无法启动：先检查 `.env` 格式、端口占用、Python/Node 版本和启动日志。
- 分析无结果或不完整：检查对应市场的数据源凭据、网络、限流与后备源日志。
- LLM 失败：从单模型配置开始，检查模型名、Base URL、协议和 API Key，再增加回退。
- 没有收到消息：运行 `--check-notify`，检查渠道凭据、路由、静默时段、去重和外部平台日志。
- Web 页面加载失败：确认 `server` 可访问、Web 已构建，或在开发时确认 Vite API 代理。

更多具体问答见[常见问题](FAQ.md)。
