"""
End-to-end demo: run a synthetic prospect through the full pipeline
and write real records to HubSpot.

Usage:
    uv run python -m scripts.run_demo --email you@example.com --phone +1234567890

The prospect's email/phone are set to yours so you can see the contact
appear in HubSpot with all custom fields populated.

Steps performed:
  1. Build a synthetic HiringSignalBrief (Series A, AI maturity 2)
  2. Run ICP classification → Segment 1
  3. Compose outbound email (routed to STAFF_SINK — no real send)
  4. Upsert HubSpot contact with all custom fields
  5. Write HiringSignalBrief + CompetitorGapBrief as HubSpot notes
  6. Log outbound activity to HubSpot
  7. Print a summary with HubSpot contact URL

Requires: HUBSPOT_ACCESS_TOKEN in .env
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Conversion Engine end-to-end demo")
    p.add_argument("--email", default="demo@tenacious-sandbox.dev",
                   help="Email to use for the synthetic prospect contact")
    p.add_argument("--phone", default="", help="Phone number (optional)")
    p.add_argument("--name", default="Demo Prospect", help="Contact name")
    return p.parse_args()


async def run(email: str, phone: str, name: str) -> None:
    from config.models import Prospect, ProspectState, Segment
    from crm_writer.writer import CRMWriter
    from icp_classifier.classifier import ClassifierConfig, classify
    from nurture_sequencer.state_machine import ProspectFSM
    from signal_pipeline.models import (
        CompetitorGapBrief, FundingEvent, HiringSignalBrief, TechStack,
    )

    now = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # 1. Build synthetic brief — Series A, AI maturity 2
    # ------------------------------------------------------------------
    brief = HiringSignalBrief(
        schema_version="1.0",
        company_id="demo-co-001",
        company_name="Demo Corp",
        last_enriched_at=now,
        crunchbase_id="cb-demo-001",
        bench_summary_version=now,
        bench_mismatch=False,
        tech_stack=TechStack(
            languages=["python", "go"],
            ml_tools=["pytorch"],
            data_tools=["dbt", "airflow"],
            confidence="medium",
        ),
        funding_event=FundingEvent(
            round_type="Series A",
            amount_usd=18_000_000.0,
            close_date="2026-01-20",
            confidence="high",
        ),
        ai_maturity_score=2,
        ai_maturity_confidence="medium",
        job_post_count=7,
        job_post_velocity_60d=3.8,
        job_post_confidence="high",
    )

    gap_brief = CompetitorGapBrief(
        schema_version="1.0",
        company_id="demo-co-001",
        generated_at=now,
        prospect_ai_maturity_score=2,
        sector_percentile=72.0,
        peer_count=6,
        peers=[],
        gaps=[],
    )

    # ------------------------------------------------------------------
    # 2. ICP classification
    # ------------------------------------------------------------------
    segment_result = classify(brief, ClassifierConfig())
    print(f"\n[ICP]  segment={segment_result.segment.value}  "
          f"confidence={segment_result.confidence:.2f}  "
          f"signals={segment_result.signals_used}")

    # ------------------------------------------------------------------
    # 3. Build prospect with your contact details
    # ------------------------------------------------------------------
    prospect = Prospect(
        prospect_id="demo-prospect-001",
        company_id="demo-co-001",
        contact_name=name,
        email=email,
        phone=phone or None,
        timezone="America/New_York",
        preferred_channel="email",
        current_state=ProspectState.COLD,
        outbound_attempt_count=0,
        segment=segment_result.segment,
        hiring_signal_brief_ref=now,
    )

    # ------------------------------------------------------------------
    # 4. Compose outbound message (kill switch = staff sink by default)
    # ------------------------------------------------------------------
    fsm = ProspectFSM(prospect)
    outbound = fsm.start_sequence(brief, gap_brief)
    print(f"\n[MSG]  channel={outbound.channel}  draft={outbound.draft}")
    print(f"       subject: {outbound.subject or '(none)'}")
    print(f"       preview: {outbound.content[:120]}...")

    # ------------------------------------------------------------------
    # 5. Write to HubSpot
    # ------------------------------------------------------------------
    token = os.environ.get("HUBSPOT_ACCESS_TOKEN", "")
    if not token:
        print("\nERROR: HUBSPOT_ACCESS_TOKEN not set — skipping CRM writes.")
        return

    crm = CRMWriter(access_token=token)

    print("\n[CRM]  Upserting contact...")
    contact_id = await crm.upsert_contact(prospect)
    print(f"       contact_id={contact_id}")

    print("[CRM]  Writing HiringSignalBrief note...")
    brief_eng_id = await crm.write_brief(brief, hs_contact_id=contact_id)
    print(f"       engagement_id={brief_eng_id}")

    print("[CRM]  Writing CompetitorGapBrief note...")
    gap_eng_id = await crm.write_brief(gap_brief, hs_contact_id=contact_id)
    print(f"       engagement_id={gap_eng_id}")

    print("[CRM]  Logging outbound activity...")
    act_eng_id = await crm.log_activity({
        "type": f"outbound_{outbound.channel}",
        "prospect_id": prospect.prospect_id,
        "channel": outbound.channel,
        "timestamp": outbound.sent_at,
        "content": f"Subject: {outbound.subject or '(no subject)'}\n\n{outbound.content}",
        "direction": "outbound",
        "draft": True,
    }, hs_contact_id=contact_id)
    print(f"       engagement_id={act_eng_id}")

    # ------------------------------------------------------------------
    # 6. Summary
    # ------------------------------------------------------------------
    print(f"""
{'='*60}
  Demo complete.

  HubSpot contact ID : {contact_id}
  View in HubSpot    : https://app.hubspot.com/contacts/search?query={email}

  Fields written to the contact record:
    email            : {email}
    prospect_id      : {prospect.prospect_id}
    company_id       : {prospect.company_id}
    icp_segment      : {segment_result.segment.value}
    prospect_state   : {prospect.current_state.value}
    last_enriched_at : {now}

  Notes attached     : HiringSignalBrief + CompetitorGapBrief
  Activity logged    : outbound email (draft=True, routed to staff sink)
{'='*60}
""")


def main() -> None:
    args = _parse_args()
    # Fall back to STAFF_SINK_EMAIL / STAFF_SINK_PHONE from .env
    email = args.email
    phone = args.phone
    if email == "demo@tenacious-sandbox.dev":
        email = os.environ.get("STAFF_SINK_EMAIL", email).strip()
    if not phone:
        phone = os.environ.get("STAFF_SINK_PHONE", "").strip()
    asyncio.run(run(email, phone, args.name))


if __name__ == "__main__":
    main()
