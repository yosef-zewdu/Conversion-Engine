import json
import uuid
import logging
from typing import Any
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Campaign, Event, Lead, Message, Trace
from db.session import get_db, AsyncSessionLocal
from orchestrators.campaign_orchestrator import CampaignOrchestrator
from webhooks.schemas import CampaignRunRequest
from webhooks.utils import campaign_to_dict, lead_summary, lead_to_prospect_dict
from webhooks.agent_runner import run_agent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/campaigns", tags=["campaigns"])

async def background_campaign_task(run_id: str, config: dict):
    """Background worker to run the campaign and persist leads to DB incrementally."""
    logger.info("Campaign task started: run_id=%s auto_outreach=%s", run_id, config.get("auto_outreach"))
    try:
        async def on_lead_callback(account_data: dict):
            """Persist a single lead to DB, then trigger outreach if autonomous mode."""
            async with AsyncSessionLocal() as db:
                contact = account_data.get("synthetic_contact") or {}
                icp = account_data.get("icp_result") or {}
                brief = account_data.get("hiring_signal_brief") or {}

                lead_id = str(uuid.uuid4())
                lead = Lead(
                    id=lead_id,
                    campaign_id=run_id,
                    company_name=account_data.get("company_name", ""),
                    company_id=account_data.get("crunchbase_id", ""),
                    contact_name=contact.get("contact_name", "Engineering Leader"),
                    email=contact.get("email", ""),
                    phone=contact.get("phone"),
                    timezone=contact.get("timezone", "UTC"),
                    preferred_channel=contact.get("preferred_channel", "email"),
                    current_state="cold",
                    segment=icp.get("segment"),
                    icp_confidence=icp.get("confidence"),
                    ai_maturity_score=(
                        brief.get("ai_maturity", {}).get("score")
                        or brief.get("ai_maturity_score")
                    ),
                    bench_mismatch=(
                        brief.get("bench_match", {}).get("bench_mismatch")
                        or brief.get("bench_mismatch")
                    ),
                    source_refs=account_data.get("source_refs"),
                    hiring_signal_brief=account_data.get("hiring_signal_brief"),
                    competitor_gap_brief=account_data.get("competitor_gap_brief"),
                    icp_result=icp,
                )
                db.add(lead)

                campaign = await db.get(Campaign, run_id)
                if campaign:
                    campaign.qualified_count = (campaign.qualified_count or 0) + 1

                await db.commit()

            # Auto-outreach: run agent and persist results, same as POST /leads/{id}/start-outreach
            logger.info("on_lead_callback: lead_id=%s auto_outreach=%s", lead_id, config.get("auto_outreach"))
            if config.get("auto_outreach"):
                try:
                    async with AsyncSessionLocal() as db:
                        lead = await db.get(Lead, lead_id)
                        if not lead:
                            return
                        prospect = lead_to_prospect_dict(lead)
                        prospect["custom_sink_email"] = config.get("custom_sink_email", "")
                    prospect["model"] = config.get("model", "qwen/qwen3-235b-a22b")

                    result = await run_agent(prospect, "", channel="email")

                    if result:
                        async with AsyncSessionLocal() as db:
                            lead = await db.get(Lead, lead_id)
                            if not lead:
                                return
                            if result.get("segment"):
                                lead.segment = result["segment"]
                            if result.get("hs_contact_id"):
                                lead.hs_contact_id = result["hs_contact_id"]
                            if result.get("bench_mismatch") is not None:
                                lead.bench_mismatch = result["bench_mismatch"]
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

                            db.add(Event(
                                lead_id=lead_id,
                                event_type="outreach_started",
                                payload={
                                    "channel": "email",
                                    "segment": result.get("segment"),
                                    "destination": result.get("destination"),
                                    "auto": True,
                                },
                            ))

                            reply_text = result.get("reply_text") or ""
                            if reply_text:
                                db.add(Message(
                                    lead_id=lead_id,
                                    direction="outbound",
                                    channel="email",
                                    body=reply_text,
                                    is_draft=True,
                                ))

                            trace_id = result.get("trace_id") or str(uuid.uuid4())
                            db.add(Trace(
                                id=trace_id,
                                lead_id=lead_id,
                                segment=result.get("segment"),
                                destination=result.get("destination"),
                                policy_decision=result.get("policy_decision"),
                            ))

                            await db.commit()
                            logger.info("Auto-outreach complete for lead %s (%s)", lead_id, lead.company_name)
                except Exception as exc:
                    logger.error("Auto-outreach failed for lead %s: %s", lead_id, exc, exc_info=True)

        orchestrator = CampaignOrchestrator()
        result = await orchestrator.run(config, run_id=run_id, on_lead=on_lead_callback)

        async with AsyncSessionLocal() as db:
            campaign = await db.get(Campaign, run_id)
            if campaign:
                campaign.status = result.get("status", "complete")
                campaign.candidate_count = result.get("candidate_count", 0)
                campaign.finished_at = datetime.now(timezone.utc)
                campaign.error = result.get("error")
                await db.commit()
            
        logger.info("Background task complete for campaign %s", run_id)
    except Exception as e:
        logger.error("Background campaign task failed: %s", e, exc_info=True)
        async with AsyncSessionLocal() as db:
            campaign = await db.get(Campaign, run_id)
            if campaign:
                campaign.status = "failed"
                campaign.error = str(e)
                await db.commit()

@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
async def run_campaign(
    body: CampaignRunRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Kick off a campaign run in the background."""
    hex_str: str = uuid.uuid4().hex
    run_id = f"run_{hex_str[:8]}"
    config = {
        "campaign_id": body.campaign_id,
        "target_segments": body.target_segments,
        "limit": body.limit,
        "mode": body.mode,
        "custom_sink_email": body.custom_sink_email,
        "outreach": {"first_channel": body.first_channel},
        "auto_outreach": body.auto_outreach,
        "model": body.model,
    }

    # Create initial "Running" record
    campaign = Campaign(
        id=run_id,
        campaign_id=body.campaign_id,
        status="running",
        config_snapshot=config,
    )
    db.add(campaign)
    await db.commit()

    # Trigger background work
    background_tasks.add_task(background_campaign_task, run_id, config)

    return {
        "campaign_run_id": run_id,
        "status": "running",
        "message": "Campaign started in the background. Refresh soon to see accounts."
    }

@router.get("")
async def list_campaigns(
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return all campaign runs, most recent first."""
    result = await db.execute(
        select(Campaign).order_by(Campaign.started_at.desc())
    )
    return [campaign_to_dict(row) for row in result.scalars().all()]

@router.get("/{campaign_run_id}")
async def get_campaign(
    campaign_run_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await db.get(Campaign, campaign_run_id)
    if not row:
        raise HTTPException(status_code=404, detail="campaign not found")
    return campaign_to_dict(row)

@router.get("/{campaign_run_id}/accounts")
async def get_campaign_accounts(
    campaign_run_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    result = await db.execute(
        select(Lead).where(Lead.campaign_id == campaign_run_id)
    )
    leads = result.scalars().all()
    return [lead_summary(l) for l in leads]

@router.get("/{campaign_run_id}/cost")
async def get_campaign_cost(
    campaign_run_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return total LLM cost for a campaign by summing traces for all its leads."""
    campaign = await db.get(Campaign, campaign_run_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="campaign not found")

    # Sum cost_usd across all traces for leads belonging to this campaign
    result = await db.execute(
        select(func.sum(Trace.cost_usd), func.count(Trace.id))
        .join(Lead, Trace.lead_id == Lead.id)
        .where(Lead.campaign_id == campaign_run_id)
    )
    row = result.one()
    total_cost = float(row[0] or 0.0)
    trace_count = int(row[1] or 0)
    return {
        "campaign_run_id": campaign_run_id,
        "total_cost_usd": round(total_cost, 6),
        "trace_count": trace_count,
    }

@router.delete("/{campaign_run_id}")
async def delete_campaign(
    campaign_run_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Delete a campaign run and all its associated leads/data."""
    campaign = await db.get(Campaign, campaign_run_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    await db.delete(campaign)
    await db.commit()
    return {"message": f"Campaign {campaign_run_id} and all related data deleted successfully"}
