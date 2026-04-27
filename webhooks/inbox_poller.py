"""
Resend Inbox Poller — polls resend.Emails.Receiving.list() every 30 seconds
and pipes new received emails through the same handler logic as /webhooks/email.

Resend's receiving email feature stores replies at a fixed address
(e.g. conversion@iavenueamb.resend.app). It does NOT fire a webhook
automatically — this poller bridges that gap.

Tracks the last-seen email_id in-memory so each email is processed exactly once
per server lifetime. On restart it re-checks the last 20 emails but deduplicates
against the DB (checks if a Message with matching resend_email_id already exists).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 30  # seconds
_seen_ids: set[str] = set()


def _extract_prospect_id(subject: str | None, body: str | None) -> str | None:
    """Extract [Lead: <id>] from subject or quoted body."""
    for text in (subject or "", body or ""):
        m = re.search(r"\[Lead:\s*([^\]]+)\]", text)
        if m:
            return m.group(1).strip()
    return None


async def _process_received_email(email_data: dict) -> None:
    """Process one received email — same logic as /webhooks/email handler."""
    from db.session import AsyncSessionLocal
    from db.models import Message, Event, Lead
    from webhooks.utils import get_prospect_by_id, get_prospect_by_email
    from webhooks.agent_runner import run_agent

    email_id = email_data.get("id", "")
    subject = email_data.get("subject", "")
    from_email = email_data.get("from", "") or ""
    to_list = email_data.get("to") or []
    if isinstance(to_list, str):
        to_list = [to_list]
    # Resend receiving stores body in "text" or "html"
    content = email_data.get("text") or email_data.get("html") or email_data.get("body") or ""

    logger.info("inbox_poller: processing email_id=%s from=%s subject=%s", email_id, from_email, subject)

    async with AsyncSessionLocal() as db:
        # Dedup: skip if we already stored a message for this resend email_id
        from sqlalchemy import select
        existing = await db.execute(
            select(Message).where(Message.resend_email_id == email_id)
        )
        if existing.scalars().first():
            logger.debug("inbox_poller: already processed email_id=%s — skipping", email_id)
            _seen_ids.add(email_id)
            return

        # Prospect lookup — subject [Lead: id] is most reliable
        prospect_id = _extract_prospect_id(subject, content)
        prospect = None

        if prospect_id:
            prospect = await get_prospect_by_id(db, prospect_id)
            logger.info("inbox_poller: id lookup prospect_id=%s found=%s", prospect_id, bool(prospect))

        if not prospect:
            for addr in to_list:
                prospect = await get_prospect_by_email(db, addr)
                if prospect:
                    break

        if not prospect and from_email:
            prospect = await get_prospect_by_email(db, from_email)

        if not prospect:
            logger.warning("inbox_poller: no prospect for email_id=%s from=%s", email_id, from_email)
            _seen_ids.add(email_id)
            return

        lead_id = prospect.get("prospect_id")
        logger.info("inbox_poller: matched lead_id=%s", lead_id)

        # Verify lead actually exists in DB before inserting message (FK guard)
        if lead_id:
            lead_row = await db.get(Lead, lead_id)
            if not lead_row:
                logger.warning("inbox_poller: lead_id=%s not in DB — skipping email_id=%s", lead_id, email_id)
                _seen_ids.add(email_id)
                return

        # Persist inbound message
        if lead_id and content:
            db.add(Message(
                lead_id=lead_id,
                direction="inbound",
                channel="email",
                body=content,
                subject=subject or None,
                intent=None,
                is_draft=False,
                resend_email_id=email_id,
            ))
            await db.commit()

    # Run agent outside the DB session to avoid holding the connection
    result = await run_agent(prospect, content, channel="email")
    logger.info("inbox_poller: agent result keys=%s", list((result or {}).keys()))

    if result and lead_id:
        reply_body = result.get("reply_text")
        if reply_body:
            async with AsyncSessionLocal() as db:
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
                    payload={"from": from_email, "subject": subject, "resend_email_id": email_id},
                ))
                await db.commit()
                logger.info("inbox_poller: reply persisted for lead_id=%s", lead_id)

    _seen_ids.add(email_id)


async def _poll_once() -> None:
    """Fetch the latest received emails from Resend and process new ones."""
    import resend

    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        return

    resend.api_key = api_key

    try:
        response = await resend.Emails.Receiving.list_async()
        # SDK returns a ListResponse object with .data
        emails = getattr(response, "data", None)
        if emails is None:
            emails = response if isinstance(response, list) else []
    except Exception as exc:
        logger.warning("inbox_poller: list_async() failed: %s", exc)
        return

    # Convert any SDK objects to plain dicts
    email_list = []
    for e in emails:
        email_list.append(e if isinstance(e, dict) else vars(e))

    new_emails = [e for e in email_list if e.get("id") not in _seen_ids]
    if not new_emails:
        return

    logger.info("inbox_poller: %d new email(s) to process", len(new_emails))
    for email_data in new_emails:
        try:
            # Fetch full content — list() only returns metadata, not body
            email_id = email_data.get("id")
            if email_id:
                try:
                    full = await resend.Emails.Receiving.get_async(email_id=email_id)
                    email_data = full if isinstance(full, dict) else vars(full)
                except Exception as exc:
                    logger.warning("inbox_poller: get_async(%s) failed: %s", email_id, exc)
            await _process_received_email(email_data)
        except Exception as exc:
            logger.error("inbox_poller: failed to process email %s: %s", email_data.get("id"), exc)


async def start_inbox_poller() -> None:
    """Long-running background task — call once at startup."""
    logger.info("inbox_poller: started, polling every %ds", _POLL_INTERVAL)
    while True:
        try:
            await _poll_once()
        except Exception as exc:
            logger.error("inbox_poller: unexpected error: %s", exc)
        await asyncio.sleep(_POLL_INTERVAL)
