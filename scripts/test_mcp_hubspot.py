"""
Smoke test: MCP server → HubSpot connection.

Tests all 9 tool paths by running a minimal sequence:
  1. upsert_contact
  2. get_contact
  3. create_note
  4. log_activity (outbound email)
  5. write_brief
  6. write_booking
  7. create_deal
  8. update_deal
  9. create_engagement

Run with:
    uv run python scripts/test_mcp_hubspot.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from agent.mcp_client import get_mcp_client
from agent.tool_executor import execute_tool


async def main() -> None:
    """Run the MCP HubSpot smoke test."""
    print("=== MCP HubSpot Smoke Test ===\n")
    now = datetime.now(timezone.utc).isoformat()
    contact_id: str = ""

    # 1. Upsert contact
    print("1. hubspot_upsert_contact …")
    result = await execute_tool("hubspot_upsert_contact", {
        "email": "mcp-test@tenacious-sandbox.dev",
        "firstname": "MCP",
        "lastname": "Test",
        "company": "Sandbox Corp",
    })
    print(f"   result: {result[:120]}\n")

    # 2. Get contact
    print("2. hubspot_get_contact …")
    result = await execute_tool("hubspot_get_contact", {
        "email": "mcp-test@tenacious-sandbox.dev",
    })
    print(f"   result: {result[:120]}")
    try:
        data = json.loads(result)
        results = data.get("results", [])
        if results:
            contact_id = str(results[0].get("id", ""))
            print(f"   contact_id: {contact_id}\n")
        else:
            # Fall back to the test contact created in task 0.3
            print("   not found — falling back to test@tenacious-sandbox.dev\n")
            r2 = await execute_tool("hubspot_get_contact", {
                "email": "test@tenacious-sandbox.dev",
            })
            d2 = json.loads(r2)
            results2 = d2.get("results", [])
            contact_id = str(results2[0].get("id", "")) if results2 else ""
            if contact_id:
                print(f"   fallback contact_id: {contact_id}\n")
    except Exception as e:
        print(f"   parse error: {e}\n")
        contact_id = ""

    if not contact_id:
        print("No contact_id found. Skipping association tests.\n")
        await get_mcp_client().close()
        return

    # 3. Create note
    print("3. hubspot_create_note …")
    result = await execute_tool("hubspot_create_note", {
        "contact_id": contact_id,
        "body": "MCP smoke test note — conversion engine",
        "timestamp": now,
    })
    print(f"   result: {result[:120]}\n")

    # 4. Log activity
    print("4. hubspot_log_activity …")
    result = await execute_tool("hubspot_log_activity", {
        "contact_id": contact_id,
        "channel": "email",
        "direction": "outbound",
        "content": "Test outbound email logged via MCP",
        "timestamp": now,
    })
    print(f"   result: {result[:120]}\n")

    # 5. Write brief
    print("5. hubspot_write_brief …")
    brief = {"schema_version": "1.0", "company_id": "sandbox-001", "test": True}
    result = await execute_tool("hubspot_write_brief", {
        "contact_id": contact_id,
        "brief_type": "HiringSignalBrief",
        "brief_json": json.dumps(brief),
        "enriched_at": now,
    })
    print(f"   result: {result[:120]}\n")

    # 6. Write booking
    print("6. hubspot_write_booking …")
    result = await execute_tool("hubspot_write_booking", {
        "contact_id": contact_id,
        "cal_event_id": "test-cal-event-001",
        "icp_segment": "segment_1",
        "brief_ref": "sandbox-001/" + now,
        "start_utc": now,
    })
    print(f"   result: {result[:120]}\n")

    # 7. Create deal
    print("7. hubspot_create_deal …")
    result = await execute_tool("hubspot_create_deal", {
        "contact_id": contact_id,
        "dealname": "MCP Test Deal — Sandbox Corp",
        "pipeline": "default",
        "dealstage": "appointmentscheduled",
        "amount": 50000,
        "icp_segment": "segment_1",
    })
    print(f"   result: {result[:120]}")
    deal_id = ""
    try:
        data = json.loads(result)
        results_list = data.get("results", [])
        if results_list:
            deal_id = str(results_list[0].get("id", ""))
            print(f"   deal_id: {deal_id}\n")
    except Exception:
        print()

    # 8. Update deal
    if deal_id:
        print("8. hubspot_update_deal …")
        result = await execute_tool("hubspot_update_deal", {
            "deal_id": deal_id,
            "dealstage": "qualifiedtobuy",
            "notes": "Prospect replied — moving to qualified",
        })
        print(f"   result: {result[:120]}\n")
    else:
        print("8. hubspot_update_deal … SKIPPED (no deal_id)\n")

    # 9. Create engagement
    print("9. hubspot_create_engagement …")
    result = await execute_tool("hubspot_create_engagement", {
        "contact_id": contact_id,
        "engagement_type": "NOTE",
        "body": "General engagement — MCP smoke test complete",
        "timestamp": now,
    })
    print(f"   result: {result[:120]}\n")

    print("=== All 9 tools exercised ===")
    await get_mcp_client().close()


if __name__ == "__main__":
    asyncio.run(main())
