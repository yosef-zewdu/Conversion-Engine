"""
Tests for AIMaturityScorer (Req 3.1, 3.2, 3.3).
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from signal_pipeline.ai_maturity_scorer import (
    AIMaturityInput,
    AIMaturityResult,
    AIMaturityScorer,
    _assign_confidence,
    _ratio_to_score,
    _score_ai_adjacent_open_roles,
    _score_bool_signal,
)
from signal_pipeline.models import AIMaturityJustificationEntry


# ---------------------------------------------------------------------------
# _score_ai_adjacent_open_roles
# ---------------------------------------------------------------------------


class TestScoreAiAdjacentOpenRoles:
    def test_none_returns_zero(self):
        assert _score_ai_adjacent_open_roles(None) == 0.0

    def test_zero_returns_zero(self):
        assert _score_ai_adjacent_open_roles(0) == 0.0

    def test_one_returns_low(self):
        assert _score_ai_adjacent_open_roles(1) == 0.3

    def test_two_returns_low(self):
        assert _score_ai_adjacent_open_roles(2) == 0.3

    def test_three_returns_medium(self):
        assert _score_ai_adjacent_open_roles(3) == 0.6

    def test_four_returns_medium(self):
        assert _score_ai_adjacent_open_roles(4) == 0.6

    def test_five_returns_high(self):
        assert _score_ai_adjacent_open_roles(5) == 1.0

    def test_large_number_returns_high(self):
        assert _score_ai_adjacent_open_roles(100) == 1.0


# ---------------------------------------------------------------------------
# _score_bool_signal
# ---------------------------------------------------------------------------


class TestScoreBoolSignal:
    def test_true_returns_one(self):
        assert _score_bool_signal(True) == 1.0

    def test_false_returns_zero(self):
        assert _score_bool_signal(False) == 0.0

    def test_none_returns_zero(self):
        assert _score_bool_signal(None) == 0.0


# ---------------------------------------------------------------------------
# _ratio_to_score
# ---------------------------------------------------------------------------


class TestRatioToScore:
    def test_zero_ratio_returns_zero(self):
        assert _ratio_to_score(0.0) == 0

    def test_below_threshold_1_returns_zero(self):
        assert _ratio_to_score(0.14) == 0

    def test_at_threshold_1_returns_one(self):
        assert _ratio_to_score(0.15) == 1

    def test_below_threshold_2_returns_one(self):
        assert _ratio_to_score(0.39) == 1

    def test_at_threshold_2_returns_two(self):
        assert _ratio_to_score(0.40) == 2

    def test_below_threshold_3_returns_two(self):
        assert _ratio_to_score(0.69) == 2

    def test_at_threshold_3_returns_three(self):
        assert _ratio_to_score(0.70) == 3

    def test_one_returns_three(self):
        assert _ratio_to_score(1.0) == 3


# ---------------------------------------------------------------------------
# _assign_confidence
# ---------------------------------------------------------------------------


class TestAssignConfidence:
    def test_no_high_signals_returns_low(self):
        inputs = AIMaturityInput()
        assert _assign_confidence(inputs) == "low"

    def test_only_roles_signal_returns_low(self):
        inputs = AIMaturityInput(ai_adjacent_open_roles=3)
        assert _assign_confidence(inputs) == "low"

    def test_only_leadership_signal_returns_low(self):
        inputs = AIMaturityInput(named_ai_ml_leadership=True)
        assert _assign_confidence(inputs) == "low"

    def test_both_high_signals_returns_medium(self):
        inputs = AIMaturityInput(ai_adjacent_open_roles=5, named_ai_ml_leadership=True)
        assert _assign_confidence(inputs) == "medium"

    def test_zero_roles_does_not_count(self):
        inputs = AIMaturityInput(ai_adjacent_open_roles=0, named_ai_ml_leadership=True)
        assert _assign_confidence(inputs) == "low"

    def test_false_leadership_does_not_count(self):
        inputs = AIMaturityInput(ai_adjacent_open_roles=5, named_ai_ml_leadership=False)
        assert _assign_confidence(inputs) == "low"


# ---------------------------------------------------------------------------
# AIMaturityScorer — score integration tests
# ---------------------------------------------------------------------------


class TestAIMaturityScorer:
    def setup_method(self):
        self.scorer = AIMaturityScorer()

    def test_all_none_returns_score_zero(self):
        """No signals → weighted_sum = 0 → score 0."""
        result = self.scorer.score(AIMaturityInput())
        assert result.score == 0

    def test_all_none_confidence_is_low(self):
        result = self.scorer.score(AIMaturityInput())
        assert result.confidence == "low"

    def test_all_signals_max_returns_score_three(self):
        """All signals at maximum → ratio = 1.0 → score 3."""
        inputs = AIMaturityInput(
            ai_adjacent_open_roles=10,
            named_ai_ml_leadership=True,
            github_ai_activity=True,
            executive_ai_commentary=True,
            modern_data_ml_stack=True,
            strategic_communications=True,
        )
        result = self.scorer.score(inputs)
        assert result.score == 3

    def test_all_signals_max_confidence_is_medium(self):
        """Both high-weight signals present → confidence medium."""
        inputs = AIMaturityInput(
            ai_adjacent_open_roles=10,
            named_ai_ml_leadership=True,
            github_ai_activity=True,
            executive_ai_commentary=True,
            modern_data_ml_stack=True,
            strategic_communications=True,
        )
        result = self.scorer.score(inputs)
        assert result.confidence == "medium"

    def test_only_low_weight_signals_score_is_low(self):
        """Only low-weight signals → weighted_sum = 2, ratio ≈ 0.167 → score 1."""
        inputs = AIMaturityInput(modern_data_ml_stack=True, strategic_communications=True)
        result = self.scorer.score(inputs)
        assert result.score == 1

    def test_only_low_weight_signals_confidence_is_low(self):
        inputs = AIMaturityInput(modern_data_ml_stack=True, strategic_communications=True)
        result = self.scorer.score(inputs)
        assert result.confidence == "low"

    def test_high_weight_roles_only_score(self):
        """5+ roles → roles_raw=1.0, weighted_sum=3.0, ratio=0.25 → score 1."""
        inputs = AIMaturityInput(ai_adjacent_open_roles=5)
        result = self.scorer.score(inputs)
        assert result.score == 1

    def test_both_high_weight_signals_score(self):
        """Both high signals → weighted_sum=6.0, ratio=0.5 → score 2."""
        inputs = AIMaturityInput(ai_adjacent_open_roles=5, named_ai_ml_leadership=True)
        result = self.scorer.score(inputs)
        assert result.score == 2

    def test_both_high_and_medium_signals_score(self):
        """High + medium signals → weighted_sum=10.0, ratio≈0.833 → score 3."""
        inputs = AIMaturityInput(
            ai_adjacent_open_roles=5,
            named_ai_ml_leadership=True,
            github_ai_activity=True,
            executive_ai_commentary=True,
        )
        result = self.scorer.score(inputs)
        assert result.score == 3

    def test_justification_has_six_entries(self):
        """Req 3.2: one justification entry per signal."""
        result = self.scorer.score(AIMaturityInput())
        assert len(result.justification) == 6

    def test_justification_signal_names(self):
        """Req 3.2: justification entries have correct signal names."""
        result = self.scorer.score(AIMaturityInput())
        names = [e.signal for e in result.justification]
        assert names == [
            "ai_adjacent_open_roles",
            "named_ai_ml_leadership",
            "github_ai_activity",
            "executive_ai_commentary",
            "modern_data_ml_stack",
            "strategic_communications",
        ]

    def test_justification_weights(self):
        """Req 3.2: justification entries carry correct weight labels."""
        result = self.scorer.score(AIMaturityInput())
        weights = {e.signal: e.weight for e in result.justification}
        assert weights["ai_adjacent_open_roles"] == "high"
        assert weights["named_ai_ml_leadership"] == "high"
        assert weights["github_ai_activity"] == "medium"
        assert weights["executive_ai_commentary"] == "medium"
        assert weights["modern_data_ml_stack"] == "low"
        assert weights["strategic_communications"] == "low"

    def test_justification_values_reflect_input(self):
        """Req 3.2: justification value field matches the raw input."""
        inputs = AIMaturityInput(
            ai_adjacent_open_roles=3,
            named_ai_ml_leadership=True,
            github_ai_activity=False,
        )
        result = self.scorer.score(inputs)
        by_signal = {e.signal: e for e in result.justification}
        assert by_signal["ai_adjacent_open_roles"].value == 3
        assert by_signal["named_ai_ml_leadership"].value is True
        assert by_signal["github_ai_activity"].value is False

    def test_justification_strings_are_non_empty(self):
        """Req 3.2: every justification entry has a non-empty string."""
        result = self.scorer.score(AIMaturityInput(ai_adjacent_open_roles=2, named_ai_ml_leadership=True))
        for entry in result.justification:
            assert isinstance(entry.justification, str)
            assert len(entry.justification) > 0

    def test_req_3_3_fewer_than_2_high_signals_confidence_low(self):
        """Req 3.3: fewer than 2 high-weight signals → confidence 'low'."""
        inputs = AIMaturityInput(
            ai_adjacent_open_roles=None,
            named_ai_ml_leadership=None,
            github_ai_activity=True,
            executive_ai_commentary=True,
            modern_data_ml_stack=True,
            strategic_communications=True,
        )
        result = self.scorer.score(inputs)
        assert result.confidence == "low"

    def test_score_boundary_ratio_0_15(self):
        """Boundary: ratio exactly 0.15 → score 1 (not 0)."""
        # weighted_sum = 0.15 * 12 = 1.8 → only achievable via roles: 1-2 roles = 0.3 * 3 = 0.9
        # Add strategic_communications=True → 0.9 + 1.0 = 1.9 → ratio ≈ 0.158 → score 1
        inputs = AIMaturityInput(ai_adjacent_open_roles=1, strategic_communications=True)
        result = self.scorer.score(inputs)
        assert result.score == 1

    def test_result_is_ai_maturity_result_instance(self):
        result = self.scorer.score(AIMaturityInput())
        assert isinstance(result, AIMaturityResult)

    def test_justification_entries_are_correct_type(self):
        result = self.scorer.score(AIMaturityInput())
        for entry in result.justification:
            assert isinstance(entry, AIMaturityJustificationEntry)


# ---------------------------------------------------------------------------
# Property 4: AI Maturity Score Range
# Feature: conversion-engine, Property 4: AI Maturity Score Range
# ---------------------------------------------------------------------------

_optional_int = st.one_of(st.none(), st.integers(min_value=0, max_value=50))
_optional_bool = st.one_of(st.none(), st.booleans())

_ai_maturity_input_strategy = st.builds(
    AIMaturityInput,
    ai_adjacent_open_roles=_optional_int,
    named_ai_ml_leadership=_optional_bool,
    github_ai_activity=_optional_bool,
    executive_ai_commentary=_optional_bool,
    modern_data_ml_stack=_optional_bool,
    strategic_communications=_optional_bool,
)


@given(_ai_maturity_input_strategy)
@settings(max_examples=100)
def test_score_always_integer_in_range(inputs: AIMaturityInput) -> None:
    """
    Property 4: For any AIMaturityInput, score is always an integer in [0, 3].

    Validates: Requirements 3.1
    # Feature: conversion-engine, Property 4: AI Maturity Score Range
    """
    scorer = AIMaturityScorer()
    result = scorer.score(inputs)
    assert isinstance(result.score, int)
    assert 0 <= result.score <= 3
