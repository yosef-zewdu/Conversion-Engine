"""
Unit tests for JobPostScraper helpers and aggregation logic.

These tests cover pure-Python logic (robots.txt gating, tech signal extraction,
aggregation, confidence mapping, login-wall detection) without requiring a live
browser or network.
"""
from __future__ import annotations

import pytest

from signal_pipeline.job_post_scraper import (
    JobPostResult,
    _aggregate,
    _confidence,
    _extract_tech_signals,
    _is_login_wall,
    _is_allowed,
)


# ---------------------------------------------------------------------------
# _confidence
# ---------------------------------------------------------------------------

class TestConfidence:
    def test_zero_sources_returns_none(self):
        """No successful sources → confidence is None."""
        assert _confidence(0) is None

    def test_one_source_returns_low(self):
        assert _confidence(1) == "low"

    def test_two_sources_returns_medium(self):
        assert _confidence(2) == "medium"

    def test_three_sources_returns_high(self):
        assert _confidence(3) == "high"

    def test_four_sources_returns_none(self):
        """More than 3 sources is not a defined case; should return None."""
        assert _confidence(4) is None


# ---------------------------------------------------------------------------
# _aggregate
# ---------------------------------------------------------------------------

class TestAggregate:
    def test_all_none_returns_none_count_and_none_confidence(self):
        """Req 2.7: all sources failed → job_post_count None, confidence None."""
        total, conf = _aggregate({"builtin": None, "wellfound": None, "careers": None})
        assert total is None
        assert conf is None

    def test_single_source_sums_and_low_confidence(self):
        total, conf = _aggregate({"builtin": 5, "wellfound": None})
        assert total == 5
        assert conf == "low"

    def test_two_sources_sums_and_medium_confidence(self):
        total, conf = _aggregate({"builtin": 3, "wellfound": 7, "careers": None})
        assert total == 10
        assert conf == "medium"

    def test_three_sources_sums_and_high_confidence(self):
        total, conf = _aggregate({"builtin": 2, "wellfound": 4, "careers": 6})
        assert total == 12
        assert conf == "high"

    def test_zero_counts_still_aggregate(self):
        """A source returning 0 is a valid (non-None) count."""
        total, conf = _aggregate({"builtin": 0, "wellfound": None})
        assert total == 0
        assert conf == "low"

    def test_empty_dict_returns_none(self):
        total, conf = _aggregate({})
        assert total is None
        assert conf is None


# ---------------------------------------------------------------------------
# _extract_tech_signals
# ---------------------------------------------------------------------------

class TestExtractTechSignals:
    def test_detects_known_keywords(self):
        text = "We use Python, Kubernetes, and AWS for our infrastructure."
        signals = _extract_tech_signals(text)
        assert "python" in signals
        assert "kubernetes" in signals
        assert "aws" in signals

    def test_case_insensitive(self):
        signals = _extract_tech_signals("PYTORCH and TensorFlow are used here.")
        assert "pytorch" in signals
        assert "tensorflow" in signals

    def test_no_false_positives_on_partial_words(self):
        # "goland" should not match "go" as a standalone keyword
        signals = _extract_tech_signals("We use goland IDE.")
        assert "go" not in signals

    def test_empty_text_returns_empty(self):
        assert _extract_tech_signals("") == []

    def test_no_keywords_returns_empty(self):
        assert _extract_tech_signals("We build great products for customers.") == []

    def test_deduplication_handled_by_caller(self):
        """_extract_tech_signals itself returns each keyword at most once."""
        text = "python python python"
        signals = _extract_tech_signals(text)
        assert signals.count("python") == 1

    def test_golang_alias_detected(self):
        signals = _extract_tech_signals("Our backend is written in Go and Golang.")
        assert "go" in signals
        assert "golang" in signals


# ---------------------------------------------------------------------------
# _is_login_wall
# ---------------------------------------------------------------------------

class TestIsLoginWall:
    def test_login_in_url(self):
        assert _is_login_wall("https://example.com/login?next=/jobs") is True

    def test_signin_in_url(self):
        assert _is_login_wall("https://example.com/signin") is True

    def test_auth_in_url(self):
        assert _is_login_wall("https://example.com/auth/callback") is True

    def test_normal_url_not_login_wall(self):
        assert _is_login_wall("https://example.com/jobs") is False

    def test_case_insensitive(self):
        assert _is_login_wall("https://example.com/Login") is True


# ---------------------------------------------------------------------------
# _is_allowed (robots.txt) — uses a mock to avoid network calls
# ---------------------------------------------------------------------------

class TestIsAllowed:
    def test_unreachable_robots_txt_fails_open(self, monkeypatch):
        """When robots.txt cannot be fetched, scraping is allowed (fail-open)."""
        import urllib.robotparser

        def _bad_read(self):
            raise OSError("network error")

        monkeypatch.setattr(urllib.robotparser.RobotFileParser, "read", _bad_read)
        assert _is_allowed("https://example.com/jobs") is True

    def test_disallowed_path_returns_false(self, monkeypatch):
        """When robots.txt explicitly disallows the path, return False."""
        import urllib.robotparser

        def _mock_read(self):
            # parse() expects an iterable of individual lines
            self.parse(["User-agent: *\n", "Disallow: /\n"])

        monkeypatch.setattr(urllib.robotparser.RobotFileParser, "read", _mock_read)
        assert _is_allowed("https://example.com/jobs") is False

    def test_allowed_path_returns_true(self, monkeypatch):
        """When robots.txt allows the path, return True."""
        import urllib.robotparser

        def _mock_read(self):
            self.parse(["User-agent: *\n", "Disallow: /private/\n"])

        monkeypatch.setattr(urllib.robotparser.RobotFileParser, "read", _mock_read)
        assert _is_allowed("https://example.com/jobs") is True


# ---------------------------------------------------------------------------
# JobPostResult dataclass
# ---------------------------------------------------------------------------

class TestJobPostResult:
    def test_default_fields(self):
        result = JobPostResult(
            job_post_count=None,
            job_post_velocity_60d=None,
            job_post_confidence=None,
        )
        assert result.sources_checked == []
        assert result.tech_signals == []

    def test_velocity_always_none_by_convention(self):
        """Req 2.7: velocity requires historical data not available in static scrape."""
        result = JobPostResult(
            job_post_count=10,
            job_post_velocity_60d=None,
            job_post_confidence="high",
            sources_checked=["builtin", "wellfound", "careers"],
            tech_signals=["python"],
        )
        assert result.job_post_velocity_60d is None
