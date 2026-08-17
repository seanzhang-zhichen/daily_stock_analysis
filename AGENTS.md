# AGENTS.md

本文件用于约束本仓库的默认开发流程，目标是减少重复沟通、减少返工，并让改动和当前项目结构保持一致。

如果本文件与仓库中的脚本、工作流、代码现状不一致，以实际可执行内容为准，并在相关改动中顺手修正文档，避免规则继续漂移。

## 1. 硬规则

- 遵循现有目录边界：
  - 后端代码放在 `backend/`，不要在根目录新增平行的 `src/`、`data_provider/`、`api/`、`bot/` 兼容实现
  - Web 前端改动在 `frontend/web/`
  - 桌面端改动在 `frontend/desktop/`
  - 部署与自动化改动在 `scripts/`、`docker/`；若恢复 GitHub Actions，则工作流放在 `.github/workflows/`
- 未经明确确认，不执行 `git pull`、`git commit`、`git tag`、`git push`。
- commit message 使用英文，不添加 `Co-Authored-By`。
- 不写死密钥、账号、路径、模型名、端口或环境差异逻辑。
- 优先复用现有模块、配置入口、脚本和测试，不新增平行实现。
- 默认稳定性优先于“顺手优化”；非当前任务直接需要的重构、抽象和基础设施迁移一律克制。
- 新增配置项时，必须同步更新 `.env.example` 和相关文档。
- 涉及用户可见能力、CLI/API 行为、部署方式、通知方式、报告结构变化时，必须同步更新相关文档与 `docs/CHANGELOG.md`。
- `docs/CHANGELOG.md` 的 `[Unreleased]` 段使用**扁平格式**：每条独立一行，格式为 `- [类型] 描述`，类型取值：`新功能`/`改进`/`修复`/`文档`/`测试`/`chore`；**禁止在 `[Unreleased]` 内新增 `### 类目标题`**，以减少并发改动的 merge 冲突。发版时由 maintainer 汇总整理成带标题的正式格式。
- `README.md` 只用于项目定位、核心能力总览、快速开始、主要入口、赞助/合作等首页级信息；非必要不更新 README，避免持续膨胀。
- 更细的模块行为、页面交互、专题配置、排障说明、字段契约、实现语义和边界条件，优先更新对应 `docs/*.md` 或专题文档，不写入 README。
- 变更中英双语文档之一时，需评估另一份是否需要同步；若未同步，交付说明里要写明原因。
- 注释、docstring、日志文案以清晰准确为准，不强制要求英文，但应与文件语境保持一致。
- **所有数据库 schema 变更（新增表、新增列、删除列、修改列类型、新增索引等）必须通过 Alembic migration 完成，禁止直接调用 `Base.metadata.create_all()` 或手写 `ALTER TABLE` 语句来变更生产 schema。** 详见"数据库迁移"一节。

## 2. AI 协作资产治理

- `AGENTS.md` 是仓库内 AI 协作规则的唯一真源。
- `CLAUDE.md` 在 Git 索引中必须是指向 `AGENTS.md` 的软链接，用于兼容 Claude 生态；Windows `core.symlinks=false` 检出为内容仅含 `AGENTS.md` 的普通文件时也视为有效工作树表示。
- 当前仓库未维护 `.github` 指令镜像；未来新增时若与本文件冲突，以 `AGENTS.md` 为准。
- 当前仓库未维护 `.claude/skills/` 协作 skill；本地分析产物可放在忽略的 `.claude/` 下，但不得作为规则真源。
- 根目录 `SKILL.md` 与 `docs/openclaw-skill-integration.md` 属于产品或外部集成说明，不是仓库协作规则真源。
- 若未来新增 `.agents/skills/` 或其他 agent 专用目录，必须先明确单一真源，再通过脚本或镜像同步；禁止手工长期维护多份同义内容。
- 修改 AI 协作治理资产时，执行：

```bash
uv run --locked python scripts/check_ai_assets.py
```

## 3. 仓库速览

- 项目定位：股票智能分析系统，覆盖 A 股、港股、美股。
- 主流程：抓取数据 -> 技术分析/新闻检索 -> LLM 分析 -> 生成报告 -> 通知推送。
- 关键入口：
  - `backend/main.py`：分析、调度和服务启动入口
  - `backend/server.py`：FastAPI ASGI 入口
  - `frontend/web/`：Web 前端
  - `frontend/desktop/`：Electron 桌面端
- 核心职责：
  - `backend/src/core/`：主流程编排
  - `backend/src/services/`：业务服务层
  - `backend/src/repositories/`：数据访问层
  - `backend/src/reports/`：报告生成
  - `backend/src/schemas/`：Schema / 数据结构
  - `backend/data_provider/`：多数据源适配与 fallback
  - `backend/api/`：FastAPI API
  - `backend/bot/`：机器人接入
  - `scripts/`：本地脚本
  - `tests/`：pytest 测试
  - `docs/`：文档与说明

## 4. 常用命令

### 运行应用

```bash
uv sync --locked
uv run --locked python backend/main.py
uv run --locked python backend/main.py --debug
uv run --locked python backend/main.py --dry-run
uv run --locked python backend/main.py --stocks 600519,hk00700,AAPL
uv run --locked python backend/main.py --market-review
uv run --locked python backend/main.py --schedule
uv run --locked python backend/main.py --serve
uv run --locked python backend/main.py --serve-only
uv run --locked uvicorn backend.server:app --reload --host 0.0.0.0 --port 8000
```

### 后端验证

```bash
uv sync --locked
./scripts/ci_gate.sh
uv run --locked python -m pytest -m "not network"
uv run --locked python -m py_compile <changed_python_files>
```

### Python 依赖管理

- 使用 `uv` 作为唯一 Python 包管理器；`pyproject.toml` 是依赖声明真源，`uv.lock` 是跨环境锁文件。
- 新增、升级或删除依赖使用 `uv add`、`uv remove`、`uv lock --upgrade-package <package>` 等命令，并同时提交 `pyproject.toml` 与 `uv.lock`。
- 不新增或手工维护 `requirements.txt`，不直接使用 `pip install` 改变项目环境。
- 本地、CI 与生产部署默认使用 `--locked` 校验 `pyproject.toml` 和 `uv.lock` 一致；生产安装使用 `uv sync --locked --no-dev`。只有明确需要忽略项目声明、完全以现有锁文件为准的受控场景才使用 `--frozen`。

### Web / Desktop

```bash
cd frontend/web
npm ci
npm run lint
npm run build

cd ../desktop
npm install
npm run build
```

## 5. 默认工作流

1. 先判断任务类型：`fix / feat / refactor / docs / chore / test / review`
2. 先读现有实现、配置、测试、脚本、工作流和文档，再动手修改。
3. 识别改动边界：后端 / API / Web / Desktop / Workflow / Docs / AI 协作资产。
4. 先判断是否命中高风险区域：配置语义、API / Schema、数据源 fallback、报告结构、认证、调度、发布流程、桌面端启动链路。
5. 只做和当前任务直接相关的最小改动，不顺手夹带无关重构。
6. 如果发现文档、脚本、工作流描述不一致，优先信任实际代码与工作流，再决定是否顺手修正文档。
7. 改完后按下面的验证矩阵执行检查。
8. 最终交付默认要说明：
   - 改了什么
   - 为什么这么改
   - 验证情况
   - 未验证项
   - 风险点
   - 回滚方式

## 6. 验证矩阵

### CI 覆盖原则

当前检出版本未包含 `.github/workflows/`，因此不能假定 GitHub Actions 已覆盖改动。以本节本地验证矩阵为最低标准；若实际存在可用的远端 CI 结果，可引用其结论，并补充远端未覆盖的改动面。

### 按改动面执行

- Python 后端改动：
  - 后端代码放在 `backend/`，根目录不维护兼容 shim
  - 优先执行：`uv sync --locked && ./scripts/ci_gate.sh`
  - 最低要求：`uv run --locked python -m py_compile <changed_python_files>`
  - 若影响 API、任务编排、报告生成、通知发送、数据源 fallback、认证、调度，交付说明中要写明是否覆盖了对应路径。

- Web 前端改动：
  - 适用范围：`frontend/web/`
  - 默认执行：`cd frontend/web && npm ci && npm run lint && npm run build`
  - 若涉及 API 联调、路由、状态管理、Markdown/图表渲染或认证状态，交付说明中要明确说明联动面和未覆盖风险。

- 桌面端改动：
  - 适用范围：`frontend/desktop/`、`scripts/run-desktop.ps1`、`scripts/build-desktop*.ps1`、`scripts/build-*.sh`、`docs/desktop-package.md`
  - 默认执行：先构建 Web，再构建桌面端
  - 如受平台限制未能完整验证，需要明确说明是否验证了 Web 构建产物、Electron 构建以及 Release 工作流影响。

- API / Schema / 认证联动改动：
  - 适用范围：`backend/api/**`、`backend/src/schemas/**`、`backend/src/services/**`、`frontend/web/**`、`frontend/desktop/**`
  - 至少覆盖对应后端验证 + 受影响客户端构建验证。
  - 若涉及登录、Cookie、会话、轮询状态、字段增删或枚举变化，必须明确写出兼容性影响。

- 文档与治理文件改动：
  - 适用范围：`README.md`、`docs/**`、`AGENTS.md`、`CLAUDE.md`，以及未来可能新增的 `.github/**`、`.claude/skills/**`
  - 不强制代码测试。
  - 需确认命令、配置项、文件名、工作流名称与实际仓库一致。
  - 改动 AI 协作治理资产时，执行 `uv run --locked python scripts/check_ai_assets.py`。

- 工作流 / 脚本 / Docker 改动：
  - 适用范围：`.github/**`、`scripts/**`、`docker/**`
  - 运行最接近改动面的本地验证。
  - 交付时说明影响了哪条流水线、发布路径或部署路径。
  - 若未执行 Docker / GitHub Actions 相关验证，明确说明原因与潜在风险。

- 网络或三方依赖相关改动：
  - 先跑离线或确定性检查。
  - 优先确认 timeout、retry、fallback、异常文案、降级路径是否仍然成立。
  - 若未执行在线验证，必须明确写出原因。

## 7. 稳定性护栏

- 配置与运行入口：
  - 修改 `.env` 语义、默认值、CLI 参数、服务启动方式、调度语义时，要同时评估本地运行、Docker、GitHub Actions、API、Web、Desktop 的影响。
  - 新配置优先做到“不配置也可运行，配置后增强能力”，避免叠加开关和互斥模式。

- 数据源与 fallback：
  - 修改 `backend/data_provider/` 时，要关注数据源优先级、失败降级、字段标准化、缓存与超时策略。
  - 单一数据源失败不应拖垮整个分析流程，除非需求明确要求 fail-fast。

- API / Web / Desktop 兼容：
  - 改 API / Schema / 认证 / 报告载荷时，要同时检查后端、Web、Desktop 的兼容性。
  - 默认优先追加字段、保留旧字段或提供兼容层，避免无提示破坏现有客户端。

- 报告 / Prompt / 通知：
  - 修改报告结构、Prompt、提取器、通知模板、机器人链路时，要检查上游输入与下游消费方是否仍兼容。
  - 单一通知渠道失败不应拖垮整个分析主流程，除非需求明确要求 fail-fast。
  - 修改 `backend/src/services/image_stock_extractor.py` 中 `EXTRACT_PROMPT` 时，要同步更新 `docs/image-extract-prompt.md` 中的完整最新 prompt。

- 工作流 / 发布 / 打包：
  - 修改自动 tag、Release、Docker 发布、日常分析或桌面端打包流程时，要评估触发条件、产物路径、权限边界和回滚方式。
  - 自动 tag 默认保持 opt-in：只有 commit title 含 `#patch`、`#minor`、`#major` 才触发版本号更新，除非需求明确要求改变发布策略。

## 8. 数据库迁移

**原则：所有 schema 变更（新增/修改/删除 表、列、索引）必须通过 Alembic migration 完成。**

### 工具与目录

- 配置文件：`alembic.ini`（项目根目录）
- 环境脚本：`backend/alembic/env.py`（自动读取 `src.config.get_config().get_db_url()`）
- 迁移脚本：`backend/alembic/versions/`（按日期命名，格式 `YYYYMMDD_<rev>_<slug>.py`）

### 常用命令

```bash
# 生成增量迁移（修改 ORM model 后执行）
uv run --locked alembic revision --autogenerate -m "描述变更内容"

# 应用所有 pending 迁移
uv run --locked alembic upgrade head

# 回滚一步
uv run --locked alembic downgrade -1

# 查看当前版本
uv run --locked alembic current

# 查看历史
uv run --locked alembic history

# 为已有数据库打基线标记（引入 Alembic 前的存量库，只需运行一次）
uv run --locked alembic stamp b0bc3c721ef0
```

### 工作流程

1. 修改 `backend/src/storage/models/` 下的 ORM 模型
2. 运行 `uv run --locked alembic revision --autogenerate -m "..."` 生成迁移文件
3. **人工 review** 生成的 `backend/alembic/versions/` 文件，确认 DDL 正确
4. 将迁移文件与模型变更作为同一项变更交付
5. 生产部署时 `uv run --locked alembic upgrade head`（或由 `DatabaseManager` 启动时自动执行）

### 存量数据库升级（首次引入 Alembic）

对使用 `create_all` 创建的已有数据库，只需打一次基线标记，不需要重新建表：

```bash
uv run --locked alembic stamp b0bc3c721ef0
```

之后正常 `uv run --locked alembic upgrade head` 即可应用后续增量迁移。

### 禁止事项

- 禁止在 `_base.py` 外或测试以外的生产代码中调用 `Base.metadata.create_all()`
- 禁止在代码中手写 `ALTER TABLE` / `CREATE TABLE` 等 DDL 语句
- 禁止直接修改已合并的迁移文件（需要修正时，创建新的迁移）

## 9. 交付与发布

- 默认交付结构：
  - `改了什么`
  - `为什么这么改`
  - `验证情况`
  - `未验证项`
  - `风险点`
  - `回滚方式`
- 如果是 `docs` 任务，可直接写：`Docs only, tests not run`，但仍需说明是否核对了命令和文件名。
- 自动 tag 默认不触发，只有 commit title 包含 `#patch`、`#minor`、`#major` 才会触发版本号更新。
- 手动打 tag 必须使用 annotated tag。
- 用户可见变更交付时需补齐验证说明。
