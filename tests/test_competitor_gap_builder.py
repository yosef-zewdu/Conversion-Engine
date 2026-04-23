"""
Tests for CompetitorGapBuilder (Req 4.1, 4.2, 4.3, 4.4).
"""
from __future__ import annotations

import json
from typing import Optional
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from signal_pipeline.ai_maturity_scorer import AIMaturityInput, AIMaturityResult, AIMaturityScorer
from signal_pipeline.competitor_gap_builder import (
    CompetitorGapBuilder,
    _band_stage,
    _compute_sector_percentile,
    _count_ai_keywords_in_text,
    _extract_gaps,
    _has_ai_ml_leadership,
    _has_executive_ai_commentary,
    _has_modern_ml_stack,
    _has_strategic_communications,
    _shares_sector,
)
from signal_pipeline.firmographic_enricher import FirmographicEnricher
from signal_pipeline.models import CompetitorGap, CompetitorGapBrief, CompetitorGapPeer


# ---------------------------------------------------------------------------
# Helpers — build minimal Crunchbase record dicts for testing
# ---------------------------------------------------------------------------


def _make_record(
    company_id: str = "test-co",
    name: str = "Test Co",
    industries: Optional[list[dict]] = None,
    investment_stage: str = "",
    funding_rounds_list: Optional[list[dict]] = None,
    about: str = "",
    full_description: str = "",
    leadership_hire: Optional[list[dict]] = None,
    builtwith_tech: Optional[list[str]] = None,
    siftery_products: Optional[list[str]] = None,
) -> dict:
    """Build a minimal Crunchbase ODM record dict for testing."""
    return {
        "id": company_id,
        "name": name,
        "industries": json.dumps(industries or []),
        "investment_stage": investment_stage,
        "funding_rounds_list": json.dumps(funding_rounds_list or []),
        "about": about,
        "full_description": full_description,
        "leadership_hire": json.dumps(leadership_hire or []),
        "builtwith_tech": json.dumps(builtwith_tech or []),
        "siftery_products": json.dumps(siftery_products or []),
    }


def _make_enricher_with_records(records: list[dict]) -> FirmographicEnricher:
    """Return a FirmographicEnricher whose _records() returns the given list."""
    enricher = MagicMock(spec=FirmographicEnricher)
    enricher._records.return_value = records
    return enricher


# ---------------------------------------------------------------------------
# _band_stage
# ---------------------------------------------------------------------------


class TestBandStage:
    def test_none_returns_unknown(self):
        assert _band_stage(None) == "unknown"

    def test_empty_returns_unknown(self):
        assert _band_stage("") == "unknown"

    def test_seed_variants(self):
        for stage in ["Seed", "Pre-Seed", "Angel", "Seed Round"]:
            assert _band_stage(stage) == "seed", f"Expected 'seed' for {stage!r}"

    def test_series_ab_variants(self):
        for stage in ["Series A", "Series B"]:
            assert _band_stage(stage) == "series_ab"

    def test_series_c_plus_variants(self):
        for stage in ["Series C", "Series D", "Series E", "Series F",
                      "Series G", "Series H", "Series I", "Series J"]:
            assert _band_stage(stage) == "series_c_plus"

    def test_public_variants(self):
        for stage in ["Post-IPO", "Public", "IPO"]:
            assert _band_stage(stage) == "public"

    def test_unknown_stage_returns_unknown(self):
        assert _band_stage("Bridge Round") == "unknown"

    def test_case_insensitive(self):
        assert _band_stage("series a") == "series_ab"
        assert _band_stage("SEED") == "seed"


# ---------------------------------------------------------------------------
# _count_ai_keywords_in_text
# ---------------------------------------------------------------------------


class TestCountAiKeywords:
    def test_none_returns_zero(self):
        assert _count_ai_keywords_in_text(None) == 0

    def test_empty_returns_zero(self):
        assert _count_ai_keywords_in_text("") == 0

    def test_no_keywords_returns_zero(self):
        assert _count_ai_keywords_in_text("We build e-commerce solutions.") == 0

    def test_single_keyword(self):
        assert _count_ai_keywords_in_text("We use AI to power our platform.") == 1

    def test_multiple_keywords(self):
        text = "We use AI, ML, and machine learning with deep learning and LLM."
        assert _count_ai_keywords_in_text(text) == 5

    def test_capped_at_five(self):
        # All 5 keywords present multiple times — should still return 5
        text = "AI AI AI ML ML machine learning deep learning LLM LLM"
        assert _count_ai_keywords_in_text(text) == 5

    def test_case_insensitive(self):
        assert _count_ai_keywords_in_text("We use Machine Learning.") >= 1


# ---------------------------------------------------------------------------
# _has_ai_ml_leadership
# ---------------------------------------------------------------------------


class TestHasAiMlLeadership:
    def test_empty_list_returns_false(self):
        assert _has_ai_ml_leadership([]) is False

    def test_non_ai_label_returns_false(self):
        events = [{"label": "New CFO appointed at Acme Corp", "key_event_date": "2024-01-01"}]
        assert _has_ai_ml_leadership(events) is False

    def test_ai_label_returns_true(self):
        events = [{"label": "Company appoints new AI Research Lead", "key_event_date": "2024-01-01"}]
        assert _has_ai_ml_leadership(events) is True

    def test_ml_label_returns_true(self):
        events = [{"label": "New VP of Machine Learning joins the team", "key_event_date": "2024-01-01"}]
        assert _has_ai_ml_leadership(events) is True

    def test_case_insensitive(self):
        events = [{"label": "Appoints Head of MACHINE LEARNING", "key_event_date": "2024-01-01"}]
        assert _has_ai_ml_leadership(events) is True


# ---------------------------------------------------------------------------
# _has_modern_ml_stack
# ---------------------------------------------------------------------------


class TestHasModernMlStack:
    def test_empty_lists_returns_false(self):
        assert _has_modern_ml_stack([], []) is False

    def test_pytorch_detected(self):
        assert _has_modern_ml_stack(["pytorch"], []) is True

    def test_tensorflow_detected(self):
        assert _has_modern_ml_stack([], ["tensorflow"]) is True

    def test_spark_detected(self):
        assert _has_modern_ml_stack(["Apache Spark"], []) is True

    def test_non_ml_tech_returns_false(self):
        assert _has_modern_ml_stack(["React", "Node.js"], ["Salesforce"]) is False


# ---------------------------------------------------------------------------
# _has_strategic_communications
# ---------------------------------------------------------------------------


class TestHasStrategicCommunications:
    def test_none_returns_false(self):
        assert _has_strategic_communications(None) is False

    def test_empty_returns_false(self):
        assert _has_strategic_communications("") is False

    def test_ai_strategy_detected(self):
        assert _has_strategic_communications("Our AI strategy drives growth.") is True

    def test_ai_first_detected(self):
        assert _has_strategic_communications("We are an AI-first company.") is True

    def test_ml_platform_detected(self):
        assert _has_strategic_communications("We build a machine learning platform.") is True

    def test_generic_text_returns_false(self):
        assert _has_strategic_communications("We provide cloud services.") is False


# ---------------------------------------------------------------------------
# _compute_sector_percentile
# ---------------------------------------------------------------------------


class TestComputeSectorPercentile:
    def test_no_peers_returns_none(self):
        assert _compute_sector_percentile([], 2) is None

    def test_all_peers_below_returns_100(self):
        peers = [
            CompetitorGapPeer(company_id="a", company_name="A", ai_maturity_score=0),
            CompetitorGapPeer(company_id="b", company_name="B", ai_maturity_score=1),
        ]
        assert _compute_sector_percentile(peers, 3) == 100.0

    def test_all_peers_above_returns_0(self):
        peers = [
            CompetitorGapPeer(company_id="a", company_name="A", ai_maturity_score=3),
            CompetitorGapPeer(company_id="b", company_name="B", ai_maturity_score=3),
        ]
        assert _compute_sector_percentile(peers, 0) == 0.0

    def test_half_below_returns_50(self):
        peers = [
            CompetitorGapPeer(company_id="a", company_name="A", ai_maturity_score=0),
            CompetitorGapPeer(company_id="b", company_name="B", ai_maturity_score=2),
        ]
        assert _compute_sector_percentile(peers, 1) == 50.0

    def test_equal_score_not_counted_as_below(self):
        peers = [
            CompetitorGapPeer(company_id="a", company_name="A", ai_maturity_score=2),
        ]
        # Prospect score == peer score → 0 peers strictly below → 0%
        assert _compute_sector_percentile(peers, 2) == 0.0


# ---------------------------------------------------------------------------
# _shares_sector
# ---------------------------------------------------------------------------


class TestSharesSector:
    def test_no_overlap_returns_false(self):
        assert _shares_sector(["fintech"], ["saas", "e-commerce"]) is False

    def test_overlap_returns_true(self):
        assert _shares_sector(["fintech", "saas"], ["saas"]) is True

    def test_empty_record_sectors_returns_false(self):
        assert _shares_sector([], ["saas"]) is False

    def test_empty_prospect_sectors_returns_false(self):
        assert _shares_sector(["saas"], []) is False


# ---------------------------------------------------------------------------
# _extract_gaps
# ---------------------------------------------------------------------------


class TestExtractGaps:
    def _make_peer_record_with_tech(self, tech: list[str]) -> dict:
        return _make_record(builtwith_tech=tech)

    def test_no_top_quartile_peers_returns_empty(self):
        gaps = _extract_gaps([], [], set())
        assert gaps == []

    def test_prospect_already_has_tech_excluded(self):
        record = self._make_peer_record_with_tech(["pytorch"])
        gaps = _extract_gaps([record], ["peer-1"], {"pytorch"})
        assert all(g.practice != "PyTorch deep learning framework" for g in gaps)

    def test_gap_detected_when_prospect_lacks_tech(self):
        record = self._make_peer_record_with_tech(["pytorch"])
        gaps = _extract_gaps([record], ["peer-1"], set())
        assert any("PyTorch" in g.practice for g in gaps)

    def test_confidence_high_for_three_or_more_peers(self):
        records = [
            self._make_peer_record_with_tech(["pytorch"]),
            self._make_peer_record_with_tech(["pytorch"]),
            self._make_peer_record_with_tech(["pytorch"]),
        ]
        gaps = _extract_gaps(records, ["p1", "p2", "p3"], set())
        pytorch_gap = next(g for g in gaps if "PyTorch" in g.practice)
        assert pytorch_gap.confidence == "high"

    def test_confidence_medium_for_two_peers(self):
        records = [
            self._make_peer_record_with_tech(["pytorch"]),
            self._make_peer_record_with_tech(["pytorch"]),
        ]
        gaps = _extract_gaps(records, ["p1", "p2"], set())
        pytorch_gap = next(g for g in gaps if "PyTorch" in g.practice)
        assert pytorch_gap.confidence == "medium"

    def test_confidence_low_for_one_peer(self):
        record = self._make_peer_record_with_tech(["pytorch"])
        gaps = _extract_gaps([record], ["p1"], set())
        pytorch_gap = next(g for g in gaps if "PyTorch" in g.practice)
        assert pytorch_gap.confidence == "low"

    def test_at_most_three_gaps_returned(self):
        # Peer has many tech tools
        record = self._make_peer_record_with_tech(
            ["pytorch", "tensorflow", "spark", "kafka", "dbt"]
        )
        gaps = _extract_gaps([record], ["p1"], set())
        assert len(gaps) <= 3

    def test_peer_refs_populated(self):
        record = self._make_peer_record_with_tech(["pytorch"])
        gaps = _extract_gaps([record], ["peer-abc"], set())
        pytorch_gap = next(g for g in gaps if "PyTorch" in g.practice)
        assert "peer-abc" in pytorch_gap.peer_refs


# ---------------------------------------------------------------------------
# CompetitorGapBuilder.build — integration tests
# ---------------------------------------------------------------------------


class TestCompetitorGapBuilderBuild:
    def setup_method(self):
        self.scorer = AIMaturityScorer()

    def _make_builder(self, records: list[dict]) -> CompetitorGapBuilder:
        enricher = _make_enricher_with_records(records)
        return CompetitorGapBuilder(enricher=enricher, scorer=self.scorer)

    def test_no_peers_returns_empty_brief(self):
        """Req 4.4: zero peers → peer_count=0, no fabrication."""
        builder = self._make_builder([])
        brief = builder.build(
            prospect_company_id="prospect-1",
            prospect_company_name="Prospect Co",
            prospect_sectors=["SaaS"],
            prospect_funding_stage="Series A",
            prospect_ai_maturity_score=1,
            prospect_tech_stack=[],
        )
        assert brief.peer_count == 0
        assert brief.peers == []
        assert brief.sector_percentile is None

    def test_prospect_excluded_from_peers(self):
        """Prospect itself must not appear in peers."""
        records = [
            _make_record(
                company_id="prospect-1",
                name="Prospect Co",
                industries=[{"id": "saas", "value": "SaaS"}],
                investment_stage="Series A",
            ),
            _make_record(
                company_id="peer-1",
                name="Peer Co",
                industries=[{"id": "saas", "value": "SaaS"}],
                investment_stage="Series A",
            ),
        ]
        builder = self._make_builder(records)
        brief = builder.build(
            prospect_company_id="prospect-1",
            prospect_company_name="Prospect Co",
            prospect_sectors=["SaaS"],
            prospect_funding_stage="Series A",
            prospect_ai_maturity_score=1,
            prospect_tech_stack=[],
        )
        peer_ids = [p.company_id for p in brief.peers]
        assert "prospect-1" not in peer_ids

    def test_sector_mismatch_excluded(self):
        """Records in different sectors are excluded."""
        records = [
            _make_record(
                company_id="peer-fintech",
                name="Fintech Co",
                industries=[{"id": "fintech", "value": "FinTech"}],
                investment_stage="Series A",
            ),
        ]
        builder = self._make_builder(records)
        brief = builder.build(
            prospect_company_id="prospect-1",
            prospect_company_name="Prospect Co",
            prospect_sectors=["SaaS"],
            prospect_funding_stage="Series A",
            prospect_ai_maturity_score=1,
            prospect_tech_stack=[],
        )
        assert brief.peer_count == 0

    def test_stage_mismatch_excluded(self):
        """Records in different stage bands are excluded."""
        records = [
            _make_record(
                company_id="peer-seed",
                name="Seed Co",
                industries=[{"id": "saas", "value": "SaaS"}],
                investment_stage="Seed",
            ),
        ]
        builder = self._make_builder(records)
        brief = builder.build(
            prospect_company_id="prospect-1",
            prospect_company_name="Prospect Co",
            prospect_sectors=["SaaS"],
            prospect_funding_stage="Series A",
            prospect_ai_maturity_score=1,
            prospect_tech_stack=[],
        )
        assert brief.peer_count == 0

    def test_matching_peer_included(self):
        """A record matching sector+stage is included as a peer."""
        records = [
            _make_record(
                company_id="peer-1",
                name="Peer Co",
                industries=[{"id": "saas", "value": "SaaS"}],
                investment_stage="Series A",
            ),
        ]
        builder = self._make_builder(records)
        brief = builder.build(
            prospect_company_id="prospect-1",
            prospect_company_name="Prospect Co",
            prospect_sectors=["SaaS"],
            prospect_funding_stage="Series A",
            prospect_ai_maturity_score=1,
            prospect_tech_stack=[],
        )
        assert brief.peer_count == 1
        assert brief.peers[0].company_id == "peer-1"

    def test_peer_count_capped_at_ten(self):
        """Req 4.1: at most 10 peers selected."""
        records = [
            _make_record(
                company_id=f"peer-{i}",
                name=f"Peer {i}",
                industries=[{"id": "saas", "value": "SaaS"}],
                investment_stage="Series A",
            )
            for i in range(15)
        ]
        builder = self._make_builder(records)
        brief = builder.build(
            prospect_company_id="prospect-1",
            prospect_company_name="Prospect Co",
            prospect_sectors=["SaaS"],
            prospect_funding_stage="Series A",
            prospect_ai_maturity_score=1,
            prospect_tech_stack=[],
        )
        assert brief.peer_count <= 10
        assert len(brief.peers) <= 10

    def test_schema_version_is_1_0(self):
        builder = self._make_builder([])
        brief = builder.build(
            prospect_company_id="p1",
            prospect_company_name="P1",
            prospect_sectors=["SaaS"],
            prospect_funding_stage="Series A",
            prospect_ai_maturity_score=0,
            prospect_tech_stack=[],
        )
        assert brief.schema_version == "1.0"

    def test_prospect_ai_maturity_score_preserved(self):
        builder = self._make_builder([])
        brief = builder.build(
            prospect_company_id="p1",
            prospect_company_name="P1",
            prospect_sectors=["SaaS"],
            prospect_funding_stage="Series A",
            prospect_ai_maturity_score=2,
            prospect_tech_stack=[],
        )
        assert brief.prospect_ai_maturity_score == 2

    def test_sector_percentile_computed_with_peers(self):
        """Req 4.2: sector_percentile is computed when peers exist."""
        records = [
            _make_record(
                company_id="peer-1",
                name="Peer Co",
                industries=[{"id": "saas", "value": "SaaS"}],
                investment_stage="Series A",
            ),
        ]
        builder = self._make_builder(records)
        brief = builder.build(
            prospect_company_id="prospect-1",
            prospect_company_name="Prospect Co",
            prospect_sectors=["SaaS"],
            prospect_funding_stage="Series A",
            prospect_ai_maturity_score=3,
            prospect_tech_stack=[],
        )
        assert brief.sector_percentile is not None
        assert 0.0 <= brief.sector_percentile <= 100.0

    def test_gaps_at_most_three(self):
        """Req 4.3: at most 3 gap practices extracted."""
        records = [
            _make_record(
                company_id="peer-1",
                name="Peer Co",
                industries=[{"id": "saas", "value": "SaaS"}],
                investment_stage="Series A",
                about="We use AI strategy and machine learning platform.",
                builtwith_tech=["pytorch", "tensorflow", "spark", "kafka", "dbt"],
            ),
        ]
        builder = self._make_builder(records)
        brief = builder.build(
            prospect_company_id="prospect-1",
            prospect_company_name="Prospect Co",
            prospect_sectors=["SaaS"],
            prospect_funding_stage="Series A",
            prospect_ai_maturity_score=0,
            prospect_tech_stack=[],
        )
        assert len(brief.gaps) <= 3

    def test_req_4_4_fewer_than_5_peers_records_actual_count(self):
        """Req 4.4: fewer than 5 peers → actual peer_count recorded, no fabrication."""
        records = [
            _make_record(
                company_id=f"peer-{i}",
                name=f"Peer {i}",
                industries=[{"id": "saas", "value": "SaaS"}],
                investment_stage="Series A",
            )
            for i in range(3)
        ]
        builder = self._make_builder(records)
        brief = builder.build(
            prospect_company_id="prospect-1",
            prospect_company_name="Prospect Co",
            prospect_sectors=["SaaS"],
            prospect_funding_stage="Series A",
            prospect_ai_maturity_score=1,
            prospect_tech_stack=[],
        )
        assert brief.peer_count == 3
        assert len(brief.peers) == 3

    def test_brief_is_competitor_gap_brief_instance(self):
        builder = self._make_builder([])
        brief = builder.build(
            prospect_company_id="p1",
            prospect_company_name="P1",
            prospect_sectors=[],
            prospect_funding_stage=None,
            prospect_ai_maturity_score=0,
            prospect_tech_stack=[],
        )
        assert isinstance(brief, CompetitorGapBrief)

    def test_generated_at_is_iso_string(self):
        from datetime import datetime
        builder = self._make_builder([])
        brief = builder.build(
            prospect_company_id="p1",
            prospect_company_name="P1",
            prospect_sectors=[],
            prospect_funding_stage=None,
            prospect_ai_maturity_score=0,
            prospect_tech_stack=[],
        )
        # Should parse without error
        datetime.fromisoformat(brief.generated_at)

    def test_peer_scores_are_in_range(self):
        """All peer AI maturity scores must be in [0, 3]."""
        records = [
            _make_record(
                company_id=f"peer-{i}",
                name=f"Peer {i}",
                industries=[{"id": "saas", "value": "SaaS"}],
                investment_stage="Series B",
            )
            for i in range(5)
        ]
        builder = self._make_builder(records)
        brief = builder.build(
            prospect_company_id="prospect-1",
            prospect_company_name="Prospect Co",
            prospect_sectors=["SaaS"],
            prospect_funding_stage="Series B",
            prospect_ai_maturity_score=1,
            prospect_tech_stack=[],
        )
        for peer in brief.peers:
            assert 0 <= peer.ai_maturity_score <= 3


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------

_stage_strategy = st.one_of(
    st.none(),
    st.sampled_from([
        "Seed", "Pre-Seed", "Angel", "Seed Round",
        "Series A", "Series B",
        "Series C", "Series D",
        "Post-IPO", "Public", "IPO",
        "Bridge Round", "",
    ]),
)

_sector_strategy = st.lists(
    st.sampled_from(["SaaS", "FinTech", "E-Commerce", "AI", "Healthcare"]),
    min_size=0,
    max_size=3,
)

_tech_stack_strategy = st.lists(
    st.sampled_from(["pytorch", "tensorflow", "spark", "kafka", "dbt", "react", "node"]),
    min_size=0,
    max_size=5,
)

_score_strategy = st.integers(min_value=0, max_value=3)


@given(
    prospect_sectors=_sector_strategy,
    prospect_funding_stage=_stage_strategy,
    prospect_ai_maturity_score=_score_strategy,
    prospect_tech_stack=_tech_stack_strategy,
)
@settings(max_examples=100)
def test_build_never_fabricates_peers(
    prospect_sectors,
    prospect_funding_stage,
    prospect_ai_maturity_score,
    prospect_tech_stack,
) -> None:
    """
    Req 4.4: peer_count always equals len(peers); no fabrication.

    For any input, the peer_count field must equal the actual number of peers
    in the peers list. The builder must never fabricate additional peer records.

    # Feature: conversion-engine, Property: No Peer Fabrication (Req 4.4)
    """
    enricher = _make_enricher_with_records([])
    scorer = AIMaturityScorer()
    builder = CompetitorGapBuilder(enricher=enricher, scorer=scorer)

    brief = builder.build(
        prospect_company_id="prospect-1",
        prospect_company_name="Prospect Co",
        prospect_sectors=prospect_sectors,
        prospect_funding_stage=prospect_funding_stage,
        prospect_ai_maturity_score=prospect_ai_maturity_score,
        prospect_tech_stack=prospect_tech_stack,
    )

    assert brief.peer_count == len(brief.peers)
    assert len(brief.peers) <= 10
    assert len(brief.gaps) <= 3


@given(
    peers_scores=st.lists(st.integers(min_value=0, max_value=3), min_size=1, max_size=10),
    prospect_score=st.integers(min_value=0, max_value=3),
)
@settings(max_examples=100)
def test_sector_percentile_in_valid_range(peers_scores, prospect_score) -> None:
    """
    Req 4.2: sector_percentile is always in [0.0, 100.0] when peers exist.

    # Feature: conversion-engine, Property: Sector Percentile Range (Req 4.2)
    """
    peers = [
        CompetitorGapPeer(company_id=f"p{i}", company_name=f"P{i}", ai_maturity_score=s)
        for i, s in enumerate(peers_scores)
    ]
    percentile = _compute_sector_percentile(peers, prospect_score)
    assert percentile is not None
    assert 0.0 <= percentile <= 100.0


@given(stage=st.text(min_size=0, max_size=30))
@settings(max_examples=100)
def test_band_stage_always_returns_valid_band(stage: str) -> None:
    """
    _band_stage always returns one of the five valid band strings.

    # Feature: conversion-engine, Property: Stage Band Validity
    """
    valid_bands = {"seed", "series_ab", "series_c_plus", "public", "unknown"}
    result = _band_stage(stage)
    assert result in valid_bands
