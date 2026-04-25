"""
Shared data models for the Conversion Engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Destination(Enum):
    STAFF_SINK = "staff_sink"
    PROSPECT = "prospect"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Segment(str, Enum):
    S1 = "segment_1"
    S2 = "segment_2"
    S3 = "segment_3"
    S4 = "segment_4"
    UNQUALIFIED = "unqualified"


class ProspectState(str, Enum):
    COLD = "cold"
    CONTACTED = "contacted"
    REPLIED = "replied"
    WARM = "warm"
    BOOKING = "booking"
    DORMANT = "dormant"
    OPTED_OUT = "opted_out"


@dataclass
class SegmentResult:
    segment: Segment
    confidence: float          # 0.0–1.0
    signals_used: list[str]
    abstained: bool


@dataclass
class Prospect:
    prospect_id: str           # UUID, generated at intake
    company_id: str            # Crunchbase company_id
    contact_name: str
    email: str
    phone: str | None
    timezone: str              # IANA timezone string e.g. "America/New_York"
    preferred_channel: str     # "email" | "sms"
    current_state: ProspectState
    outbound_attempt_count: int
    segment: Segment | None = None
    hiring_signal_brief_ref: str | None = None  # last_enriched_at timestamp


@dataclass
class Slot:
    start_utc: str             # ISO 8601
    end_utc: str
    local_display: str         # formatted in prospect tz


@dataclass
class Booking:
    cal_event_id: str
    prospect_id: str
    segment: Segment
    brief_ref: str             # HiringSignalBrief.company_id + last_enriched_at


# ── Act IV: 3-stage chain data models ────────────────────────────

@dataclass
class ResearchField:
    """Single extracted fact with confidence gating pre-applied by Stage 1."""
    included: bool
    confidence: str | None = None   # "high" | "medium" | "low" | None
    fact: str | None = None         # Pre-screened fact text for Stage 2 Closer
    phrasing: str = "omit"          # "assertive" | "interrogative" | "omit"
    reason: str | None = None       # Exclusion reason when included=False


@dataclass
class ResearchSummary:
    """
    Output of Stage 1 ResearcherAgent.

    The Closer (Stage 2) receives ONLY this object — never the raw
    HiringSignalBrief.  Structural separation is the mechanism's core guarantee:
    the Closer cannot assert low-confidence facts it has never seen.
    """
    company_name: str
    funding: ResearchField
    hiring: ResearchField
    ai_maturity: ResearchField
    competitor_gap: ResearchField
    layoff: ResearchField
    bench_mismatch: bool = False

    def to_prompt_block(self) -> str:
        """Format for Stage 2 Closer prompt (no raw brief data leaks through)."""
        lines = [f"company: {self.company_name}"]
        for attr in ("funding", "hiring", "ai_maturity", "competitor_gap", "layoff"):
            rf: ResearchField = getattr(self, attr)
            if rf.included:
                lines.append(
                    f"{attr}: {rf.fact}  "
                    f"[phrasing={rf.phrasing}, confidence={rf.confidence}]"
                )
            else:
                lines.append(f"{attr}: OMIT — {rf.reason or 'excluded'}")
        if self.bench_mismatch:
            lines.append("bench_mismatch: True — do not make capacity commitments")
        return "\n".join(lines)
