"""
Tests for FundingEventFetcher (Req 2.5).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from signal_pipeline.funding_event_fetcher import (
    FundingEventFetcher,
    FundingEventResult,
    _assign_confidence,
    _extract_amount_usd,
    _extract_round_type,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

REF_DATE = date(2024, 6, 1)
WINDOW = 180


def _round(announced_on: str, title: str = "Series A - Acme", value_usd: float | None = 1_000_000) -> dict:
    """Build a minimal funding round dict."""
    money_raised = {"currency": "USD", "value": value_usd, "value_usd": value_usd} if value_usd is not None else None
    return {
        "announced_on": announced_on,
        "title": title,
        "money_raised": money_raised,
        "id": "test-round",
        "uuid": "00000000-0000-0000-0000-000000000000",
    }


# ---------------------------------------------------------------------------
# Unit tests — _extract_round_type
# ---------------------------------------------------------------------------

class TestExtractRoundType:
    def test_splits_on_dash(self):
        assert _extract_round_type("Series A - Acme Corp") == "Series A"

    def test_no_dash_returns_full_title(self):
        assert _extract_round_type("Seed") == "Seed"

    def test_normalises_seed_round(self):
        assert _extract_round_type("Seed Round - Foo") == "Seed"

    def test_normalises_series_a_round(self):
        assert _extract_round_type("Series A Round - Bar") == "Series A"

    def test_normalises_series_b_round(self):
        assert _extract_round_type("Series B Round") == "Series B"

    def test_empty_string_returns_none(self):
        assert _extract_round_type("") is None

    def test_whitespace_only_returns_none(self):
        assert _extract_round_type("   ") is None

    def test_preserves_unknown_type(self):
        assert _extract_round_type("Pre-Seed - Startup") == "Pre-Seed"


# ---------------------------------------------------------------------------
# Unit tests — _extract_amount_usd
# ---------------------------------------------------------------------------

class TestExtractAmountUsd:
    def test_returns_value_usd(self):
        r = {"money_raised": {"currency": "USD", "value": 1_000_000, "value_usd": 1_000_000}}
        assert _extract_amount_usd(r) == 1_000_000.0

    def test_missing_money_raised_returns_none(self):
        assert _extract_amount_usd({}) is None

    def test_null_money_raised_returns_none(self):
        assert _extract_amount_usd({"money_raised": None}) is None

    def test_null_value_usd_returns_none(self):
        assert _extract_amount_usd({"money_raised": {"value_usd": None}}) is None

    def test_non_usd_currency_still_uses_value_usd(self):
        r = {"money_raised": {"currency": "CNY", "value": 50_000_000, "value_usd": 7_849_646}}
        assert _extract_amount_usd(r) == 7_849_646.0


# ---------------------------------------------------------------------------
# Unit tests — _assign_confidence
# ---------------------------------------------------------------------------

class TestAssignConfidence:
    def test_high_when_amount_present(self):
        assert _assign_confidence(1_000_000.0, "2024-01-01") == "high"

    def test_medium_when_only_date(self):
        assert _assign_confidence(None, "2024-01-01") == "medium"

    def test_low_when_neither(self):
        assert _assign_confidence(None, None) == "low"


# ---------------------------------------------------------------------------
# Unit tests — FundingEventFetcher.fetch
# ---------------------------------------------------------------------------

class TestFundingEventFetcher:
    def setup_method(self):
        self.fetcher = FundingEventFetcher()

    def test_empty_list_returns_not_detected(self):
        result = self.fetcher.fetch([], reference_date=REF_DATE)
        assert result.detected is False
        assert result.round_type is None
        assert result.amount_usd is None
        assert result.close_date is None
        assert result.confidence is None

    def test_round_within_window_detected(self):
        rounds = [_round("2024-05-01")]
        result = self.fetcher.fetch(rounds, reference_date=REF_DATE)
        assert result.detected is True
        assert result.close_date == "2024-05-01"

    def test_round_outside_window_not_detected(self):
        old_date = (REF_DATE - timedelta(days=WINDOW + 1)).isoformat()
        rounds = [_round(old_date)]
        result = self.fetcher.fetch(rounds, reference_date=REF_DATE)
        assert result.detected is False

    def test_round_exactly_on_window_boundary_detected(self):
        boundary = (REF_DATE - timedelta(days=WINDOW)).isoformat()
        rounds = [_round(boundary)]
        result = self.fetcher.fetch(rounds, reference_date=REF_DATE)
        assert result.detected is True

    def test_returns_most_recent_round(self):
        rounds = [
            _round("2024-01-15", title="Series A - Acme"),
            _round("2024-04-20", title="Series B - Acme"),
        ]
        result = self.fetcher.fetch(rounds, reference_date=REF_DATE)
        assert result.close_date == "2024-04-20"
        assert result.round_type == "Series B"

    def test_round_type_extracted_correctly(self):
        rounds = [_round("2024-05-01", title="Seed Round - Startup")]
        result = self.fetcher.fetch(rounds, reference_date=REF_DATE)
        assert result.round_type == "Seed"

    def test_amount_usd_populated(self):
        rounds = [_round("2024-05-01", value_usd=5_000_000)]
        result = self.fetcher.fetch(rounds, reference_date=REF_DATE)
        assert result.amount_usd == 5_000_000.0

    def test_missing_money_raised_sets_amount_none(self):
        r = {"announced_on": "2024-05-01", "title": "Series A - Acme", "money_raised": None}
        result = self.fetcher.fetch([r], reference_date=REF_DATE)
        assert result.amount_usd is None

    def test_confidence_high_when_amount_present(self):
        rounds = [_round("2024-05-01", value_usd=1_000_000)]
        result = self.fetcher.fetch(rounds, reference_date=REF_DATE)
        assert result.confidence == "high"

    def test_confidence_medium_when_no_amount(self):
        rounds = [_round("2024-05-01", value_usd=None)]
        result = self.fetcher.fetch(rounds, reference_date=REF_DATE)
        assert result.confidence == "medium"

    def test_round_in_future_not_detected(self):
        future = (REF_DATE + timedelta(days=10)).isoformat()
        rounds = [_round(future)]
        result = self.fetcher.fetch(rounds, reference_date=REF_DATE)
        assert result.detected is False

    def test_invalid_date_skipped(self):
        rounds = [{"announced_on": "not-a-date", "title": "Series A - Acme", "money_raised": None}]
        result = self.fetcher.fetch(rounds, reference_date=REF_DATE)
        assert result.detected is False

    def test_default_reference_date_is_today(self):
        """fetch() with no reference_date should use today and not raise."""
        today = date.today()
        recent = (today - timedelta(days=10)).isoformat()
        rounds = [_round(recent)]
        result = self.fetcher.fetch(rounds)
        assert result.detected is True

    def test_crunchbase_example_round(self):
        """Validates the exact example from crunchbase_schema_notes.md."""
        rounds = [{
            "announced_on": "2015-09-15",
            "id": "cdfortis-com-series-a--d3fa5f88",
            "money_raised": {"currency": "CNY", "value": 50000000, "value_usd": 7849646},
            "title": "Series A - Cdfortis.com",
            "uuid": "d3fa5f88-4ee4-4518-bd35-076e1d41d11b",
        }]
        # Use a reference date where this round is within 180 days
        ref = date(2015, 10, 1)
        result = self.fetcher.fetch(rounds, reference_date=ref)
        assert result.detected is True
        assert result.round_type == "Series A"
        assert result.amount_usd == 7_849_646.0
        assert result.close_date == "2015-09-15"
        assert result.confidence == "high"


# ---------------------------------------------------------------------------
# Property-based tests
# **Validates: Requirements 2.5**
# ---------------------------------------------------------------------------

@st.composite
def funding_round_strategy(draw):
    """Generate a valid funding round dict within or outside the 180-day window."""
    ref = REF_DATE
    # announced_on: anywhere from 400 days ago to 10 days in the future
    delta = draw(st.integers(min_value=-400, max_value=10))
    announced_on = (ref + timedelta(days=delta)).isoformat()
    title = draw(st.one_of(
        st.just("Series A - Acme"),
        st.just("Seed Round - Foo"),
        st.just("Series B - Bar"),
        st.just("Pre-Seed"),
        st.just(""),
    ))
    value_usd = draw(st.one_of(st.none(), st.floats(min_value=0, max_value=1e10, allow_nan=False)))
    money_raised = None if value_usd is None else {"currency": "USD", "value": value_usd, "value_usd": value_usd}
    return {"announced_on": announced_on, "title": title, "money_raised": money_raised}


@given(rounds=st.lists(funding_round_strategy(), min_size=0, max_size=20))
@settings(max_examples=200)
def test_property_detected_iff_round_in_window(rounds):
    """
    **Validates: Requirements 2.5**

    detected=True iff at least one round has announced_on within [ref-180, ref].
    """
    fetcher = FundingEventFetcher()
    result = fetcher.fetch(rounds, reference_date=REF_DATE)

    window_start = REF_DATE - timedelta(days=WINDOW)
    has_in_window = any(
        window_start <= date.fromisoformat(r["announced_on"]) <= REF_DATE
        for r in rounds
        if r.get("announced_on") and _is_valid_date(r["announced_on"])
    )

    assert result.detected == has_in_window


@given(rounds=st.lists(funding_round_strategy(), min_size=1, max_size=20))
@settings(max_examples=200)
def test_property_most_recent_round_selected(rounds):
    """
    **Validates: Requirements 2.5**

    When detected, close_date is the maximum announced_on among in-window rounds.
    """
    fetcher = FundingEventFetcher()
    result = fetcher.fetch(rounds, reference_date=REF_DATE)

    if not result.detected:
        return  # nothing to check

    window_start = REF_DATE - timedelta(days=WINDOW)
    in_window_dates = [
        date.fromisoformat(r["announced_on"])
        for r in rounds
        if r.get("announced_on") and _is_valid_date(r["announced_on"])
        and window_start <= date.fromisoformat(r["announced_on"]) <= REF_DATE
    ]
    assert result.close_date == max(in_window_dates).isoformat()


@given(rounds=st.lists(funding_round_strategy(), min_size=1, max_size=20))
@settings(max_examples=200)
def test_property_confidence_rules(rounds):
    """
    **Validates: Requirements 2.5**

    Confidence follows the defined rules: high/medium/low based on data presence.
    """
    fetcher = FundingEventFetcher()
    result = fetcher.fetch(rounds, reference_date=REF_DATE)

    if not result.detected:
        assert result.confidence is None
        return

    if result.amount_usd is not None:
        assert result.confidence == "high"
    elif result.close_date is not None:
        assert result.confidence == "medium"
    else:
        assert result.confidence == "low"


# ---------------------------------------------------------------------------
# Helper used in property tests
# ---------------------------------------------------------------------------

def _is_valid_date(s: str) -> bool:
    """Return True if s is a parseable ISO date."""
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False
