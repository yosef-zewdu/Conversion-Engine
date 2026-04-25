import os
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Campaign, Lead, Message, Trace

def lead_to_prospect_dict(lead: Lead) -> dict[str, Any]:
    return {
        "prospect_id": lead.id,
        "company_id": lead.company_id or lead.id,
        "contact_name": lead.contact_name,
        "email": lead.email,
        "phone": lead.phone,
        "timezone": lead.timezone,
        "preferred_channel": lead.preferred_channel,
        "current_state": lead.current_state,
        "outbound_attempt_count": lead.outbound_attempt_count,
        "segment": lead.segment,
        "hiring_signal_brief_ref": None,
        # Pass pre-baked briefs so node_enrich uses them directly (no re-enrichment)
        "hiring_signal_brief": lead.hiring_signal_brief,
        "competitor_gap_brief": lead.competitor_gap_brief,
    }

def create_demo_prospect_dict() -> dict[str, Any]:
    return {
        "prospect_id": "demo-prospect-001",
        "company_id": "test-demo-co-001",
        "contact_name": "Demo Prospect",
        "email": os.environ.get("STAFF_SINK_EMAIL", "demo@tenacious-sandbox.dev"),
        "phone": os.environ.get("STAFF_SINK_PHONE"),
        "timezone": "America/New_York",
        "preferred_channel": "email",
        "current_state": "cold",
        "outbound_attempt_count": 0,
        "segment": None,
    }

async def get_prospect_by_id(db: AsyncSession, prospect_id: str) -> dict[str, Any] | None:
    lead = await db.get(Lead, prospect_id)
    if not lead:
        return None
    return lead_to_prospect_dict(lead)

async def get_prospect_by_email(db: AsyncSession, email: str) -> dict[str, Any] | None:
    result = await db.execute(select(Lead).where(Lead.email == email))
    lead = result.scalars().first()
    if not lead:
        demo_email = os.environ.get("STAFF_SINK_EMAIL", "demo@tenacious-sandbox.dev")
        if email.lower() == demo_email.lower():
            return create_demo_prospect_dict()
        return None
    return lead_to_prospect_dict(lead)

async def get_prospect_by_phone(db: AsyncSession, phone: str) -> dict[str, Any] | None:
    stripped = phone.lstrip("+")
    result = await db.execute(select(Lead).where(
        (Lead.phone == phone) | (Lead.phone.like(f"%{stripped}"))
    ))
    lead = result.scalars().first()
    if lead:
        return lead_to_prospect_dict(lead)

    demo_phone = os.environ.get("STAFF_SINK_PHONE", "")
    if stripped and demo_phone and (stripped in demo_phone or demo_phone in stripped):
        return create_demo_prospect_dict()

    return None

def campaign_to_dict(c: Campaign) -> dict[str, Any]:
    return {
        "id": c.id,
        "campaign_id": c.campaign_id,
        "status": c.status,
        "started_at": c.started_at.isoformat() if c.started_at else None,
        "finished_at": c.finished_at.isoformat() if c.finished_at else None,
        "candidate_count": c.candidate_count,
        "qualified_count": c.qualified_count,
        "queued_outreach_count": c.queued_outreach_count,
        "config_snapshot": c.config_snapshot,
        "error": c.error,
    }

def lead_summary(l: Lead) -> dict[str, Any]:
    icp = l.icp_result or {}
    brief = l.hiring_signal_brief or {}
    return {
        "id": l.id,
        "company_name": l.company_name,
        "segment": l.segment,
        "ai_maturity_score": l.ai_maturity_score,
        "bench_mismatch": l.bench_mismatch,
        "icp_confidence": l.icp_confidence,
        "current_state": l.current_state,
        "decision": icp.get("decision", "qualified"),
        "job_velocity": (brief.get("hiring_velocity") or {}).get("velocity_label"),
        "signals_used": icp.get("signals_used", []),
    }

def lead_detail(l: Lead) -> dict[str, Any]:
    return {
        **lead_summary(l),
        "contact_name": l.contact_name,
        "email": l.email,
        "phone": l.phone,
        "timezone": l.timezone,
        "preferred_channel": l.preferred_channel,
        "outbound_attempt_count": l.outbound_attempt_count,
        "hs_contact_id": l.hs_contact_id,
        "cal_event_id": l.cal_event_id,
        "campaign_id": l.campaign_id,
        "source_refs": l.source_refs,
        "created_at": l.created_at.isoformat() if l.created_at else None,
        "updated_at": l.updated_at.isoformat() if l.updated_at else None,
    }

def message_to_dict(m: Message) -> dict[str, Any]:
    return {
        "id": m.id,
        "direction": m.direction,
        "channel": m.channel,
        "body": m.body,
        "subject": m.subject,
        "intent": m.intent,
        "is_draft": m.is_draft,
        "sent_at": m.sent_at.isoformat() if m.sent_at else None,
    }

def trace_to_dict(t: Trace) -> dict[str, Any]:
    return {
        "id": t.id,
        "lead_id": t.lead_id,
        "model": t.model,
        "prompt_tokens": t.prompt_tokens,
        "completion_tokens": t.completion_tokens,
        "latency_seconds": t.latency_seconds,
        "cost_usd": t.cost_usd,
        "segment": t.segment,
        "destination": t.destination,
        "policy_decision": t.policy_decision,
        "unsupported_claim_count": t.unsupported_claim_count,
        "bench_overcommitment": t.bench_overcommitment,
        "pricing_violation": t.pricing_violation,
        "source_refs": t.source_refs,
        "hs_status": t.hs_status,
        "cal_booking_status": t.cal_booking_status,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }
