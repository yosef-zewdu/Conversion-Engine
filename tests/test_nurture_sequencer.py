"""
Property-based tests for the Nurture Sequencer FSM and message composition.

Properties tested:
  2  — Draft Metadata on All Outbound Content
  5  — Low-Confidence Score Triggers Interrogative Phrasing
  7  — Competitor Gap Confidence Gate
  9  — Honesty Constraint — Confidence Gating
  10 — Aggressive Hiring Threshold
  12 — Email-First Channel Ordering
  13 — SMS Gate

Requirements: 1.4, 3.4, 4.5, 6.1, 6.2, 6.3, 8.1, 8.2
"""
from __future__ import annotations

from typing import Optional

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from config.models import Prospect, ProspectState, Segment
from nurture_sequencer.outbound_message import OutboundMessage
from nurture_sequencer.state_machine import ProspectFSM, compose_outbound
from signal_pipeline.models import (
    CompetitorGap,
    CompetitorGapBrief,
    HiringSignalBrief,
)

# ---------------------------------------------------------------------------
# Shared Hypothesis strategies
# ---------------------------------------------------------------------------

_confidence_strategy = st.sampled_from(["high", "medium", "low"])
_nullable_confidence_strategy = st.one_of(st.none(), _confidence_strategy)
_channel_strategy = st.sampled_from(["email", "sms", "voice"])
_segment_strategy = st.sampled_from(list(Segment))
_state_strategy = st.sampled_from(list(ProspectState))

_prospect_strategy = st.builds(
    Prospect,
    prospect_id=st.uuids().map(str),
    company_id=st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"))),
    contact_name=st.just("Alex"),
    email=st.just("alex@example.com"),
    phone=st.none(),
    timezone=st.just("America/New_York"),
    preferred_channel=st.sampled_from(["email", "sms"]),
    current_state=st.just(ProspectState.COLD),
    outbound_attempt_count=st.just(0),
    segment=_segment_strategy,
    hiring_signal_brief_ref=st.none(),
)

_hiring_signal_brief_strategy = st.builds(
    HiringSignalBrief,
    schema_version=st.just("1.0"),
    company_id=st.just("co-001"),
    company_name=st.just("Acme Corp"),
    last_enriched_at=st.just("2024-01-01T00:00:00Z"),
    ai_maturity_score=st.one_of(st.none(), st.integers(min_value=0, max_value=3)),
    ai_maturity_confidence=_nullable_confidence_strategy,
    job_post_count=st.one_of(st.none(), st.integers(min_value=0, max_value=50)),
    job_post_velocity_60d=st.one_of(st.none(), st.floats(min_value=0.0, max_value=20.0, allow_nan=False)),
    job_post_confidence=_nullable_confidence_strategy,
)

_competitor_gap_strategy = st.builds(
    CompetitorGap,
    practice=st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Zs"))),
    evidence=st.just("peer evidence"),
    confidence=_confidence_strategy,
    peer_refs=st.just([]),
)

_gap_brief_strategy = st.builds(
    CompetitorGapBrief,
    schema_version=st.just("1.0"),
    company_id=st.just("co-001"),
    generated_at=st.just("2024-01-01T00:00:00Z"),
    gaps=st.lists(_competitor_gap_strategy, min_size=0, max_size=3),
)

_nullable_gap_brief_strategy = st.one_of(st.none(), _gap_brief_strategy)


# ---------------------------------------------------------------------------
# Helper: build a minimal cold prospect
# ---------------------------------------------------------------------------


def _make_cold_prospect(
    segment: Segment = Segment.S1,
    preferred_channel: str = "email",
) -> Prospect:
    """Return a minimal cold prospect for FSM tests."""
    return Prospect(
        prospect_id="test-prospect-001",
        company_id="co-001",
        contact_name="Alex",
        email="alex@example.com",
        phone=None,
        timezone="America/New_York",
        preferred_channel=preferred_channel,
        current_state=ProspectState.COLD,
        outbound_attempt_count=0,
        segment=segment,
        hiring_signal_brief_ref=None,
    )


def _make_brief(
    ai_maturity_score: Optional[int] = None,
    ai_maturity_confidence: Optional[str] = None,
    job_post_count: Optional[int] = None,
    job_post_velocity_60d: Optional[float] = None,
    job_post_confidence: Optional[str] = None,
) -> HiringSignalBrief:
    """Return a minimal HiringSignalBrief with the given fields."""
    return HiringSignalBrief(
        schema_version="1.0",
        company_id="co-001",
        company_name="Acme Corp",
        last_enriched_at="2024-01-01T00:00:00Z",
        ai_maturity_score=ai_maturity_score,
        ai_maturity_confidence=ai_maturity_confidence,
        job_post_count=job_post_count,
        job_post_velocity_60d=job_post_velocity_60d,
        job_post_confidence=job_post_confidence,
    )


def _make_gap_brief(gaps: list[CompetitorGap]) -> CompetitorGapBrief:
    """Return a minimal CompetitorGapBrief with the given gaps."""
    return CompetitorGapBrief(
        schema_version="1.0",
        company_id="co-001",
        generated_at="2024-01-01T00:00:00Z",
        gaps=gaps,
    )


# ---------------------------------------------------------------------------
# Property 2: Draft Metadata on All Outbound Content
# ---------------------------------------------------------------------------

# Feature: conversion-engine, Property 2: Draft Metadata on All Outbound Content
@given(
    prospect=_prospect_strategy,
    brief=_hiring_signal_brief_strategy,
    gap_brief=_nullable_gap_brief_strategy,
    channel=_channel_strategy,
)
@settings(max_examples=100)
def test_all_outbound_has_draft_true(
    prospect: Prospect,
    brief: HiringSignalBrief,
    gap_brief: Optional[CompetitorGapBrief],
    channel: str,
) -> None:
    """
    Every outbound message produced by compose_outbound has draft=True.

    Validates: Requirements 1.4
    """
    msg = compose_outbound(prospect, brief, gap_brief, channel)
    assert msg.draft is True
    assert msg.metadata.get("draft") is True


# ---------------------------------------------------------------------------
# Property 5: Low-Confidence Score Triggers Interrogative Phrasing
# ---------------------------------------------------------------------------

# Feature: conversion-engine, Property 5: Low-Confidence Score Triggers Interrogative Phrasing
@given(
    prospect=_prospect_strategy,
    score=st.integers(min_value=2, max_value=3),
    gap_brief=_nullable_gap_brief_strategy,
    channel=_channel_strategy,
)
@settings(max_examples=100)
def test_low_confidence_ai_maturity_uses_interrogative(
    prospect: Prospect,
    score: int,
    gap_brief: Optional[CompetitorGapBrief],
    channel: str,
) -> None:
    """
    When ai_maturity_confidence == "low" and ai_maturity_score >= 2,
    the message must NOT contain assertive AI maturity phrases and MUST
    contain interrogative markers.

    Validates: Requirements 3.4
    """
    brief = _make_brief(ai_maturity_score=score, ai_maturity_confidence="low")
    msg = compose_outbound(prospect, brief, gap_brief, channel)
    content_lower = msg.content.lower()

    # Must NOT contain assertive phrases
    assertive_phrases = [
        "you are actively investing in ai",
        "you are building ai",
        "your ai investment",
    ]
    for phrase in assertive_phrases:
        assert phrase not in content_lower, (
            f"Assertive phrase {phrase!r} found in message with low AI confidence"
        )

    # MUST contain at least one interrogative marker
    interrogative_markers = ["we noticed", "signals suggesting", "?"]
    assert any(marker in content_lower for marker in interrogative_markers), (
        f"No interrogative marker found in message with low AI confidence. "
        f"Content: {msg.content!r}"
    )


# ---------------------------------------------------------------------------
# Property 7: Competitor Gap Confidence Gate
# ---------------------------------------------------------------------------

# Feature: conversion-engine, Property 7: Competitor Gap Confidence Gate
@given(
    prospect=_prospect_strategy,
    brief=_hiring_signal_brief_strategy,
    gap_practices=st.lists(
        st.text(min_size=3, max_size=20, alphabet=st.characters(whitelist_categories=("Lu", "Ll"))),
        min_size=1,
        max_size=3,
    ),
    channel=_channel_strategy,
)
@settings(max_examples=100)
def test_low_confidence_gaps_never_referenced(
    prospect: Prospect,
    brief: HiringSignalBrief,
    gap_practices: list[str],
    channel: str,
) -> None:
    """
    When all gaps have confidence "low", no gap practice name appears in the message.

    Validates: Requirements 4.5
    """
    gaps = [
        CompetitorGap(
            practice=practice,
            evidence="some evidence",
            confidence="low",
            peer_refs=[],
        )
        for practice in gap_practices
    ]
    gap_brief = _make_gap_brief(gaps)
    msg = compose_outbound(prospect, brief, gap_brief, channel)
    content_lower = msg.content.lower()

    import re
    for practice in gap_practices:
        # Use word-boundary matching: practice must appear as a whole word,
        # not as a substring of an unrelated word (e.g. "cal" inside "call").
        pattern = r"\b" + re.escape(practice.lower()) + r"\b"
        assert not re.search(pattern, content_lower), (
            f"Low-confidence gap practice {practice!r} was referenced in message"
        )


# ---------------------------------------------------------------------------
# Property 9: Honesty Constraint — Confidence Gating
# ---------------------------------------------------------------------------

# Feature: conversion-engine, Property 9: Honesty Constraint — Confidence Gating
@given(
    prospect=_prospect_strategy,
    job_post_count=st.one_of(st.none(), st.integers(min_value=0, max_value=50)),
    gap_brief=_nullable_gap_brief_strategy,
    channel=_channel_strategy,
)
@settings(max_examples=100)
def test_low_or_null_job_post_confidence_uses_interrogative(
    prospect: Prospect,
    job_post_count: Optional[int],
    gap_brief: Optional[CompetitorGapBrief],
    channel: str,
) -> None:
    """
    When job_post_confidence is "low" or None, the message must NOT assert
    job-post claims and MUST use interrogative phrasing when referencing job posts.

    Validates: Requirements 6.1, 6.2
    """
    # Test with "low" confidence
    brief_low = _make_brief(
        job_post_count=job_post_count,
        job_post_confidence="low",
    )
    msg_low = compose_outbound(prospect, brief_low, gap_brief, channel)
    _assert_no_assertive_job_post_claim(msg_low.content, job_post_count)

    # Test with None confidence
    brief_none = _make_brief(
        job_post_count=job_post_count,
        job_post_confidence=None,
    )
    msg_none = compose_outbound(prospect, brief_none, gap_brief, channel)
    _assert_no_assertive_job_post_claim(msg_none.content, job_post_count)


def _assert_no_assertive_job_post_claim(content: str, count: Optional[int]) -> None:
    """
    Assert that the content does not contain assertive job-post claims.

    Assertive patterns: "We see N open engineering roles." (without interrogative)
    Interrogative patterns: "we noticed signals suggesting", "is that right?"
    """
    content_lower = content.lower()

    # "aggressive hiring" must never appear with low/null confidence
    assert "aggressive hiring" not in content_lower, (
        "Assertive 'aggressive hiring' claim found with low/null job_post_confidence"
    )

    # If job posts are mentioned, must use interrogative phrasing
    if count is not None and count > 0:
        # If the count appears in the content, it must be in an interrogative context
        if str(count) in content:
            interrogative_markers = ["we noticed", "signals suggesting", "is that right", "?"]
            assert any(m in content_lower for m in interrogative_markers), (
                f"Job post count {count} referenced without interrogative phrasing "
                f"under low/null confidence. Content: {content!r}"
            )


# ---------------------------------------------------------------------------
# Property 10: Aggressive Hiring Threshold
# ---------------------------------------------------------------------------

# Feature: conversion-engine, Property 10: Aggressive Hiring Threshold
@given(
    prospect=_prospect_strategy,
    job_post_count=st.one_of(
        st.none(),
        st.integers(min_value=0, max_value=4),   # count < 5
    ),
    job_post_velocity_60d=st.one_of(
        st.none(),
        st.floats(min_value=0.0, max_value=2.99, allow_nan=False),  # velocity < 3.0
    ),
    gap_brief=_nullable_gap_brief_strategy,
    channel=_channel_strategy,
)
@settings(max_examples=100)
def test_aggressive_hiring_not_used_below_threshold(
    prospect: Prospect,
    job_post_count: Optional[int],
    job_post_velocity_60d: Optional[float],
    gap_brief: Optional[CompetitorGapBrief],
    channel: str,
) -> None:
    """
    When job_post_count < 5 OR job_post_velocity_60d < 3.0 (or either is None),
    the message must NOT contain "aggressive hiring".

    Validates: Requirements 6.3
    """
    # Case A: count below threshold, any velocity
    brief_low_count = _make_brief(
        job_post_count=job_post_count,
        job_post_velocity_60d=job_post_velocity_60d,
        job_post_confidence="high",  # high confidence so we'd otherwise assert
    )
    msg = compose_outbound(prospect, brief_low_count, gap_brief, channel)
    assert "aggressive hiring" not in msg.content.lower(), (
        f"'aggressive hiring' found when count={job_post_count} and "
        f"velocity={job_post_velocity_60d} (threshold not met)"
    )


@given(
    prospect=_prospect_strategy,
    job_post_count=st.integers(min_value=5, max_value=50),
    job_post_velocity_60d=st.one_of(
        st.none(),
        st.floats(min_value=0.0, max_value=2.99, allow_nan=False),  # velocity < 3.0
    ),
    gap_brief=_nullable_gap_brief_strategy,
    channel=_channel_strategy,
)
@settings(max_examples=100)
def test_aggressive_hiring_not_used_when_velocity_below_threshold(
    prospect: Prospect,
    job_post_count: int,
    job_post_velocity_60d: Optional[float],
    gap_brief: Optional[CompetitorGapBrief],
    channel: str,
) -> None:
    """
    When count >= 5 but velocity < 3.0 (or None), no "aggressive hiring".

    Validates: Requirements 6.3
    """
    brief = _make_brief(
        job_post_count=job_post_count,
        job_post_velocity_60d=job_post_velocity_60d,
        job_post_confidence="high",
    )
    msg = compose_outbound(prospect, brief, gap_brief, channel)
    assert "aggressive hiring" not in msg.content.lower(), (
        f"'aggressive hiring' found when velocity={job_post_velocity_60d} < 3.0"
    )


# ---------------------------------------------------------------------------
# Property 12: Email-First Channel Ordering
# ---------------------------------------------------------------------------

# Feature: conversion-engine, Property 12: Email-First Channel Ordering
@given(
    prospect=_prospect_strategy,
    brief=_hiring_signal_brief_strategy,
    gap_brief=_nullable_gap_brief_strategy,
)
@settings(max_examples=100)
def test_start_sequence_always_returns_email(
    prospect: Prospect,
    brief: HiringSignalBrief,
    gap_brief: Optional[CompetitorGapBrief],
) -> None:
    """
    For any new prospect (state=cold, outbound_attempt_count=0, email_reply_count=0),
    start_sequence() always returns a message with channel == "email".

    Validates: Requirements 8.1
    """
    # Ensure prospect is cold with zero attempts
    prospect.current_state = ProspectState.COLD
    prospect.outbound_attempt_count = 0

    fsm = ProspectFSM(prospect)
    msg = fsm.start_sequence(brief, gap_brief)

    assert msg.channel == "email", (
        f"First outbound message must use email channel, got {msg.channel!r}"
    )


# ---------------------------------------------------------------------------
# Property 13: SMS Gate
# ---------------------------------------------------------------------------

# Feature: conversion-engine, Property 13: SMS Gate
@given(
    brief=_hiring_signal_brief_strategy,
    gap_brief=_nullable_gap_brief_strategy,
)
@settings(max_examples=100)
def test_sms_not_sent_without_email_reply(
    brief: HiringSignalBrief,
    gap_brief: Optional[CompetitorGapBrief],
) -> None:
    """
    When _email_reply_count == 0, send_next_outbound() must never return
    a message with channel == "sms".

    Validates: Requirements 8.2
    """
    # Build a prospect that prefers SMS but has never replied to an email
    prospect = Prospect(
        prospect_id="test-sms-gate",
        company_id="co-001",
        contact_name="Alex",
        email="alex@example.com",
        phone="+1234567890",
        timezone="America/New_York",
        preferred_channel="sms",
        current_state=ProspectState.CONTACTED,
        outbound_attempt_count=1,
        segment=Segment.S1,
        hiring_signal_brief_ref=None,
    )

    fsm = ProspectFSM(prospect)
    # Confirm no email replies have been recorded
    assert fsm._email_reply_count == 0

    msg = fsm.send_next_outbound(brief, gap_brief)

    if msg is not None:
        assert msg.channel != "sms", (
            f"SMS dispatched without prior email reply. Got channel={msg.channel!r}"
        )


# ---------------------------------------------------------------------------
# Unit tests — FSM state transitions
# ---------------------------------------------------------------------------


class TestProspectFSMTransitions:
    """Unit tests for FSM state transitions."""

    def _make_brief(self) -> HiringSignalBrief:
        return _make_brief()

    def test_start_sequence_transitions_cold_to_contacted(self):
        prospect = _make_cold_prospect()
        fsm = ProspectFSM(prospect)
        fsm.start_sequence(self._make_brief(), None)
        assert fsm.state == ProspectState.CONTACTED

    def test_start_sequence_raises_if_not_cold(self):
        prospect = _make_cold_prospect()
        prospect.current_state = ProspectState.CONTACTED
        fsm = ProspectFSM(prospect)
        with pytest.raises(ValueError):
            fsm.start_sequence(self._make_brief(), None)

    def test_inbound_reply_transitions_contacted_to_replied(self):
        prospect = _make_cold_prospect()
        prospect.current_state = ProspectState.CONTACTED
        fsm = ProspectFSM(prospect)
        fsm.handle_inbound_reply("email", "Thanks for reaching out!")
        assert fsm.state == ProspectState.REPLIED

    def test_opt_out_transitions_to_opted_out(self):
        prospect = _make_cold_prospect()
        prospect.current_state = ProspectState.CONTACTED
        fsm = ProspectFSM(prospect)
        fsm.handle_inbound_reply("sms", "STOP")
        assert fsm.state == ProspectState.OPTED_OUT

    def test_receive_opt_out_transitions_to_opted_out(self):
        prospect = _make_cold_prospect()
        fsm = ProspectFSM(prospect)
        fsm.receive_opt_out("email")
        assert fsm.state == ProspectState.OPTED_OUT

    def test_opted_out_send_next_returns_none(self):
        prospect = _make_cold_prospect()
        prospect.current_state = ProspectState.OPTED_OUT
        fsm = ProspectFSM(prospect)
        assert fsm.send_next_outbound(self._make_brief(), None) is None

    def test_dormant_send_next_returns_none(self):
        prospect = _make_cold_prospect()
        prospect.current_state = ProspectState.DORMANT
        fsm = ProspectFSM(prospect)
        assert fsm.send_next_outbound(self._make_brief(), None) is None

    def test_three_unanswered_attempts_transitions_to_dormant(self):
        prospect = _make_cold_prospect()
        prospect.current_state = ProspectState.CONTACTED
        prospect.outbound_attempt_count = 3
        fsm = ProspectFSM(prospect)
        result = fsm.send_next_outbound(self._make_brief(), None)
        assert result is None
        assert fsm.state == ProspectState.DORMANT

    def test_email_reply_increments_reply_count(self):
        prospect = _make_cold_prospect()
        prospect.current_state = ProspectState.CONTACTED
        fsm = ProspectFSM(prospect)
        fsm.handle_inbound_reply("email", "Interested!")
        assert fsm._email_reply_count == 1

    def test_sms_reply_does_not_increment_email_reply_count(self):
        prospect = _make_cold_prospect()
        prospect.current_state = ProspectState.CONTACTED
        fsm = ProspectFSM(prospect)
        fsm.handle_inbound_reply("sms", "Sure")
        assert fsm._email_reply_count == 0

    def test_can_send_sms_false_initially(self):
        prospect = _make_cold_prospect()
        fsm = ProspectFSM(prospect)
        assert fsm.can_send_sms is False

    def test_can_send_sms_true_after_email_reply(self):
        prospect = _make_cold_prospect()
        prospect.current_state = ProspectState.CONTACTED
        fsm = ProspectFSM(prospect)
        fsm.handle_inbound_reply("email", "Yes, interested!")
        assert fsm.can_send_sms is True

    def test_booking_intent_transitions_replied_to_warm(self):
        prospect = _make_cold_prospect()
        prospect.current_state = ProspectState.REPLIED
        fsm = ProspectFSM(prospect)
        fsm.handle_inbound_reply("email", "Yes, let's book a call!")
        assert fsm.state == ProspectState.WARM


# ---------------------------------------------------------------------------
# Unit tests — compose_outbound rules
# ---------------------------------------------------------------------------


class TestComposeOutbound:
    """Unit tests for compose_outbound message composition rules."""

    def test_draft_always_true(self):
        prospect = _make_cold_prospect()
        brief = _make_brief()
        msg = compose_outbound(prospect, brief, None, "email")
        assert msg.draft is True

    def test_email_has_subject(self):
        prospect = _make_cold_prospect()
        brief = _make_brief()
        msg = compose_outbound(prospect, brief, None, "email")
        assert msg.subject is not None

    def test_sms_has_no_subject(self):
        prospect = _make_cold_prospect()
        brief = _make_brief()
        msg = compose_outbound(prospect, brief, None, "sms")
        assert msg.subject is None

    def test_s1_high_score_uses_scale_framing(self):
        prospect = _make_cold_prospect(segment=Segment.S1)
        brief = _make_brief(ai_maturity_score=2, ai_maturity_confidence="high")
        msg = compose_outbound(prospect, brief, None, "email")
        assert "scale your ai team" in msg.content.lower()

    def test_s2_low_score_uses_stand_up_framing(self):
        prospect = _make_cold_prospect(segment=Segment.S2)
        brief = _make_brief(ai_maturity_score=1, ai_maturity_confidence="medium")
        msg = compose_outbound(prospect, brief, None, "email")
        assert "stand up your first ai function" in msg.content.lower()

    def test_aggressive_hiring_present_when_threshold_met(self):
        prospect = _make_cold_prospect()
        brief = _make_brief(
            job_post_count=5,
            job_post_velocity_60d=3.0,
            job_post_confidence="high",
        )
        msg = compose_outbound(prospect, brief, None, "email")
        assert "aggressive hiring" in msg.content.lower()

    def test_aggressive_hiring_absent_when_count_below_threshold(self):
        prospect = _make_cold_prospect()
        brief = _make_brief(
            job_post_count=4,
            job_post_velocity_60d=5.0,
            job_post_confidence="high",
        )
        msg = compose_outbound(prospect, brief, None, "email")
        assert "aggressive hiring" not in msg.content.lower()

    def test_aggressive_hiring_absent_when_velocity_below_threshold(self):
        prospect = _make_cold_prospect()
        brief = _make_brief(
            job_post_count=10,
            job_post_velocity_60d=2.9,
            job_post_confidence="high",
        )
        msg = compose_outbound(prospect, brief, None, "email")
        assert "aggressive hiring" not in msg.content.lower()

    def test_high_confidence_gap_referenced(self):
        prospect = _make_cold_prospect()
        brief = _make_brief()
        gap = CompetitorGap(
            practice="MLOps pipeline",
            evidence="peer evidence",
            confidence="high",
            peer_refs=[],
        )
        gap_brief = _make_gap_brief([gap])
        msg = compose_outbound(prospect, brief, gap_brief, "email")
        assert "mlops pipeline" in msg.content.lower()

    def test_low_confidence_gap_not_referenced(self):
        prospect = _make_cold_prospect()
        brief = _make_brief()
        gap = CompetitorGap(
            practice="MLOps pipeline",
            evidence="weak evidence",
            confidence="low",
            peer_refs=[],
        )
        gap_brief = _make_gap_brief([gap])
        msg = compose_outbound(prospect, brief, gap_brief, "email")
        assert "mlops pipeline" not in msg.content.lower()

    def test_low_job_post_confidence_uses_interrogative(self):
        prospect = _make_cold_prospect()
        brief = _make_brief(job_post_count=10, job_post_confidence="low")
        msg = compose_outbound(prospect, brief, None, "email")
        content_lower = msg.content.lower()
        assert any(m in content_lower for m in ["we noticed", "signals suggesting", "is that right", "?"])

    def test_null_job_post_confidence_uses_interrogative(self):
        prospect = _make_cold_prospect()
        brief = _make_brief(job_post_count=10, job_post_confidence=None)
        msg = compose_outbound(prospect, brief, None, "email")
        content_lower = msg.content.lower()
        assert any(m in content_lower for m in ["we noticed", "signals suggesting", "is that right", "?"])
