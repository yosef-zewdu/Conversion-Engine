"""
HubSpot MCP tool definitions for the ConversationAgent.

These are the 9 tool schemas passed to the LLM (OpenRouter) so it can
decide when to call HubSpot operations during the conversation loop.
The actual execution is handled by mcp_client.py which talks to the
running @hubspot/mcp-server process.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Tool schemas (OpenAI-compatible function-calling format)
# ---------------------------------------------------------------------------

HUBSPOT_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "hubspot_get_contact",
            "description": (
                "Look up an existing HubSpot contact by email address. "
                "Use this first to check if the prospect already exists before upserting."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {
                        "type": "string",
                        "description": "The prospect's email address.",
                    }
                },
                "required": ["email"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hubspot_upsert_contact",
            "description": (
                "Create or update a HubSpot contact record for a prospect. "
                "Include enrichment fields: crunchbase_id, last_enriched_at, "
                "icp_segment, ai_maturity_score, bench_mismatch."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {"type": "string", "description": "Prospect email (used as unique key)."},
                    "firstname": {"type": "string"},
                    "lastname": {"type": "string"},
                    "phone": {"type": "string"},
                    "company": {"type": "string"},
                    "icp_segment": {"type": "string", "description": "segment_1 | segment_2 | segment_3 | segment_4 | unqualified"},
                    "ai_maturity_score": {"type": "integer", "description": "0–3"},
                    "last_enriched_at": {"type": "string", "description": "ISO 8601 datetime"},
                    "bench_mismatch": {"type": "boolean"},
                },
                "required": ["email"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hubspot_create_note",
            "description": (
                "Attach a note to a HubSpot contact. Use for logging conversation "
                "turns, enrichment briefs, or any freeform context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contact_id": {"type": "string", "description": "HubSpot contact ID."},
                    "body": {"type": "string", "description": "Note content (plain text or HTML)."},
                    "timestamp": {"type": "string", "description": "ISO 8601 datetime of the event."},
                },
                "required": ["contact_id", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hubspot_log_activity",
            "description": (
                "Write an outbound send or inbound reply activity record to HubSpot. "
                "Call this for every message sent or received."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contact_id": {"type": "string"},
                    "channel": {"type": "string", "description": "email | sms | voice"},
                    "direction": {"type": "string", "description": "outbound | inbound"},
                    "content": {"type": "string", "description": "Message body or summary."},
                    "timestamp": {"type": "string", "description": "ISO 8601 datetime."},
                },
                "required": ["contact_id", "channel", "direction", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hubspot_write_brief",
            "description": (
                "Attach a HiringSignalBrief or CompetitorGapBrief JSON to a HubSpot "
                "contact as a note. Call this after enrichment completes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contact_id": {"type": "string"},
                    "brief_type": {"type": "string", "description": "HiringSignalBrief | CompetitorGapBrief"},
                    "brief_json": {"type": "string", "description": "JSON-serialised brief."},
                    "enriched_at": {"type": "string", "description": "ISO 8601 datetime."},
                },
                "required": ["contact_id", "brief_type", "brief_json"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hubspot_write_booking",
            "description": (
                "Write a confirmed Cal.com booking record to HubSpot. "
                "Call this when a discovery call is booked."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contact_id": {"type": "string"},
                    "cal_event_id": {"type": "string"},
                    "icp_segment": {"type": "string"},
                    "brief_ref": {"type": "string", "description": "company_id + last_enriched_at"},
                    "start_utc": {"type": "string", "description": "ISO 8601 UTC start time."},
                },
                "required": ["contact_id", "cal_event_id", "icp_segment"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hubspot_create_deal",
            "description": (
                "Create a new deal in HubSpot when a prospect qualifies for a segment. "
                "Set dealname, pipeline, dealstage, and amount."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contact_id": {"type": "string"},
                    "dealname": {"type": "string"},
                    "pipeline": {"type": "string", "description": "HubSpot pipeline ID or name."},
                    "dealstage": {"type": "string", "description": "HubSpot deal stage ID or name."},
                    "amount": {"type": "number", "description": "Estimated deal value in USD."},
                    "icp_segment": {"type": "string"},
                },
                "required": ["contact_id", "dealname", "pipeline", "dealstage"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hubspot_update_deal",
            "description": "Update an existing HubSpot deal's stage or properties.",
            "parameters": {
                "type": "object",
                "properties": {
                    "deal_id": {"type": "string"},
                    "dealstage": {"type": "string"},
                    "amount": {"type": "number"},
                    "notes": {"type": "string"},
                },
                "required": ["deal_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hubspot_create_engagement",
            "description": (
                "Create a general HubSpot engagement (CALL, MEETING, EMAIL, NOTE). "
                "Use for events that don't fit the other tool categories."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contact_id": {"type": "string"},
                    "engagement_type": {"type": "string", "description": "NOTE | CALL | MEETING | EMAIL"},
                    "body": {"type": "string"},
                    "timestamp": {"type": "string", "description": "ISO 8601 datetime."},
                    "metadata": {"type": "object", "description": "Extra key-value pairs."},
                },
                "required": ["contact_id", "engagement_type", "body"],
            },
        },
    },
]
