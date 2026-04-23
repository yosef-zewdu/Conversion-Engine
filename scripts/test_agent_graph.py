"""
Smoke test for the LangGraph conversation agent.

Sends one synthetic inbound message through the full graph:
  enrich → classify → check_command → llm → tools → kill_switch → send_email → persist

Run with:
    uv run python scripts/test_agent_graph.py
"""
from __future__ import annotations

import asyncio
import json
from dotenv import load_dotenv

load_dotenv()

from agent.graph import handle_inbound


async def main() -> None:
    """Run the agent graph smoke test."""
    print("=== LangGraph Agent Smoke Test ===\n")

    # Synthetic prospect (maps to a Crunchbase ODM record)
    prospect = {
        "prospect_id": "test-prospect-001",
        "company_id": "test-acme-corp-001",
        "contact_name": "Alex Chen",
        "email": "johndoe@gmail.com",   # staff sink email
        "phone": None,
        "timezone": "America/New_York",
        "preferred_channel": "email",
        "current_state": "cold",
        "outbound_attempt_count": 0,
        "segment": None,
        "hiring_signal_brief_ref": None,
    }

    inbound_text = (
        "Hi, I saw your outreach about AI engineering teams. "
        "We're a Series B company and we're scaling fast. "
        "Can you tell me more about what Tenacious offers?"
    )

    print(f"Prospect: {prospect['contact_name']} <{prospect['email']}>")
    print(f"Inbound: {inbound_text}\n")
    print("Running graph...\n")

    final_state = await handle_inbound(
        prospect_dict=prospect,
        inbound_text=inbound_text,
        channel="email",
    )

    print(f"Segment:       {final_state.get('segment')}")
    print(f"Bench mismatch:{final_state.get('bench_mismatch')}")
    print(f"Destination:   {final_state.get('destination')}")
    print(f"HubSpot ID:    {final_state.get('hs_contact_id')}")
    print(f"Error:         {final_state.get('error')}")
    print(f"\nReply:\n{final_state.get('reply_text')}")

    # Show message count
    msgs = final_state.get("messages", [])
    print(f"\nMessages in state: {len(msgs)}")
    for m in msgs:
        role = type(m).__name__
        content_preview = str(m.content)[:80] if m.content else "(tool call)"
        print(f"  [{role}] {content_preview}")


if __name__ == "__main__":
    import asyncio
    from agent.mcp_client import get_mcp_client

    async def run() -> None:
        await main()
        try:
            await get_mcp_client().close()
        except Exception:
            pass

    asyncio.run(run())
