# -*- coding: utf-8 -*-
"""WebUI 前端静态资源构建辅助。

仅在显式启用 WebUI 的启动路径中调用；纯 API 启动不会触发本模块。
可通过设置 ``WEBUI_AUTO_BUILD=false`` 关闭启动时自动构建，仅做产物校验。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Sequence

logger = logging.getLogger(__name__)

# 视为「关闭」的环境变量字符串集合，大小写不敏感。
_FALSEY_ENV_VALUES = {"0", "false", "no", "off"}
# 触发前端构建的输入文件清单（配置 + 入口 HTML）。
_BUILD_INPUT_FILES = (
    "package.json",
    "package-lock.json",
    "vite.config.ts",
    "tsconfig.json",
    "tsconfig.app.json",
    "tsconfig.node.json",
    "eslint.config.js",
    "postcss.config.js",
    "tailwind.config.js",
    "index.html",
)
# 触发构建的源码目录。
_BUILD_INPUT_DIRS = ("src", "public")


def _is_truthy_env(var_name: str, default: str = "true") -> bool:
    """解析常见的环境变量真值/假值表达（大小写不敏感）。"""
    value = os.getenv(var_name, default).strip().lower()
    return value not in _FALSEY_ENV_VALUES


def _safe_mtime(path: Path) -> float:
    """安全地读取文件 mtime，无法访问时返回 0 便于比较。"""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _tree_latest_mtime(root: Path) -> float:
    """返回目录树下最新文件的 mtime，遍历失败时回退到根目录 mtime。"""
    if not root.exists():
        return 0.0
    latest = 0.0
    try:
        for p in root.rglob("*"):
            if p.is_file():
                latest = max(latest, _safe_mtime(p))
    except OSError:
        # 递归遍历在受限环境下失败时，回退到根目录 mtime。
        latest = max(latest, _safe_mtime(root))
    return latest


def _max_mtime(paths: Iterable[Path]) -> float:
    """返回一组路径中最大的 mtime。"""
    latest = 0.0
    for path in paths:
        latest = max(latest, _safe_mtime(path))
    return latest


def _resolve_artifact_index(frontend_dir: Path) -> Path:
    """解析本仓库首选的前端产物入口 HTML 路径。"""
    # 优先 static/index.html：这是本仓库 Vite 输出的目标位置。
    static_index = (frontend_dir / ".." / ".." / "static" / "index.html").resolve()
    dist_index = frontend_dir / "dist" / "index.html"
    build_index = frontend_dir / "build" / "index.html"
    if static_index.exists():
        return static_index

    # 没有 static 时取 dist / build 中 mtime 最新的那份作为回退。
    fallback_candidates = [p for p in (dist_index, build_index) if p.exists()]
    if not fallback_candidates:
        return static_index
    return max(fallback_candidates, key=_safe_mtime)


def _needs_dependency_install(frontend_dir: Path, package_json: Path, lock_file: Path, force_build: bool) -> bool:
    """判断 npm 依赖是否缺失或比 package 输入更旧，需要重装。"""
    node_modules_dir = frontend_dir / "node_modules"
    install_marker = node_modules_dir / ".package-lock.json"
    deps_marker_mtime = _safe_mtime(install_marker) if install_marker.exists() else _safe_mtime(node_modules_dir)
    deps_input_mtime = _max_mtime((package_json, lock_file))
    return force_build or (not node_modules_dir.exists()) or (deps_marker_mtime < deps_input_mtime)


def _collect_build_inputs_latest_mtime(frontend_dir: Path) -> float:
    """返回前端构建配置与源码输入中的最新 mtime。"""
    latest = _max_mtime(frontend_dir / filename for filename in _BUILD_INPUT_FILES)
    for dirname in _BUILD_INPUT_DIRS:
        latest = max(latest, _tree_latest_mtime(frontend_dir / dirname))
    return latest


def _needs_frontend_build(frontend_dir: Path, force_build: bool) -> tuple[bool, Path]:
    """判断前端产物是否需要重新构建，并返回产物入口路径。"""
    artifact_index = _resolve_artifact_index(frontend_dir)
    inputs_latest_mtime = _collect_build_inputs_latest_mtime(frontend_dir)
    artifact_mtime = _safe_mtime(artifact_index)
    needs_build = force_build or (not artifact_index.exists()) or (artifact_mtime < inputs_latest_mtime)
    return needs_build, artifact_index


def _run_frontend_commands(commands: Sequence[Sequence[str]], frontend_dir: Path) -> bool:
    """按顺序执行前端命令，整体成功才返回 True，失败不向上抛异常。"""
    try:
        for command in commands:
            logger.info("执行前端命令: %s", " ".join(command))
            subprocess.run(command, cwd=frontend_dir, check=True)
        logger.info("前端静态资源构建完成")
        return True
    except subprocess.CalledProcessError as exc:
        cmd_display = " ".join(exc.cmd) if isinstance(exc.cmd, (list, tuple)) else str(exc.cmd)
        logger.error(
            "前端命令执行失败（exit_code=%s）: %s",
            getattr(exc, "returncode", "N/A"),
            cmd_display,
        )
        return False


def _manual_build_command(frontend_dir: Path) -> str:
    """返回供用户手动构建前端时使用的 shell 命令。"""
    lock_file = frontend_dir / "package-lock.json"
    install_cmd = "npm ci" if lock_file.exists() else "npm install"
    return f'cd "{frontend_dir}" && {install_cmd} && npm run build'


def _has_static_assets(static_dir: Path) -> bool:
    """检查 static/assets/ 是否存在且包含 CSS/JS 文件。

    index.html 存在但 assets/ 为空或缺失时，浏览器无法加载样式与脚本，
    会导致页面元素异常放大、布局错乱（纯裸 HTML 渲染）。
    """
    assets_dir = static_dir / "assets"
    if not assets_dir.is_dir():
        return False
    try:
        return any(
            f.suffix in (".js", ".css") and f.is_file()
            for f in assets_dir.iterdir()
        )
    except OSError:
        return False


def _warn_if_assets_missing(artifact_index: Path, frontend_dir: Path) -> None:
    """当 index.html 存在但 assets/ 缺失时，发出页面显示异常警告。"""
    static_dir = artifact_index.parent
    assets_dir = static_dir / "assets"
    if not _has_static_assets(static_dir):
        logger.warning(
            "检测到 %s 但 %s 目录不存在或无 CSS/JS 文件，"
            "WebUI 将因缺少样式与脚本而显示异常（元素过大、布局错乱）",
            artifact_index,
            assets_dir,
        )
        logger.warning(
            "请重新构建前端以修复此问题: %s",
            _manual_build_command(frontend_dir),
        )
        logger.warning(
            "Docker 用户请执行: docker compose -f ./docker/docker-compose.yml build --no-cache"
        )


def prepare_webui_frontend_assets() -> bool:
    """为 WebUI 启动准备前端静态资源。

    默认模式（``WEBUI_AUTO_BUILD=true``）：
    - 依赖或源码发生变化、或产物缺失时，自动执行 ``npm install`` / ``npm run build``。

    手动模式（``WEBUI_AUTO_BUILD=false``）：
    - 后端启动时不编译前端。
    - 仅检查既有产物是否可用。
    """
    # 前端项目位于仓库根目录下 frontend/web，向上两级回到项目根再拼接。
    frontend_dir = Path(__file__).resolve().parents[2] / "frontend" / "web"
    auto_build_enabled = _is_truthy_env("WEBUI_AUTO_BUILD", "true")
    artifact_index = _resolve_artifact_index(frontend_dir)

    if not auto_build_enabled:
        if artifact_index.exists():
            logger.info("WEBUI_AUTO_BUILD=false，检测到前端静态产物: %s", artifact_index)
            _warn_if_assets_missing(artifact_index, frontend_dir)
            return True
        logger.warning("未检测到 WebUI 前端静态产物: %s", artifact_index)
        logger.warning("当前配置 WEBUI_AUTO_BUILD=false，不会在后端启动时自动编译前端")
        logger.warning("请先手动构建前端: %s", _manual_build_command(frontend_dir))
        logger.warning("如需启动时自动构建，可设置 WEBUI_AUTO_BUILD=true")
        return False

    force_build = _is_truthy_env("WEBUI_FORCE_BUILD", "false")
    needs_build, artifact_index = _needs_frontend_build(frontend_dir=frontend_dir, force_build=force_build)

    if not needs_build:
        logger.info("检测到可直接复用的前端静态产物，跳过运行时自动构建: %s", artifact_index)
        _warn_if_assets_missing(artifact_index, frontend_dir)
        return True

    package_json = frontend_dir / "package.json"
    if not package_json.exists():
        logger.warning("未找到前端项目，无法自动构建: %s", package_json)
        logger.warning("可先手动检查前端目录或关闭 WEBUI_AUTO_BUILD")
        return False

    npm_path = shutil.which("npm")
    if not npm_path:
        logger.warning("未检测到 npm，无法自动构建前端")
        logger.warning("请先手动构建前端静态资源: %s", _manual_build_command(frontend_dir))
        return False

    lock_file = frontend_dir / "package-lock.json"
    needs_install = _needs_dependency_install(
        frontend_dir=frontend_dir,
        package_json=package_json,
        lock_file=lock_file,
        force_build=force_build,
    )

    commands = []
    # 有 lockfile 时使用 npm ci 保证版本一致，无 lockfile 时退回 npm install。
    if needs_install:
        lock_exists = (frontend_dir / "package-lock.json").exists()
        commands.append([npm_path, "ci" if lock_exists else "install"])
    if needs_build:
        commands.append([npm_path, "run", "build"])

    logger.info(
        "前端构建检查结果: needs_install=%s, needs_build=%s, artifact=%s",
        needs_install,
        needs_build,
        artifact_index,
    )
    return _run_frontend_commands(commands=commands, frontend_dir=frontend_dir)
