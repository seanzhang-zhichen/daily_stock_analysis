# -*- coding: utf-8 -*-
"""Regression tests for stock profile Deep Research attachment."""

import unittest
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

try:
    import json_repair  # noqa: F401
except ModuleNotFoundError:
    json_repair_stub = MagicMock()
    json_repair_stub.repair_json = lambda value, *args, **kwargs: value
    sys.modules["json_repair"] = json_repair_stub

if "newspaper" not in sys.modules:
    newspaper_stub = MagicMock()
    newspaper_stub.Article = MagicMock()
    newspaper_stub.Config = MagicMock()
    sys.modules["newspaper"] = newspaper_stub

from src.analyzer import AnalysisResult, GeminiAnalyzer
from src.agent.executor import AgentExecutor
from src.agent.orchestrator import AgentOrchestrator
from src.core.pipeline import StockAnalysisPipeline
from src.enums import ReportType


class PipelineStockProfileTestCase(unittest.TestCase):
    def _make_pipeline(self, *, agent_mode=False, analysis_skills=None, agent_skills=None, agent_available=True):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.config = SimpleNamespace(
            agent_mode=agent_mode,
            agent_skills=agent_skills or [],
            agent_deep_research_budget=30000,
            agent_deep_research_timeout=600,
            agent_deep_research_max_sub_questions=8,
            agent_deep_research_sub_question_steps=6,
            llm_model_list=[],
            agent_litellm_model="",
            litellm_model="test-model",
            litellm_fallback_models=[],
            is_agent_available=MagicMock(return_value=agent_available),
        )
        pipeline.analysis_skills = analysis_skills
        pipeline.user_id = 7
        pipeline._emit_progress = MagicMock()
        return pipeline

    def _make_analyzer(self):
        analyzer = GeminiAnalyzer.__new__(GeminiAnalyzer)
        analyzer._config_override = SimpleNamespace(
            report_language="zh",
            news_max_age_days=3,
            news_strategy_profile="short",
        )
        analyzer._skill_instructions_override = ""
        analyzer._default_skill_policy_override = ""
        analyzer._use_legacy_default_prompt_override = False
        analyzer._resolved_prompt_state = {
            "skill_instructions": "",
            "default_skill_policy": "",
            "use_legacy_default_prompt": False,
        }
        return analyzer

    def _make_pipeline_for_analyze_stock(self):
        pipeline = self._make_pipeline(agent_available=True)
        pipeline.config.enable_realtime_quote = False
        pipeline.config.enable_chip_distribution = True
        pipeline.config.fundamental_stage_timeout_seconds = 1.5
        pipeline.config.report_language = "zh"
        pipeline.config.report_integrity_enabled = False
        pipeline.fetcher_manager = MagicMock()
        pipeline.fetcher_manager.get_stock_name.return_value = "贵州茅台"
        pipeline.fetcher_manager.get_realtime_quote.return_value = None
        pipeline.fetcher_manager.get_chip_distribution.return_value = None
        pipeline.fetcher_manager.get_fundamental_context.return_value = {
            "source_chain": [],
            "coverage": {},
        }
        pipeline.fetcher_manager.build_failed_fundamental_context.return_value = {
            "source_chain": [],
            "coverage": {},
        }
        pipeline.db = MagicMock()
        pipeline.db.save_fundamental_snapshot.return_value = None
        pipeline.db.get_data_range.return_value = []
        pipeline.db.get_analysis_context.return_value = {
            "code": "600519",
            "stock_name": "贵州茅台",
            "date": "2026-05-28",
            "today": {},
            "yesterday": {},
        }
        pipeline.db.save_analysis_history.return_value = None
        pipeline.search_service = SimpleNamespace(is_available=False)
        pipeline.social_sentiment_service = SimpleNamespace(is_available=False)
        pipeline.trend_analyzer = MagicMock()
        pipeline.analyzer = MagicMock()
        pipeline._attach_belong_boards_to_fundamental_context = MagicMock(side_effect=lambda code, ctx: ctx)
        pipeline._enhance_context = MagicMock(return_value={"realtime": {}})
        pipeline._build_context_snapshot = MagicMock(return_value={})
        pipeline.save_context_snapshot = False
        return pipeline

    def test_stock_profile_research_skips_when_agent_unavailable(self):
        pipeline = self._make_pipeline(agent_mode=False, analysis_skills=None, agent_skills=[], agent_available=False)

        with patch("src.agent.research.ResearchAgent") as research_agent:
            profile = pipeline._build_deep_research_stock_profile("600519", "贵州茅台", {})

        research_agent.assert_not_called()
        pipeline.config.is_agent_available.assert_called_once()
        self.assertIsNone(profile)

    def test_stock_profile_research_returns_successful_result_before_report(self):
        pipeline = self._make_pipeline(agent_mode=True)
        research_result = SimpleNamespace(
            success=True,
            report="## 公司概况\n\n主营高端白酒。\n\n如果你愿意，我可以下一步把这些内容整理成模板。",
            sub_questions=["主营业务是什么", "行业地位如何"],
            total_tokens=123,
        )

        with patch("src.agent.factory.get_tool_registry", return_value=MagicMock()), patch(
            "src.agent.llm_adapter.LLMToolAdapter",
            return_value=MagicMock(),
        ), patch("src.agent.research.ResearchAgent") as research_agent:
            research_agent.return_value.research.return_value = research_result
            profile = pipeline._build_deep_research_stock_profile("600519", "贵州茅台", {"report_language": "zh"})

        self.assertEqual(profile["research_method"], "deep_research")
        self.assertIn("主营高端白酒", profile["research_report"])
        self.assertNotIn("如果你愿意", profile["research_report"])
        self.assertEqual(profile["research_sources"], ["主营业务是什么", "行业地位如何"])
        self.assertEqual(profile["research_token_usage"], 123)
        self.assertEqual(research_agent.call_args.kwargs["token_budget"], 30000)
        self.assertEqual(research_agent.call_args.kwargs["max_sub_questions"], 8)
        self.assertEqual(research_agent.call_args.kwargs["sub_question_max_steps"], 6)
        self.assertIn("禁止输出", research_agent.return_value.research.call_args.args[0])
        self.assertEqual(research_agent.return_value.research.call_args.kwargs["timeout_seconds"], 600)

    def test_stock_profile_report_cleanup_drops_meta_only_output(self):
        cleaned = StockAnalysisPipeline._clean_stock_profile_report(
            "如果你愿意，我可以下一步把这些内容整理成模板。"
        )

        self.assertEqual(cleaned, "")

    def test_stock_profile_research_failure_blocks_report_generation(self):
        pipeline = self._make_pipeline(agent_mode=True)

        with patch("src.agent.factory.get_tool_registry", return_value=MagicMock()), patch(
            "src.agent.llm_adapter.LLMToolAdapter",
            return_value=MagicMock(),
        ), patch("src.agent.research.ResearchAgent") as research_agent:
            research_agent.return_value.research.side_effect = RuntimeError("research failed")
            with self.assertRaises(RuntimeError):
                pipeline._build_deep_research_stock_profile("600519", "贵州茅台", {"report_language": "zh"})

    def test_analyzer_prompt_includes_blocking_deep_research_profile(self):
        analyzer = self._make_analyzer()
        prompt = analyzer._format_prompt(
            {
                "code": "600519",
                "stock_name": "贵州茅台",
                "date": "2026-05-28",
                "today": {},
                "stock_profile": {
                    "research_report": "## 公司概况\n\n主营高端白酒。",
                    "research_method": "deep_research",
                    "research_sources": ["主营业务是什么"],
                },
            },
            "贵州茅台",
            report_language="zh",
        )

        self.assertIn("Deep Research 股票基本情况", prompt)
        self.assertIn("主营高端白酒", prompt)
        self.assertIn("stock_profile", prompt)

    def test_analyze_stock_injects_profile_before_formal_report_and_keeps_it_authoritative(self):
        pipeline = self._make_pipeline_for_analyze_stock()
        research_result = SimpleNamespace(
            success=True,
            report="## 公司概况\n\n主营高端白酒。",
            sub_questions=["主营业务是什么"],
            total_tokens=123,
        )
        llm_result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=78,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="基本面稳健",
            stock_profile={"research_report": "LLM 生成的错误 profile"},
        )
        pipeline.analyzer.analyze.return_value = llm_result

        with patch("src.agent.factory.get_tool_registry", return_value=MagicMock()), patch(
            "src.agent.llm_adapter.LLMToolAdapter",
            return_value=MagicMock(),
        ), patch("src.agent.research.ResearchAgent") as research_agent:
            research_agent.return_value.research.return_value = research_result
            result = pipeline.analyze_stock("600519", ReportType.SIMPLE, "q1")

        self.assertIs(result, llm_result)
        formal_context = pipeline.analyzer.analyze.call_args.args[0]
        self.assertEqual(formal_context["stock_profile"]["research_method"], "deep_research")
        self.assertIn("主营高端白酒", formal_context["stock_profile"]["research_report"])
        self.assertEqual(result.stock_profile["research_method"], "deep_research")
        self.assertIn("主营高端白酒", result.stock_profile["research_report"])
        self.assertNotEqual(result.stock_profile["research_report"], "LLM 生成的错误 profile")

    def test_agent_executor_user_message_includes_deep_research_profile(self):
        executor = AgentExecutor.__new__(AgentExecutor)
        message = executor._build_user_message(
            "请分析股票 600519",
            {
                "stock_code": "600519",
                "report_language": "zh",
                "stock_profile": {
                    "research_report": "## 公司概况\n\n主营高端白酒。",
                    "research_method": "deep_research",
                },
            },
        )

        self.assertIn("系统已完成的 Deep Research 股票基本情况", message)
        self.assertIn("主营高端白酒", message)

    def test_orchestrator_context_carries_deep_research_profile_to_agents(self):
        orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
        ctx = orchestrator._build_context(
            "请分析股票 600519",
            {
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "report_language": "zh",
                "stock_profile": {
                    "research_report": "## 公司概况\n\n主营高端白酒。",
                    "research_method": "deep_research",
                },
            },
        )

        self.assertEqual(ctx.get_data("stock_profile")["research_method"], "deep_research")
        self.assertIn("主营高端白酒", ctx.get_data("stock_profile")["research_report"])


if __name__ == "__main__":
    unittest.main()
