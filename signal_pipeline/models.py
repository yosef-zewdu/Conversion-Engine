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
    weight: ConfidenceLevel
    value: Union[str, float, bool, None] = None
    justification: str


# ---------------------------------------------------------------------------
# HiringSignalBrief
# ---------------------------------------------------------------------------

IcpSegmentValue = Optional[
    Literal["segment_1", "segment_2", "segment_3", "segment_4", "unqualified"]
]


class HiringSignalBrief(BaseModel):
    schema_version: Literal["1.0"]
    company_id: str
    company_name: str
    last_enriched_at: str  # ISO 8601 datetime
    crunchbase_id: Optional[str] = None
    bench_summary_version: Optional[str] = None  # ISO 8601 datetime
    bench_mismatch: Optional[bool] = None
    tech_stack: Optional[TechStack] = None
    funding_event: Optional[FundingEvent] = None
    layoff_event: Optional[LayoffEvent] = None
    job_post_count: Optional[int] = None
    job_post_velocity_60d: Optional[float] = None
    job_post_confidence: NullableConfidence = None
    leadership_change: Optional[LeadershipChange] = None
    ai_maturity_score: Optional[int] = Field(default=None, ge=0, le=3)
    ai_maturity_confidence: Optional[ConfidenceLevel] = None
    ai_maturity_justification: list[AIMaturityJustificationEntry] = Field(
        default_factory=list
    )
    icp_segment: IcpSegmentValue = None
    icp_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    icp_signals_used: list[str] = Field(default_factory=list)


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
