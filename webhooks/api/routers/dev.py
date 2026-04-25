import uuid
from typing import Any
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Event, Lead, Message, Trace
from db.session import get_db
from webhooks.schemas import CreateLeadRequest, SimulateReplyRequest
from webhooks.agent_runner import run_agent
from webhooks.utils import lead_to_prospect_dict

router = APIRouter(prefix="/dev", tags=["dev"])


@router.post("/create-lead", status_code=201)
async def create_lead(
    body: CreateLeadRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Seed a synthetic lead with pre-baked briefs for demo and testing."""
    lead_id = str(uuid.uuid4())
    lead = Lead(
        id=lead_id,
        company_name=body.company_name,
        company_id=body.company_id or lead_id,
        contact_name=body.contact_name,
        email=body.email,
        phone=body.phone,
        timezone=body.timezone,
        preferred_channel=body.preferred_channel,
        current_state=body.current_state,
        segment=body.segment,
        icp_confidence=body.icp_confidence,
        ai_maturity_score=body.ai_maturity_score,
        bench_mismatch=body.bench_mismatch,
        source_refs=body.source_refs,
        hiring_signal_brief=body.hiring_signal_brief,
        competitor_gap_brief=body.competitor_gap_brief,
        icp_result=body.icp_result,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(lead)
    await db.commit()
    return {"lead_id": lead_id, "company_name": body.company_name}


_SCENARIO_TEXTS: dict[str, str] = {
    "interested_positive": "Thanks for reaching out! This sounds very relevant to what we're building. Can we schedule a call?",
    "asks_for_pricing": "Interesting pitch. What are your typical rates for a team of 3 ML engineers?",
    "asks_for_capacity": "Do you have engineers available with experience in LLM fine-tuning? When could they start?",
    "asks_for_sms": "I prefer SMS for scheduling. Can you text me instead?",
    "defensive_about_competitor_gap": "We're already using the same tools as those competitors you mentioned. I'm not sure your analysis is correct.",
    "timezone_confusion": "I'd love a call — but what timezone are your available slots in? I'm in Nairobi (EAT).",
    "interested_with_correction": "Good timing, but the funding round you mentioned was actually $8M not $14M. Still, interested.",
}

@router.post("/simulate-reply")
async def simulate_reply(
    body: SimulateReplyRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Inject a synthetic inbound reply and run it through the full agent pipeline."""
    lead = await db.get(Lead, body.lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="lead not found")

    text = body.custom_text or _SCENARIO_TEXTS.get(
        body.scenario, "Thanks for the message, I'd like to learn more."
    )

    prospect = lead_to_prospect_dict(lead)

    inbound_msg = Message(
        lead_id=body.lead_id,
        direction="inbound",
        channel=body.channel,
        body=text,
        intent=body.scenario,
        is_draft=False,
    )
    db.add(inbound_msg)

    result = await run_agent(prospect, text, body.channel)

    lead.current_state = "replied"
    lead.updated_at = datetime.now(timezone.utc)
    if result:
        if result.get("hs_contact_id"):
            lead.hs_contact_id = result["hs_contact_id"]
        if result.get("segment"):
            lead.segment = result["segment"]
        if result.get("icp_result"):
            lead.icp_result = result["icp_result"]

    event = Event(
        lead_id=body.lead_id,
        event_type="simulated_reply",
        payload={"scenario": body.scenario, "channel": body.channel, "text": text},
    )
    db.add(event)

    if result:
        reply_text = result.get("reply_text") or ""
        if reply_text:
            out_msg = Message(
                lead_id=body.lead_id,
                direction="outbound",
                channel=body.channel,
                body=reply_text,
                is_draft=True,
            )
            db.add(out_msg)

        trace_id = result.get("trace_id") or str(uuid.uuid4())
        trace = Trace(
            id=trace_id,
            lead_id=body.lead_id,
            segment=result.get("segment"),
            destination=result.get("destination"),
            policy_decision=result.get("policy_decision"),
        )
        db.add(trace)

    await db.commit()

    return {
        "lead_id": body.lead_id,
        "scenario": body.scenario,
        "inbound_text": text,
        "agent_result": result or {},
    }

@router.post("/replay-event")
async def replay_event(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Re-run the agent for any stored event by event_id."""
    body = await request.json()
    event_id: int = body.get("event_id")
    result_row = await db.execute(select(Event).where(Event.id == event_id))
    event = result_row.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="event not found")

    lead = await db.get(Lead, event.lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="lead not found")

    payload = event.payload or {}
    text = payload.get("text", "")
    channel = payload.get("channel", "email")

    prospect = lead_to_prospect_dict(lead)
    result = await run_agent(prospect, text, channel)
    return {"event_id": event_id, "agent_result": result or {}}
