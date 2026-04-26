import json
import uuid
import logging
from typing import Any
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Campaign, Lead
from db.session import get_db, AsyncSessionLocal
from orchestrators.campaign_orchestrator import CampaignOrchestrator
from webhooks.schemas import CampaignRunRequest
from webhooks.utils import campaign_to_dict, lead_summary

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/campaigns", tags=["campaigns"])

async def background_campaign_task(run_id: str, config: dict):
    """Background worker to run the campaign and persist leads to DB incrementally."""
    try:
        async def on_lead_callback(account_data: dict):
            """Persist a single lead to DB as it is processed."""
            async with AsyncSessionLocal() as db:
                contact = account_data.get("synthetic_contact") or {}
                icp = account_data.get("icp_result") or {}
                brief = account_data.get("hiring_signal_brief") or {}
                
                lead = Lead(
                    id=str(uuid.uuid4()),
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
                    ai_maturity_score=brief.get("ai_maturity_score"),
                    bench_mismatch=brief.get("bench_mismatch"),
                    source_refs=account_data.get("source_refs"),
                    hiring_signal_brief=account_data.get("hiring_signal_brief"),
                    competitor_gap_brief=account_data.get("competitor_gap_brief"),
                    icp_result=icp,
                )
                db.add(lead)
                
                # Update Campaign qualified count
                campaign = await db.get(Campaign, run_id)
                if campaign:
                    campaign.qualified_count = (campaign.qualified_count or 0) + 1
                
                await db.commit()

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
        "outreach": {"first_channel": body.first_channel},
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
