"""
SQLAlchemy ORM models for the Conversion Engine operator store.

Tables:
  campaigns    — campaign runs (one per POST /campaigns/run)
  leads        — one row per qualified prospect account
  messages     — outbound/inbound messages per lead
  events       — FSM state transitions and other lifecycle events
  traces       — LLM call records (cost, latency, policy decisions)
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # camp_xxxxx
    campaign_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    qualified_count: Mapped[int] = mapped_column(Integer, default=0)
    queued_outreach_count: Mapped[int] = mapped_column(Integer, default=0)
    config_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    leads: Mapped[list[Lead]] = relationship("Lead", back_populates="campaign", cascade="all, delete-orphan")


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # prospect_id UUID
    campaign_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=True
    )
    company_name: Mapped[str] = mapped_column(String(256), nullable=False)
    company_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    contact_name: Mapped[str] = mapped_column(String(256), nullable=False)
    email: Mapped[str] = mapped_column(String(256), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    preferred_channel: Mapped[str] = mapped_column(String(16), default="email")
    current_state: Mapped[str] = mapped_column(String(32), default="cold")
    segment: Mapped[str | None] = mapped_column(String(64), nullable=True)
    icp_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_maturity_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bench_mismatch: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    hs_contact_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cal_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    outbound_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    source_refs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    hiring_signal_brief: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    competitor_gap_brief: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    icp_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    campaign: Mapped[Campaign | None] = relationship("Campaign", back_populates="leads")
    messages: Mapped[list[Message]] = relationship("Message", back_populates="lead", cascade="all, delete-orphan")
    events: Mapped[list[Event]] = relationship("Event", back_populates="lead", cascade="all, delete-orphan")
    traces: Mapped[list[Trace]] = relationship("Trace", back_populates="lead", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[str] = mapped_column(String(64), ForeignKey("leads.id"), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)   # "outbound" | "inbound"
    channel: Mapped[str] = mapped_column(String(16), nullable=False)    # "email" | "sms"
    body: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str | None] = mapped_column(String(512), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(64), nullable=True)   # classified intent
    is_draft: Mapped[bool] = mapped_column(Boolean, default=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    lead: Mapped[Lead] = relationship("Lead", back_populates="messages")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[str] = mapped_column(String(64), ForeignKey("leads.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # e.g. "state_transition", "email_sent", "sms_sent", "booking_created", "agent_classified"
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    lead: Mapped[Lead] = relationship("Lead", back_populates="events")


class Trace(Base):
    __tablename__ = "traces"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)   # Langfuse trace_id
    lead_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("leads.id", ondelete="CASCADE"), nullable=True
    )
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    segment: Mapped[str | None] = mapped_column(String(64), nullable=True)
    destination: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy_decision: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    unsupported_claim_count: Mapped[int] = mapped_column(Integer, default=0)
    bench_overcommitment: Mapped[bool] = mapped_column(Boolean, default=False)
    pricing_violation: Mapped[bool] = mapped_column(Boolean, default=False)
    source_refs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    hs_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cal_booking_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    lead: Mapped[Lead | None] = relationship("Lead", back_populates="traces")
