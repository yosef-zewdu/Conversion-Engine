"""
Tool executor — maps LLM tool-call names to HubSpot MCP server calls.

When the LLM returns a tool_call in its response, the agent passes it here.
This module translates the arguments and calls the MCP client, then returns
the result as a string the LLM can read in the next turn.

Owner ID is read from HUBSPOT_OWNER_ID env var (set during task 0.3 setup).
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from agent.mcp_client import get_mcp_client

logger = logging.getLogger(__name__)


async def execute_tool(tool_name: str, arguments: dict[str, Any]) -> str:
    """Execute a HubSpot MCP tool call and return a string result.

    Args:
        tool_name: The tool name from the LLM tool_call.
        arguments: The arguments dict from the LLM tool_call.

    Returns:
        A JSON string or plain text result to feed back to the LLM.
    """
    client = get_mcp_client()
    logger.info("Executing MCP tool: %s args=%s", tool_name, list(arguments.keys()))

    try:
        if tool_name == "hubspot_get_contact":
            return await _get_contact(client, arguments)
        elif tool_name == "hubspot_upsert_contact":
            return await _upsert_contact(client, arguments)
        elif tool_name == "hubspot_create_note":
            return await _create_note(client, arguments)
        elif tool_name == "hubspot_log_activity":
            return await _log_activity(client, arguments)
        elif tool_name == "hubspot_write_brief":
            return await _write_brief(client, arguments)
        elif tool_name == "hubspot_write_booking":
            return await _write_booking(client, arguments)
        elif tool_name == "hubspot_create_deal":
            return await _create_deal(client, arguments)
        elif tool_name == "hubspot_update_deal":
            return await _update_deal(client, arguments)
        elif tool_name == "hubspot_create_engagement":
            return await _create_engagement(client, arguments)
        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

    except Exception as exc:  # noqa: BLE001
        logger.error("MCP tool %s failed: %s", tool_name, exc)
        return json.dumps({"error": str(exc), "tool": tool_name})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _owner_id() -> int:
    """Return the HubSpot owner ID from HUBSPOT_OWNER_ID env var.

    Returns:
        Integer owner ID, or raises if not set.
    """
    val = os.environ.get("HUBSPOT_OWNER_ID", "").strip()
    if not val:
        raise RuntimeError(
            "HUBSPOT_OWNER_ID is not set. "
            "Run: curl -s 'https://api.hubapi.com/crm/v3/owners?limit=1' "
            "-H 'Authorization: Bearer $HUBSPOT_ACCESS_TOKEN' and add the id to .env"
        )
    return int(val)


def _iso_to_epoch_ms(iso_str: str) -> int:
    """Convert ISO 8601 string to epoch milliseconds.

    Args:
        iso_str: ISO 8601 datetime string.

    Returns:
        Unix epoch time in milliseconds.
    """
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return int(dt.astimezone(timezone.utc).timestamp() * 1000)
    except ValueError:
        return int(time.time() * 1000)


def _dump(result: Any) -> str:
    """Serialise MCP result to string.

    Args:
        result: MCP tool result (dict or str).

    Returns:
        JSON string.
    """
    return json.dumps(result) if not isinstance(result, str) else result


# ---------------------------------------------------------------------------
# Individual tool handlers
# ---------------------------------------------------------------------------


async def _get_contact(client: Any, args: dict) -> str:
    """Look up a HubSpot contact by email."""
    result = await client.call("hubspot-search-objects", {
        "objectType": "contacts",
        "query": args["email"],
        "properties": ["email", "firstname", "lastname", "phone"],
    })
    return _dump(result)


async def _upsert_contact(client: Any, args: dict) -> str:
    """Create a HubSpot contact with standard and enrichment properties.

    Falls back to searching for the existing contact on 409 conflict.
    """
    _STANDARD = {
        "email", "firstname", "lastname", "phone", "company",
        "website", "jobtitle", "city", "country",
        # Custom enrichment properties (pre-created via setup_hubspot_properties.py)
        "icp_segment", "last_enriched_at", "ai_maturity_score", "bench_mismatch",
    }
    props = {k: str(v) for k, v in args.items() if k in _STANDARD and v is not None}
    try:
        result = await client.call("hubspot-batch-create-objects", {
            "objectType": "contacts",
            "inputs": [{"properties": props}],
        })
        return _dump(result)
    except Exception as exc:
        # 409 conflict = contact already exists — search for it instead
        if "409" in str(exc) or "already" in str(exc).lower() or "Expecting value" in str(exc):
            email = args.get("email", "")
            if email:
                search_result = await client.call("hubspot-search-objects", {
                    "objectType": "contacts",
                    "query": email,
                    "properties": ["email", "firstname", "lastname"],
                })
                return _dump(search_result)
        raise


async def _create_note(client: Any, args: dict) -> str:
    """Attach a note to a HubSpot contact."""
    ts = args.get("timestamp", datetime.now(timezone.utc).isoformat())
    result = await client.call("hubspot-create-engagement", {
        "type": "NOTE",
        "ownerId": _owner_id(),
        "associations": {"contactIds": [int(args["contact_id"])]},
        "metadata": {"body": args["body"]},
        "timestamp": _iso_to_epoch_ms(ts),
    })
    return _dump(result)


async def _log_activity(client: Any, args: dict) -> str:
    """Write an outbound/inbound activity record."""
    channel = args.get("channel", "email")
    direction = args.get("direction", "outbound")
    body = f"[{direction.upper()} / {channel.upper()}]\n{args.get('content', '')}"
    ts = args.get("timestamp", datetime.now(timezone.utc).isoformat())
    result = await client.call("hubspot-create-engagement", {
        "type": "NOTE",
        "ownerId": _owner_id(),
        "associations": {"contactIds": [int(args["contact_id"])]},
        "metadata": {"body": body},
        "timestamp": _iso_to_epoch_ms(ts),
    })
    return _dump(result)


async def _write_brief(client: Any, args: dict) -> str:
    """Attach a brief JSON as a note to a HubSpot contact."""
    enriched_at = args.get("enriched_at", datetime.now(timezone.utc).isoformat())
    body = f"[{args.get('brief_type', 'Brief')}]\nEnriched at: {enriched_at}\n\n{args.get('brief_json', '{}')}"
    result = await client.call("hubspot-create-engagement", {
        "type": "NOTE",
        "ownerId": _owner_id(),
        "associations": {"contactIds": [int(args["contact_id"])]},
        "metadata": {"body": body},
        "timestamp": _iso_to_epoch_ms(enriched_at),
    })
    return _dump(result)


async def _write_booking(client: Any, args: dict) -> str:
    """Write a Cal.com booking record to HubSpot as a NOTE."""
    start_utc = args.get("start_utc", datetime.now(timezone.utc).isoformat())
    body = (
        f"[BOOKING CONFIRMED]\n"
        f"Cal.com Event ID: {args['cal_event_id']}\n"
        f"Segment: {args.get('icp_segment', '')}\n"
        f"Brief ref: {args.get('brief_ref', '')}\n"
        f"Start (UTC): {start_utc}"
    )
    result = await client.call("hubspot-create-engagement", {
        "type": "NOTE",
        "ownerId": _owner_id(),
        "associations": {"contactIds": [int(args["contact_id"])]},
        "metadata": {"body": body},
        "timestamp": _iso_to_epoch_ms(start_utc),
    })
    return _dump(result)


async def _create_deal(client: Any, args: dict) -> str:
    """Create a new HubSpot deal associated with a contact."""
    props = {
        "dealname": args["dealname"],
        "pipeline": args.get("pipeline", "default"),
        "dealstage": args.get("dealstage", "appointmentscheduled"),
    }
    if args.get("amount"):
        props["amount"] = str(args["amount"])
    result = await client.call("hubspot-batch-create-objects", {
        "objectType": "deals",
        "inputs": [{
            "properties": props,
            "associations": [{
                "to": {"id": args["contact_id"]},
                "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 3}],
            }],
        }],
    })
    return _dump(result)


async def _update_deal(client: Any, args: dict) -> str:
    """Update an existing HubSpot deal."""
    props: dict[str, str] = {}
    if args.get("dealstage"):
        props["dealstage"] = args["dealstage"]
    if args.get("amount"):
        props["amount"] = str(args["amount"])
    if args.get("notes"):
        props["description"] = args["notes"]
    result = await client.call("hubspot-batch-update-objects", {
        "objectType": "deals",
        "inputs": [{"id": args["deal_id"], "properties": props}],
    })
    return _dump(result)


async def _create_engagement(client: Any, args: dict) -> str:
    """Create a general HubSpot engagement (NOTE or TASK)."""
    eng_type = args.get("engagement_type", "NOTE")
    if eng_type not in ("NOTE", "TASK"):
        eng_type = "NOTE"
    ts = args.get("timestamp", datetime.now(timezone.utc).isoformat())
    result = await client.call("hubspot-create-engagement", {
        "type": eng_type,
        "ownerId": _owner_id(),
        "associations": {"contactIds": [int(args["contact_id"])]},
        "metadata": {"body": args.get("body", "")},
        "timestamp": _iso_to_epoch_ms(ts),
    })
    return _dump(result)
