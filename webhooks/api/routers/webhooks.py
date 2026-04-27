import logging
import re
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from db.session import get_db
from webhooks.agent_runner import run_agent
from webhooks.utils import get_prospect_by_id, get_prospect_by_email, get_prospect_by_phone

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_BOUNCE_TYPES = frozenset(["email.bounced", "email.delivery_delayed", "email.delivery_failed", "email.complained"])

def _extract_tags(tags) -> dict[str, str]:
    """Normalise Resend tags — list[{name,value}] or dict[str,str]."""
    if isinstance(tags, list):
        return {t["name"]: t["value"] for t in tags if "name" in t and "value" in t}
    if isinstance(tags, dict):
        return tags
    return {}

def _prospect_id_from_subject(subject: str) -> str | None:
    if not subject:
        return None
    m = re.search(r"\[Lead:\s*([^\]]+)\]", subject)
    return m.group(1).strip() if m else None

@router.post("/email", status_code=status.HTTP_200_OK)
async def handle_email_reply(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    try:
        body = await request.json()
    except Exception:
        return {"received": "ok", "note": "invalid json"}

    event_type = body.get("type", "")
    data = body.get("data") or {}

    # Log full payload at DEBUG so we can diagnose future issues
    logger.info("email webhook: type=%s data_keys=%s", event_type, list(data.keys()))
    logger.debug("email webhook full payload: %s", body)

    if event_type in _BOUNCE_TYPES:
        logger.warning("email webhook: bounce type=%s", event_type)
        return {"received": "bounce_logged"}

    # Accept both "email.replied" (send webhook) and "email.received" (inbound routing)
    _INBOUND_TYPES = frozenset(["email.replied", "email.received"])
    if event_type not in _INBOUND_TYPES:
        logger.info("email webhook: ignoring non-inbound type=%s", event_type)
        return {"received": "ok", "note": f"ignored: {event_type}"}

    # Extract fields — Resend reply payload shape varies; be defensive
    tags = _extract_tags(data.get("tags", []))
    prospect_id = tags.get("prospect_id")
    subject = data.get("subject", "")
    from_email = data.get("from", "") or data.get("reply_from", "")
    to_list = data.get("to") or []
    if isinstance(to_list, str):
        to_list = [to_list]
    content = data.get("text") or data.get("html") or data.get("reply_text") or ""

    logger.info("email webhook reply: prospect_id=%s from=%s to=%s subject=%s",
                prospect_id, from_email, to_list, subject)

    # Prospect lookup — three layers
    prospect = None
    if prospect_id:
        prospect = await get_prospect_by_id(db, prospect_id)
        logger.info("email webhook: id-tag lookup prospect_id=%s found=%s", prospect_id, bool(prospect))

    if not prospect:
        pid_from_subject = _prospect_id_from_subject(subject)
        if pid_from_subject:
            prospect = await get_prospect_by_id(db, pid_from_subject)
            logger.info("email webhook: subject lookup pid=%s found=%s", pid_from_subject, bool(prospect))

    if not prospect:
        for addr in to_list:
            prospect = await get_prospect_by_email(db, addr)
            if prospect:
                logger.info("email webhook: to-addr lookup addr=%s found lead", addr)
                break

    if not prospect and from_email:
        prospect = await get_prospect_by_email(db, from_email)
        logger.info("email webhook: from-email lookup addr=%s found=%s", from_email, bool(prospect))

    if not prospect:
        logger.warning("email webhook: no prospect found — prospect_id=%s from=%s to=%s",
                       prospect_id, from_email, to_list)
        return {"received": "ok", "note": "prospect not found"}

    lead_id = prospect.get("prospect_id")
    logger.info("email webhook: matched lead_id=%s company=%s", lead_id, prospect.get("company_name", ""))

    # Dedup — skip if this subject was already processed as an inbound message
    if lead_id and subject:
        from sqlalchemy import select
        from db.models import Message as _Msg
        existing = await db.execute(
            select(_Msg).where(_Msg.lead_id == lead_id, _Msg.subject == subject, _Msg.direction == "inbound")
        )
        if existing.scalars().first():
            logger.info("email webhook: duplicate subject already processed — skipping lead_id=%s", lead_id)
            return {"received": "ok", "note": "duplicate"}

    # Persist inbound message
    if lead_id and content:
        from db.models import Message
        db.add(Message(
            lead_id=lead_id,
            direction="inbound",
            channel="email",
            body=content,
            subject=subject or None,
            intent=None,
            is_draft=False,
        ))
        await db.commit()

    # Run agent
    result = await run_agent(prospect, content, channel="email")
    logger.info("email webhook: agent result keys=%s", list((result or {}).keys()))

    # Persist outbound reply
    if result and lead_id:
        reply_body = result.get("reply_text")
        if reply_body:
            from db.models import Message, Event, Lead
            from datetime import datetime, timezone
            db.add(Message(
                lead_id=lead_id,
                direction="outbound",
                channel="email",
                body=reply_body,
                intent=result.get("intent"),
                is_draft=False,
            ))
            # Update lead state
            lead = await db.get(Lead, lead_id)
            if lead:
                lead.current_state = "replied"
                lead.outbound_attempt_count = (lead.outbound_attempt_count or 0) + 1
                lead.updated_at = datetime.now(timezone.utc)
            db.add(Event(
                lead_id=lead_id,
                event_type="reply_received",
                payload={"from": from_email, "subject": subject},
            ))
            await db.commit()
            logger.info("email webhook: outbound reply persisted for lead_id=%s", lead_id)

    return {"received": "ok"}


async def _handle_inbound_email(request: Request, db: AsyncSession) -> dict[str, str]:
    """Shared logic for any inbound email — used by both /email-inbound and /email (replied)."""
    try:
        body = await request.json()
    except Exception:
        return {"received": "ok", "note": "invalid json"}

    logger.info("inbound email: keys=%s", list(body.keys()))
    logger.debug("inbound email full payload: %s", body)

    # Resend inbound webhook shape:
    # { "from": "...", "to": ["..."], "subject": "...", "text": "...", "html": "..." }
    from_email = body.get("from", "") or ""
    to_list = body.get("to") or []
    if isinstance(to_list, str):
        to_list = [to_list]
    subject = body.get("subject", "") or ""
    content = body.get("text") or body.get("html") or ""

    prospect_id = _prospect_id_from_subject(subject)
    prospect = None

    if prospect_id:
        prospect = await get_prospect_by_id(db, prospect_id)
        logger.info("inbound email: subject lookup pid=%s found=%s", prospect_id, bool(prospect))

    if not prospect:
        for addr in to_list:
            prospect = await get_prospect_by_email(db, addr)
            if prospect:
                logger.info("inbound email: to-addr lookup addr=%s matched", addr)
                break

    if not prospect and from_email:
        prospect = await get_prospect_by_email(db, from_email)
        logger.info("inbound email: from-email lookup addr=%s found=%s", from_email, bool(prospect))

    if not prospect:
        logger.warning("inbound email: no prospect found from=%s to=%s subject=%s", from_email, to_list, subject)
        return {"received": "ok", "note": "prospect not found"}

    lead_id = prospect.get("prospect_id")
    logger.info("inbound email: matched lead_id=%s", lead_id)

    if lead_id and content:
        from db.models import Message
        db.add(Message(
            lead_id=lead_id,
            direction="inbound",
            channel="email",
            body=content,
            subject=subject or None,
            intent=None,
            is_draft=False,
        ))
        await db.commit()

    result = await run_agent(prospect, content, channel="email")
    logger.info("inbound email: agent result keys=%s", list((result or {}).keys()))

    if result and lead_id:
        reply_body = result.get("reply_text")
        if reply_body:
            from db.models import Message, Event, Lead
            from datetime import datetime, timezone
            db.add(Message(
                lead_id=lead_id,
                direction="outbound",
                channel="email",
                body=reply_body,
                intent=result.get("intent"),
                is_draft=False,
            ))
            lead = await db.get(Lead, lead_id)
            if lead:
                lead.current_state = "replied"
                lead.outbound_attempt_count = (lead.outbound_attempt_count or 0) + 1
                lead.updated_at = datetime.now(timezone.utc)
            db.add(Event(
                lead_id=lead_id,
                event_type="reply_received",
                payload={"from": from_email, "subject": subject},
            ))
            await db.commit()
            logger.info("inbound email: outbound reply persisted for lead_id=%s", lead_id)

    return {"received": "ok"}


@router.post("/email-inbound", status_code=status.HTTP_200_OK)
async def handle_email_inbound(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Resend inbound routing webhook — fires when email arrives at conversion@iavenueamb.resend.app."""
    return await _handle_inbound_email(request, db)


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
