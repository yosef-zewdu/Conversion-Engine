"""
Integration test: single synthetic prospect end-to-end pipeline.

Covers the full flow:
  Signal Pipeline → ICP Classifier → Nurture Sequencer →
  Kill Switch → Staff Sink → CRM Writer

All external I/O (HubSpot API, Playwright scraping) is mocked so the test
runs offline without real credentials.

Requirements: 1.2, 2.8, 5.1, 8.1, 10.1
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.models import Destination, Prospect, ProspectState, Segment
from signal_pipeline.models import (
    CompetitorGapBrief,
    FundingEvent,
    HiringSignalBrief,
    TechStack,
)


# ---------------------------------------------------------------------------
# Helpers — synthetic prospect and brief factories
# ---------------------------------------------------------------------------


def _make_prospect(
    segment: Optional[Segment] = None,
    state: ProspectState = ProspectState.COLD,
) -> Prospect:
    """Return a minimal synthetic prospect for integration tests."""
    return Prospect(
        prospect_id="synth-prospect-001",
        company_id="synth-co-001",
        contact_name="Jordan Lee",
        email="jordan@synthcorp.example",
        phone=None,
        timezone="America/New_York",
        preferred_channel="email",
        current_state=state,
        outbound_attempt_count=0,
        segment=segment,
        hiring_signal_brief_ref=None,
    )


def _make_hiring_signal_brief(
    company_id: str = "synth-co-001",
    ai_maturity_score: int = 2,
    ai_maturity_confidence: str = "medium",
    funding_event: Optional[FundingEvent] = None,
) -> HiringSignalBrief:
    """Return a minimal valid HiringSignalBrief for integration tests."""
    return HiringSignalBrief(
        schema_version="1.0",
        company_id=company_id,
        company_name="Synth Corp",
        last_enriched_at=datetime.now(timezone.utc).isoformat(),
        crunchbase_id="cb-synth-001",
        bench_summary_version=datetime.now(timezone.utc).isoformat(),
        bench_mismatch=False,
        tech_stack=TechStack(
            languages=["python"],
            ml_tools=["pytorch"],
            data_tools=["dbt"],
            confidence="medium",
        ),
        funding_event=funding_event,
        ai_maturity_score=ai_maturity_score,
        ai_maturity_confidence=ai_maturity_confidence,
        job_post_count=8,
        job_post_velocity_60d=4.0,
        job_post_confidence="high",
    )


def _make_gap_brief(company_id: str = "synth-co-001") -> CompetitorGapBrief:
    """Return a minimal valid CompetitorGapBrief."""
    return CompetitorGapBrief(
        schema_version="1.0",
        company_id=company_id,
        generated_at=datetime.now(timezone.utc).isoformat(),
        prospect_ai_maturity_score=2,
        sector_percentile=65.0,
        peer_count=5,
        peers=[],
        gaps=[],
    )


def _make_mock_crm() -> MagicMock:
    """Return a CRMWriter mock with all async methods stubbed."""
    crm = MagicMock()
    crm.upsert_contact = AsyncMock(return_value="hs-contact-001")
    crm.log_activity = AsyncMock(return_value="hs-engagement-001")
    crm.write_brief = AsyncMock(return_value="hs-engagement-002")
    crm.write_booking = AsyncMock(return_value="hs-engagement-003")
    return crm


# ---------------------------------------------------------------------------
# Integration test class
# ---------------------------------------------------------------------------


class TestEndToEndPipeline:
    """Integration tests for the full single-prospect pipeline.

    All HubSpot API calls and Playwright scraping are mocked.
    The Kill Switch is forced to STAFF_SINK (default safe state).
    """

    # ------------------------------------------------------------------
    # Test 1: Kill Switch defaults to STAFF_SINK (Req 1.2)
    # ------------------------------------------------------------------

    def test_kill_switch_routes_to_staff_sink_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With KILL_SWITCH unset, outbound destination must be STAFF_SINK.

        Validates: Requirement 1.2
        """
        monkeypatch.delenv("KILL_SWITCH", raising=False)

        from config.kill_switch import get_outbound_destination

        destination = get_outbound_destination()
        assert destination == Destination.STAFF_SINK

    # ------------------------------------------------------------------
    # Test 2: Full pipeline produces a valid HiringSignalBrief (Req 2.8)
    # ------------------------------------------------------------------

    def test_pipeline_produces_validated_hiring_signal_brief(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pipeline must produce a schema-valid HiringSignalBrief.

        Validates: Requirement 2.8
        """
        monkeypatch.delenv("KILL_SWITCH", raising=False)

        brief = _make_hiring_signal_brief()
        gap_brief = _make_gap_brief()
        mock_crm = _make_mock_crm()

        from signal_pipeline.pipeline_runner import PipelineRunner

        runner = PipelineRunner(crm_writer=mock_crm)

        with patch.object(runner, "_enrich", new=AsyncMock(return_value=(brief, gap_brief))):
            prospect = _make_prospect()
            result = asyncio.run(runner.run(prospect))

        assert result.error is None
        assert result.hiring_signal_brief is not None
        assert result.hiring_signal_brief.schema_version == "1.0"
        assert result.hiring_signal_brief.company_id == "synth-co-001"
        assert result.hiring_signal_brief.last_enriched_at is not None

    # ------------------------------------------------------------------
    # Test 3: ICP Classifier assigns a segment (Req 5.1)
    # ------------------------------------------------------------------

    def test_pipeline_assigns_icp_segment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pipeline must assign a non-null ICP segment to the prospect.

        Validates: Requirement 5.1
        """
        monkeypatch.delenv("KILL_SWITCH", raising=False)

        # Series A funding → should qualify for Segment 1
        funding = FundingEvent(
            round_type="Series A",
            amount_usd=15_000_000.0,
            close_date="2026-01-15",
            confidence="high",
        )
        brief = _make_hiring_signal_brief(funding_event=funding)
        gap_brief = _make_gap_brief()
        mock_crm = _make_mock_crm()

        from signal_pipeline.pipeline_runner import PipelineRunner

        runner = PipelineRunner(crm_writer=mock_crm)

        with patch.object(runner, "_enrich", new=AsyncMock(return_value=(brief, gap_brief))):
            prospect = _make_prospect()
            result = asyncio.run(runner.run(prospect))

        assert result.error is None
        assert result.segment is not None
        assert result.segment == Segment.S1

    # ------------------------------------------------------------------
    # Test 4: First outbound message uses email channel (Req 8.1)
    # ------------------------------------------------------------------

    def test_first_outbound_message_uses_email_channel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The first outbound message for any prospect must use the email channel.

        Validates: Requirement 8.1
        """
        monkeypatch.delenv("KILL_SWITCH", raising=False)

        brief = _make_hiring_signal_brief()
        gap_brief = _make_gap_brief()
        mock_crm = _make_mock_crm()

        from signal_pipeline.pipeline_runner import PipelineRunner

        runner = PipelineRunner(crm_writer=mock_crm)

        with patch.object(runner, "_enrich", new=AsyncMock(return_value=(brief, gap_brief))):
            prospect = _make_prospect()
            result = asyncio.run(runner.run(prospect))

        assert result.error is None
        assert result.outbound_message is not None
        assert result.outbound_message.channel == "email", (
            f"Expected email channel for first outbound, got {result.outbound_message.channel!r}"
        )

    # ------------------------------------------------------------------
    # Test 5: Outbound message carries draft=True (Req 1.4)
    # ------------------------------------------------------------------

    def test_outbound_message_has_draft_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every outbound message must carry draft=True in its metadata.

        Validates: Requirement 1.4
        """
        monkeypatch.delenv("KILL_SWITCH", raising=False)

        brief = _make_hiring_signal_brief()
        gap_brief = _make_gap_brief()
        mock_crm = _make_mock_crm()

        from signal_pipeline.pipeline_runner import PipelineRunner

        runner = PipelineRunner(crm_writer=mock_crm)

        with patch.object(runner, "_enrich", new=AsyncMock(return_value=(brief, gap_brief))):
            prospect = _make_prospect()
            result = asyncio.run(runner.run(prospect))

        assert result.outbound_message is not None
        assert result.outbound_message.draft is True
        assert result.outbound_message.metadata.get("draft") is True

    # ------------------------------------------------------------------
    # Test 6: CRM upsert_contact is called (Req 10.1)
    # ------------------------------------------------------------------

    def test_crm_upsert_contact_called(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pipeline must call CRMWriter.upsert_contact for the prospect.

        Validates: Requirement 10.1
        """
        monkeypatch.delenv("KILL_SWITCH", raising=False)

        brief = _make_hiring_signal_brief()
        gap_brief = _make_gap_brief()
        mock_crm = _make_mock_crm()

        from signal_pipeline.pipeline_runner import PipelineRunner

        runner = PipelineRunner(crm_writer=mock_crm)

        with patch.object(runner, "_enrich", new=AsyncMock(return_value=(brief, gap_brief))):
            prospect = _make_prospect()
            result = asyncio.run(runner.run(prospect))

        assert result.error is None
        mock_crm.upsert_contact.assert_called_once()
        call_args = mock_crm.upsert_contact.call_args[0][0]
        assert call_args.prospect_id == "synth-prospect-001"

    # ------------------------------------------------------------------
    # Test 7: CRM write_brief is called for HiringSignalBrief (Req 10.1)
    # ------------------------------------------------------------------

    def test_crm_write_brief_called(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pipeline must write the HiringSignalBrief to CRM.

        Validates: Requirement 10.1
        """
        monkeypatch.delenv("KILL_SWITCH", raising=False)

        brief = _make_hiring_signal_brief()
        gap_brief = _make_gap_brief()
        mock_crm = _make_mock_crm()

        from signal_pipeline.pipeline_runner import PipelineRunner

        runner = PipelineRunner(crm_writer=mock_crm)

        with patch.object(runner, "_enrich", new=AsyncMock(return_value=(brief, gap_brief))):
            prospect = _make_prospect()
            result = asyncio.run(runner.run(prospect))

        assert result.error is None
        # write_brief called at least once (HiringSignalBrief) + once for CompetitorGapBrief
        assert mock_crm.write_brief.call_count >= 1

    # ------------------------------------------------------------------
    # Test 8: CRM log_activity is called for outbound (Req 10.1)
    # ------------------------------------------------------------------

    def test_crm_log_activity_called_for_outbound(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pipeline must log the outbound activity to CRM.

        Validates: Requirement 10.1
        """
        monkeypatch.delenv("KILL_SWITCH", raising=False)

        brief = _make_hiring_signal_brief()
        gap_brief = _make_gap_brief()
        mock_crm = _make_mock_crm()

        from signal_pipeline.pipeline_runner import PipelineRunner

        runner = PipelineRunner(crm_writer=mock_crm)

        with patch.object(runner, "_enrich", new=AsyncMock(return_value=(brief, gap_brief))):
            prospect = _make_prospect()
            result = asyncio.run(runner.run(prospect))

        assert result.error is None
        mock_crm.log_activity.assert_called_once()
        activity = mock_crm.log_activity.call_args[0][0]
        assert activity["direction"] == "outbound"
        assert activity["prospect_id"] == "synth-prospect-001"

    # ------------------------------------------------------------------
    # Test 9: Destination is STAFF_SINK when kill switch is unset (Req 1.2)
    # ------------------------------------------------------------------

    def test_pipeline_destination_is_staff_sink(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With KILL_SWITCH unset, the pipeline result destination must be STAFF_SINK.

        Validates: Requirement 1.2
        """
        monkeypatch.delenv("KILL_SWITCH", raising=False)

        brief = _make_hiring_signal_brief()
        gap_brief = _make_gap_brief()
        mock_crm = _make_mock_crm()

        from signal_pipeline.pipeline_runner import PipelineRunner

        runner = PipelineRunner(crm_writer=mock_crm)

        with patch.object(runner, "_enrich", new=AsyncMock(return_value=(brief, gap_brief))):
            prospect = _make_prospect()
            result = asyncio.run(runner.run(prospect))

        assert result.destination == Destination.STAFF_SINK

    # ------------------------------------------------------------------
    # Test 10: Validation failure blocks CRM writes (Req 2.8)
    # ------------------------------------------------------------------

    def test_validation_failure_blocks_crm_writes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When brief validation fails, no CRM writes should occur.

        Validates: Requirement 2.8
        """
        monkeypatch.delenv("KILL_SWITCH", raising=False)

        mock_crm = _make_mock_crm()

        from signal_pipeline.hiring_signal_brief_assembler import BriefValidationError
        from signal_pipeline.pipeline_runner import PipelineRunner

        runner = PipelineRunner(crm_writer=mock_crm)

        with patch.object(
            runner,
            "_enrich",
            new=AsyncMock(side_effect=BriefValidationError("schema_version: field required")),
        ):
            prospect = _make_prospect()
            result = asyncio.run(runner.run(prospect))

        assert result.error is not None
        assert "validation_failed" in result.error
        mock_crm.upsert_contact.assert_not_called()
        mock_crm.write_brief.assert_not_called()
        mock_crm.log_activity.assert_not_called()

    # ------------------------------------------------------------------
    # Test 11: Unqualified prospect still gets email outreach (Req 5.1)
    # ------------------------------------------------------------------

    def test_unqualified_prospect_still_gets_email(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unqualified prospect should still receive an exploratory email.

        Validates: Requirement 5.1 (unqualified → generic exploratory email)
        """
        monkeypatch.delenv("KILL_SWITCH", raising=False)

        # Brief with no qualifying signals → unqualified
        brief = HiringSignalBrief(
            schema_version="1.0",
            company_id="synth-co-002",
            company_name="No Signal Corp",
            last_enriched_at=datetime.now(timezone.utc).isoformat(),
            ai_maturity_score=0,
            ai_maturity_confidence="low",
        )
        gap_brief = _make_gap_brief(company_id="synth-co-002")
        mock_crm = _make_mock_crm()

        from signal_pipeline.pipeline_runner import PipelineRunner

        runner = PipelineRunner(crm_writer=mock_crm)

        with patch.object(runner, "_enrich", new=AsyncMock(return_value=(brief, gap_brief))):
            prospect = _make_prospect()
            result = asyncio.run(runner.run(prospect))

        assert result.error is None
        assert result.segment == Segment.UNQUALIFIED
        # Even unqualified prospects get an outbound email
        assert result.outbound_message is not None
        assert result.outbound_message.channel == "email"
