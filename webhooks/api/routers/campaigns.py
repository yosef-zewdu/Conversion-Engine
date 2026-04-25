import json
import uuid
from typing import Any
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Campaign, Lead
from db.session import get_db
from orchestrators.campaign_orchestrator import CampaignOrchestrator
from webhooks.schemas import CampaignRunRequest
from webhooks.utils import campaign_to_dict, lead_summary

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
async def run_campaign(
    body: CampaignRunRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Kick off a campaign run synchronously and persist the result."""
    config = {
        "campaign_id": body.campaign_id,
        "target_segments": body.target_segments,
        "limit": body.limit,
        "mode": body.mode,
        "outreach": {"first_channel": body.first_channel},
    }

    orchestrator = CampaignOrchestrator()
    result = await orchestrator.run(config)

    campaign = Campaign(
        id=result["campaign_run_id"],
        campaign_id=result["campaign_id"],
        status=result.get("status", "complete"),
        candidate_count=result.get("candidate_count", 0),
        qualified_count=result.get("qualified_count", 0),
        queued_outreach_count=result.get("queued_outreach_count", 0),
        config_snapshot=config,
        error=result.get("error"),
    )
    if result.get("status") != "running":
        campaign.finished_at = datetime.now(timezone.utc)
    db.add(campaign)

    qualified_path = result.get("qualified_accounts_path", "")
    leads_added: list[dict] = []
    if qualified_path:
        p = Path(qualified_path)
        if p.exists():
            for line in p.read_text().splitlines():
                if not line.strip():
                    continue
                account = json.loads(line)
                contact = account.get("synthetic_contact") or {}
                icp = account.get("icp_result") or {}
                brief = account.get("hiring_signal_brief") or {}
                prospect_id = str(uuid.uuid4())

                lead = Lead(
                    id=prospect_id,
                    campaign_id=campaign.id,
                    company_name=account.get("company_name", ""),
                    company_id=account.get("crunchbase_id", ""),
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
                    source_refs=account.get("source_refs"),
                    hiring_signal_brief=account.get("hiring_signal_brief"),
                    competitor_gap_brief=account.get("competitor_gap_brief"),
                    icp_result=icp,
                )
                db.add(lead)
                leads_added.append({"lead_id": prospect_id, "company_name": lead.company_name})

    await db.commit()

    return {
        "campaign_run_id": result["campaign_run_id"],
        "status": result.get("status"),
        "candidate_count": result.get("candidate_count", 0),
        "qualified_count": result.get("qualified_count", 0),
        "leads_registered": len(leads_added),
    }

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
