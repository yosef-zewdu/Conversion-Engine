"""
Webhook handler — FastAPI app for inbound reply events.

Endpoints:
  POST /webhooks/email    — Resend reply events
  POST /webhooks/sms      — Africa's Talking reply events
  POST /webhooks/cal      — Cal.com booking events
  POST /webhooks/hubspot  — HubSpot timeline events (optional)

Each endpoint parses the inbound payload, extracts prospect_id,
feeds the event into the NurtureSequencer FSM, and logs to HubSpot.

Requirements: 8.8, 10.1, 10.2
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request, Response, status

from crm_writer.writer import CRMWriter
from nurture_sequencer.state_machine import ProspectFSM

logger = logging.getLogger(__name__)

app = FastAPI(title="Conversion Engine Webhook Handler")

# Module-level CRMWriter singleton (shared across requests)
_crm_writer = CRMWriter()

# In-memory prospect FSM registry: prospect_id -> ProspectFSM
# In production this would be backed by a persistent store.
_fsm_registry: dict[str, ProspectFSM] = {}

# Phone-number → prospect_id lookup (populated when prospects are registered)
_phone_registry: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe.

    Returns:
        JSON object with status "ok".
    """
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Favicon
# ---------------------------------------------------------------------------


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """Suppress 404 for favicon requests."""
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Resend — email reply events
# ---------------------------------------------------------------------------


@app.post("/webhooks/email", status_code=status.HTTP_200_OK)
async def handle_email_reply(request: Request) -> dict[str, str]:
    """Receive inbound email reply events from Resend.

    Expected payload shape (Resend webhook)::

        {
          "type": "email.replied",
          "data": {
            "email_id": "...",
            "from": "prospect@example.com",
            "subject": "Re: ...",
            "text": "...",
            "tags": { "prospect_id": "uuid" }
          }
        }

    Parses the event, extracts prospect_id from tags, calls
    NurtureSequencer.handle_inbound_reply(), and logs the reply to HubSpot.

    Args:
        request: Incoming FastAPI request.

    Returns:
        ``{"received": "ok"}`` on success.
    """
    payload: dict[str, Any] = await request.json()
    logger.info("email webhook received: type=%s", payload.get("type"))

    data = payload.get("data", {})
    prospect_id: str | None = (data.get("tags") or {}).get("prospect_id")
    content: str = data.get("text", "")

    if prospect_id:
        await _handle_inbound_reply(
            prospect_id=prospect_id,
            content=content,
            channel="email",
        )
    else:
        logger.warning("email webhook: no prospect_id in tags — payload=%s", payload)

    return {"received": "ok"}


# ---------------------------------------------------------------------------
# Africa's Talking — SMS reply events
# ---------------------------------------------------------------------------


@app.post("/webhooks/sms", status_code=status.HTTP_200_OK)
async def handle_sms_reply(request: Request) -> Response:
    """Receive inbound SMS reply events from Africa's Talking.

    Africa's Talking sends form-encoded POST data::

        phoneNumber=+254700000000&text=STOP&to=20880&date=...

    The prospect_id is looked up by phone number.

    Args:
        request: Incoming FastAPI request.

    Returns:
        Plain 200 with no body (required by Africa's Talking).
    """
    form = await request.form()
    phone = str(form.get("phoneNumber", ""))
    text = str(form.get("text", ""))
    logger.info("sms webhook received: from=%s text=%s", phone, text)

    prospect_id = _lookup_prospect_by_phone(phone)
    if prospect_id:
        await _handle_inbound_reply(
            prospect_id=prospect_id,
            content=text,
            channel="sms",
        )
    else:
        logger.warning("sms webhook: no prospect found for phone=%s", phone)

    # Africa's Talking expects a plain 200 with no body
    return Response(status_code=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Cal.com — booking events
# ---------------------------------------------------------------------------


@app.post("/webhooks/cal", status_code=status.HTTP_200_OK)
async def handle_cal_event(request: Request) -> dict[str, str]:
    """Receive booking lifecycle events from Cal.com.

    Expected payload shape::

        {
          "triggerEvent": "BOOKING_CREATED",
          "payload": {
            "uid": "cal-event-uid",
            "attendees": [{ "email": "prospect@example.com" }],
            "metadata": { "prospect_id": "uuid" }
          }
        }

    Args:
        request: Incoming FastAPI request.

    Returns:
        ``{"received": "ok"}`` on success.
    """
    payload: dict[str, Any] = await request.json()
    trigger = payload.get("triggerEvent", "")
    logger.info("cal webhook received: trigger=%s", trigger)

    cal_payload = payload.get("payload", {})
    prospect_id: str | None = (cal_payload.get("metadata") or {}).get("prospect_id")
    cal_event_id: str = cal_payload.get("uid", "")

    if prospect_id and trigger == "BOOKING_CREATED":
        logger.info(
            "booking confirmed: prospect_id=%s cal_event_id=%s",
            prospect_id,
            cal_event_id,
        )
        await _log_activity(
            prospect_id=prospect_id,
            activity_type="booking_confirmed",
            channel="voice",
            content=f"Cal.com booking confirmed. Event ID: {cal_event_id}",
        )
    elif trigger == "BOOKING_CANCELLED":
        logger.info("booking cancelled: cal_event_id=%s", cal_event_id)
    else:
        logger.info("cal webhook: unhandled trigger=%s", trigger)

    return {"received": "ok"}


# ---------------------------------------------------------------------------
# HubSpot — timeline events (optional)
# ---------------------------------------------------------------------------


@app.post("/webhooks/hubspot", status_code=status.HTTP_200_OK)
async def handle_hubspot_event(request: Request) -> dict[str, str]:
    """Receive HubSpot timeline / CRM events (optional integration).

    Args:
        request: Incoming FastAPI request.

    Returns:
        ``{"received": "ok"}`` on success.
    """
    payload: dict[str, Any] = await request.json()
    logger.info("hubspot webhook received: %s", payload)
    return {"received": "ok"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _handle_inbound_reply(
    prospect_id: str,
    content: str,
    channel: str,
) -> None:
    """Feed an inbound reply into the NurtureSequencer FSM and log to HubSpot.

    Looks up the ProspectFSM for the given prospect_id, calls
    handle_inbound_reply(), then logs the inbound activity to HubSpot via
    CRMWriter.

    Args:
        prospect_id: UUID of the prospect who replied.
        content: Raw text of the inbound reply.
        channel: Channel the reply arrived on (``"email"`` or ``"sms"``).
    """
    logger.info(
        "inbound reply: prospect_id=%s channel=%s content_len=%d",
        prospect_id,
        channel,
        len(content),
    )

    # Update FSM state if we have a registered FSM for this prospect
    fsm = _fsm_registry.get(prospect_id)
    if fsm is not None:
        fsm.handle_inbound_reply(channel=channel, content=content)
        logger.info(
            "FSM updated: prospect_id=%s new_state=%s",
            prospect_id,
            fsm.state.value,
        )
    else:
        logger.warning(
            "No FSM registered for prospect_id=%s — reply logged but state not updated",
            prospect_id,
        )

    # Log inbound reply to HubSpot (Req 10.2)
    await _log_activity(
        prospect_id=prospect_id,
        activity_type=f"inbound_{channel}",
        channel=channel,
        content=content,
        direction="inbound",
    )


async def _log_activity(
    prospect_id: str,
    activity_type: str,
    channel: str,
    content: str,
    direction: str = "inbound",
) -> None:
    """Write an activity record to HubSpot via CRMWriter.

    Swallows exceptions so webhook endpoints always return 200.

    Args:
        prospect_id: UUID of the prospect.
        activity_type: Internal type string (e.g. ``"inbound_email"``).
        channel: Channel string (``"email"``, ``"sms"``, ``"voice"``).
        content: Message body or summary.
        direction: ``"inbound"`` or ``"outbound"``.
    """
    try:
        await _crm_writer.log_activity({
            "type": activity_type,
            "prospect_id": prospect_id,
            "channel": channel,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "content": content,
            "direction": direction,
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "CRM activity log failed for prospect_id=%s: %s", prospect_id, exc
        )


def _lookup_prospect_by_phone(phone: str) -> str | None:
    """Look up a prospect_id by phone number from the in-memory registry.

    Args:
        phone: Phone number string (e.g. ``"+254700000000"``).

    Returns:
        The prospect_id string, or ``None`` if not found.
    """
    return _phone_registry.get(phone)


# ---------------------------------------------------------------------------
# Registry helpers (called by the pipeline runner to register prospects)
# ---------------------------------------------------------------------------


def register_prospect_fsm(prospect_id: str, fsm: ProspectFSM) -> None:
    """Register a ProspectFSM so inbound webhooks can update its state.

    Args:
        prospect_id: UUID of the prospect.
        fsm: The ProspectFSM instance managing this prospect's state.
    """
    _fsm_registry[prospect_id] = fsm
    logger.info("Registered FSM for prospect_id=%s", prospect_id)


def register_phone(phone: str, prospect_id: str) -> None:
    """Register a phone-number → prospect_id mapping for SMS lookup.

    Args:
        phone: Phone number string.
        prospect_id: UUID of the prospect.
    """
    _phone_registry[phone] = prospect_id
    logger.info("Registered phone %s → prospect_id=%s", phone, prospect_id)
