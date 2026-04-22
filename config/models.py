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
