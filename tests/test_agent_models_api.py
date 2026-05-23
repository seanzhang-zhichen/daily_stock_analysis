# -*- coding: utf-8 -*-
"""Tests for the Agent models discovery service and endpoint."""

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from api.v1.endpoints import agent
from src.config import Config
from src.services.agent_model_service import list_agent_model_deployments


def _build_config(**overrides):
    config = Config(
        litellm_model="gemini/gemini-2.5-flash",
        litellm_fallback_models=["openai/gpt-4o-mini"],
        llm_model_list=[],
        llm_channels=[],
        litellm_config_path=None,
        llm_models_source="",
        openai_base_url=None,
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


class AgentModelsApiTestCase(unittest.TestCase):
    def test_models_endpoint_returns_litellm_config_deployments(self) -> None:
        config = _build_config(
            litellm_config_path="config/litellm.yaml",
            llm_models_source="litellm_config",
            llm_model_list=[
                {
                    "model_name": "gemini-primary",
                    "litellm_params": {"model": "gemini/gemini-2.5-flash", "api_key": "secret-1"},
                },
                {
                    "model_name": "openai-fallback",
                    "litellm_params": {"model": "openai/gpt-4o-mini", "api_key": "secret-2"},
                },
            ],
        )

        deployments = list_agent_model_deployments(config)

        self.assertEqual(len(deployments), 2)
        self.assertEqual(deployments[0]["source"], "litellm_config")
        self.assertTrue(deployments[0]["is_primary"])
        self.assertFalse("api_key" in str(deployments))

    def test_models_endpoint_returns_channel_deployments_with_api_base(self) -> None:
        config = _build_config(
            llm_channels=[{"name": "openai"}],
            llm_models_source="llm_channels",
            llm_model_list=[
                {
                    "model_name": "openai/gpt-4o-mini",
                    "litellm_params": {
                        "model": "openai/gpt-4o-mini",
                        "api_key": "secret-1",
                        "api_base": "https://api.example.com/v1",
                    },
                }
            ],
        )

        deployments = list_agent_model_deployments(config)

        self.assertEqual(deployments[0]["source"], "llm_channels")
        self.assertEqual(deployments[0]["api_base"], "https://api.example.com/v1")

    def test_models_endpoint_uses_agent_primary_override_for_primary_marker(self) -> None:
        config = _build_config(
            litellm_model="gemini/gemini-2.5-flash",
            litellm_fallback_models=["openai/gpt-4o-mini"],
            agent_litellm_model="openai/gpt-4o-mini",
            llm_channels=[{"name": "mixed"}],
            llm_models_source="llm_channels",
            llm_model_list=[
                {
                    "model_name": "gemini/gemini-2.5-flash",
                    "litellm_params": {"model": "gemini/gemini-2.5-flash", "api_key": "secret-g"},
                },
                {
                    "model_name": "openai/gpt-4o-mini",
                    "litellm_params": {"model": "openai/gpt-4o-mini", "api_key": "secret-o"},
                },
            ],
        )

        deployments = list_agent_model_deployments(config)
        by_model = {item["model"]: item for item in deployments}

        self.assertTrue(by_model["openai/gpt-4o-mini"]["is_primary"])
        self.assertFalse(by_model["openai/gpt-4o-mini"]["is_fallback"])
        self.assertFalse(by_model["gemini/gemini-2.5-flash"]["is_primary"])
        self.assertFalse(by_model["gemini/gemini-2.5-flash"]["is_fallback"])

    def test_models_endpoint_returns_empty_for_direct_env_primary_without_router_deployments(self) -> None:
        config = _build_config(
            litellm_model="cohere/command-r-plus",
            litellm_fallback_models=[],
            llm_model_list=[],
        )

        deployments = list_agent_model_deployments(config)

        self.assertEqual(deployments, [])

    def test_models_endpoint_returns_empty_list_when_no_model_is_configured(self) -> None:
        config = _build_config(
            litellm_model="",
            litellm_fallback_models=[],
            llm_model_list=[],
        )

        self.assertEqual(list_agent_model_deployments(config), [])


class AgentModelsEndpointTestCase(unittest.TestCase):
    def test_endpoint_returns_sorted_models_without_secrets(self) -> None:
        config = _build_config(
            llm_channels=[{"name": "primary"}, {"name": "secondary"}],
            llm_model_list=[
                {
                    "model_name": "openai/gpt-4o-mini",
                    "litellm_params": {
                        "model": "openai/gpt-4o-mini",
                        "api_key": "secret-openai",
                        "api_base": "https://api.openai.example/v1",
                    },
                },
                {
                    "model_name": "gemini/gemini-2.5-flash",
                    "litellm_params": {
                        "model": "gemini/gemini-2.5-flash",
                        "api_key": "secret-gemini",
                    },
                },
            ],
        )

        with patch("api.v1.endpoints.agent.get_config", return_value=config):
            payload = asyncio.run(agent.get_agent_models()).model_dump()

        self.assertEqual(len(payload["models"]), 2)
        self.assertEqual(payload["models"][0]["model"], "gemini/gemini-2.5-flash")
        self.assertTrue(payload["models"][0]["is_primary"])
        self.assertEqual(payload["models"][1]["model"], "openai/gpt-4o-mini")
        self.assertTrue(payload["models"][1]["is_fallback"])
        self.assertNotIn("api_key", str(payload))


class AgentSkillsEndpointTestCase(unittest.TestCase):
    def test_skills_endpoint_returns_skill_metadata_shape(self) -> None:
        config = _build_config()
        skill_manager = SimpleNamespace(
            list_skills=lambda: [
                SimpleNamespace(
                    name="bull_trend",
                    display_name="多头趋势",
                    description="趋势跟随",
                    user_invocable=True,
                    default_priority=20,
                    default_active=True,
                ),
                SimpleNamespace(
                    name="chan_theory",
                    display_name="缠论",
                    description="结构分析",
                    user_invocable=True,
                    default_priority=40,
                    default_active=False,
                ),
            ]
        )

        with patch("api.v1.endpoints.agent.get_config", return_value=config), patch(
            "src.agent.factory.get_skill_manager",
            return_value=skill_manager,
        ):
            payload = asyncio.run(agent.get_skills()).model_dump()

        self.assertEqual(payload["default_skill_id"], "bull_trend")
        self.assertEqual([item["id"] for item in payload["skills"]], ["bull_trend", "chan_theory"])

    def test_legacy_strategies_endpoint_preserves_legacy_field_names(self) -> None:
        config = _build_config()
        skill_manager = SimpleNamespace(
            list_skills=lambda: [
                SimpleNamespace(
                    name="bull_trend",
                    display_name="多头趋势",
                    description="趋势跟随",
                    user_invocable=True,
                    default_priority=20,
                    default_active=True,
                ),
            ]
        )

        with patch("api.v1.endpoints.agent.get_config", return_value=config), patch(
            "src.agent.factory.get_skill_manager",
            return_value=skill_manager,
        ):
            payload = asyncio.run(agent.get_strategies()).model_dump()

        self.assertNotIn("skills", payload)
        self.assertEqual(payload["default_strategy_id"], "bull_trend")
        self.assertEqual(
            payload["strategies"],
            [
                {
                    "id": "bull_trend",
                    "name": "多头趋势",
                    "description": "趋势跟随",
                }
            ],
        )

    def test_chat_request_empty_skills_clears_context_without_triggering_activate_all(self) -> None:
        config = SimpleNamespace(is_agent_available=lambda: True)
        executor = MagicMock()
        executor.chat.return_value = SimpleNamespace(success=True, content="ok", error=None)
        request = agent.ChatRequest(message="hello", skills=[], context={"skills": ["old_skill"]})
        real_get_running_loop = asyncio.get_running_loop

        class _ImmediateLoop:
            def __init__(self, loop):
                self._loop = loop

            def run_in_executor(self, _executor, func):
                future = self._loop.create_future()
                future.set_result(func())
                return future

        with patch("api.v1.endpoints.agent.get_config", return_value=config), patch(
            "api.v1.endpoints.agent._build_executor",
            return_value=executor,
        ) as mock_build_executor, patch(
            "api.v1.endpoints.agent.asyncio.get_running_loop",
            side_effect=lambda: _ImmediateLoop(real_get_running_loop()),
        ):
            payload = asyncio.run(agent.agent_chat(request)).model_dump()

        mock_build_executor.assert_called_once_with(config, None)
        executor.chat.assert_called_once()
        self.assertEqual(executor.chat.call_args.kwargs["context"]["skills"], [])
        self.assertEqual(payload["content"], "ok")

    def test_agent_chat_refunds_quota_when_executor_returns_failure(self) -> None:
        config = SimpleNamespace(is_agent_available=lambda: True)
        executor = MagicMock()
        executor.chat.return_value = SimpleNamespace(success=False, content="", error="tool failed")
        request = agent.ChatRequest(message="hello")
        user = SimpleNamespace(id=7)
        db = MagicMock()
        outcome = SimpleNamespace(exceeded=False, consumed=True, on_date=None)
        real_get_running_loop = asyncio.get_running_loop

        class _ImmediateLoop:
            def __init__(self, loop):
                self._loop = loop

            def run_in_executor(self, _executor, func):
                future = self._loop.create_future()
                future.set_result(func())
                return future

        with patch("api.v1.endpoints.agent.get_config", return_value=config), patch(
            "api.v1.endpoints.agent._build_executor",
            return_value=executor,
        ), patch(
            "api.v1.endpoints.agent.enforce_quota",
            return_value=outcome,
        ), patch(
            "api.v1.endpoints.agent.refund_quota",
        ) as refund_mock, patch(
            "api.v1.endpoints.agent.asyncio.get_running_loop",
            side_effect=lambda: _ImmediateLoop(real_get_running_loop()),
        ):
            payload = asyncio.run(agent.agent_chat(request, db=db, current_user=user)).model_dump()

        self.assertFalse(payload["success"])
        refund_mock.assert_called_once_with(db, user=user, kind="agent", on_date=None)
        self.assertEqual(db.commit.call_count, 2)

    def test_agent_research_refunds_quota_when_result_is_unsuccessful(self) -> None:
        async def failed_research(*_args, **_kwargs):
            return SimpleNamespace(
                timed_out=False,
                success=False,
                report="",
                sub_questions=[],
                total_tokens=0,
                error="research failed",
            )

        config = SimpleNamespace(
            is_agent_available=lambda: True,
            agent_deep_research_budget=30000,
            agent_deep_research_timeout=180,
        )
        request = agent.ResearchRequest(question="why")
        user = SimpleNamespace(id=7)
        db = MagicMock()
        outcome = SimpleNamespace(exceeded=False, consumed=True, on_date=None)

        with patch("api.v1.endpoints.agent.get_config", return_value=config), patch(
            "api.v1.endpoints.agent.enforce_quota",
            return_value=outcome,
        ), patch(
            "api.v1.endpoints.agent.refund_quota",
        ) as refund_mock, patch(
            "src.agent.factory.get_tool_registry",
            return_value=MagicMock(),
        ), patch(
            "src.agent.llm_adapter.LLMToolAdapter",
            return_value=MagicMock(),
        ), patch(
            "src.agent.research.ResearchAgent",
            return_value=MagicMock(),
        ), patch(
            "api.v1.endpoints.agent._run_research_in_background",
            side_effect=failed_research,
        ):
            payload = asyncio.run(agent.agent_research(request, db=db, current_user=user)).model_dump()

        self.assertFalse(payload["success"])
        refund_mock.assert_called_once_with(db, user=user, kind="agent", on_date=None)
        self.assertEqual(db.commit.call_count, 2)


class AgentModelsSourceDetectionTestCase(unittest.TestCase):
    @patch("src.config.setup_env")
    @patch.object(Config, "_parse_litellm_yaml", return_value=[])
    def test_load_from_env_marks_channels_as_actual_source_after_yaml_fallback(
        self,
        _mock_parse_yaml,
        _mock_setup_env,
    ) -> None:
        env = {
            "LITELLM_CONFIG": "config/missing.yaml",
            "LLM_CHANNELS": "primary",
            "LLM_PRIMARY_API_KEY": "channel-secret-key",
            "LLM_PRIMARY_MODELS": "openai/gpt-4o-mini",
            "OPENAI_API_KEY": "",
            "AIHUBMIX_KEY": "",
            "GEMINI_API_KEY": "",
            "ANTHROPIC_API_KEY": "",
            "DEEPSEEK_API_KEY": "",
        }

        with patch.dict(os.environ, env, clear=True):
            config = Config._load_from_env()

        self.assertEqual(config.llm_models_source, "llm_channels")
        self.assertEqual(config.llm_model_list[0]["litellm_params"]["model"], "openai/gpt-4o-mini")

    @patch("src.config.setup_env")
    @patch.object(Config, "_parse_litellm_yaml", return_value=[])
    def test_load_from_env_does_not_create_model_list_after_yaml_fallback(
        self,
        _mock_parse_yaml,
        _mock_setup_env,
    ) -> None:
        env = {
            "LITELLM_CONFIG": "config/missing.yaml",
            "LLM_CHANNELS": "",
            "OPENAI_API_KEY": "direct-openai-key",
            "LITELLM_MODEL": "gpt-4o-mini",
            "AIHUBMIX_KEY": "",
            "GEMINI_API_KEY": "",
            "ANTHROPIC_API_KEY": "",
            "DEEPSEEK_API_KEY": "",
        }

        with patch.dict(os.environ, env, clear=True):
            config = Config._load_from_env()

        self.assertEqual(config.llm_models_source, "")
        self.assertEqual(config.llm_model_list, [])


if __name__ == "__main__":
    unittest.main()
