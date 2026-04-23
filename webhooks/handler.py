"""
Webhook handler — FastAPI app for inbound reply events.

Endpoints:
  POST /webhooks/email    — Resend reply events
  POST /webhooks/sms      — Africa's Talking reply events
  POST /webhooks/cal      — Cal.com booking events
  POST /webhooks/hubspot  — HubSpot timeline events (optional)

Each endpoint parses the inbound payload, resolves the prospect,
and delegates to ConversationAgent (LangGraph) for the full
enrich → classify → LLM → HubSpot → send loop.

Requirements: 1.1–1.5, 8.1–8.7, 10.1–10.5, 11.1
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request, Response, status

from agent.agent import ConversationAgent
from config.models import ProspectState, Segment

logger = logging.getLogger(__name__)

app = FastAPI(title="Conversion Engine Webhook Handler")

# Module-level agent singleton
_agent = ConversationAgent()

# In-memory prospect registry: prospect_id → prospect dict
# In production this would be backed by a persistent store.
_prospect_registry: dict[str, dict[str, Any]] = {}

# Phone-number → prospect_id lookup
_phone_registry: dict[str, str] = {}


@app.on_event("startup")
async def _register_demo_prospect() -> None:
    """Pre-register the staff-sink prospect so SMS webhooks can match by phone."""
    phone = os.environ.get("STAFF_SINK_PHONE", "").strip()
    email = os.environ.get("STAFF_SINK_EMAIL", "demo@tenacious-sandbox.dev").strip()

    prospect = {
        "prospect_id": "demo-prospect-001",
        "company_id": "test-demo-co-001",
        "contact_name": "Demo Prospect",
        "email": email,
        "phone": phone or None,
        "timezone": "America/New_York",
        "preferred_channel": "email",
        "current_state": ProspectState.COLD.value,
        "outbound_attempt_count": 0,
        "segment": None,
        "hiring_signal_brief_ref": None,
    }
    _prospect_registry["demo-prospect-001"] = prospect

    if phone:
        _phone_registry[phone] = "demo-prospect-001"
        _phone_registry[phone.lstrip("+")] = "demo-prospect-001"

    # Wildcard fallback for sandbox testing
    _phone_registry["__any__"] = "demo-prospect-001"
    logger.info("Demo prospect registered: email=%s phone=%s", email, phone)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """Suppress 404 for favicon requests."""
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Resend — email reply events
# ---------------------------------------------------------------------------


_BOUNCE_EVENT_TYPES: frozenset[str] = frozenset({
    "email.bounced",
    "email.delivery_delayed",
    "email.delivery_failed",
    "email.complained",
})

_REPLY_EVENT_TYPES: frozenset[str] = frozenset({
    "email.replied",
    "email.opened",
})


@app.post("/webhooks/email", status_code=status.HTTP_200_OK)
async def handle_email_reply(request: Request) -> dict[str, str]:
    """Receive inbound email reply events from Resend.

    Handles reply events, bounce/delivery-failure events, and rejects
    malformed payloads with a 400 response.

    Expected payload::

        {
          "type": "email.replied",
          "data": {
            "from": "prospect@example.com",
            "text": "...",
            "tags": { "prospect_id": "uuid" }
          }
        }

    Args:
        request: Incoming FastAPI request.

    Returns:
        ``{"received": "ok"}`` on success, ``{"received": "bounce_logged"}``
        for bounce/delivery-failure events.
    """
    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        logger.warning("email webhook: malformed JSON payload")
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="malformed payload: expected JSON")

    event_type: str = payload.get("type", "")
    logger.info("email webhook: type=%s", event_type)

    data = payload.get("data")
    if not isinstance(data, dict):
        logger.warning("email webhook: missing or non-dict 'data' field in payload type=%s", event_type)
        return {"received": "ok", "note": "no data field"}

    prospect_id: str | None = (data.get("tags") or {}).get("prospect_id")
    from_email: str = data.get("from", "") or data.get("to", "")

    # Hard bounces and delivery failures — log and do NOT run the agent
    if event_type in _BOUNCE_EVENT_TYPES:
        logger.warning(
            "email webhook: %s for prospect_id=%s email=%s — flagging as undeliverable",
            event_type,
            prospect_id,
            from_email,
        )
        prospect = _resolve_prospect_by_id(prospect_id) or _resolve_prospect_by_email(from_email)
        if prospect:
            prospect["_email_bounce"] = event_type
            prospect["_email_bounce_at"] = datetime.now(timezone.utc).isoformat()
        return {"received": "bounce_logged"}

    # Reply events — run the agent
    if event_type in _REPLY_EVENT_TYPES or event_type == "":
        content: str = data.get("text", "") or data.get("html", "")
        prospect = _resolve_prospect_by_id(prospect_id) or _resolve_prospect_by_email(from_email)
        if prospect:
            await _run_agent(prospect, content, channel="email")
        else:
            logger.warning(
                "email webhook: no prospect found for id=%s from=%s",
                prospect_id, from_email,
            )
        return {"received": "ok"}

    # Unknown event type — log and acknowledge without running agent
    logger.info("email webhook: unhandled event type=%s — acknowledging", event_type)
    return {"received": "ok", "note": f"unhandled type: {event_type}"}


# ---------------------------------------------------------------------------
# Africa's Talking — SMS reply events
# ---------------------------------------------------------------------------


@app.post("/webhooks/sms", status_code=status.HTTP_200_OK)
async def handle_sms_reply(request: Request) -> Response:
    """Receive inbound SMS reply events from Africa's Talking."""
    form = await request.form()
    phone = str(form.get("phoneNumber", ""))
    text = str(form.get("text", ""))
    logger.info("sms webhook: from=%s text=%s", phone, text)

    prospect_id = _lookup_prospect_by_phone(phone)
    prospect = _resolve_prospect_by_id(prospect_id)

    if prospect:
        await _run_agent(prospect, text, channel="sms")
    else:
        logger.warning("sms webhook: no prospect for phone=%s", phone)

    return Response(status_code=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Cal.com — booking events
# ---------------------------------------------------------------------------


@app.post("/webhooks/cal", status_code=status.HTTP_200_OK)
async def handle_cal_event(request: Request) -> dict[str, str]:
    """Receive booking lifecycle events from Cal.com."""
    payload: dict[str, Any] = await request.json()
    trigger = payload.get("triggerEvent", "")
    logger.info("cal webhook: trigger=%s", trigger)

    cal_payload = payload.get("payload", {})
    prospect_id: str | None = (cal_payload.get("metadata") or {}).get("prospect_id")
    cal_event_id: str = cal_payload.get("uid", "")

    if trigger == "BOOKING_CREATED" and prospect_id:
        prospect = _resolve_prospect_by_id(prospect_id)
        if prospect:
            msg = f"Your discovery call has been confirmed. Cal.com event ID: {cal_event_id}"
            await _run_agent(prospect, msg, channel="email", cal_event_id=cal_event_id)

    return {"received": "ok"}


# ---------------------------------------------------------------------------
# HubSpot — timeline events (optional)
# ---------------------------------------------------------------------------


@app.post("/webhooks/hubspot", status_code=status.HTTP_200_OK)
async def handle_hubspot_event(request: Request) -> dict[str, str]:
    """Receive HubSpot timeline / CRM events."""
    payload: dict[str, Any] = await request.json()
    logger.info("hubspot webhook: %s", payload)
    return {"received": "ok"}


# ---------------------------------------------------------------------------
# Core agent runner
# ---------------------------------------------------------------------------


async def _run_agent(
    prospect: dict[str, Any],
    inbound_text: str,
    channel: str,
    cal_event_id: str | None = None,
) -> None:
    """Delegate an inbound message to the ConversationAgent.

    Runs the full LangGraph pipeline: enrich → classify → LLM →
    HubSpot tools → kill switch → send → persist.

    Swallows exceptions so webhook endpoints always return 200.

    Args:
        prospect: Prospect dict with at minimum prospect_id and email.
        inbound_text: Raw text of the inbound message.
        channel: Channel the message arrived on ("email" | "sms").
        cal_event_id: Cal.com booking UID when triggered by BOOKING_CREATED.
    """
    try:
        result = await _agent.handle(prospect, inbound_text, channel, cal_event_id)
        logger.info(
            "Agent completed: prospect_id=%s segment=%s destination=%s hs_id=%s",
            prospect.get("prospect_id"),
            result.get("segment"),
            result.get("destination"),
            result.get("hs_contact_id"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Agent failed for prospect_id=%s: %s",
            prospect.get("prospect_id"),
            exc,
        )


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------


def register_prospect(prospect_dict: dict[str, Any]) -> None:
    """Register a prospect dict so inbound webhooks can resolve it.

    Args:
        prospect_dict: Must include prospect_id, email, and optionally phone.
    """
    pid = prospect_dict["prospect_id"]
    _prospect_registry[pid] = prospect_dict
    phone = prospect_dict.get("phone")
    if phone:
        _phone_registry[phone] = pid
        _phone_registry[phone.lstrip("+")] = pid
    logger.info("Registered prospect: prospect_id=%s email=%s", pid, prospect_dict.get("email"))


def _resolve_prospect_by_id(prospect_id: str | None) -> dict[str, Any] | None:
    """Look up a prospect dict by prospect_id.

    Args:
        prospect_id: UUID string or None.

    Returns:
        Prospect dict or None.
    """
    if not prospect_id:
        return None
    return _prospect_registry.get(prospect_id)


def _resolve_prospect_by_email(email: str) -> dict[str, Any] | None:
    """Look up a prospect dict by email address.

    Falls back to the demo prospect for sandbox testing.

    Args:
        email: Email address string.

    Returns:
        Prospect dict or None.
    """
    for p in _prospect_registry.values():
        if p.get("email", "").lower() == email.lower():
            return p
    # Sandbox fallback
    return _prospect_registry.get("demo-prospect-001")


def _lookup_prospect_by_phone(phone: str) -> str | None:
    """Look up a prospect_id by phone number.

    Args:
        phone: Phone number string.

    Returns:
        prospect_id string or None.
    """
    pid = _phone_registry.get(phone)
    if pid:
        return pid
    # Try stripping/adding + prefix
    stripped = phone.lstrip("+")
    for registered, p_id in _phone_registry.items():
        if registered == "__any__":
            continue
        if stripped in registered.lstrip("+") or registered.lstrip("+") in stripped:
            return p_id
    return _phone_registry.get("__any__")
