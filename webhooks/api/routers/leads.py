import uuid
from typing import Any
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Event, Lead, Message, Trace
from db.session import get_db
from webhooks.schemas import StartOutreachRequest
from webhooks.agent_runner import run_agent
from webhooks.utils import lead_to_prospect_dict, lead_detail, message_to_dict, trace_to_dict

router = APIRouter(prefix="/leads", tags=["leads"])

@router.post("/{lead_id}/start-outreach")
async def start_outreach(
    lead_id: str,
    body: StartOutreachRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    lead = await db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="lead not found")

    prospect = lead_to_prospect_dict(lead)

    result = await run_agent(prospect, body.inbound_text, body.channel)

    if result:
        if result.get("segment"):
            lead.segment = result["segment"]
        if result.get("hs_contact_id"):
            lead.hs_contact_id = result["hs_contact_id"]
        if result.get("bench_mismatch") is not None:
            lead.bench_mismatch = result["bench_mismatch"]
        # Persist enriched briefs if the agent produced them (or re-confirmed pre-baked)
        if result.get("hiring_signal_brief"):
            lead.hiring_signal_brief = result["hiring_signal_brief"]
            ai_score = result["hiring_signal_brief"].get("ai_maturity_score")
            if ai_score is not None:
                lead.ai_maturity_score = ai_score
        if result.get("competitor_gap_brief"):
            lead.competitor_gap_brief = result["competitor_gap_brief"]
        if result.get("icp_result"):
            lead.icp_result = result["icp_result"]
            icp_conf = result["icp_result"].get("confidence")
            if icp_conf is not None:
                lead.icp_confidence = icp_conf
        lead.outbound_attempt_count += 1
        lead.current_state = "contacted"
        lead.updated_at = datetime.now(timezone.utc)

        event = Event(
            lead_id=lead_id,
            event_type="outreach_started",
            payload={
                "channel": body.channel,
                "segment": result.get("segment"),
                "destination": result.get("destination"),
            },
        )
        db.add(event)

        reply_text = result.get("reply_text") or ""
        if reply_text:
            msg = Message(
                lead_id=lead_id,
                direction="outbound",
                channel=body.channel,
                body=reply_text,
                is_draft=True,
            )
            db.add(msg)

        trace_id = result.get("trace_id") or str(uuid.uuid4())
        trace = Trace(
            id=trace_id,
            lead_id=lead_id,
            segment=result.get("segment"),
            destination=result.get("destination"),
            policy_decision=result.get("policy_decision"),
        )
        db.add(trace)

    await db.commit()
    return {"lead_id": lead_id, "status": "outreach_started", "agent_result": result or {}}

@router.get("/{lead_id}")
async def get_lead(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    lead = await db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="lead not found")
    return lead_detail(lead)

@router.get("/{lead_id}/briefs")
async def get_lead_briefs(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    lead = await db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="lead not found")
    return {
        "hiring_signal_brief": lead.hiring_signal_brief,
        "competitor_gap_brief": lead.competitor_gap_brief,
        "icp_result": lead.icp_result,
        "bench_match": {
            "bench_mismatch": lead.bench_mismatch,
            "ai_maturity_score": lead.ai_maturity_score,
        },
    }

@router.get("/{lead_id}/messages")
async def get_lead_messages(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    result = await db.execute(
        select(Message).where(Message.lead_id == lead_id).order_by(Message.sent_at)
    )
    return [message_to_dict(m) for m in result.scalars().all()]

@router.get("/{lead_id}/traces")
async def get_lead_traces(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    result = await db.execute(
        select(Trace).where(Trace.lead_id == lead_id).order_by(Trace.created_at)
    )
    return [trace_to_dict(t) for t in result.scalars().all()]
