"""
Send the signal-grounded outreach email via Resend.

Usage:
    uv run python -m scripts.test_resend_demo

Requires: RESEND_API_KEY, STAFF_SINK_EMAIL in .env
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    api_key = os.environ.get("RESEND_API_KEY", "")
    to_email = os.environ.get("STAFF_SINK_EMAIL", "").strip()

    from config.models import Prospect, ProspectState, Segment
    from nurture_sequencer.state_machine import ProspectFSM
    from signal_pipeline.models import CompetitorGapBrief, FundingEvent, HiringSignalBrief

    now = datetime.now(timezone.utc).isoformat()
    brief = HiringSignalBrief(
        schema_version="1.0", company_id="demo-co-001", company_name="Demo Corp",
        last_enriched_at=now, ai_maturity_score=2, ai_maturity_confidence="medium",
        funding_event=FundingEvent(round_type="Series A", amount_usd=18_000_000.0,
                                   close_date="2026-01-20", confidence="high"),
        job_post_count=7, job_post_velocity_60d=3.8, job_post_confidence="high",
    )
    gap = CompetitorGapBrief(schema_version="1.0", company_id="demo-co-001",
                              generated_at=now, peer_count=6)
    prospect = Prospect(
        prospect_id="demo-001", company_id="demo-co-001", contact_name="Demo Prospect",
        email=to_email, phone=None, timezone="America/New_York",
        preferred_channel="email", current_state=ProspectState.COLD,
        outbound_attempt_count=0, segment=Segment.S1, hiring_signal_brief_ref=now,
    )
    msg = ProspectFSM(prospect).start_sequence(brief, gap)

    html_body = msg.content.replace("\n", "<br>")
    payload = {
        "from": "Conversion Engine <onboarding@resend.dev>",
        "to": [to_email],
        "subject": msg.subject or "Signal-grounded outreach — Demo Corp",
        "html": f"<p>{html_body}</p><hr><p><small>draft=true | routed via staff sink</small></p>",
    }

    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload, timeout=15,
    )

    if resp.status_code == 200:
        data = resp.json()
        print(f"Email sent to {to_email}")
        print(f"  subject    : {payload['subject']}")
        print(f"  message_id : {data.get('id')}")
        print(f"  body preview: {msg.content[:120]}...")
    else:
        print(f"ERROR {resp.status_code}: {resp.text}")


if __name__ == "__main__":
    main()
