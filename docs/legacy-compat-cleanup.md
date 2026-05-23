# 老版本兼容清理待处理清单

本清单用于跟踪开发阶段不再保留旧版本兼容逻辑的清理工作。原则是优先清理低风险 alias、旧字段和旧环境变量回退；对导入路径、启动入口、LLM 配置体系等高影响项单独拆分处理。

## 状态说明

- `待处理`：已识别但尚未修改。
- `进行中`：正在改动或验证。
- `已完成`：代码、测试和文档已同步。
- `暂缓`：影响面较大，需独立任务处理。

## 清理项

| 状态 | 范围 | 清理内容 | 说明 |
| --- | --- | --- | --- |
| 已完成 | Agent 配置 | 移除 `AGENT_STRATEGY_*` 到 `AGENT_SKILL_*` 的旧配置别名 | 只保留 `AGENT_SKILL_DIR`、`AGENT_SKILL_AUTOWEIGHT`、`AGENT_SKILL_ROUTING`。 |
| 已完成 | Agent 配置 | 移除 `AGENT_ORCHESTRATOR_MODE=strategy/skill` 旧枚举值归一化 | 只接受当前有效值 `quick`、`standard`、`full`、`specialist`。 |
| 已完成 | Analysis API | 移除 `strategies` 请求字段对 `skills` 的兼容别名 | 请求体只保留 `skills`。 |
| 已完成 | 用户链接配置 | 移除 `PUBLIC_BASE_URL`、`APP_BASE_URL` 对用户邮件链接的旧回退 | 退订 API 链接只使用 `USER_PUBLIC_BASE_URL`，前端页面链接只使用 `USER_FRONTEND_BASE_URL`。 |
| 已完成 | Stocks API | 移除图片识别响应中的 `codes` 兼容字段 | 前端和测试只消费 `items`。 |
| 已完成 | 系统配置 | 移除 `export_desktop_env` / `import_desktop_env` 方法 | API 已使用通用 `/api/v1/system/config/export` 与 `/import`，服务层只保留 `export_env` / `import_env`。 |
| 已完成 | 启动入口/导入路径 | 移除根目录 `main.py`、`server.py`、`webui.py` shim 和 `backend/__init__.py` 的 `sys.path` 兼容 | 启动命令、Docker、CI 和测试导入已切到 `backend/` 真实入口。 |
| 已完成 | LLM 配置 | 收敛 legacy env 自动推断模型列表 | 移除基于 Anspire / channel 的隐式 `LITELLM_MODEL` 与 fallback 推断，只保留显式 LLM 配置。 |
| 已完成 | 调度配置 | 收敛 `RUN_IMMEDIATELY` 与 `SCHEDULE_RUN_IMMEDIATELY` 旧语义 | 只保留 `SCHEDULE_RUN_IMMEDIATELY` 作为启动期立即运行配置。 |

## 本轮完成记录

- Removed low-risk legacy compatibility aliases for Agent config, Analysis API request fields, and user public URL environment fallbacks.
- Removed the Stocks API `codes` compatibility response field and desktop-specific system config import/export service aliases.
- Removed the remaining high-risk legacy compatibility layers for root startup shims, LLM model inference, and schedule immediate-run aliases.
