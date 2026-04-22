"""
Webhook handler — FastAPI app for inbound reply events.

Endpoints:
  POST /webhooks/email    — Resend reply events
  POST /webhooks/sms      — Africa's Talking reply events
  POST /webhooks/cal      — Cal.com booking events
  POST /webhooks/hubspot  — HubSpot timeline events (optional)

Each endpoint parses the inbound payload, extracts prospect_id,
feeds the event into the NurtureSequencer FSM, and logs to HubSpot.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

app = FastAPI(title="Conversion Engine Webhook Handler")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Landing Page & Favicon
# ---------------------------------------------------------------------------

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Resend — email reply events
# ---------------------------------------------------------------------------

@app.post("/webhooks/email", status_code=status.HTTP_200_OK)
async def handle_email_reply(request: Request) -> dict[str, str]:
    """
    Receive inbound email reply events from Resend.

    Expected payload shape (Resend webhook):
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
    """
    payload: dict[str, Any] = await request.json()
    logger.info("email webhook received: type=%s", payload.get("type"))

    data = payload.get("data", {})
    prospect_id: str | None = (data.get("tags") or {}).get("prospect_id")

    if prospect_id:
        _handle_inbound_reply(
            prospect_id=prospect_id,
            content=data.get("text", ""),
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
    """
    Receive inbound SMS reply events from Africa's Talking.

    Africa's Talking sends form-encoded POST data:
      phoneNumber=+254700000000&text=STOP&to=20880&date=...
    The prospect_id is looked up by phone number.
    """
    form = await request.form()
    phone = form.get("phoneNumber", "")
    text = form.get("text", "")
    logger.info("sms webhook received: from=%s text=%s", phone, text)

    prospect_id = _lookup_prospect_by_phone(str(phone))
    if prospect_id:
        _handle_inbound_reply(
            prospect_id=prospect_id,
            content=str(text),
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
    """
    Receive booking lifecycle events from Cal.com.

    Expected payload shape:
      {
        "triggerEvent": "BOOKING_CREATED" | "BOOKING_CANCELLED" | "BOOKING_RESCHEDULED",
        "payload": {
          "uid": "cal-event-uid",
          "attendees": [{ "email": "prospect@example.com", "name": "..." }],
          "metadata": { "prospect_id": "uuid" }
        }
      }
    """
    payload: dict[str, Any] = await request.json()
    trigger = payload.get("triggerEvent", "")
    logger.info("cal webhook received: trigger=%s", trigger)

    cal_payload = payload.get("payload", {})
    prospect_id: str | None = (cal_payload.get("metadata") or {}).get("prospect_id")
    cal_event_id: str = cal_payload.get("uid", "")

    if prospect_id and trigger == "BOOKING_CREATED":
        logger.info("booking confirmed: prospect_id=%s cal_event_id=%s", prospect_id, cal_event_id)
        # TODO: wire to BookingAgent.confirm_booking() and CRMWriter.write_booking()
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
    """
    Receive HubSpot timeline / CRM events (optional integration).
    """
    payload: dict[str, Any] = await request.json()
    logger.info("hubspot webhook received: %s", payload)
    return {"received": "ok"}


# ---------------------------------------------------------------------------
# Internal helpers (stubs — wired to real components in later tasks)
# ---------------------------------------------------------------------------

def _handle_inbound_reply(prospect_id: str, content: str, channel: str) -> None:
    """
    Feed an inbound reply into the NurtureSequencer FSM and log to HubSpot.

    Stub implementation — full wiring done in Task 10.
    """
    logger.info(
        "inbound reply: prospect_id=%s channel=%s content_len=%d",
        prospect_id,
        channel,
        len(content),
    )
    # TODO (Task 10): NurtureSequencer.handle_inbound_reply(prospect_id, content, channel)
    # TODO (Task 10): CRMWriter.log_activity(inbound reply activity)


def _lookup_prospect_by_phone(phone: str) -> str | None:
    """
    Look up a prospect_id by phone number.

    Stub — full implementation in Task 10 when CRMWriter is wired.
    """
    # TODO (Task 10): query HubSpot contact by phone number
    return None
