"""
Tests for ICP Classifier (icp_classifier/classifier.py) and
Bench-to-Brief Match (icp_classifier/bench_match.py).

Requirements: 5.1–5.7, 7.1–7.3
"""
from __future__ import annotations

from datetime import date, timedelta, timezone, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from config.models import Segment, SegmentResult
from icp_classifier.bench_match import BenchMatchResult, BenchToBriefMatch
from icp_classifier.classifier import ClassifierConfig, classify
from signal_pipeline.models import (
    FundingEvent,
    HiringSignalBrief,
    LayoffEvent,
    LeadershipChange,
    TechStack,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TODAY = datetime.now(timezone.utc).date()


def _date_str(days_ago: int) -> str:
    """Return an ISO date string for N days ago."""
    return (_TODAY - timedelta(days=days_ago)).isoformat()


def _base_brief(**kwargs) -> HiringSignalBrief:
    """Build a minimal valid HiringSignalBrief with optional overrides."""
    defaults = dict(
        schema_version="1.0",
        company_id="test-co",
        company_name="Test Co",
        last_enriched_at=_TODAY.isoformat() + "T00:00:00Z",
        icp_segment=None,
        icp_confidence=None,
        icp_signals_used=[],
    )
    defaults.update(kwargs)
    return HiringSignalBrief(**defaults)


def _default_config(**kwargs) -> ClassifierConfig:
    """Build a ClassifierConfig with default threshold 0.4."""
    return ClassifierConfig(abstention_threshold=0.4, **kwargs)


# ---------------------------------------------------------------------------
# Segment 1 — Series A/B funding $5–30M, last 6 months
# ---------------------------------------------------------------------------


class TestSegment1:
    def _brief_with_funding(self, round_type, amount_usd, days_ago, confidence=None):
        fe = FundingEvent(
            round_type=round_type,
            amount_usd=amount_usd,
            close_date=_date_str(days_ago),
            confidence=confidence,
        )
        return _base_brief(funding_event=fe)

    def test_series_a_qualifies(self):
        brief = self._brief_with_funding("Series A", 10_000_000, 30, "high")
        result = classify(brief, _default_config())
        assert result.segment == Segment.S1
        assert result.abstained is False

    def test_series_b_qualifies(self):
        brief = self._brief_with_funding("Series B", 20_000_000, 60, "medium")
        result = classify(brief, _default_config())
        assert result.segment == Segment.S1

    def test_series_a_case_insensitive(self):
        brief = self._brief_with_funding("series a", 10_000_000, 30, "high")
        result = classify(brief, _default_config())
        assert result.segment == Segment.S1

    def test_series_c_does_not_qualify(self):
        brief = self._brief_with_funding("Series C", 10_000_000, 30, "high")
        result = classify(brief, _default_config())
        assert result.segment != Segment.S1

    def test_amount_exactly_5m_qualifies(self):
        brief = self._brief_with_funding("Series A", 5_000_000, 30, "high")
        result = classify(brief, _default_config())
        assert result.segment == Segment.S1

    def test_amount_exactly_30m_qualifies(self):
        brief = self._brief_with_funding("Series A", 30_000_000, 30, "high")
        result = classify(brief, _default_config())
        assert result.segment == Segment.S1

    def test_amount_below_5m_does_not_qualify(self):
        brief = self._brief_with_funding("Series A", 4_999_999, 30, "high")
        result = classify(brief, _default_config())
        assert result.segment != Segment.S1

    def test_amount_above_30m_does_not_qualify(self):
        brief = self._brief_with_funding("Series A", 30_000_001, 30, "high")
        result = classify(brief, _default_config())
        assert result.segment != Segment.S1

    def test_within_180_days_qualifies(self):
        brief = self._brief_with_funding("Series A", 10_000_000, 179, "high")
        result = classify(brief, _default_config())
        assert result.segment == Segment.S1

    def test_exactly_180_days_qualifies(self):
        brief = self._brief_with_funding("Series A", 10_000_000, 180, "high")
        result = classify(brief, _default_config())
        assert result.segment == Segment.S1

    def test_beyond_180_days_does_not_qualify(self):
        brief = self._brief_with_funding("Series A", 10_000_000, 181, "high")
        result = classify(brief, _default_config(ignore_dates=False))
        assert result.segment != Segment.S1

    def test_confidence_high_maps_to_0_9(self):
        # S1 requires headcount + open_roles data for full confidence per icp_definition.md
        brief = self._brief_with_funding("Series A", 10_000_000, 30, "high")
        result = classify(brief, _default_config(employee_min=30, employee_max=60, open_roles=7))
        assert result.confidence == pytest.approx(0.9)

    def test_confidence_medium_maps_to_0_7(self):
        brief = self._brief_with_funding("Series A", 10_000_000, 30, "medium")
        result = classify(brief, _default_config(employee_min=30, employee_max=60, open_roles=7))
        assert result.confidence == pytest.approx(0.7)

    def test_confidence_low_maps_to_0_5(self):
        brief = self._brief_with_funding("Series A", 10_000_000, 30, "low")
        result = classify(brief, _default_config(employee_min=30, employee_max=60, open_roles=7))
        assert result.confidence == pytest.approx(0.5)

    def test_signals_used(self):
        brief = self._brief_with_funding("Series A", 10_000_000, 30, "high")
        result = classify(brief, _default_config())
        assert "funding_event.round_type" in result.signals_used
        assert "funding_event.amount_usd" in result.signals_used
        assert "funding_event.close_date" in result.signals_used


# ---------------------------------------------------------------------------
# Segment 2 — 200–2,000 employees + layoff, last 120 days
# ---------------------------------------------------------------------------


class TestSegment2:
    def _brief_with_layoff(self, days_ago=30, confidence=None):
        le = LayoffEvent(
            event_date=_date_str(days_ago),
            headcount_affected=50,
            percentage_cut=10.0,
            confidence=confidence,
        )
        return _base_brief(layoff_event=le)

    def test_layoff_with_employee_count_in_range_qualifies(self):
        brief = self._brief_with_layoff(30, "high")
        result = classify(brief, _default_config(employee_min=500, employee_max=1000))
        assert result.segment == Segment.S2

    def test_layoff_without_employee_count_qualifies_lower_confidence(self):
        brief = self._brief_with_layoff(30, "high")
        result = classify(brief, _default_config())
        assert result.segment == Segment.S2
        assert result.confidence <= 0.6

    def test_employee_min_above_2000_disqualifies(self):
        brief = self._brief_with_layoff(30, "high")
        result = classify(brief, _default_config(employee_min=2001))
        assert result.segment != Segment.S2

    def test_employee_max_below_200_disqualifies(self):
        brief = self._brief_with_layoff(30, "high")
        result = classify(brief, _default_config(employee_max=199))
        assert result.segment != Segment.S2

    def test_employee_min_exactly_2000_qualifies(self):
        brief = self._brief_with_layoff(30, "high")
        result = classify(brief, _default_config(employee_min=2000))
        assert result.segment == Segment.S2

    def test_employee_max_exactly_200_qualifies(self):
        brief = self._brief_with_layoff(30, "high")
        result = classify(brief, _default_config(employee_max=200))
        assert result.segment == Segment.S2

    def test_layoff_at_120_day_boundary_qualifies(self):
        """Layoff exactly at 120-day boundary qualifies (already filtered by scanner)."""
        brief = self._brief_with_layoff(120, "high")
        result = classify(brief, _default_config(employee_min=500, employee_max=1000))
        assert result.segment == Segment.S2

    def test_confidence_high_maps_to_0_85(self):
        # S2 requires open_roles data for full confidence per icp_definition.md
        brief = self._brief_with_layoff(30, "high")
        result = classify(brief, _default_config(employee_min=500, employee_max=1000, open_roles=5))
        assert result.confidence == pytest.approx(0.85)

    def test_confidence_medium_maps_to_0_65(self):
        brief = self._brief_with_layoff(30, "medium")
        result = classify(brief, _default_config(employee_min=500, employee_max=1000))
        assert result.confidence == pytest.approx(0.65)

    def test_signals_used(self):
        brief = self._brief_with_layoff(30, "high")
        result = classify(brief, _default_config(employee_min=500, employee_max=1000))
        assert "layoff_event.event_date" in result.signals_used
        assert "employee_count" in result.signals_used


# ---------------------------------------------------------------------------
# Segment 3 — New CTO/VP Eng, last 90 days
# ---------------------------------------------------------------------------


class TestSegment3:
    def _brief_with_leadership(self, days_ago=30, confidence=None):
        lc = LeadershipChange(
            role="CTO",
            appointment_date=_date_str(days_ago),
            confidence=confidence,
        )
        return _base_brief(leadership_change=lc)

    def test_leadership_change_qualifies(self):
        brief = self._brief_with_leadership(30, "high")
        result = classify(brief, _default_config())
        assert result.segment == Segment.S3

    def test_leadership_at_90_day_boundary_qualifies(self):
        """Leadership change exactly at 90-day boundary qualifies (already filtered)."""
        brief = self._brief_with_leadership(90, "high")
        result = classify(brief, _default_config())
        assert result.segment == Segment.S3

    def test_confidence_high_maps_to_0_9(self):
        # S3 requires headcount data for full confidence per icp_definition.md
        brief = self._brief_with_leadership(30, "high")
        result = classify(brief, _default_config(employee_min=100, employee_max=300))
        assert result.confidence == pytest.approx(0.9)

    def test_confidence_medium_maps_to_0_7(self):
        brief = self._brief_with_leadership(30, "medium")
        result = classify(brief, _default_config(employee_min=100, employee_max=300))
        assert result.confidence == pytest.approx(0.7)

    def test_signals_used(self):
        brief = self._brief_with_leadership(30, "high")
        result = classify(brief, _default_config())
        assert "leadership_change.role" in result.signals_used
        assert "leadership_change.appointment_date" in result.signals_used


# ---------------------------------------------------------------------------
# Segment 4 — Capability gap + AI maturity >= 2
# ---------------------------------------------------------------------------


class TestSegment4:
    def _brief_with_ai(self, score, ml_tools=None):
        ts = TechStack(ml_tools=ml_tools or ["pytorch"])
        return _base_brief(ai_maturity_score=score, tech_stack=ts)

    def test_score_2_with_ml_tools_qualifies(self):
        brief = self._brief_with_ai(2)
        result = classify(brief, _default_config())
        assert result.segment == Segment.S4

    def test_score_3_with_ml_tools_qualifies(self):
        brief = self._brief_with_ai(3)
        result = classify(brief, _default_config())
        assert result.segment == Segment.S4

    def test_score_1_does_not_qualify(self):
        brief = self._brief_with_ai(1)
        result = classify(brief, _default_config())
        assert result.segment != Segment.S4

    def test_score_0_does_not_qualify(self):
        brief = self._brief_with_ai(0)
        result = classify(brief, _default_config())
        assert result.segment != Segment.S4

    def test_score_2_without_ml_tools_still_qualifies(self):
        # Per official icp_definition.md, S4 gate is ai_maturity_score >= 2 only.
        # ml_tools are not a hard requirement — they're an additional signal if present.
        ts = TechStack(ml_tools=[])
        brief = _base_brief(ai_maturity_score=2, tech_stack=ts)
        result = classify(brief, _default_config())
        assert result.segment == Segment.S4

    def test_score_2_confidence_is_0_65(self):
        brief = self._brief_with_ai(2)
        result = classify(brief, _default_config())
        assert result.confidence == pytest.approx(0.65)

    def test_score_3_confidence_is_0_85(self):
        brief = self._brief_with_ai(3)
        result = classify(brief, _default_config())
        assert result.confidence == pytest.approx(0.85)

    def test_signals_used(self):
        brief = self._brief_with_ai(2)
        result = classify(brief, _default_config())
        assert "ai_maturity_score" in result.signals_used
        # tech_stack.ml_tools is included when present (this brief has ml_tools)
        assert "tech_stack.ml_tools" in result.signals_used


# ---------------------------------------------------------------------------
# Abstention
# ---------------------------------------------------------------------------


class TestAbstention:
    def test_no_signals_returns_unqualified(self):
        brief = _base_brief()
        result = classify(brief, _default_config())
        assert result.segment == Segment.UNQUALIFIED
        assert result.abstained is True

    def test_abstained_has_empty_signals(self):
        brief = _base_brief()
        result = classify(brief, _default_config())
        assert result.signals_used == []

    def test_abstained_confidence_is_zero(self):
        brief = _base_brief()
        result = classify(brief, _default_config())
        assert result.confidence == 0.0

    def test_high_threshold_causes_abstention(self):
        """A segment that would normally qualify is blocked by a high threshold."""
        lc = LeadershipChange(
            role="CTO",
            appointment_date=_date_str(30),
            confidence="low",  # maps to 0.5
        )
        brief = _base_brief(leadership_change=lc)
        config = ClassifierConfig(abstention_threshold=0.6)
        result = classify(brief, config)
        assert result.segment == Segment.UNQUALIFIED
        assert result.abstained is True


# ---------------------------------------------------------------------------
# Tie-breaking: S1 wins over S2 at same confidence
# ---------------------------------------------------------------------------


class TestTieBreaking:
    def test_s2_wins_over_s1_at_same_confidence(self):
        """Official priority S2 > S3 > S4 > S1: S2 beats S1 when both qualify."""
        fe = FundingEvent(
            round_type="Series A",
            amount_usd=10_000_000,
            close_date=_date_str(30),
            confidence="high",  # → 0.9
        )
        le = LayoffEvent(
            event_date=_date_str(30),
            headcount_affected=100,
            percentage_cut=15.0,
            confidence="high",  # → 0.85
        )
        brief = _base_brief(funding_event=fe, layoff_event=le)
        result = classify(brief, _default_config(
            employee_min=500, employee_max=1000,
            open_roles=7,  # satisfies S1 >= 5 and S2 >= 3
        ))
        assert result.segment == Segment.S2

    def test_higher_confidence_wins_over_priority(self):
        """S1 at 0.9 beats S3 at 0.7 (confidence takes precedence over priority)."""
        fe = FundingEvent(
            round_type="Series A",
            amount_usd=10_000_000,
            close_date=_date_str(30),
            confidence="high",   # → 0.9
        )
        lc = LeadershipChange(
            role="CTO",
            appointment_date=_date_str(30),
            confidence="medium",  # → 0.7
        )
        brief = _base_brief(funding_event=fe, leadership_change=lc)
        result = classify(brief, _default_config(
            employee_min=60, employee_max=80, open_roles=6,  # qualifies S1
        ))
        assert result.segment == Segment.S1

    def test_s3_wins_over_s1_at_equal_confidence(self):
        """Official priority: S3 beats S1 when both qualify at the same confidence."""
        fe = FundingEvent(
            round_type="Series A",
            amount_usd=10_000_000,
            close_date=_date_str(30),
            confidence="high",  # → 0.9
        )
        lc = LeadershipChange(
            role="CTO",
            appointment_date=_date_str(30),
            confidence="high",  # → 0.9
        )
        brief = _base_brief(funding_event=fe, leadership_change=lc)
        result = classify(brief, _default_config(
            employee_min=60, employee_max=80,
            open_roles=6,  # qualifies S1 (>= 5)
        ))
        assert result.segment == Segment.S3


# ---------------------------------------------------------------------------
# SegmentResult fields (Req 5.7)
# ---------------------------------------------------------------------------


class TestSegmentResultFields:
    def test_result_has_segment(self):
        brief = _base_brief()
        result = classify(brief, _default_config())
        assert isinstance(result.segment, Segment)

    def test_result_has_confidence(self):
        brief = _base_brief()
        result = classify(brief, _default_config())
        assert isinstance(result.confidence, float)

    def test_result_has_signals_used(self):
        brief = _base_brief()
        result = classify(brief, _default_config())
        assert isinstance(result.signals_used, list)

    def test_result_has_abstained(self):
        brief = _base_brief()
        result = classify(brief, _default_config())
        assert isinstance(result.abstained, bool)

    def test_qualified_result_has_non_empty_signals(self):
        fe = FundingEvent(
            round_type="Series A",
            amount_usd=10_000_000,
            close_date=_date_str(30),
            confidence="high",
        )
        brief = _base_brief(funding_event=fe)
        result = classify(brief, _default_config())
        assert len(result.signals_used) > 0


# ---------------------------------------------------------------------------
# BenchToBriefMatch tests
# ---------------------------------------------------------------------------


class TestBenchToBriefMatch:
    def _matcher(self):
        return BenchToBriefMatch()

    def _bench(self, engineers=None, version="2026-01-01T00:00:00Z"):
        return {
            "version": version,
            "available_engineers": engineers or {"python": 4, "go": 2},
        }

    def test_matching_language_no_mismatch(self):
        ts = TechStack(languages=["python"])
        result = self._matcher().match(ts, self._bench())
        assert result.bench_mismatch is False
        assert "python" in result.matched_stacks

    def test_matching_ml_tool_no_mismatch(self):
        ts = TechStack(ml_tools=["pytorch"])
        bench = self._bench(engineers={"pytorch": 2})
        result = self._matcher().match(ts, bench)
        assert result.bench_mismatch is False

    def test_no_matching_stack_is_mismatch(self):
        ts = TechStack(languages=["rust"])
        result = self._matcher().match(ts, self._bench())
        assert result.bench_mismatch is True
        assert result.matched_stacks == []

    def test_zero_engineers_counts_as_mismatch(self):
        ts = TechStack(languages=["python"])
        bench = self._bench(engineers={"python": 0})
        result = self._matcher().match(ts, bench)
        assert result.bench_mismatch is True

    def test_none_tech_stack_no_mismatch(self):
        result = self._matcher().match(None, self._bench())
        assert result.bench_mismatch is False

    def test_empty_tech_stack_no_mismatch(self):
        ts = TechStack(languages=[], ml_tools=[])
        result = self._matcher().match(ts, self._bench())
        assert result.bench_mismatch is False

    def test_version_recorded(self):
        ts = TechStack(languages=["python"])
        bench = self._bench(version="2026-04-22T00:00:00Z")
        result = self._matcher().match(ts, bench)
        assert result.bench_summary_version == "2026-04-22T00:00:00Z"

    def test_case_insensitive_matching(self):
        ts = TechStack(languages=["Python"])
        result = self._matcher().match(ts, self._bench())
        assert result.bench_mismatch is False

    def test_partial_match_no_mismatch(self):
        """At least one stack matches → no mismatch."""
        ts = TechStack(languages=["python", "rust"])
        result = self._matcher().match(ts, self._bench())
        assert result.bench_mismatch is False
        assert "python" in result.matched_stacks

    def test_req_7_2_bench_mismatch_flag(self):
        """Req 7.2: bench_mismatch=True when zero available engineers for stack."""
        ts = TechStack(languages=["cobol"])
        result = self._matcher().match(ts, self._bench())
        assert result.bench_mismatch is True

    def test_req_7_3_version_timestamp_recorded(self):
        """Req 7.3: bench_summary_version is recorded from the bench summary."""
        ts = TechStack(languages=["python"])
        bench = {"version": "2026-04-22T00:00:00Z", "available_engineers": {"python": 3}}
        result = self._matcher().match(ts, bench)
        assert result.bench_summary_version == "2026-04-22T00:00:00Z"


# ---------------------------------------------------------------------------
# Property 6: Segment 4 Gated on AI Maturity Score
# Feature: conversion-engine, Property 6: Segment 4 Gated on AI Maturity Score
# ---------------------------------------------------------------------------

_nullable_confidence = st.one_of(st.none(), st.sampled_from(["high", "medium", "low"]))
_nullable_str = st.one_of(st.none(), st.text(min_size=1, max_size=50))
_nullable_float = st.one_of(st.none(), st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
_nullable_int = st.one_of(st.none(), st.integers(min_value=0, max_value=100))

_funding_event_strategy = st.one_of(
    st.none(),
    st.builds(
        FundingEvent,
        round_type=st.sampled_from(["Series A", "Series B", "Series C", "Seed"]),
        amount_usd=st.one_of(st.none(), st.floats(min_value=0, max_value=100_000_000, allow_nan=False)),
        close_date=st.dates(
            min_value=date(2024, 1, 1), max_value=date.today()
        ).map(lambda d: d.isoformat()),
        confidence=_nullable_confidence,
    ),
)

_layoff_event_strategy = st.one_of(
    st.none(),
    st.builds(
        LayoffEvent,
        event_date=st.dates(
            min_value=date(2024, 1, 1), max_value=date.today()
        ).map(lambda d: d.isoformat()),
        headcount_affected=_nullable_int,
        percentage_cut=st.one_of(st.none(), st.floats(min_value=0.0, max_value=100.0, allow_nan=False)),
        confidence=_nullable_confidence,
    ),
)

_leadership_change_strategy = st.one_of(
    st.none(),
    st.builds(
        LeadershipChange,
        role=st.sampled_from(["CTO", "VP Engineering", "Head of Engineering"]),
        appointment_date=st.dates(
            min_value=date(2024, 1, 1), max_value=date.today()
        ).map(lambda d: d.isoformat()),
        confidence=_nullable_confidence,
    ),
)

_tech_stack_strategy = st.one_of(
    st.none(),
    st.builds(
        TechStack,
        languages=st.lists(st.sampled_from(["python", "go", "rust", "java"]), max_size=3),
        ml_tools=st.lists(st.sampled_from(["pytorch", "tensorflow", "mlflow"]), max_size=3),
        data_tools=st.lists(st.sampled_from(["spark", "kafka", "dbt"]), max_size=3),
        confidence=_nullable_confidence,
    ),
)

# Scores 0 or 1 only — the property under test
_low_ai_maturity_score = st.one_of(st.none(), st.integers(min_value=0, max_value=1))

_brief_low_ai_strategy = st.builds(
    HiringSignalBrief,
    schema_version=st.just("1.0"),
    company_id=st.text(min_size=1, max_size=20),
    company_name=st.text(min_size=1, max_size=50),
    last_enriched_at=st.just("2025-01-01T00:00:00Z"),
    funding_event=_funding_event_strategy,
    layoff_event=_layoff_event_strategy,
    leadership_change=_leadership_change_strategy,
    ai_maturity_score=_low_ai_maturity_score,
    tech_stack=_tech_stack_strategy,
    icp_segment=st.none(),
    icp_confidence=st.none(),
    icp_signals_used=st.just([]),
)

_classifier_config_strategy = st.builds(
    ClassifierConfig,
    abstention_threshold=st.floats(min_value=0.0, max_value=0.5, allow_nan=False),
    employee_min=st.one_of(st.none(), st.integers(min_value=0, max_value=5000)),
    employee_max=st.one_of(st.none(), st.integers(min_value=0, max_value=5000)),
)


# Feature: conversion-engine, Property 6: Segment 4 Gated on AI Maturity Score
@given(_brief_low_ai_strategy, _classifier_config_strategy)
@settings(max_examples=100)
def test_segment_4_never_assigned_for_low_ai_maturity(
    brief: HiringSignalBrief, config: ClassifierConfig
) -> None:
    """
    Property 6: Briefs with ai_maturity_score of 0 or 1 (or None) never yield segment_4.

    Validates: Requirements 3.5, 5.5
    # Feature: conversion-engine, Property 6: Segment 4 Gated on AI Maturity Score
    """
    result = classify(brief, config)
    assert result.segment != Segment.S4
