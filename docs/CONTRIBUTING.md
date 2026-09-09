# 贡献指南

感谢参与 Daily Stock Analysis。提交 Issue 或 Pull Request 前，请先阅读[架构说明](architecture.md)、[开发指南](development.md)和根目录 `AGENTS.md`。

## 提交问题

请提供可复现步骤、预期与实际行为、运行环境及脱敏日志。数据源、模型和通知问题需要说明使用的提供商及错误时间，但不得提交密钥、Webhook、Cookie 或其他凭据。

## 提交代码

1. 从最新目标分支创建独立分支。
2. 保持改动聚焦，避免顺带重构无关模块。
3. 为行为变化补充或更新测试和文档。
4. 按改动面运行[开发指南](development.md)中的验证。
5. 在 PR 中说明改动、验证、未验证项、兼容性与回滚方式。

## 开发约定

- Python 依赖只通过 `uv` 与 `pyproject.toml`/`uv.lock` 管理。
- 后端、Web、桌面端和部署脚本必须保留各自目录边界。
- API、schema、认证、报告、数据源或通知变更需要检查上下游兼容性。
- 数据库 schema 变更必须带新的 Alembic migration。
- 用户可见行为变更需更新相应文档和 `docs/CHANGELOG.md` 的 `[Unreleased]` 段。

提交信息使用英文；本仓库采用 Conventional Commits 风格。维护者负责最终合并、发布和版本标签。
