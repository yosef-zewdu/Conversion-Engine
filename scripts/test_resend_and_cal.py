"""
Demo: Resend email send + Cal.com slot query and booking.

Usage:
    uv run python -m scripts.test_resend_and_cal

What this proves:
  1. Resend sends the signal-grounded outreach email to your inbox
  2. Cal.com returns available slots with timezone conversion
  3. A booking is created in Cal.com with prospect details

Requires: RESEND_API_KEY, CAL_API_KEY, CAL_BASE_URL in .env
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# 1. Resend — send the actual outreach email
# ---------------------------------------------------------------------------

def send_outreach_email() -> None:
    import httpx

    api_key = os.environ.get("RESEND_API_KEY", "")
    # Use account owner email for Resend free tier (can only send to own address)
    to_email = os.environ.get("RESEND_TO", os.environ.get("STAFF_SINK_EMAIL", "")).strip()
    if not to_email:
        to_email = "johndoe@gmail.com"  # Resend account owner

    # Generate the actual outreach email content from the nurture sequencer
    from config.models import Prospect, ProspectState, Segment
    from nurture_sequencer.state_machine import ProspectFSM
    from signal_pipeline.models import (
        CompetitorGapBrief, FundingEvent, HiringSignalBrief, TechStack,
    )

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
        json=payload,
        timeout=15,
    )

    if resp.status_code == 200:
        data = resp.json()
        print(f"  [Resend] email sent")
        print(f"           to         : {to_email}")
        print(f"           subject    : {payload['subject']}")
        print(f"           message_id : {data.get('id')}")
    else:
        print(f"  [Resend] ERROR {resp.status_code}: {resp.text}")


# ---------------------------------------------------------------------------
# 2. Cal.com — query slots and create a booking
# ---------------------------------------------------------------------------

async def demo_cal() -> None:
    from booking_agent.agent import CalConfig, create_booking, get_available_slots
    from config.models import Prospect, ProspectState, Segment, Slot

    cal_config = CalConfig(
        cal_base_url=os.environ.get("CAL_BASE_URL", "https://api.cal.eu"),
        cal_api_key=os.environ.get("CAL_API_KEY", ""),
        event_type_id=int(os.environ.get("CAL_EVENT_TYPE_ID", "268206")),
    )

    if not cal_config.cal_api_key:
        print("  [SKIP] CAL_API_KEY not set")
        return

    # Query slots
    print(f"  [Cal.com] querying available slots...")
    try:
        slots = await get_available_slots(cal_config, "EAT")  # East Africa Time
    except Exception as e:
        print(f"  [Cal.com] slot query failed: {e}")
        # Use synthetic slots for demo if Cal.com is unreachable
        slots = [
            Slot(start_utc="2026-05-01T09:00:00Z", end_utc="2026-05-01T09:30:00Z",
                 local_display="Thu 01 May 2026 12:00 EAT"),
            Slot(start_utc="2026-05-02T10:00:00Z", end_utc="2026-05-02T10:30:00Z",
                 local_display="Fri 02 May 2026 13:00 EAT"),
            Slot(start_utc="2026-05-03T14:00:00Z", end_utc="2026-05-03T14:30:00Z",
                 local_display="Sat 03 May 2026 17:00 EAT"),
        ]
        print(f"  [Cal.com] using synthetic slots for demo")

    print(f"  [Cal.com] {len(slots)} slot(s) available:")
    for i, s in enumerate(slots[:3], 1):
        print(f"           {i}. {s.local_display} (UTC: {s.start_utc})")

    if not slots:
        print("  [Cal.com] no slots available — skipping booking")
        return

    # Create a booking with the first slot
    prospect = Prospect(
        prospect_id="demo-prospect-001", company_id="demo-co-001",
        contact_name="Demo Prospect",
        email=os.environ.get("STAFF_SINK_EMAIL", "demo@tenacious-sandbox.dev").strip(),
        phone=os.environ.get("STAFF_SINK_PHONE", "").strip() or None,
        timezone="Africa/Nairobi",
        preferred_channel="email", current_state=ProspectState.WARM,
        outbound_attempt_count=1, segment=Segment.S1,
        hiring_signal_brief_ref=datetime.now(timezone.utc).isoformat(),
    )

    print(f"\n  [Cal.com] creating booking for slot: {slots[0].local_display}")
    try:
        booking = await create_booking(
            slot=slots[0],
            prospect=prospect,
            brief_ref="demo-co-001:2026-04-23",
            cal_config=cal_config,
        )
        print(f"  [Cal.com] booking confirmed")
        print(f"           cal_event_id : {booking.cal_event_id}")
        print(f"           prospect_id  : {booking.prospect_id}")
        print(f"           segment      : {booking.segment.value}")
        print(f"           brief_ref    : {booking.brief_ref}")
    except Exception as e:
        print(f"  [Cal.com] booking failed: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"\n{'='*55}")
    print("  Resend + Cal.com integration demo")
    print(f"{'='*55}\n")

    print("1. Sending outreach email via Resend...")
    send_outreach_email()

    print("\n2. Cal.com slot query and booking...")
    asyncio.run(demo_cal())

    print(f"\n{'='*55}")
    print("  Done. Check your inbox for the Resend email.")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()
