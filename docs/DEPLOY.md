# 🚀 部署指南

本文档介绍如何将 A股自选股智能分析系统部署到服务器。

## 📋 部署方案对比

| 方案 | 优点 | 缺点 | 推荐场景 |
|------|------|------|----------|
| **Docker Compose** ⭐ | 一键部署、环境隔离、易迁移、易升级 | 需要安装 Docker | **推荐**：大多数场景 |
| **直接部署** | 简单直接、无额外依赖 | 环境依赖、迁移麻烦 | 临时测试 |
| **Systemd 服务** | 系统级管理、开机自启 | 配置繁琐 | 长期稳定运行 |
| **Supervisor** | 进程管理、自动重启 | 需要额外安装 | 多进程管理 |

**结论：推荐使用 Docker Compose，迁移最快最方便！**

---

## 🐳 方案一：Docker Compose 部署（推荐）

### 1. 安装 Docker

```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# CentOS
sudo yum install -y docker
sudo systemctl start docker
sudo systemctl enable docker

# 确认服务器支持 Docker Compose V2
docker compose version
```

### 2. 准备配置文件

```bash
# 克隆代码（或上传代码到服务器）
git clone <your-repo-url> /opt/stock-analyzer
cd /opt/stock-analyzer

# 复制并编辑配置文件
cp .env.example .env
vim .env  # 填入真实的 API Key 等配置
```

如果 Docker 构建时下载 npm / apt 依赖较慢，默认 Compose 构建参数已使用国内镜像源；也可以在 `.env` 中覆盖：

```env
DOCKER_NPM_REGISTRY=https://registry.npmmirror.com
DOCKER_APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian
DOCKER_APT_SECURITY_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian-security
```

Python 包下载地址由已提交的 `uv.lock` 固定。不要在 `uv sync --locked` 时覆盖 `UV_DEFAULT_INDEX`，否则 uv 会把索引来源变化视为需要重新锁定并终止构建。

### 3. 一键启动

```bash
# 构建并启动（同时包含定时分析和 Web 界面服务）
docker compose -f ./docker/docker-compose.yml up -d

# 查看日志
docker compose -f ./docker/docker-compose.yml logs -f

# 查看运行状态
docker compose -f ./docker/docker-compose.yml ps
```

启动成功后，在浏览器输入 `http://服务器公网IP:8000` 即可打开 Web 管理界面。如果打不开，记得先在云服务器控制台的「安全组」里放行 8000 端口。

> 不知道怎么访问？→ [云服务器 Web 界面访问指南](deploy-webui-cloud.md)

### 4. 常用管理命令

```bash
# 停止服务
docker compose -f ./docker/docker-compose.yml down

# 重启服务
docker compose -f ./docker/docker-compose.yml restart

# 更新代码后重新部署
git pull
docker compose -f ./docker/docker-compose.yml build --no-cache
docker compose -f ./docker/docker-compose.yml up -d

# 进入容器调试
docker compose -f ./docker/docker-compose.yml exec -u dsa stock-analyzer bash

# 手动执行一次分析
docker compose -f ./docker/docker-compose.yml exec -u dsa stock-analyzer python backend/main.py --no-notify
```

### 5. 数据持久化

数据自动保存在宿主机目录：
- `./data/` - 数据库文件
- `./logs/` - 日志文件
- `./reports/` - 分析报告

### 6. 权限说明

Docker 镜像启动入口会自动创建并修复 `./data`、`./logs`、`./reports` 对应挂载目录的权限，然后降权为非 root 用户 (`dsa`, UID 1000) 运行应用。普通部署不需要手动 `chown` / `chmod`。

如果你显式指定了 `--user` / Compose `user:`，或使用只读挂载、rootless Docker、NFS 等不允许容器修复属主的环境，请确保实际运行用户对这些目录具备写入权限。

---

## 🖥️ 方案二：直接部署

### 1. 安装 Python 环境

```bash
# 安装 Python 3.10+ 与 curl
sudo apt update
sudo apt install -y python3.10 curl

# 安装 uv（也可使用发行版提供的 uv 包）
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
```

### 2. 安装依赖

```bash
cd /opt/stock-analyzer
uv sync --locked --no-dev
```

### 3. 配置环境变量

```bash
cp .env.example .env
vim .env  # 填入配置
```

### 4. 运行

```bash
# 单次运行
uv run --locked --no-dev python backend/main.py

# 定时任务模式（前台运行）
uv run --locked --no-dev python backend/main.py --schedule

# 后台运行（使用 nohup）
nohup uv run --locked --no-dev python backend/main.py --schedule > /dev/null 2>&1 &

# 启动 Web 管理界面（云服务器需先在 .env 中设置 WEBUI_HOST=0.0.0.0）
uv run --locked --no-dev python backend/main.py --webui-only

# 启动 Web 界面（启动时执行一次分析；需每日定时请加 --schedule 或设 SCHEDULE_ENABLED=true）
uv run --locked --no-dev python backend/main.py --webui
```

> 不知道怎么访问？→ [云服务器 Web 界面访问指南](deploy-webui-cloud.md)

---

## 🔧 方案三：Systemd 服务

创建 systemd 服务文件实现开机自启和自动重启：

### 1. 创建服务文件

```bash
sudo vim /etc/systemd/system/stock-analyzer.service
```

内容：
```ini
[Unit]
Description=A股自选股智能分析系统
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/stock-analyzer
Environment="PATH=/opt/stock-analyzer/.venv/bin"
ExecStart=/opt/stock-analyzer/.venv/bin/python backend/main.py --schedule
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

### 2. 启动服务

```bash
# 重载配置
sudo systemctl daemon-reload

# 启动服务
sudo systemctl start stock-analyzer

# 开机自启
sudo systemctl enable stock-analyzer

# 查看状态
sudo systemctl status stock-analyzer

# 查看日志
journalctl -u stock-analyzer -f
```

---

## ⚙️ 配置说明

### 必须配置项

| 配置项 | 说明 | 获取方式 |
|--------|------|----------|
| `ANSPIRE_API_KEYS` / `AIHUBMIX_KEY` / `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | AI 模型至少配置一个；推荐优先 Anspire 或 AIHubMix | 对应服务商控制台 |
| `STOCK_LIST` | 自选股列表 | 逗号分隔的股票代码 |
| 通知渠道 | 至少配置一个，如企业微信、飞书、Telegram 或邮件 | 对应通知平台 |

### 可选配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `SCHEDULE_ENABLED` | `false` | 是否启用定时任务 |
| `SCHEDULE_TIME` | `18:00` | 每日执行时间 |
| `MARKET_REVIEW_ENABLED` | `true` | 是否启用大盘复盘 |
| `ANSPIRE_API_KEYS` | - | Anspire 大模型与新闻搜索（推荐） |
| `AIHUBMIX_KEY` | - | AIHubMix 一 Key 多模型（推荐） |
| `SERPAPI_API_KEYS` | - | SerpAPI 实时金融新闻搜索（推荐） |
| `TAVILY_API_KEYS` | - | Tavily 新闻搜索（可选） |
| `MINIMAX_API_KEYS` | - | MiniMax 搜索（可选） |

---

## 🌐 代理配置

如果服务器在国内，访问 Gemini API 需要代理：

### Docker 方式

编辑 `docker-compose.yml`：
```yaml
environment:
  - http_proxy=http://your-proxy:port
  - https_proxy=http://your-proxy:port
```

### 直接部署方式

编辑 `main.py` 顶部：
```python
os.environ["http_proxy"] = "http://your-proxy:port"
os.environ["https_proxy"] = "http://your-proxy:port"
```

---

## 📊 监控与维护

### 日志查看

```bash
# Docker 方式
docker compose -f ./docker/docker-compose.yml logs -f --tail=100

# 直接部署
tail -f /opt/stock-analyzer/logs/stock_analysis_*.log
```

### 健康检查

```bash
# 检查进程
ps aux | grep main.py

# 检查最近的报告
ls -la /opt/stock-analyzer/reports/
```

### 定期维护

```bash
# 清理旧日志（保留7天）
find /opt/stock-analyzer/logs -mtime +7 -delete

# 清理旧报告（保留30天）
find /opt/stock-analyzer/reports -mtime +30 -delete
```

---

## ❓ 常见问题

### 1. Docker 构建失败

```bash
# 清理缓存重新构建
docker compose -f ./docker/docker-compose.yml build --no-cache
```

### 2. API 访问超时

检查代理配置，确保服务器能访问 Gemini API。

### 3. 版本升级后数据库 schema 未更新

Docker 镜像启动应用主进程前会先运行 `alembic upgrade head`；直接使用 `uv run --locked --no-dev python backend/main.py ...` 启动时，后端会在首次初始化数据库连接时兜底执行同一迁移。若为首次从不含 Alembic 的旧版本升级，需在启动前手动打一次基线标记：

```bash
uv run --locked --no-dev alembic stamp b0bc3c721ef0
```

之后正常启动即可，后续迁移均自动执行。Docker Compose 同时启动 `analyzer` 和 `server` 时，entrypoint 会用 `/app/data/.dsa-startup-migration.lock` 串行化启动期迁移，避免两个容器同时升级 schema。

### 4. 数据库锁定

```bash
# 停止服务后删除 lock 文件
rm /opt/stock-analyzer/data/*.lock
```

### 5. 内存不足

调整 `docker-compose.yml` 中的内存限制：
```yaml
deploy:
  resources:
    limits:
      memory: 1G
```

### 6. WebUI 打开后 UI 元素异常变大 / 布局错乱

**症状**：能访问 8000 端口，但页面上的文字、按钮、卡片异常放大，没有正常布局。

**根因**：`static/index.html` 存在，但 CSS/JS 资源文件缺失（`static/assets/` 为空或不存在），浏览器无法加载样式与脚本，导致裸 HTML 渲染。

**解决方法**：

- **Docker 部署**：执行以下命令重新构建镜像（确保前端已正确打包进镜像）：
  ```bash
  docker compose -f ./docker/docker-compose.yml down
  docker compose -f ./docker/docker-compose.yml build --no-cache
  docker compose -f ./docker/docker-compose.yml up -d
  ```
  构建完成后刷新浏览器缓存（`Ctrl+Shift+R`）再访问。

- **直接部署（uv + Python）**：先构建前端，再启动服务：
  ```bash
  # 安装 Node.js 18+（推荐 20+，如尚未安装）
  # 构建前端
  cd frontend/web
  npm ci
  npm run build
  cd ../..
  # 启动服务
  uv run --locked --no-dev python backend/main.py --webui-only
  ```

**验证**：用浏览器开发者工具（F12 → Network）检查是否有 `/assets/index-*.js` 和 `/assets/index-*.css` 的 404 错误；如有，说明资源缺失，按上述步骤重新构建即可。

### 7. Docker 启动时报 `docker-entrypoint.sh: no such file or directory`

**症状**：

```text
exec /usr/local/bin/docker-entrypoint.sh: no such file or directory
```

**根因**：在 Windows 环境中，如果 `docker/entrypoint.sh` 被检出或编辑成 CRLF 行尾，Linux 容器会把 shebang 解释为 `/bin/sh\r`，表现为入口脚本“找不到”。仓库已在 `.gitattributes` 中声明 `*.sh` 和 `docker/entrypoint.sh` 使用 LF，但旧工作区或旧镜像仍可能保留 CRLF。

**解决方法**：

```bash
git checkout -- docker/entrypoint.sh docker/Dockerfile
git ls-files --eol docker/entrypoint.sh docker/Dockerfile
docker compose -f ./docker/docker-compose.yml build --no-cache
docker compose -f ./docker/docker-compose.yml up -d
```

`git ls-files --eol` 中 `docker/entrypoint.sh` 应显示 `w/lf`。如果仍为 `w/crlf`，请先将该文件转换为 LF 后再重新构建镜像。

### 8. Docker 启动时报 `/app/main.py` 不存在

**症状**：

```text
python: can't open file '/app/main.py': [Errno 2] No such file or directory
```

**根因**：当前镜像复制真实后端代码到 `/app/backend/`，容器内入口应使用 `backend/main.py`。如果 `docker-compose.yml` 仍覆盖为 `python main.py ...`，就会查找不存在的 `/app/main.py`。

**解决方法**：确认 `docker/docker-compose.yml` 的 `server` 服务命令为：

```yaml
command: ["python", "backend/main.py", "--serve-only", "--host", "0.0.0.0", "--port", "${API_PORT:-8000}"]
```

修改后重建容器：

```bash
docker compose -f ./docker/docker-compose.yml up -d --force-recreate server
```

### 9. Docker 中 MySQL 不能使用 `localhost`

**症状**：

```text
Can't connect to MySQL server on 'localhost'
```

**根因**：容器内的 `localhost` 指向当前应用容器本身，不是宿主机，也不是其他 MySQL 容器。只要 `.env` 配置了 `DATABASE_URL`，`DATABASE_PATH` 会被忽略。

**推荐配置**：

- **使用默认 SQLite**：清空或注释 `DATABASE_URL`，并使用容器内持久化路径。

  ```env
  DATABASE_URL=
  DATABASE_PATH=/app/data/stock_analysis.db
  ```

- **MySQL 在宿主机上**：将 `localhost` 改为 Docker 访问宿主机的地址。

  ```env
  DATABASE_URL=mysql+pymysql://user:password@host.docker.internal:3306/dsa_db
  ```

- **MySQL 也在 Compose 内**：将主机名改为 MySQL 服务名，例如 `mysql`。

  ```env
  DATABASE_URL=mysql+pymysql://user:password@mysql:3306/dsa_db
  ```

改完 `.env` 后重启服务：

```bash
docker compose -f ./docker/docker-compose.yml up -d --force-recreate server
docker compose -f ./docker/docker-compose.yml logs -f server
```

---

## 🔄 快速迁移

从一台服务器迁移到另一台：

```bash
# 源服务器：打包
cd /opt/stock-analyzer
tar -czvf stock-analyzer-backup.tar.gz .env data/ logs/ reports/

# 目标服务器：部署
mkdir -p /opt/stock-analyzer
cd /opt/stock-analyzer
git clone <your-repo-url> .
tar -xzvf stock-analyzer-backup.tar.gz
docker compose -f ./docker/docker-compose.yml up -d
```

---

## 🌐 云服务器上部署了，但不知道怎么用浏览器访问？

详见 → [云服务器 Web 界面访问指南](deploy-webui-cloud.md)

涵盖：直接部署和 Docker 两种方式的启动与访问、安全组/防火墙配置、常见问题排查、Nginx 反向代理（可选）。

---

**祝部署顺利！🎉**
