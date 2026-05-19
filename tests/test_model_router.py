# -*- coding: utf-8 -*-
"""Unit tests for src/users/model_router.py.

Covers resolve_model_route() with the new platform_primary_model /
platform_models parameters, BYOK path, plan-filtered path, and the
as_litellm_kwargs helper.
"""

import sys
import unittest
from unittest.mock import MagicMock, patch

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from src.users.model_router import (
    ModelRoute,
    _filter_allowed_models,
    as_litellm_kwargs,
    resolve_model_route,
)


class TestModelRoute(unittest.TestCase):
    def test_uses_byok_true(self):
        r = ModelRoute(source="byok", primary_model="gpt-4o", models_to_try=["gpt-4o"])
        self.assertTrue(r.uses_byok)

    def test_uses_byok_false(self):
        r = ModelRoute(source="platform", primary_model="gemini/gemini-2.0-flash", models_to_try=[])
        self.assertFalse(r.uses_byok)


class TestFilterAllowedModels(unittest.TestCase):
    def test_empty_allowed_returns_all(self):
        self.assertEqual(_filter_allowed_models(["a", "b"], []), ["a", "b"])

    def test_filters_to_intersection(self):
        self.assertEqual(_filter_allowed_models(["a", "b", "c"], ["b", "c"]), ["b", "c"])

    def test_none_match_returns_empty(self):
        self.assertEqual(_filter_allowed_models(["a"], ["z"]), [])


class TestAsLitellmKwargs(unittest.TestCase):
    def test_byok_route_includes_api_key_and_base(self):
        r = ModelRoute(
            source="byok",
            primary_model="openai/gpt-4o",
            models_to_try=["openai/gpt-4o"],
            api_key="sk-test",
            api_base="https://custom.api",
        )
        kwargs = as_litellm_kwargs(r)
        self.assertEqual(kwargs["model"], "openai/gpt-4o")
        self.assertEqual(kwargs["api_key"], "sk-test")
        self.assertEqual(kwargs["api_base"], "https://custom.api")

    def test_platform_route_omits_key_and_base(self):
        r = ModelRoute(
            source="platform",
            primary_model="gemini/gemini-2.0-flash",
            models_to_try=["gemini/gemini-2.0-flash"],
        )
        kwargs = as_litellm_kwargs(r)
        self.assertEqual(kwargs["model"], "gemini/gemini-2.0-flash")
        self.assertNotIn("api_key", kwargs)
        self.assertNotIn("api_base", kwargs)


class TestResolveModelRoute(unittest.TestCase):
    def _make_db(self):
        return MagicMock()

    def _make_config(self, primary="gemini/gemini-2.0-flash", fallbacks=None):
        cfg = MagicMock()
        cfg.litellm_model = primary
        cfg.litellm_fallback_models = fallbacks or []
        cfg.llm_model_list = []
        return cfg

    def test_no_user_returns_platform_route(self):
        db = self._make_db()
        cfg = self._make_config()
        with patch("src.users.model_router.get_effective_agent_models_to_try", return_value=["gemini/gemini-2.0-flash"]), \
             patch("src.users.model_router.get_effective_agent_primary_model", return_value="gemini/gemini-2.0-flash"):
            route = resolve_model_route(db, user=None, config=cfg)
        self.assertEqual(route.source, "platform")
        self.assertFalse(route.uses_byok)

    def test_explicit_platform_models_respected(self):
        db = self._make_db()
        cfg = self._make_config()
        explicit = ["openai/gpt-4o", "anthropic/claude-3-5-sonnet"]
        with patch("src.users.model_router.get_effective_agent_models_to_try", return_value=["default"]), \
             patch("src.users.model_router.get_effective_agent_primary_model", return_value="default"), \
             patch("src.users.model_router.resolve_user_plan") as mock_plan, \
             patch("src.users.model_router.load_user_mode_settings", return_value=MagicMock()):
            mock_plan.return_value = MagicMock(can_byok=False, allowed_models=[])
            user = MagicMock()
            route = resolve_model_route(db, user=user, config=cfg, platform_models=explicit)
        self.assertEqual(route.models_to_try, explicit)

    def test_plan_allowed_models_filters_platform_list(self):
        db = self._make_db()
        cfg = self._make_config()
        platform = ["gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet"]
        allowed = ["gpt-4o-mini"]
        with patch("src.users.model_router.get_effective_agent_models_to_try", return_value=platform), \
             patch("src.users.model_router.get_effective_agent_primary_model", return_value="gpt-4o"), \
             patch("src.users.model_router.resolve_user_plan") as mock_plan, \
             patch("src.users.model_router.load_user_mode_settings", return_value=MagicMock()):
            mock_plan.return_value = MagicMock(can_byok=False, allowed_models=allowed)
            user = MagicMock()
            route = resolve_model_route(db, user=user, config=cfg, platform_models=platform)
        self.assertEqual(route.models_to_try, ["gpt-4o-mini"])
        self.assertEqual(route.primary_model, "gpt-4o-mini")

    def test_byok_route_returned_when_can_byok_and_credential_found(self):
        db = self._make_db()
        cfg = self._make_config()
        byok = ModelRoute(
            source="byok",
            primary_model="openai/gpt-4o",
            models_to_try=["openai/gpt-4o"],
            api_key="sk-user",
        )
        with patch("src.users.model_router.get_effective_agent_models_to_try", return_value=[]), \
             patch("src.users.model_router.get_effective_agent_primary_model", return_value="gemini/gemini-2.0-flash"), \
             patch("src.users.model_router.resolve_user_plan") as mock_plan, \
             patch("src.users.model_router.load_user_mode_settings", return_value=MagicMock()), \
             patch("src.users.model_router._select_byok_credential", return_value=byok):
            mock_plan.return_value = MagicMock(can_byok=True, allowed_models=[])
            user = MagicMock()
            route = resolve_model_route(db, user=user, config=cfg)
        self.assertTrue(route.uses_byok)
        self.assertEqual(route.api_key, "sk-user")


class TestLLMToolAdapterRouting(unittest.TestCase):
    """Smoke-test that LLMToolAdapter stores user_id and routes correctly."""

    def _make_adapter(self, user_id=None):
        from src.agent.llm_adapter import LLMToolAdapter
        cfg = MagicMock()
        cfg.llm_model_list = []
        cfg.llm_channel_config = None
        cfg.litellm_model = "gemini/gemini-2.0-flash"
        cfg.litellm_fallback_models = []
        cfg.llm_temperature = 0.7
        cfg.llm_max_tokens = 4096
        with patch("src.agent.llm_adapter.Router"):
            adapter = LLMToolAdapter(cfg, user_id=user_id)
        return adapter

    def test_user_id_stored(self):
        adapter = self._make_adapter(user_id=42)
        self.assertEqual(adapter._user_id, 42)

    def test_no_user_id_resolve_returns_none(self):
        adapter = self._make_adapter(user_id=None)
        result = adapter._resolve_user_model_route(["gemini/gemini-2.0-flash"])
        self.assertIsNone(result)

    def test_resolve_returns_none_when_user_not_found(self):
        adapter = self._make_adapter(user_id=999)
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = None
        mock_db = MagicMock()
        mock_db.session_scope.return_value.__enter__ = lambda s, *a: mock_session
        mock_db.session_scope.return_value.__exit__ = MagicMock(return_value=False)
        with patch("src.agent.llm_adapter.get_db", return_value=mock_db):
            result = adapter._resolve_user_model_route(["gemini/gemini-2.0-flash"])
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
