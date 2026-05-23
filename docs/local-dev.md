# 本地启动指南

本文档说明如何在本地启动完整的前后端服务，适用于本地开发、调试和体验。

---

## 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | 3.10+ | 后端运行时 |
| Node.js | 20.19+ 或 22.12+（推荐 22 LTS） | 前端构建与开发服务器，Vite 7 要求 |
| pip | 最新即可 | Python 包管理 |
| npm | 随 Node.js 附带 | 前端包管理 |

---

## 一、基础准备

### 1. 克隆代码

```bash
git clone https://github.com/ZhuLinsen/daily_stock_analysis.git
cd daily_stock_analysis
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，至少填写：

| 变量 | 说明 |
|------|------|
| `ANSPIRE_API_KEYS` 或其他 AI Key | AI 大模型，至少配置一个（[LLM 配置指南](LLM_CONFIG_GUIDE.md)） |
| `STOCK_LIST` | 自选股代码，如 `600519,hk00700,AAPL` |

> 通知渠道（微信/飞书/邮件等）可选，不配置也能本地运行。

### 3. 安装后端依赖

```bash
pip install -r requirements.txt
```

### 4. 数据库初始化

**全新部署**：后端启动时会自动建表并运行 Alembic 迁移，无需额外操作。

**从旧版本升级**（首次引入 Alembic 前已有数据库）：在启动服务前运行一次：

```bash
alembic stamp b0bc3c721ef0
```

此后每次服务启动都会自动执行 `alembic upgrade head`，应用所有新增迁移。

---

## 二、启动方式

### 方式 A：开发模式（前后端分离，推荐调试时使用）

前后端分别启动，前端带热重载，适合修改 UI 代码时使用。

**终端 1 - 启动后端（仅 API 服务，端口 8000）**

```bash
python backend/main.py --serve-only
```

`--serve-only` 只启动 FastAPI API，不会安装、构建或托管 Web 前端静态资源。

或用 uvicorn 带热重载：

```bash
uvicorn backend.backend.server:app --reload --host 127.0.0.1 --port 8000
```

**终端 2 - 启动前端开发服务器（端口 5200）**

```bash
cd frontend/web
npm ci
npm run dev
```

前端开发服务器会自动将 `/api/*` 请求代理到后端 `http://127.0.0.1:8000`，无需额外配置跨域。注册验证邮件中的前端页面链接默认指向 `http://localhost:5200`；如需改成自定义域名，可设置 `USER_FRONTEND_BASE_URL`。

**访问地址**

| 服务 | 地址 |
|------|------|
| 前端（带热重载） | http://localhost:5200 |
| 后端 API | http://localhost:8000 |
| API 文档（Swagger） | http://localhost:8000/docs |

---

### 方式 B：生产模式 / 本地体验（前端构建后由后端统一托管）

前端构建输出到 `static/`，后端在 8000 端口同时托管 API 和静态文件，只需访问一个地址。显式 WebUI 启动路径会按需检查并构建前端资源；如果你希望构建过程可控，建议先手动执行步骤 1。

**步骤 1 - 构建前端**

```bash
cd frontend/web
npm ci
npm run build
cd ../..
```

构建产物输出到项目根目录的 `static/` 文件夹。

**步骤 2 - 启动 WebUI 服务（同时托管前端静态文件）**

```bash
python backend/main.py --webui-only
```

**访问地址**

| 服务 | 地址 |
|------|------|
| Web 界面 + API | http://localhost:8000 |
| API 文档（Swagger） | http://localhost:8000/docs |

> 如果页面样式异常（布局错乱、元素放大），说明 `static/assets/` 为空，重新执行步骤 1 构建前端即可。详见 [部署指南 FAQ](DEPLOY.md#5-webui-打开后-ui-元素异常变大--布局错乱)。

---

## 三、常用命令速查

### 数据库迁移（Alembic）

```bash
# 查看当前数据库版本
alembic current

# 手动应用所有待执行迁移
alembic upgrade head

# 回滚一步
alembic downgrade -1

# 查看迁移历史
alembic history

# 为存量库打基线标记（从旧版升级，只需运行一次）
alembic stamp b0bc3c721ef0

# 新增 schema 变更后生成迁移文件（修改 ORM model 后执行）
alembic revision --autogenerate -m "describe_change"
```

> 服务启动时会自动对文件型 SQLite 和网络数据库执行 `alembic upgrade head`，通常无需手动触发。

### 后端

```bash
# 仅启动 API 服务（不执行分析）
python backend/main.py --serve-only

# 仅启动 WebUI 服务（不执行分析，会按需准备并托管前端静态资源）
python backend/main.py --webui-only

# 启动 WebUI 服务 + 立即执行一次分析
python backend/main.py --webui

# 启动 API 服务 + 立即执行一次分析
python backend/main.py --serve

# 启动 API 服务 + 定时任务
python backend/main.py --serve --schedule

# 直接执行一次分析（不启动 API）
python backend/main.py

# 调试模式（输出更多日志）
python backend/main.py --debug

# 干跑模式（不实际调用 AI，用于测试流程）
python backend/main.py --dry-run

# 仅分析指定股票
python backend/main.py --stocks 600519,hk00700,AAPL
```

### 前端

```bash
cd frontend/web

# 安装依赖
npm ci

# 启动开发服务器（热重载，端口 5200）
npm run dev

# 构建生产包（输出到 ../../static/）
npm run build

# 代码检查
npm run lint
```

---

## 四、常见问题

**Q：后端启动报 `ModuleNotFoundError`**

```bash
pip install -r requirements.txt
```

**Q：前端 `npm ci` 报错**

确认 Node.js 版本 ≥ 20.19（或 ≥ 22.12），Vite 7 要求，建议使用 22 LTS：

```bash
node -v
```

**Q：访问 `http://localhost:8000` 返回 404**

确认已执行 `npm run build` 并在 `static/` 目录下存在 `index.html`：

```bash
ls static/
```

**Q：API 调用 401 / 403**

系统默认启用多用户认证。直接访问 Web 界面注册账号后即可正常使用，或参考 [To C 多用户模式](to-c-mode.md)。

**Q：如何在本地关闭调试日志**

`.env` 中设置 `LOG_LEVEL=WARNING`。

---

## 延伸阅读

- [完整配置与部署指南](full-guide.md) - 所有环境变量、数据源、通知等配置
- [LLM 配置指南](LLM_CONFIG_GUIDE.md) - 大模型渠道配置
- [部署指南](DEPLOY.md) - Docker / 服务器 / GitHub Actions 部署
- [桌面端打包说明](desktop-package.md) - Electron 桌面端构建
- [AGENTS.md §9 数据库迁移](../AGENTS.md) - Alembic 工作流与规范
