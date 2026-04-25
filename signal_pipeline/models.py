"""
Pydantic v2 models for HiringSignalBrief and CompetitorGapBrief.
"""
from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared confidence enum values (nullable variant used in sub-objects)
# ---------------------------------------------------------------------------

ConfidenceLevel = Literal["high", "medium", "low"]
NullableConfidence = Optional[Literal["high", "medium", "low"]]


# ---------------------------------------------------------------------------
# HiringSignalBrief sub-objects
# ---------------------------------------------------------------------------


class TechStack(BaseModel):
    languages: list[str] = Field(default_factory=list)
    ml_tools: list[str] = Field(default_factory=list)
    data_tools: list[str] = Field(default_factory=list)
    confidence: NullableConfidence = None


class FundingEvent(BaseModel):
    round_type: str
    amount_usd: Optional[float] = None
    close_date: str  # date string YYYY-MM-DD
    confidence: NullableConfidence = None


class LayoffEvent(BaseModel):
    event_date: str  # date string YYYY-MM-DD
    headcount_affected: Optional[int] = None
    percentage_cut: Optional[float] = None
    confidence: NullableConfidence = None


class LeadershipChange(BaseModel):
    role: str
    appointment_date: str  # date string YYYY-MM-DD
    confidence: NullableConfidence = None


class AIMaturityJustificationEntry(BaseModel):
    signal: str
    status: str                              # free-text description of what was found
    weight: ConfidenceLevel
    confidence: ConfidenceLevel              # confidence in this specific input
    source_url: Optional[str] = None        # public URL backing the justification


# ---------------------------------------------------------------------------
# HiringVelocity — official schema field (hiring_signal_brief.schema.json)
# ---------------------------------------------------------------------------

VelocityLabel = Literal[
    "tripled_or_more", "doubled", "increased_modestly", "flat", "declined", "insufficient_signal"
]


class HiringVelocity(BaseModel):
    """Hiring velocity from the official schema. Replaces job_post_count + job_post_velocity_60d."""
    open_roles_today: int = Field(ge=0)
    open_roles_60_days_ago: int = Field(ge=0, description="From 60-day-prior job-post snapshot.")
    velocity_label: VelocityLabel = "insufficient_signal"
    signal_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    sources: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# HiringSignalBrief
# ---------------------------------------------------------------------------

IcpSegmentValue = Optional[
    Literal[
        "segment_1_series_a_b",
        "segment_2_mid_market_restructure",
        "segment_3_leadership_transition",
        "segment_4_specialized_capability",
        "abstain",
        # legacy values retained for backward-compat during migration
        "segment_1", "segment_2", "segment_3", "segment_4", "unqualified",
    ]
]

HonestyFlag = Literal[
    "weak_hiring_velocity_signal",
    "weak_ai_maturity_signal",
    "conflicting_segment_signals",
    "layoff_overrides_funding",
    "bench_gap_detected",
    "tech_stack_inferred_not_confirmed",
]


class HiringSignalBrief(BaseModel):
    schema_version: Literal["1.0"]
    company_id: str
    company_name: str
    last_enriched_at: str  # ISO 8601 datetime
    crunchbase_id: Optional[str] = None
    bench_summary_version: Optional[str] = None  # ISO 8601 datetime
    bench_mismatch: Optional[bool] = None

    # Structured sub-objects
    tech_stack: Optional[TechStack] = None
    funding_event: Optional[FundingEvent] = None
    layoff_event: Optional[LayoffEvent] = None
    leadership_change: Optional[LeadershipChange] = None

    # Official hiring velocity block (replaces flat job_post_count / job_post_velocity_60d)
    hiring_velocity: Optional[HiringVelocity] = None

    # Legacy flat fields — kept for pipeline backward-compat; prefer hiring_velocity going forward
    job_post_count: Optional[int] = None
    job_post_velocity_60d: Optional[float] = None
    job_post_confidence: NullableConfidence = None

    # AI maturity
    ai_maturity_score: Optional[int] = Field(default=None, ge=0, le=3)
    ai_maturity_confidence: Optional[ConfidenceLevel] = None
    ai_maturity_justification: list[AIMaturityJustificationEntry] = Field(
        default_factory=list
    )

    # Classification output
    icp_segment: IcpSegmentValue = None
    icp_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    icp_signals_used: list[str] = Field(default_factory=list)

    # Honesty flags the agent must respect when composing outreach
    honesty_flags: list[HonestyFlag] = Field(default_factory=list)

    # Employee headcount bounds from firmographic enrichment (for classifier gates)
    employee_count_min: Optional[int] = None
    employee_count_max: Optional[int] = None


# ---------------------------------------------------------------------------
# CompetitorGapBrief sub-objects
# ---------------------------------------------------------------------------


class CompetitorGapPeer(BaseModel):
    company_id: str
    company_name: str
    ai_maturity_score: int = Field(ge=0, le=3)


class CompetitorGap(BaseModel):
    practice: str
    evidence: str
    confidence: ConfidenceLevel
    peer_refs: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# CompetitorGapBrief
# ---------------------------------------------------------------------------


class CompetitorGapBrief(BaseModel):
    schema_version: Literal["1.0"]
    company_id: str
    generated_at: str  # ISO 8601 datetime
    prospect_ai_maturity_score: Optional[int] = Field(default=None, ge=0, le=3)
    sector_percentile: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    peer_count: Optional[int] = Field(default=None, ge=0)
    peers: list[CompetitorGapPeer] = Field(default_factory=list, max_length=10)
    gaps: list[CompetitorGap] = Field(default_factory=list, max_length=3)
