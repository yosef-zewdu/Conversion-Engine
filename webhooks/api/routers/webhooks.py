import logging
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from db.session import get_db
from webhooks.schemas import ResendPayload, CalcomPayload
from webhooks.agent_runner import run_agent
from webhooks.utils import get_prospect_by_id, get_prospect_by_email, get_prospect_by_phone

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_BOUNCE_EVENT_TYPES = frozenset([
    "email.bounced",
    "email.delivery_delayed",
    "email.delivery_failed",
    "email.complained",
])

_REPLY_EVENT_TYPES = frozenset([
    "email.replied",
    "email.opened",
])

@router.post("/email", status_code=status.HTTP_200_OK)
async def handle_email_reply(
    payload: ResendPayload,
    db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    event_type = payload.type
    data = payload.data
    logger.info("email webhook: type=%s", event_type)

    prospect_id = data.tags.get("prospect_id")
    from_email = data.from_email

    if event_type in _BOUNCE_EVENT_TYPES:
        logger.warning("email webhook: %s for prospect_id=%s email=%s", event_type, prospect_id, from_email)
        return {"received": "bounce_logged"}

    if event_type in _REPLY_EVENT_TYPES or event_type == "":
        content = data.text or data.html or ""
        prospect = None
        if prospect_id:
            prospect = await get_prospect_by_id(db, prospect_id)
        if not prospect:
            prospect = await get_prospect_by_email(db, from_email)

        if prospect:
            lead_id = prospect.get("prospect_id")

            # ── Persist the inbound message so it appears in the timeline ──
            if lead_id and content:
                from db.models import Message
                inbound_msg = Message(
                    lead_id=lead_id,
                    direction="inbound",
                    channel="email",
                    body=content,
                    subject=None,
                    intent=None,        # will be classified by the orchestrator
                    is_draft=False,
                )
                db.add(inbound_msg)
                await db.commit()

            result = await run_agent(prospect, content, channel="email")

            # ── Persist the outbound AI reply if the orchestrator produced one ──
            if result and lead_id:
                reply_body = result.get("reply_text")
                intent = result.get("intent")
                if reply_body:
                    from db.models import Message
                    outbound_msg = Message(
                        lead_id=lead_id,
                        direction="outbound",
                        channel="email",
                        body=reply_body,
                        intent=intent,
                        is_draft=result.get("policy_decision", {}).get("allowed") is False,
                    )
                    db.add(outbound_msg)
                    await db.commit()
        else:
            logger.warning("email webhook: no prospect found for id=%s from=%s", prospect_id, from_email)
        return {"received": "ok"}

    return {"received": "ok", "note": f"unhandled type: {event_type}"}

@router.post("/sms", status_code=status.HTTP_200_OK)
async def handle_sms_reply(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    from fastapi import Response
    form = await request.form()
    phone = str(form.get("phoneNumber", ""))
    text = str(form.get("text", ""))
    logger.info("sms webhook: from=%s text=%s", phone, text)

    prospect = await get_prospect_by_phone(db, phone)

    if prospect:
        await run_agent(prospect, text, channel="sms")
    else:
        logger.warning("sms webhook: no prospect for phone=%s", phone)

    return Response(status_code=status.HTTP_200_OK)

@router.post("/cal", status_code=status.HTTP_200_OK)
async def handle_cal_event(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    try:
        body = await request.json()
    except Exception:
        return {"received": "ok", "note": "invalid json"}

    trigger = body.get("triggerEvent", "")
    logger.info("cal webhook: trigger=%s", trigger)

    # Cal.com sends a PING when first registering the webhook — always 200
    if not trigger or trigger == "PING":
        return {"received": "ok"}

    cal_payload = body.get("payload", {})
    prospect_id = (cal_payload.get("metadata") or {}).get("prospect_id")
    cal_event_id = cal_payload.get("uid", "")
    start_time = cal_payload.get("startTime", "")
    end_time = cal_payload.get("endTime", "")
    attendee_tz = (cal_payload.get("attendees") or [{}])[0].get("timeZone", "UTC")

    if trigger == "BOOKING_CREATED" and prospect_id:
        from db.models import Lead, Message, Event as LeadEvent
        from datetime import datetime, timezone

        lead = await db.get(Lead, prospect_id)
        if lead:
            lead.current_state = "warm"
            lead.cal_event_id = cal_event_id
            lead.updated_at = datetime.now(timezone.utc)

            conf_body = (
                f"Discovery call confirmed.\n"
                f"Time: {start_time} → {end_time} ({attendee_tz})\n"
                f"Cal.com Event: {cal_event_id}"
            )
            msg = Message(
                lead_id=prospect_id,
                direction="outbound",
                channel="email",
                body=conf_body,
                subject="Discovery Call Confirmed",
                intent="chooses_slot",
                is_draft=False,
            )
            db.add(msg)

            evt = LeadEvent(
                lead_id=prospect_id,
                event_type="booking_created",
                payload={
                    "channel": "cal",
                    "cal_event_id": cal_event_id,
                    "calData": {
                        "startTime": start_time,
                        "endTime": end_time,
                        "timezone": attendee_tz,
                    }
                },
            )
            db.add(evt)
            await db.commit()

            prospect = await get_prospect_by_id(db, prospect_id)
            if prospect:
                msg_text = f"Your discovery call has been confirmed. Cal.com event ID: {cal_event_id}"
                await run_agent(prospect, msg_text, channel="email", cal_event_id=cal_event_id)
        else:
            logger.warning("cal webhook: no lead found for prospect_id=%s", prospect_id)
    elif trigger == "BOOKING_CREATED":
        logger.warning("cal webhook: BOOKING_CREATED but no prospect_id in metadata — payload=%s", cal_payload)

    return {"received": "ok"}

@router.post("/hubspot", status_code=status.HTTP_200_OK)
async def handle_hubspot_event(request: Request) -> dict[str, str]:
    payload = await request.json()
    logger.info("hubspot webhook: %s", payload)
    return {"received": "ok"}
