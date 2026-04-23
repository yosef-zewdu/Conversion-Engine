"""
Nurture Sequencer FSM and message composition logic.

Implements a per-prospect finite state machine that drives multi-channel
outreach according to the Tenacious style guide and honesty constraints.

States: cold → contacted → replied → warm → booking → dormant | opted_out

Requirements: 1.4, 3.4, 4.5, 6.1, 6.2, 6.3, 8.1, 8.2, 8.5, 8.6, 8.7
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from config.models import Prospect, ProspectState, Segment
from nurture_sequencer.outbound_message import OutboundMessage
from signal_pipeline.models import CompetitorGapBrief, HiringSignalBrief

# ---------------------------------------------------------------------------
# Opt-out detection
# ---------------------------------------------------------------------------

_OPT_OUT_KEYWORDS = {"stop", "unsub", "unsubscribe"}


def is_opt_out(content: str) -> bool:
    """
    Return True if content contains STOP, UNSUB, or UNSUBSCRIBE (case-insensitive).

    Args:
        content: Inbound message text to inspect.

    Returns:
        ``True`` when the content signals an opt-out intent.
    """
    normalised = content.lower()
    return any(keyword in normalised for keyword in _OPT_OUT_KEYWORDS)


# ---------------------------------------------------------------------------
# Booking-intent detection (simple heuristic)
# ---------------------------------------------------------------------------

_BOOKING_KEYWORDS = {
    "book",
    "schedule",
    "call",
    "meeting",
    "calendar",
    "slot",
    "available",
    "availability",
    "interested",
    "yes",
    "sure",
    "sounds good",
    "let's talk",
    "let's chat",
}


def _has_booking_intent(content: str) -> bool:
    """
    Return True when the inbound reply suggests booking intent.

    Args:
        content: Inbound message text.

    Returns:
        ``True`` when booking-intent keywords are detected.
    """
    normalised = content.lower()
    return any(kw in normalised for kw in _BOOKING_KEYWORDS)


# ---------------------------------------------------------------------------
# Message composition helpers
# ---------------------------------------------------------------------------

_ASSERTIVE_PHRASES = [
    "you are actively investing in ai",
    "you are building ai",
    "your ai investment",
    "you are scaling your ai",
]

_INTERROGATIVE_MARKERS = [
    "we noticed",
    "signals suggesting",
    "?",
]


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _ai_maturity_framing(
    prospect: Prospect,
    brief: HiringSignalBrief,
) -> str:
    """
    Produce the AI maturity pitch fragment for the message body.

    Rules (Req 8.7, 3.4):
    - Segment 1 or 2 with score >= 2 → "scale your AI team" framing.
    - Segment 1 or 2 with score <= 1 → "stand up your first AI function" framing.
    - When ai_maturity_confidence == "low" and score >= 2 → interrogative phrasing.

    Args:
        prospect: The prospect being messaged.
        brief: The enriched HiringSignalBrief.

    Returns:
        A short string fragment to embed in the message body.
    """
    score = brief.ai_maturity_score
    confidence = brief.ai_maturity_confidence
    segment = prospect.segment

    is_s1_or_s2 = segment in (Segment.S1, Segment.S2)

    if is_s1_or_s2:
        if score is not None and score >= 2:
            if confidence == "low":
                # Req 3.4: low confidence → interrogative phrasing
                return (
                    "we noticed signals suggesting your team may be ready to scale "
                    "your AI function — is that something you're exploring?"
                )
            # High/medium confidence assertive framing
            return "scale your AI team"
        else:
            # score <= 1 or None
            return "stand up your first AI function"

    # Non-S1/S2 segments: use interrogative when confidence is low and score >= 2
    if score is not None and score >= 2 and confidence == "low":
        return (
            "we noticed signals suggesting your team may be investing in AI — "
            "is that right?"
        )
    if score is not None and score >= 2:
        return "scale your AI team"
    return "stand up your first AI function"


def _job_post_fragment(brief: HiringSignalBrief) -> str:
    """
    Produce the job-post signal fragment, respecting the honesty constraint.

    Rules (Req 6.1, 6.2, 6.3):
    - Only assert when job_post_confidence is "medium" or "high".
    - Use interrogative phrasing for "low" or None.
    - Only use "aggressive hiring" when count >= 5 AND velocity >= 3.0.

    Args:
        brief: The enriched HiringSignalBrief.

    Returns:
        A string fragment describing the job-post signal, or empty string.
    """
    confidence = brief.job_post_confidence
    count = brief.job_post_count
    velocity = brief.job_post_velocity_60d

    if confidence in ("medium", "high"):
        # Assertive path — check aggressive hiring threshold (Req 6.3)
        if (
            count is not None
            and count >= 5
            and velocity is not None
            and velocity >= 3.0
        ):
            return (
                f"We see {count} open engineering roles with aggressive hiring "
                f"velocity over the last 60 days."
            )
        if count is not None:
            return f"We see {count} open engineering roles."
        return "We see active engineering hiring."
    else:
        # Low or None confidence → interrogative (Req 6.2)
        if count is not None:
            return (
                f"We noticed signals suggesting around {count} open engineering "
                "roles — is that right?"
            )
        return "we noticed signals suggesting active engineering hiring."


def _gap_fragment(gap_brief: Optional[CompetitorGapBrief]) -> str:
    """
    Produce the competitor gap fragment, gated on confidence (Req 4.5).

    Only references gaps with confidence "medium" or "high".

    Args:
        gap_brief: The CompetitorGapBrief, or None.

    Returns:
        A string fragment describing the highest-confidence gap, or empty string.
    """
    if gap_brief is None:
        return ""

    eligible = [
        g for g in gap_brief.gaps if g.confidence in ("medium", "high")
    ]
    if not eligible:
        return ""

    # Pick the first eligible gap (highest confidence first)
    eligible.sort(key=lambda g: 0 if g.confidence == "high" else 1)
    gap = eligible[0]
    return (
        f"Peers in your sector show strong signal for {gap.practice} — "
        "a practice we haven't seen public evidence of at your company yet."
    )


def compose_outbound(
    prospect: Prospect,
    brief: HiringSignalBrief,
    gap_brief: Optional[CompetitorGapBrief],
    channel: str,
) -> OutboundMessage:
    """
    Compose a single outbound message for the given prospect and channel.

    Enforces all honesty, confidence-gating, and tone rules structurally:
    1. ``draft`` is ALWAYS ``True`` (Req 1.4).
    2. Channel selection: first message always ``"email"``; SMS only if
       ``_email_reply_count >= 1`` — callers must enforce this gate.
    3. AI maturity framing adapted to segment and confidence (Req 8.7, 3.4).
    4. Job-post claims gated on confidence (Req 6.1, 6.2).
    5. "aggressive hiring" only when count >= 5 AND velocity >= 3.0 (Req 6.3).
    6. Competitor gap only referenced when confidence is medium/high (Req 4.5).
    7. Low AI maturity confidence → interrogative phrasing (Req 3.4).

    Args:
        prospect: The prospect being messaged.
        brief: The enriched HiringSignalBrief.
        gap_brief: The CompetitorGapBrief, or None.
        channel: Delivery channel — ``"email"``, ``"sms"``, or ``"voice"``.

    Returns:
        A fully composed ``OutboundMessage`` with ``draft=True``.
    """
    ai_fragment = _ai_maturity_framing(prospect, brief)
    job_fragment = _job_post_fragment(brief)
    gap_fragment = _gap_fragment(gap_brief)

    parts = [
        f"Hi {prospect.contact_name},",
        "",
        f"I'm reaching out because {job_fragment}",
        "",
        f"Given your stage, we can help you {ai_fragment}.",
    ]

    if gap_fragment:
        parts += ["", gap_fragment]

    parts += [
        "",
        "Would you be open to a 20-minute call to explore fit?",
        "",
        "Best,",
        "Tenacious Team",
    ]

    content = "\n".join(parts)

    subject: Optional[str] = None
    if channel == "email":
        subject = f"Engineering hiring signal — {brief.company_name}"

    return OutboundMessage(
        prospect_id=prospect.prospect_id,
        channel=channel,
        content=content,
        subject=subject,
        draft=True,  # ALWAYS True — Req 1.4
        sent_at=_now_iso(),
        metadata={"draft": True},
    )


# ---------------------------------------------------------------------------
# ProspectFSM
# ---------------------------------------------------------------------------


class ProspectFSM:
    """
    Per-prospect finite state machine driving the nurture sequence.

    State transitions:
    - ``cold`` → ``contacted``: on ``start_sequence()`` — sends email 1.
    - ``contacted`` + inbound reply → ``replied``.
    - ``contacted`` + outbound sent (attempt < 3) → stay ``contacted``, increment count.
    - ``contacted`` + outbound sent (attempt >= 3) → ``dormant``.
    - ``replied`` + booking intent detected → ``warm``.
    - ``any`` + opt_out → ``opted_out``.
    - ``opted_out`` or ``dormant``: ``send_next_outbound()`` returns ``None``.

    Args:
        prospect: The ``Prospect`` dataclass instance to manage.
    """

    def __init__(self, prospect: Prospect) -> None:
        """
        Initialise the FSM for the given prospect.

        Args:
            prospect: The prospect whose state this FSM manages.
        """
        self._prospect = prospect
        self._email_reply_count: int = 0

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> ProspectState:
        """
        Current FSM state.

        Returns:
            The prospect's current ``ProspectState``.
        """
        return self._prospect.current_state

    @property
    def can_send_sms(self) -> bool:
        """
        True only if the prospect has replied to at least one email (Req 8.2).

        Returns:
            ``True`` when ``_email_reply_count >= 1``.
        """
        return self._email_reply_count >= 1

    # ------------------------------------------------------------------
    # FSM transitions
    # ------------------------------------------------------------------

    def start_sequence(
        self,
        brief: HiringSignalBrief,
        gap_brief: Optional[CompetitorGapBrief],
    ) -> OutboundMessage:
        """
        Transition cold → contacted and compose the first outbound email.

        Args:
            brief: The enriched HiringSignalBrief.
            gap_brief: The CompetitorGapBrief, or None.

        Returns:
            The first outbound ``OutboundMessage`` (always email channel).

        Raises:
            ValueError: If the prospect is not in the ``cold`` state.
        """
        if self._prospect.current_state != ProspectState.COLD:
            raise ValueError(
                f"start_sequence() called on prospect in state "
                f"{self._prospect.current_state!r}; expected 'cold'."
            )

        # Transition cold → contacted
        self._prospect.current_state = ProspectState.CONTACTED
        self._prospect.outbound_attempt_count += 1

        # First message is ALWAYS email (Req 8.1)
        return compose_outbound(self._prospect, brief, gap_brief, "email")

    def handle_inbound_reply(self, channel: str, content: str) -> None:
        """
        Process an inbound reply; update state and reply counts.

        - STOP/UNSUB → ``opted_out`` (Req 8.5).
        - Email reply → increment ``_email_reply_count``; transition to ``replied``
          if currently ``contacted``.
        - Booking intent in ``replied`` state → transition to ``warm``.

        Args:
            channel: The channel the reply arrived on (``"email"`` or ``"sms"``).
            content: The raw text of the inbound reply.
        """
        # Opt-out takes priority on any channel (Req 8.5)
        if is_opt_out(content):
            self._prospect.current_state = ProspectState.OPTED_OUT
            return

        if channel == "email":
            self._email_reply_count += 1

        current = self._prospect.current_state

        if current == ProspectState.CONTACTED:
            self._prospect.current_state = ProspectState.REPLIED
        elif current == ProspectState.REPLIED:
            if _has_booking_intent(content):
                self._prospect.current_state = ProspectState.WARM

    def send_next_outbound(
        self,
        brief: HiringSignalBrief,
        gap_brief: Optional[CompetitorGapBrief],
    ) -> Optional[OutboundMessage]:
        """
        Compose and return the next outbound message, or None if halted.

        Returns ``None`` when the prospect is ``opted_out`` or ``dormant``.
        Transitions ``contacted`` → ``dormant`` after 3 unanswered attempts (Req 8.6).

        Channel selection (Req 8.1, 8.2):
        - Always email when ``_email_reply_count == 0``.
        - SMS available only after at least one email reply.

        Args:
            brief: The enriched HiringSignalBrief.
            gap_brief: The CompetitorGapBrief, or None.

        Returns:
            The next ``OutboundMessage``, or ``None`` if outreach is halted.
        """
        current = self._prospect.current_state

        # Halted states
        if current in (ProspectState.OPTED_OUT, ProspectState.DORMANT):
            return None

        # Dormancy gate: 3 unanswered attempts → dormant (Req 8.6)
        if (
            current == ProspectState.CONTACTED
            and self._prospect.outbound_attempt_count >= 3
        ):
            self._prospect.current_state = ProspectState.DORMANT
            return None

        # Channel selection (Req 8.1, 8.2)
        if self.can_send_sms and self._prospect.preferred_channel == "sms":
            channel = "sms"
        else:
            channel = "email"

        self._prospect.outbound_attempt_count += 1
        return compose_outbound(self._prospect, brief, gap_brief, channel)

    def receive_opt_out(self, channel: str) -> None:
        """
        Handle STOP/UNSUB — transition to opted_out (Req 8.5).

        Args:
            channel: The channel on which the opt-out was received.
        """
        self._prospect.current_state = ProspectState.OPTED_OUT
