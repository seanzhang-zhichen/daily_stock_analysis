# -*- coding: utf-8 -*-
"""策略 / 技能列表命令处理器。

被机器人命令分发器（`bot/dispatcher.py`）按 ``/strategies`` 触发，
列出当前可用的交易策略及其激活状态，便于用户挑选要叠加的技能。
"""

import logging
from typing import List

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse

logger = logging.getLogger(__name__)


class StrategiesCommand(BotCommand):
    """``/strategies`` 命令处理器：列出所有可用策略（可按激活过滤）。

    用法示例：
        ``/strategies``         列出全部策略。
        ``/strategies active``  仅列出当前已激活的策略。
    """

    @property
    def name(self) -> str:
        """返回调度器使用的主要命令名。"""
        return "strategies"

    @property
    def aliases(self) -> List[str]:
        """返回列出 Agent 技能/策略的命令别名。"""
        return ["skills", "策略", "策略列表"]

    @property
    def description(self) -> str:
        """返回帮助列表中展示的简短描述。"""
        return "查看可用交易策略"

    @property
    def usage(self) -> str:
        """返回帮助中展示的参数格式。"""
        return "/strategies [active]"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        """列出所有（或仅激活的）策略并按分类分组渲染。"""
        # 兼容中英文 ``active`` 触发词；大小写无关
        show_active_only = bool(args and args[0].lower() in ("active", "激活", "已激活"))

        try:
            from src.agent.factory import get_skill_manager
            from src.config import get_config

            config = get_config()
            sm = get_skill_manager(config)
            from src.agent.factory import DEFAULT_AGENT_SKILLS

            # 从配置中读取激活集合，不修改 skill_manager，避免与 /ask 等命令的运行时状态互扰
            configured_active: set = set(config.agent_skills or DEFAULT_AGENT_SKILLS)

            all_skills = sm.list_skills()
            if not all_skills:
                return BotResponse.text_response("📋 暂无可用策略。请检查 strategies/ 目录。")

            skills = all_skills
            if show_active_only:
                skills = [s for s in all_skills if s.name in configured_active]
                if not skills:
                    return BotResponse.text_response("📋 当前没有激活的策略。")

            # 按 category 字段分组，已知类别固定以 emoji 标签呈现
            categories = {"trend": "📈 趋势类", "pattern": "📊 形态类", "reversal": "🔄 反转类", "framework": "🧩 框架类"}
            grouped = {}
            for skill in skills:
                cat = skill.category or "trend"
                grouped.setdefault(cat, []).append(skill)

            lines = ["📋 **交易策略列表**", ""]

            # 优先按预定义顺序展示，未识别类别追加在末尾
            ordered_keys = ["trend", "pattern", "reversal", "framework"]
            for cat_key in ordered_keys + [k for k in grouped if k not in ordered_keys]:
                cat_skills = grouped.get(cat_key)
                if not cat_skills:
                    continue
                cat_label = categories.get(cat_key, f"📌 {cat_key}")
                lines.append(f"**{cat_label}**")
                for s in cat_skills:
                    status = "✅" if s.name in configured_active else "⬜"
                    source_tag = ""
                    if s.source and s.source != "builtin":
                        source_tag = " (自定义)"
                    lines.append(f"  {status} `{s.name}` — {s.display_name}{source_tag}")
                    lines.append(f"      {s.description}")
                lines.append("")

            active_count = sum(1 for s in all_skills if s.name in configured_active)
            total_count = len(all_skills)
            lines.append(f"共 {total_count} 个策略，已激活 {active_count} 个")
            lines.append(f"\n💡 使用 `/ask <股票代码> <策略名>` 指定策略分析")

            return BotResponse.markdown_response("\n".join(lines))

        except Exception as e:
            # 列出命令不应让机器人进程崩溃，统一降级为带错误信息的文本响应
            logger.error(f"Strategies command failed: {e}")
            logger.exception("Strategies error details:")
            return BotResponse.text_response(f"⚠️ 获取策略列表失败: {str(e)}")
