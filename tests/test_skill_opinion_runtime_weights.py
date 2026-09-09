from types import SimpleNamespace

from src.services.skill_opinion_outcome_service import SkillOpinionOutcomeService
from src.services.skill_opinion_weight_service import (
    MAX_SKILL_OPINION_WEIGHT_FACTOR,
    MIN_SKILL_OPINION_WEIGHT_FACTOR,
    SkillOpinionWeightService,
)


class _Rows:
    def __init__(self, rows): self.rows = rows
    def all(self): return self.rows


class _Session:
    def __init__(self, rows): self.rows = rows
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, statement): return _Rows(self.rows)


class _Db:
    def __init__(self, rows): self.rows = rows
    def get_session(self): return _Session(self.rows)


def test_beta_prior_weight_is_bounded_and_neutral_below_sample_threshold():
    service = SkillOpinionWeightService(_Db([
        ("good", 30, 30),
        ("too_small", 29, 29),
    ]))
    weights = service.compute_weights(["good", "too_small", "missing"])

    assert 1.0 < weights["good"] <= MAX_SKILL_OPINION_WEIGHT_FACTOR
    assert weights["too_small"] == 1.0
    assert weights["missing"] == 1.0


def test_beta_prior_prevents_extreme_negative_weight():
    service = SkillOpinionWeightService(_Db([("bad", 30, 0)]))
    weight = service.compute_weights(["bad"])["bad"]

    assert MIN_SKILL_OPINION_WEIGHT_FACTOR <= weight < 1.0


def test_outcome_evaluator_handles_bullish_bearish_and_missing_data():
    bars = [SimpleNamespace(close=value) for value in (10, 11, 12, 13)]
    assert SkillOpinionOutcomeService._evaluate("buy", bars, 3)[:3] == ("evaluated", "hit", True)
    assert SkillOpinionOutcomeService._evaluate("sell", bars, 3)[:3] == ("evaluated", "miss", False)
    assert SkillOpinionOutcomeService._evaluate("hold", bars, 3)[:3] == ("observational", "observational", None)
    assert SkillOpinionOutcomeService._evaluate("buy", bars, 10)[:3] == ("pending", None, None)
