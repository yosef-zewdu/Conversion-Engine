"""
Tests for LeadershipChangeDetector (Req 2.4).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from signal_pipeline.leadership_change_detector import (
    LeadershipChangeDetector,
    LeadershipChangeResult,
    _assign_confidence,
    _detect_role,
    _parse_date,
)

# ---------------------------------------------------------------------------
# _detect_role
# ---------------------------------------------------------------------------

class TestDetectRole:
    def test_cto_abbreviation(self):
        assert _detect_role("Acme appoints new CTO") == "CTO"

    def test_chief_technology_officer(self):
        assert _detect_role("Jane Doe named Chief Technology Officer") == "CTO"

    def test_chief_technical_officer(self):
        assert _detect_role("Firm hires Chief Technical Officer") == "CTO"

    def test_vp_engineering(self):
        assert _detect_role("Company names VP Engineering") == "VP Engineering"

    def test_vp_of_engineering(self):
        assert _detect_role("Startup hires VP of Engineering") == "VP Engineering"

    def test_vice_president_engineering(self):
        assert _detect_role("New Vice President Engineering announced") == "VP Engineering"

    def test_vice_president_of_engineering(self):
        assert _detect_role("Appoints Vice President of Engineering") == "VP Engineering"

    def test_head_of_engineering(self):
        assert _detect_role("Firm names Head of Engineering") == "VP Engineering"

    def test_case_insensitive_cto(self):
        assert _detect_role("new cto hired") == "CTO"

    def test_case_insensitive_vp(self):
        assert _detect_role("vp of engineering role filled") == "VP Engineering"

    def test_no_match_returns_none(self):
        assert _detect_role("Williams Blackstock Architects names new CEO") is None

    def test_empty_label_returns_none(self):
        assert _detect_role("") is None

    def test_cto_not_partial_word(self):
        # "ACTOR" should not match \bCTO\b
        assert _detect_role("The ACTOR won an award") is None


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------

class TestParseDate:
    def test_valid_date(self):
        assert _parse_date("2023-03-22") == date(2023, 3, 22)

    def test_empty_string(self):
        assert _parse_date("") is None

    def test_invalid_format(self):
        assert _parse_date("March 22, 2023") is None

    def test_whitespace(self):
        assert _parse_date("  2023-03-22  ") == date(2023, 3, 22)


# ---------------------------------------------------------------------------
# _assign_confidence
# ---------------------------------------------------------------------------

class TestAssignConfidence:
    def test_high_when_date_and_role(self):
        assert _assign_confidence(date(2023, 1, 1), "CTO") == "high"

    def test_medium_when_date_no_role(self):
        assert _assign_confidence(date(2023, 1, 1), None) == "medium"

    def test_low_when_no_date(self):
        assert _assign_confidence(None, "CTO") == "low"

    def test_low_when_neither(self):
        assert _assign_confidence(None, None) == "low"


# ---------------------------------------------------------------------------
# LeadershipChangeDetector.detect
# ---------------------------------------------------------------------------

TODAY = date(2024, 6, 15)
WITHIN_WINDOW = (TODAY - timedelta(days=30)).isoformat()
OUTSIDE_WINDOW = (TODAY - timedelta(days=100)).isoformat()


class TestLeadershipChangeDetector:
    def setup_method(self):
        self.detector = LeadershipChangeDetector()

    def test_detects_cto_within_window(self):
        events = [{"key_event_date": WITHIN_WINDOW, "label": "Acme names new CTO", "link": "https://example.com"}]
        result = self.detector.detect(events, reference_date=TODAY)
        assert result.detected is True
        assert result.role == "CTO"
        assert result.appointment_date == WITHIN_WINDOW
        assert result.confidence == "high"
        assert result.source_url == "https://example.com"

    def test_detects_vp_engineering_within_window(self):
        events = [{"key_event_date": WITHIN_WINDOW, "label": "Startup hires VP of Engineering", "link": None}]
        result = self.detector.detect(events, reference_date=TODAY)
        assert result.detected is True
        assert result.role == "VP Engineering"

    def test_ignores_event_outside_window(self):
        events = [{"key_event_date": OUTSIDE_WINDOW, "label": "Acme names new CTO", "link": "https://example.com"}]
        result = self.detector.detect(events, reference_date=TODAY)
        assert result.detected is False

    def test_ignores_non_cto_vp_roles(self):
        events = [{"key_event_date": WITHIN_WINDOW, "label": "Company names new CEO", "link": None}]
        result = self.detector.detect(events, reference_date=TODAY)
        assert result.detected is False

    def test_empty_list_returns_not_detected(self):
        result = self.detector.detect([], reference_date=TODAY)
        assert result.detected is False
        assert result.role is None
        assert result.appointment_date is None
        assert result.confidence is None

    def test_returns_most_recent_when_multiple_matches(self):
        older = (TODAY - timedelta(days=60)).isoformat()
        newer = (TODAY - timedelta(days=10)).isoformat()
        events = [
            {"key_event_date": older, "label": "Acme names new CTO", "link": "https://old.com"},
            {"key_event_date": newer, "label": "Acme hires VP of Engineering", "link": "https://new.com"},
        ]
        result = self.detector.detect(events, reference_date=TODAY)
        assert result.appointment_date == newer
        assert result.source_url == "https://new.com"

    def test_null_result_fields_are_none(self):
        result = self.detector.detect([], reference_date=TODAY)
        assert result == LeadershipChangeResult(
            detected=False, role=None, appointment_date=None,
            confidence=None, source_url=None, label=None,
        )

    def test_label_preserved_in_result(self):
        label = "Acme names new Chief Technology Officer"
        events = [{"key_event_date": WITHIN_WINDOW, "label": label, "link": None}]
        result = self.detector.detect(events, reference_date=TODAY)
        assert result.label == label

    def test_missing_date_excluded(self):
        events = [{"key_event_date": "", "label": "Acme names new CTO", "link": None}]
        result = self.detector.detect(events, reference_date=TODAY)
        assert result.detected is False

    def test_non_dict_events_skipped(self):
        events = ["not a dict", None, {"key_event_date": WITHIN_WINDOW, "label": "New CTO hired", "link": None}]
        result = self.detector.detect(events, reference_date=TODAY)
        assert result.detected is True

    def test_default_reference_date_is_today(self):
        """Smoke test: passing no reference_date should not raise."""
        events = [{"key_event_date": date.today().isoformat(), "label": "New CTO hired", "link": None}]
        result = self.detector.detect(events)
        assert result.detected is True

    def test_window_boundary_inclusive(self):
        """Event exactly 90 days ago should be included."""
        boundary = (TODAY - timedelta(days=90)).isoformat()
        events = [{"key_event_date": boundary, "label": "New CTO hired", "link": None}]
        result = self.detector.detect(events, reference_date=TODAY)
        assert result.detected is True

    def test_event_one_day_past_window_excluded(self):
        just_outside = (TODAY - timedelta(days=91)).isoformat()
        events = [{"key_event_date": just_outside, "label": "New CTO hired", "link": None}]
        result = self.detector.detect(events, reference_date=TODAY)
        assert result.detected is False
