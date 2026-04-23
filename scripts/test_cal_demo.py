"""
Query Cal.com slots and create a booking for the demo prospect.

Usage:
    uv run python -m scripts.test_cal_demo

Requires: CAL_API_KEY, CAL_BASE_URL, CAL_EVENT_TYPE_ID in .env
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    from booking_agent.agent import CalConfig, create_booking, get_available_slots
    from config.models import Prospect, ProspectState, Segment

    cal_config = CalConfig(
        cal_base_url=os.environ.get("CAL_BASE_URL", "https://api.cal.com"),
        cal_api_key=os.environ.get("CAL_API_KEY", ""),
        event_type_id=int(os.environ.get("CAL_EVENT_TYPE_ID", "5465977")),
    )

    prospect = Prospect(
        prospect_id="demo-prospect-001", company_id="demo-co-001",
        contact_name="Demo Prospect",
        email=os.environ.get("STAFF_SINK_EMAIL").strip(),
        phone=os.environ.get("STAFF_SINK_PHONE", "").strip() or None,
        timezone="Africa/Nairobi",
        preferred_channel="email", current_state=ProspectState.WARM,
        outbound_attempt_count=1, segment=Segment.S1,
        hiring_signal_brief_ref=datetime.now(timezone.utc).isoformat(),
    )

    # 1. Query slots
    print(f"Querying slots from {cal_config.cal_base_url} event_type={cal_config.event_type_id}...")
    try:
        slots = await get_available_slots(cal_config, "EAT")
        print(f"  {len(slots)} slot(s) returned from Cal.com:")
        for i, s in enumerate(slots[:3], 1):
            print(f"  {i}. {s.local_display}  (UTC: {s.start_utc})")
    except Exception as e:
        print(f"  Slot query failed: {e}")
        return

    if not slots:
        print("  No slots available — add availability in your Cal.com calendar.")
        return

    # 2. Create booking with first slot
    print(f"\nCreating booking for: {slots[0].local_display}")
    try:
        booking = await create_booking(
            slot=slots[0],
            prospect=prospect,
            brief_ref="demo-co-001:2026-04-23",
            cal_config=cal_config,
        )
        print(f"  Booking confirmed")
        print(f"  cal_event_id : {booking.cal_event_id}")
        print(f"  prospect_id  : {booking.prospect_id}")
        print(f"  segment      : {booking.segment.value}")
        print(f"\nView in Cal.com dashboard: https://cal.com/trp-y/bookings")
    except Exception as e:
        print(f"  Booking failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
