# 开发指南

## 本地环境

```powershell
uv sync --locked
Set-Location frontend/web
npm ci
```

后端与前端的启动方式见[快速开始](getting-started.md)。不要使用 `pip install` 改写项目依赖；Python 依赖声明在 `pyproject.toml`，锁定版本在 `uv.lock`。

## 验证

后端完整门禁：

```bash
./scripts/ci_gate.sh
```

针对改动文件的最低语法检查：

```bash
uv run --locked python -m py_compile backend/path/to/changed_file.py
```

Web 改动：

```bash
cd frontend/web
npm run lint
npm run build
```

桌面端改动需要先构建 Web，再执行：

```bash
cd frontend/desktop
npm install
npm run build
```

网络相关能力优先做确定性、离线验证；在线数据源、第三方模型和外部通知应单独说明验证环境与结果。

## 数据库迁移

修改 `backend/src/storage/models/` 的 ORM 模型后，生成并审查新的 migration：

```bash
uv run --locked alembic revision --autogenerate -m "describe change"
uv run --locked alembic upgrade head
```

迁移文件放在 `backend/alembic/versions/`。已合并 migration 不可修改；更正 schema 必须创建新的 migration。生产代码不得通过 `Base.metadata.create_all()` 或手写 `ALTER TABLE` 改变 schema。

## 代码边界

- 后端业务代码仅放在 `backend/`；不要在根目录新增兼容实现。
- Web 改动位于 `frontend/web/`，桌面端改动位于 `frontend/desktop/`。
- 自动化与部署文件位于 `scripts/`、`docker/`。
- 接口变更应同时检查 API schema、Web 和桌面端的兼容性。
- 数据源、通知与报告改动必须保留失败降级与错误隔离。

项目协作规则、文档同步要求和完整验证矩阵见仓库根目录的 `AGENTS.md`；提交前也应阅读[贡献指南](CONTRIBUTING.md)。
