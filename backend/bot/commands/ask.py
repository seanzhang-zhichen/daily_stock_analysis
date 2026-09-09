# -*- coding: utf-8 -*-
"""问股命令处理器：使用 Agent 技能对单只或多只股票进行分析。

被机器人命令分发器（`bot/dispatcher.py`）按 ``/ask`` 触发，
支持以下用法：
    /ask 600519                        使用默认技能分析
    /ask 600519 用缠论分析              从参数文本中识别技能
    /ask 600519 chan_theory             直接以技能 id 指定技能
    /ask 600519,000858 波浪理论         多股对比分析并叠加技能
"""

import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse
from data_provider.base import canonical_stock_code
from src.config import get_config
from src.storage import get_db

logger = logging.getLogger(__name__)


class AskCommand(BotCommand):
    """``/ask`` 命令处理器：调用 Agent 用特定技能分析股票。"""

    # 多股并行分析的整体超时（包含 PortfolioAgent 复评），防止 LLM 过长拖死机器人交互
    _MULTI_ANALYZE_TIMEOUT_S = 150.0

    @property
    def name(self) -> str:
        """返回调度器使用的主要命令名。"""
        return "ask"

    @property
    def aliases(self) -> List[str]:
        """返回问股命令的本地化别名。"""
        return ["问股"]

    @property
    def description(self) -> str:
        """返回帮助列表中展示的简短描述。"""
        return "使用 Agent 技能分析股票"

    @property
    def usage(self) -> str:
        """返回帮助中展示的参数格式。"""
        return "/ask <股票代码[,代码2,...]> [技能名称]"

    def _merge_code_args(self, args: List[str]) -> tuple[str, List[str]]:
        """将分散在多个参数里的股票代码合并为一个串，并返回剩余参数。

        兼容多种分隔写法：英文逗号、中文逗号、``vs`` 分隔词，
        允许用户在聊天里混用而无需关心空格。
        """
        if not args:
            return "", []

        # 匹配单只股票代码；忽略大小写、容忍多余逗号
        code_like = re.compile(
            r"^,?(\d{6}|hk\d{5}|[A-Za-z]{1,5}(\.[A-Za-z]{1,2})?),?$",
            re.IGNORECASE,
        )
        raw_codes_parts = [args[0]]
        rest_args = list(args[1:])

        # 持续消费后续 token，直到遇到第一个非代码片段
        while rest_args:
            token = rest_args[0]
            prev = raw_codes_parts[-1].rstrip()

            # ``A vs B`` 形式的代码分隔词：消费掉 ``vs`` 和下一个代码
            if token.lower() == "vs" and len(rest_args) > 1 and code_like.match(rest_args[1]):
                raw_codes_parts.append(rest_args[1])
                rest_args = rest_args[2:]
                continue

            # 含显式逗号分隔符时合并相邻 token；中英文逗号均兼容
            has_comma_separator = (
                prev.endswith(",")
                or prev.endswith("，")
                or token.lstrip().startswith(",")
                or token.lstrip().startswith("，")
            )
            if code_like.match(token) and has_comma_separator:
                raw_codes_parts.append(token)
                rest_args = rest_args[1:]
                continue

            break

        normalized_parts = [part.strip(",，") for part in raw_codes_parts]
        raw_code_str = ",".join(normalized_parts)
        return raw_code_str, rest_args

    def _parse_stock_codes(self, raw: str) -> List[str]:
        """从首个参数（或合并后的串）中解析出一组规范化的股票代码。"""
        # 统一替换中文逗号后按英文逗号拆分，再交给 canonical_stock_code 规范化
        parts = [p.strip().upper() for p in raw.replace("，", ",").split(",") if p.strip()]
        return [canonical_stock_code(part) for part in parts]

    def _validate_single_code(self, code: str) -> Optional[str]:
        """校验单只股票代码格式（A 股 6 位 / 港股 HK+5 位 / 美股字母）。"""
        normalized = code.upper()
        is_a_stock = re.match(r"^\d{6}$", normalized)
        is_hk_stock = re.match(r"^HK\d{5}$", normalized)
        is_us_stock = re.match(r"^[A-Z]{1,5}(\.[A-Z]{1,2})?$", normalized)

        if not (is_a_stock or is_hk_stock or is_us_stock):
            return f"无效的股票代码: {normalized}（A股6位数字 / 港股HK+5位数字 / 美股1-5个字母）"
        return None

    def validate_args(self, args: List[str]) -> Optional[str]:
        """校验参数：必须提供至少一只合法股票代码，且一次最多 5 只。"""
        if not args:
            return "请输入股票代码。用法: /ask <股票代码[,代码2,...]> [技能名称]"

        raw_code_str, _ = self._merge_code_args(args)
        codes = self._parse_stock_codes(raw_code_str)
        if not codes:
            return "请输入至少一个有效的股票代码"

        for code in codes:
            error = self._validate_single_code(code)
            if error:
                return error

        # 一次最多 5 只，平衡成本与响应长度
        if len(codes) > 5:
            return "一次最多分析 5 只股票"

        return None

    @staticmethod
    def _load_skills() -> List[object]:
        """加载当前可用的 Agent 技能列表；注册异常时降级为空列表。"""
        try:
            from src.agent.factory import get_skill_manager

            sm = get_skill_manager()
            return list(sm.list_skills())
        except Exception as e:
            # 技能注册表加载失败不应阻塞命令执行，仅记录并退化为空列表
            logger.warning("_load_skills failed: Failed to load skills: %s", e, exc_info=True)
            return []

    @classmethod
    def _get_default_skill_id(cls) -> str:
        """从当前技能注册表中解析主默认技能 id。"""
        try:
            from src.agent.skills.defaults import get_primary_default_skill_id

            return get_primary_default_skill_id(cls._load_skills())
        except Exception as e:
            logger.warning("_get_default_skill_id failed: Failed to resolve default skill id: %s", e, exc_info=True)
            return ""

    @classmethod
    def _build_skill_alias_pairs(cls) -> List[tuple[str, str]]:
        """构造 ``(别名, 技能 id)`` 的有序映射，用于从自然语言里模糊匹配技能。

        按别名长度倒序，确保 ``"缠论分析"`` 比 ``"缠论"`` 先匹配，防止子串抢先。
        """
        alias_pairs: List[tuple[str, str]] = []
        for skill in cls._load_skills():
            skill_id = str(getattr(skill, "name", "")).strip()
            if not skill_id:
                continue
            aliases = [skill_id, getattr(skill, "display_name", "")] + list(getattr(skill, "aliases", []) or [])
            for alias in aliases:
                alias_text = str(alias).strip()
                if alias_text:
                    alias_pairs.append((alias_text, skill_id))

        alias_pairs.sort(key=lambda item: (len(item[0]), item[0]), reverse=True)
        return alias_pairs

    def _parse_skill(self, args: List[str]) -> str:
        """从参数中解析出最终使用的技能 id；未命中时退回默认技能。

        匹配优先级：精确 id > 别名子串包含 > 默认技能 id。
        """
        default_skill_id = self._get_default_skill_id()
        if len(args) < 2:
            return default_skill_id

        skill_text = " ".join(args[1:]).strip()
        available_ids = {str(getattr(skill, "name", "")).strip() for skill in self._load_skills()}
        if skill_text in available_ids:
            return skill_text

        for alias_text, skill_id in self._build_skill_alias_pairs():
            # 任意别名出现在用户文本里即视为命中
            if alias_text in skill_text:
                return skill_id

        return default_skill_id

    def _resolve_skill_name(self, skill_id: Optional[str]) -> str:
        """将技能 id 解析为人类可读的展示名；缺省退回 ``"default"``。"""
        if not skill_id:
            return "default"
        for skill in self._load_skills():
            if str(getattr(skill, "name", "")).strip() == skill_id:
                display_name = str(getattr(skill, "display_name", "")).strip()
                return display_name or skill_id
        return skill_id

    @staticmethod
    def _build_execution_context(stock_code: str, skill_id: str) -> Dict[str, Any]:
        """构造 Agent 执行所需的上下文（股票代码与选定的技能/策略列表）。"""
        selected = [skill_id] if skill_id else []
        return {
            "stock_code": stock_code,
            "skills": selected,
            "strategies": selected,
        }

    @staticmethod
    def _build_user_message(stock_code: str, skill_id: str, skill_text: str) -> str:
        """根据股票代码与可选技能文本，构造发给 Agent 的自然语言指令。"""
        user_msg = f"请分析股票 {stock_code}"
        if skill_id:
            user_msg = f"请使用 {skill_id} 技能分析股票 {stock_code}"
        if skill_text:
            user_msg = f"请分析股票 {stock_code}，{skill_text}"
        return user_msg

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        """执行问股命令：解析股票与技能后分派给单股或多股分析流程。"""
        config = get_config()

        if not config.agent_mode:
            return BotResponse.text_response(
                "⚠️ Agent 模式未开启，无法使用问股功能。\n请在配置中设置 `AGENT_MODE=true`。"
            )

        raw_code_str, remaining_args = self._merge_code_args(args)
        codes = self._parse_stock_codes(raw_code_str)
        skill_id = self._parse_skill(["placeholder"] + remaining_args) if remaining_args else self._get_default_skill_id()
        skill_text = " ".join(remaining_args).strip()

        logger.info("[AskCommand] Stocks: %s, Skill: %s, Extra: %s", codes, skill_id, skill_text)

        # 单只股票走专用分支（更简洁），多只股票走并行对比分支
        if len(codes) == 1:
            return self._analyze_single(config, message, codes[0], skill_id, skill_text)

        return self._analyze_multi(config, message, codes, skill_id, skill_text)

    def _analyze_single(
        self,
        config,
        message: BotMessage,
        code: str,
        skill_id: str,
        skill_text: str,
    ) -> BotResponse:
        """在独立会话中调用 Agent，对单只股票执行分析并返回文本响应。"""
        try:
            from src.agent.factory import build_agent_executor

            executor = build_agent_executor(config, skills=[skill_id] if skill_id else None)
            user_msg = self._build_user_message(code, skill_id, skill_text)
            # 每次问股都生成独立会话，避免不同次问股的上下文串味
            session_id = f"{message.platform}_{message.user_id}:ask_{code}_{uuid.uuid4()}"
            result = executor.chat(
                message=user_msg,
                session_id=session_id,
                context=self._build_execution_context(code, skill_id),
            )

            if result.success:
                skill_name = self._resolve_skill_name(skill_id)
                header = f"📊 {code} | 技能: {skill_name}\n{'─' * 30}\n"
                return BotResponse.text_response(header + result.content)
            return BotResponse.text_response(f"⚠️ 分析失败: {result.error}")

        except Exception as exc:
            logger.error("Ask command failed: %s", exc)
            logger.exception("Ask error details:")
            return BotResponse.text_response(f"⚠️ 问股执行出错: {str(exc)}")

    def _analyze_multi(
        self,
        config,
        message: BotMessage,
        codes: List[str],
        skill_id: str,
        skill_text: str,
    ) -> BotResponse:
        """并行分析多只股票，并在最终附带组合视角复评。"""
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, as_completed

        skill_name = self._resolve_skill_name(skill_id)
        results: Dict[str, Dict[str, Any]] = {}
        errors: Dict[str, str] = {}
        started_at = time.monotonic()
        overall_timeout_s = self._MULTI_ANALYZE_TIMEOUT_S

        platform = message.platform
        user_id = message.user_id

        def _run_one(stock_code: str) -> Tuple[str, Optional[Dict[str, Any]], Optional[str]]:
            """在独立会话中分析一只股票，返回 ``(code, payload, error)``。"""
            try:
                from src.agent.conversation import conversation_manager
                from src.agent.factory import build_agent_executor

                executor = build_agent_executor(config, skills=[skill_id] if skill_id else None)
                user_msg = self._build_user_message(stock_code, skill_id, skill_text)
                # 每只股票各自独立会话，避免多股上下文相互污染
                session_id = f"{platform}_{user_id}:ask_{stock_code}_{uuid.uuid4()}"
                conversation_manager.add_message(session_id, "user", user_msg)

                result = executor.run(
                    task=user_msg,
                    context=self._build_execution_context(stock_code, skill_id),
                )
                # 解析失败但 Agent 仍有可读文本内容时，也接受作为结果（降级为 freeform）
                if result.success or self._should_accept_fallback_content(result):
                    dashboard = result.dashboard if isinstance(result.dashboard, dict) else None
                    formatted_analysis = self._format_stock_result(stock_code, dashboard, result.content)
                    conversation_manager.add_message(session_id, "assistant", formatted_analysis)
                    return (
                        stock_code,
                        {
                            "content": result.content,
                            "dashboard": dashboard,
                            "signal": self._extract_signal(dashboard),
                            "confidence": self._extract_confidence(dashboard),
                            "summary": self._extract_summary(stock_code, dashboard, result.content),
                            "markdown": formatted_analysis,
                            "stock_name": self._extract_stock_name(stock_code, dashboard),
                            "risk_flags": self._extract_risk_flags(dashboard),
                        },
                        None,
                    )

                error_note = f"[分析失败] {result.error or '未知错误'}"
                conversation_manager.add_message(session_id, "assistant", error_note)
                return (stock_code, None, result.error or "未知错误")
            except Exception as exc:
                # 单只异常不能影响整批，错误信息直接返回给汇总步骤
                return (stock_code, None, str(exc))

        # 并发写库前先预热 DB 连接，避免线程首请求被握手阻塞
        get_db()
        # 限制最大并发为股票数与 5 中的较小者，避免对 LLM/数据源造成突发压力
        pool = ThreadPoolExecutor(max_workers=min(len(codes), 5))
        future_map = {pool.submit(_run_one, code): code for code in codes}
        try:
            for future in as_completed(future_map, timeout=overall_timeout_s):
                try:
                    code, content, error = future.result(timeout=5)
                    if content is not None:
                        results[code] = content
                    else:
                        errors[code] = error or "未知错误"
                except Exception as exc:
                    code = future_map[future]
                    errors[code] = f"执行异常: {exc}"
        except FutureTimeoutError:
            # 整体超时：对未完成的任务记录超时错误，已完成的结果保留
            logger.warning("[AskCommand] Multi-stock analysis hit overall timeout (%.1fs)", overall_timeout_s)
            for future, code in future_map.items():
                if code in results or code in errors:
                    continue
                if future.done():
                    try:
                        code_done, content, error = future.result(timeout=0)
                        if content is not None:
                            results[code_done] = content
                        else:
                            errors[code] = error or "未知错误"
                    except Exception as exc:
                        errors[code] = f"执行异常: {exc}"
                else:
                    errors[code] = "分析超时（未在 150 秒内完成）"
        finally:
            # 不等齐所有任务，避免被单个慢请求拖到 150s
            pool.shutdown(wait=False, cancel_futures=True)

        # 兜底：极少数边界情况（线程池自身异常）下补齐 errors 记录
        for code in codes:
            if code not in results and code not in errors:
                errors[code] = "分析超时"

        parts = [f"📊 **多股对比分析** | 技能: {skill_name}", f"{'─' * 30}", ""]

        # 把剩余预算透传给 PortfolioAgent，避免整体超出 _MULTI_ANALYZE_TIMEOUT_S
        remaining_timeout_s = max(0.0, overall_timeout_s - (time.monotonic() - started_at))
        portfolio_section = self._build_portfolio_section(
            config,
            codes,
            results,
            timeout_s=remaining_timeout_s,
        )
        if portfolio_section:
            parts.append(portfolio_section)
            parts.append("")

        # 汇总表：仅当至少两只成功才有意义输出
        if len(results) >= 2:
            parts.append("| 股票 | 信号 | 置信度 | 摘要 |")
            parts.append("|------|------|--------|------|")
            for code in codes:
                if code in results:
                    item = results[code]
                    signal = item.get("signal") or "unknown"
                    confidence = item.get("confidence")
                    confidence_text = f"{confidence:.0%}" if isinstance(confidence, (int, float)) else "-"
                    summary_line = str(item.get("summary") or "分析完成").replace("|", "/")[:80]
                    parts.append(f"| {code} | {signal} | {confidence_text} | {summary_line} |")
                elif code in errors:
                    parts.append(f"| {code} | error | - | ⚠️ {errors[code][:40]} |")
            parts.append("")

        for code in codes:
            if code in results:
                parts.append(f"### {code}")
                parts.append(results[code]["markdown"])
                parts.append("")
            elif code in errors:
                parts.append(f"### {code}")
                parts.append(f"⚠️ 分析失败: {errors[code]}")
                parts.append("")

        return BotResponse.markdown_response("\n".join(parts))

    @staticmethod
    def _should_accept_fallback_content(result: Any) -> bool:
        """当 dashboard JSON 解析失败但 Agent 仍有可用正文时，仍保留该结果。

        目的：避免仅因结构化解析失败就把 LLM 的有效回答丢弃。
        """
        if getattr(result, "success", False):
            return True

        content = getattr(result, "content", "")
        error = str(getattr(result, "error", "") or "")
        if not isinstance(content, str) or not content.strip():
            return False

        return error == "Failed to parse dashboard JSON from agent response"

    @staticmethod
    def _extract_stock_name(stock_code: str, dashboard: Optional[Dict[str, Any]]) -> str:
        """从 dashboard 中取出股票名称字段，缺失时回退到股票代码。"""
        if isinstance(dashboard, dict):
            stock_name = dashboard.get("stock_name")
            if isinstance(stock_name, str) and stock_name.strip():
                return stock_name.strip()
        return stock_code

    @staticmethod
    def _extract_signal(dashboard: Optional[Dict[str, Any]]) -> str:
        """从 dashboard 中抽出标准化的决策信号（如 ``buy``/``sell``）。"""
        if isinstance(dashboard, dict):
            signal = dashboard.get("decision_type")
            if isinstance(signal, str) and signal.strip():
                return signal.strip()
        return "unknown"

    @staticmethod
    def _extract_confidence(dashboard: Optional[Dict[str, Any]]) -> Optional[float]:
        """从 dashboard 中抽取 0..1 区间的置信度。

        优先使用数值型 ``sentiment_score``（0-100，按比例归一），
        缺失时再退回到中文等级映射（``高/中/低``）。
        """
        if not isinstance(dashboard, dict):
            return None

        score = dashboard.get("sentiment_score")
        try:
            return max(0.0, min(1.0, float(score) / 100.0))
        except (TypeError, ValueError):
            pass

        level = str(dashboard.get("confidence_level") or "").strip()
        return {"高": 0.85, "中": 0.65, "低": 0.45}.get(level)

    @staticmethod
    def _extract_summary(stock_code: str, dashboard: Optional[Dict[str, Any]], raw_content: str) -> str:
        """优先从 dashboard 字段、其次从自由文本中拼接出一句话摘要。

        寻找候选顺序：``analysis_summary`` → ``risk_warning`` → ``trend_prediction`` →
        ``dashboard.core_conclusion.one_sentence`` → 正文首条非空短行 → 兜底默认文案。
        """
        if isinstance(dashboard, dict):
            for key in ("analysis_summary", "risk_warning", "trend_prediction"):
                value = dashboard.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            dashboard_block = dashboard.get("dashboard")
            if not isinstance(dashboard_block, dict):
                dashboard_block = {}
            core_conclusion = dashboard_block.get("core_conclusion")
            if not isinstance(core_conclusion, dict):
                core_conclusion = {}
            core = core_conclusion.get("one_sentence")
            if isinstance(core, str) and core.strip():
                return core.strip()

        # 文本兜底：跳过空行、过短行与疑似 JSON 片段
        for line in raw_content.splitlines():
            stripped = line.strip()
            if stripped and len(stripped) > 4 and not stripped.startswith(("{", "}", "\"")):
                return stripped[:120]
        return f"{stock_code} 分析完成"

    @staticmethod
    def _extract_risk_flags(dashboard: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
        """把 dashboard 中的风险提示归一化为组合视角输入所需的 flag 列表。"""
        if not isinstance(dashboard, dict):
            return []

        flags: List[Dict[str, str]] = []
        dashboard_block = dashboard.get("dashboard")
        if not isinstance(dashboard_block, dict):
            dashboard_block = {}
        intelligence = dashboard_block.get("intelligence")
        if not isinstance(intelligence, dict):
            intelligence = {}
        # 最多取前 5 条，避免组合叠加时风险信号过多导致 LLM 输入爆掉
        for alert in intelligence.get("risk_alerts", [])[:5]:
            if isinstance(alert, str) and alert.strip():
                flags.append({"category": "portfolio_input", "description": alert.strip(), "severity": "medium"})

        risk_warning = dashboard.get("risk_warning")
        if isinstance(risk_warning, str) and risk_warning.strip():
            flags.append({"category": "portfolio_input", "description": risk_warning.strip(), "severity": "medium"})
        return flags

    @staticmethod
    def _format_sniper_value(value: Any) -> Optional[str]:
        """规范化 sniper 价格字段：去除中英文标签前缀并清掉空占位符。"""
        if value is None:
            return None

        text = str(value).strip()
        if not text or text in {"-", "—", "N/A", "None"}:
            return None

        # 同时剥离中英文冒号标签，保证不同 Agent 输出都能正常展示
        prefixes = (
            "理想买入点：",
            "次优买入点：",
            "止损位：",
            "目标位：",
            "理想买入点:",
            "次优买入点:",
            "止损位:",
            "目标位:",
        )
        for prefix in prefixes:
            if text.startswith(prefix):
                stripped = text[len(prefix):].strip()
                return stripped or None

        return text

    @staticmethod
    def _format_stock_result(stock_code: str, dashboard: Optional[Dict[str, Any]], raw_content: str) -> str:
        """把单只股票的分析结果渲染为紧凑的 markdown，供机器人嵌入/汇总。"""
        if not isinstance(dashboard, dict):
            content = raw_content
            if len(content) > 800:
                content = content[:800] + "\n... (已截断，完整分析请单独查询)"
            return content

        lines = []
        stock_name = dashboard.get("stock_name")
        # 仅展示真实名称，避免与代码同框时的重复噪音
        if isinstance(stock_name, str) and stock_name.strip() and stock_name.strip() != stock_code:
            lines.append(f"**名称**: {stock_name.strip()}")

        decision = dashboard.get("decision_type")
        confidence = AskCommand._extract_confidence(dashboard)
        trend = dashboard.get("trend_prediction")
        if isinstance(decision, str):
            # 把结论/置信度/趋势拼成一行展示，缺失字段自动隐藏
            lines.append(
                f"**结论**: {decision}"
                + (f" | **置信度**: {confidence:.0%}" if isinstance(confidence, (int, float)) else "")
                + (f" | **趋势**: {trend}" if isinstance(trend, str) and trend.strip() else "")
            )

        summary = AskCommand._extract_summary(stock_code, dashboard, raw_content)
        if summary:
            lines.append(f"**摘要**: {summary}")

        operation = dashboard.get("operation_advice")
        if isinstance(operation, str) and operation.strip():
            lines.append(f"**操作建议**: {operation.strip()}")

        risk_warning = dashboard.get("risk_warning")
        if isinstance(risk_warning, str) and risk_warning.strip():
            lines.append(f"**风险提示**: {risk_warning.strip()}")

        dashboard_block = dashboard.get("dashboard")
        if not isinstance(dashboard_block, dict):
            dashboard_block = {}
        battle_plan = dashboard_block.get("battle_plan")
        if not isinstance(battle_plan, dict):
            battle_plan = {}
        sniper = battle_plan.get("sniper_points")
        if isinstance(sniper, dict):
            price_parts = []
            # 顺序固定为「理想买入 → 次优买入 → 止损 → 目标」，便于用户扫读
            for key in ("ideal_buy", "secondary_buy", "stop_loss", "take_profit"):
                value = AskCommand._format_sniper_value(sniper.get(key))
                if value:
                    price_parts.append(f"{key}={value}")
            if price_parts:
                lines.append("**关键点位**: " + " | ".join(price_parts))

        return "\n\n".join(lines) if lines else raw_content[:800]

    def _build_portfolio_section(
        self,
        config,
        codes: List[str],
        results: Dict[str, Dict[str, Any]],
        timeout_s: Optional[float] = None,
    ) -> str:
        """为多股分析结果追加组合视角总结（小标题 + 多维风险/调仓建议）。"""
        if len(results) < 2:
            return ""

        # 剩余预算非正时直接跳过，避免拖垮整体响应
        if timeout_s is not None and timeout_s <= 0:
            logger.info("[AskCommand] Skip portfolio overlay because no timeout budget remains")
            return ""

        def _render_overlay() -> str:
            """调用 PortfolioAgent 渲染组合视角文本。"""
            from src.agent.agents.portfolio_agent import PortfolioAgent
            from src.agent.factory import get_tool_registry
            from src.agent.llm_adapter import LLMToolAdapter
            from src.agent.protocols import AgentContext

            stock_opinions: Dict[str, Dict[str, Any]] = {}
            risk_flags: List[Dict[str, str]] = []
            stock_list: List[str] = []
            for code in codes:
                item = results.get(code)
                if not item:
                    continue
                stock_list.append(code)
                stock_opinions[code] = {
                    "signal": item.get("signal", "unknown"),
                    "confidence": item.get("confidence", 0.5),
                    "summary": item.get("summary", ""),
                    "stock_name": item.get("stock_name", code),
                }
                risk_flags.extend(item.get("risk_flags", []))

            ctx = AgentContext(query=f"Portfolio overlay for {', '.join(stock_list)}")
            ctx.data["stock_opinions"] = stock_opinions
            ctx.data["stock_list"] = stock_list
            # 截断风险信号数组，防止 Agent 输入过长
            ctx.risk_flags.extend(risk_flags[:10])

            agent = PortfolioAgent(
                tool_registry=get_tool_registry(),
                llm_adapter=LLMToolAdapter(config),
            )
            stage_result = agent.run(ctx)
            if not stage_result.success:
                return ""

            assessment = ctx.data.get("portfolio_assessment")
            if not isinstance(assessment, dict):
                return ""

            lines = ["## 组合视角", ""]
            summary = assessment.get("summary")
            if isinstance(summary, str) and summary.strip():
                lines.append(summary.strip())
                lines.append("")

            risk_score = assessment.get("portfolio_risk_score")
            if risk_score is not None:
                lines.append(f"- 组合风险分: {risk_score}")
            sector_warnings = assessment.get("sector_warnings") or []
            if sector_warnings:
                lines.append(f"- 行业集中: {'；'.join(str(item) for item in sector_warnings[:3])}")
            correlation_warnings = assessment.get("correlation_warnings") or []
            if correlation_warnings:
                lines.append(f"- 相关性风险: {'；'.join(str(item) for item in correlation_warnings[:3])}")
            rebalance = assessment.get("rebalance_suggestions") or []
            if rebalance:
                lines.append(f"- 调仓建议: {'；'.join(str(item) for item in rebalance[:3])}")
            positions = assessment.get("positions") or []
            if positions:
                position_parts = []
                for position in positions[:5]:
                    if not isinstance(position, dict):
                        continue
                    code = position.get("code")
                    weight = position.get("suggested_weight")
                    signal = position.get("signal")
                    if code and weight is not None:
                        try:
                            weight_text = f"{float(weight):.0%}"
                        except (TypeError, ValueError):
                            weight_text = str(weight)
                        suffix = f" ({signal})" if signal else ""
                        position_parts.append(f"{code}: {weight_text}{suffix}")
                if position_parts:
                    lines.append(f"- 建议仓位: {'；'.join(position_parts)}")

            return "\n".join(lines)

        if timeout_s is None:
            try:
                return _render_overlay()
            except Exception as exc:
                logger.warning("[AskCommand] Portfolio overlay failed: %s", exc)
                return ""

        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

        # 用单独线程跑 PortfolioAgent，命中预算立即放弃，避免影响整体响应
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(_render_overlay)
        try:
            return future.result(timeout=timeout_s)
        except FutureTimeoutError:
            logger.warning("[AskCommand] Portfolio overlay timed out after %.2fs", timeout_s)
            return ""
        except Exception as exc:
            logger.warning("[AskCommand] Portfolio overlay failed: %s", exc)
            return ""
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
