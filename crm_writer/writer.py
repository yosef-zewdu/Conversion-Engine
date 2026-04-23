"""
CRM Writer — HubSpot integration for the Conversion Engine.

Wraps the HubSpot API with:
- Rate limiting: ≤ 100 calls per 10-second window (Req 10.5)
- Exponential backoff retry: max 3 attempts with 1s/2s/4s delays (Req 10.4)
- Langfuse emission on final failure for manual recovery

Provides four public coroutines:
    upsert_contact  — create/update a HubSpot contact record
    log_activity    — write an outbound send or inbound reply activity
    write_brief     — write a HiringSignalBrief or CompetitorGapBrief record
    write_booking   — write a confirmed Cal.com booking record

Requirements: 10.1, 10.2, 10.3, 10.4, 10.5
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import time
from typing import Any

import httpx

from config.models import Booking, Prospect
from signal_pipeline.models import CompetitorGapBrief, HiringSignalBrief

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate-limiter constants
# ---------------------------------------------------------------------------

_RATE_LIMIT_CALLS = 100       # max calls per window
_RATE_LIMIT_WINDOW = 10.0     # seconds

# ---------------------------------------------------------------------------
# Retry constants
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_BACKOFF_DELAYS = (1.0, 2.0, 4.0)  # seconds between attempts


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class _RateLimiter:
    """
    Token-bucket style rate limiter for HubSpot API calls.

    Tracks call timestamps in a deque and blocks (via asyncio.sleep) when
    the window is saturated.

    Args:
        max_calls: Maximum number of calls allowed per window.
        window_seconds: Duration of the sliding window in seconds.
    """

    def __init__(self, max_calls: int = _RATE_LIMIT_CALLS, window_seconds: float = _RATE_LIMIT_WINDOW) -> None:
        self._max_calls = max_calls
        self._window = window_seconds
        self._timestamps: collections.deque[float] = collections.deque()

    async def acquire(self) -> None:
        """
        Wait until a call slot is available within the rate-limit window.

        Returns:
            None — resumes when a slot is available.
        """
        while True:
            now = time.monotonic()
            # Evict timestamps outside the current window
            while self._timestamps and self._timestamps[0] <= now - self._window:
                self._timestamps.popleft()

            if len(self._timestamps) < self._max_calls:
                self._timestamps.append(now)
                return

            # Window is full — sleep until the oldest call expires
            sleep_for = self._window - (now - self._timestamps[0])
            await asyncio.sleep(max(sleep_for, 0.0))


# ---------------------------------------------------------------------------
# Langfuse failure emitter
# ---------------------------------------------------------------------------


def _emit_failure_to_langfuse(operation: str, payload: dict[str, Any]) -> None:
    """
    Emit a failed HubSpot write payload to Langfuse for manual recovery.

    Falls back to a structured log entry so the payload is never silently lost.

    Args:
        operation: Human-readable name of the failed operation.
        payload: The full payload that failed to write.
    """
    try:
        from langfuse import Langfuse  # type: ignore[import]
        lf = Langfuse(
            public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
            secret_key=os.environ.get("LANGFUSE_SECRET_KEY", ""),
        )
        trace = lf.trace(
            name=f"crm_write_failure:{operation}",
            input=payload,
            metadata={"operation": operation, "recovery": "manual"},
        )
        lf.flush()
        logger.info("Failed payload emitted to Langfuse trace_id=%s", getattr(trace, "id", "unknown"))
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Could not emit failure to Langfuse (operation=%s): %s. Payload: %s",
            operation,
            exc,
            json.dumps(payload, default=str),
        )


# ---------------------------------------------------------------------------
# CRMWriter
# ---------------------------------------------------------------------------


class CRMWriter:
    """
    Thin async wrapper around the HubSpot Contacts and Engagements APIs.

    All writes are queued through a shared rate-limiter enforcing ≤ 100
    HubSpot API calls per 10-second window.  Each call is retried up to
    3 times with exponential backoff (1s → 2s → 4s).  On final failure
    the payload is emitted to Langfuse for manual recovery.

    Args:
        access_token: HubSpot private-app access token.  Defaults to the
            ``HUBSPOT_ACCESS_TOKEN`` environment variable.
        base_url: HubSpot API base URL.  Override in tests.
        rate_limiter: Shared ``_RateLimiter`` instance.  A new one is
            created when ``None``.
    """

    _HUBSPOT_BASE = "https://api.hubapi.com"

    def __init__(
        self,
        access_token: str | None = None,
        base_url: str | None = None,
        rate_limiter: _RateLimiter | None = None,
    ) -> None:
        self._token = access_token or os.environ.get("HUBSPOT_ACCESS_TOKEN", "")
        self._base = (base_url or self._HUBSPOT_BASE).rstrip("/")
        self._limiter = rate_limiter or _RateLimiter()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        """
        Build the Authorization header for HubSpot API requests.

        Returns:
            Dict with ``Authorization`` and ``Content-Type`` headers.
        """
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    async def _call(
        self,
        method: str,
        path: str,
        payload: dict[str, Any],
        operation: str,
    ) -> dict[str, Any]:
        """
        Execute a rate-limited, retried HubSpot API call.

        Retries up to ``_MAX_RETRIES`` times with exponential backoff.
        Emits the payload to Langfuse on final failure.

        Args:
            method: HTTP method (``"POST"``, ``"PATCH"``, etc.).
            path: API path relative to the HubSpot base URL.
            payload: JSON body to send.
            operation: Human-readable label used in logs and Langfuse.

        Returns:
            Parsed JSON response dict from HubSpot.

        Raises:
            httpx.HTTPStatusError: Re-raised after all retries are exhausted
                and the payload has been emitted to Langfuse.
        """
        url = f"{self._base}{path}"
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            await self._limiter.acquire()
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.request(
                        method, url, json=payload, headers=self._headers()
                    )
                    response.raise_for_status()
                    return response.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_exc = exc
                delay = _BACKOFF_DELAYS[attempt]
                logger.warning(
                    "HubSpot %s attempt %d/%d failed: %s — retrying in %.1fs",
                    operation,
                    attempt + 1,
                    _MAX_RETRIES,
                    exc,
                    delay,
                )
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(delay)

        # All retries exhausted
        logger.error(
            "HubSpot %s failed after %d attempts. Emitting payload to Langfuse.",
            operation,
            _MAX_RETRIES,
        )
        _emit_failure_to_langfuse(operation, {"url": url, "payload": payload})
        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def upsert_contact(self, prospect: Prospect) -> str:
        """
        Create or update a HubSpot contact record for a prospect.

        Writes: ``crunchbase_id``, ``last_enriched_at``, segment,
        ``ai_maturity_score``, and ``bench_mismatch`` (Req 10.1).

        Uses HubSpot's upsert-by-email endpoint so duplicate contacts are
        not created on re-enrichment.

        Args:
            prospect: The ``Prospect`` dataclass with contact details and
                enrichment metadata.

        Returns:
            The HubSpot contact ID string (``"id"`` field from the response).
        """
        properties: dict[str, Any] = {
            "email": prospect.email,
            "firstname": prospect.contact_name.split()[0] if prospect.contact_name else "",
            "lastname": " ".join(prospect.contact_name.split()[1:]) if prospect.contact_name else "",
            "phone": prospect.phone or "",
            # Enrichment fields
            "prospect_id": prospect.prospect_id,
            "company_id": prospect.company_id,
            "icp_segment": prospect.segment.value if prospect.segment else "",
            "prospect_state": prospect.current_state.value,
        }

        # Attach brief-level fields when available via hiring_signal_brief_ref
        if prospect.hiring_signal_brief_ref:
            properties["last_enriched_at"] = prospect.hiring_signal_brief_ref

        payload = {"properties": properties}

        # HubSpot upsert: CRM v3 batch upsert by email (idProperty=email)
        path = "/crm/v3/objects/contacts/batch/upsert"
        upsert_payload = {
            "inputs": [
                {
                    "idProperty": "email",
                    "id": prospect.email,
                    "properties": properties,
                }
            ]
        }
        data = await self._call("POST", path, upsert_payload, operation="upsert_contact")

        # Batch upsert returns {"results": [{"id": "...", ...}]}
        results = data.get("results", [])
        contact_id = str(results[0].get("id", "")) if results else str(data.get("id", ""))
        logger.info(
            "Upserted HubSpot contact: prospect_id=%s contact_id=%s",
            prospect.prospect_id,
            contact_id,
        )
        return contact_id

    async def log_activity(self, activity: dict[str, Any], hs_contact_id: str = "") -> str:
        """
        Write a HubSpot activity record for an outbound send or inbound reply.

        Records channel, timestamp, and message content (Req 10.2).

        Args:
            activity: Dict containing at minimum:
                - ``type``: activity type string (e.g. ``"outbound_email"``)
                - ``prospect_id``: UUID of the prospect
                - ``channel``: ``"email"``, ``"sms"``, or ``"voice"``
                - ``timestamp``: ISO 8601 datetime string
                - ``content``: message body or summary
            hs_contact_id: HubSpot contact ID to associate the engagement with.

        Returns:
            The HubSpot engagement ID string.
        """
        engagement_type = _map_activity_type(activity.get("type", ""))

        associations: dict[str, Any] = {}
        if hs_contact_id:
            associations["contactIds"] = [int(hs_contact_id)]

        payload = {
            "engagement": {
                "active": True,
                "type": engagement_type,
                "timestamp": _iso_to_epoch_ms(activity.get("timestamp", "")),
            },
            "associations": associations,
            "metadata": {
                "body": activity.get("content", ""),
                "channel": activity.get("channel", ""),
                "prospect_id": activity.get("prospect_id", ""),
                "direction": activity.get("direction", "outbound"),
                **{k: v for k, v in activity.items() if k not in {
                    "type", "prospect_id", "channel", "timestamp", "content", "direction"
                }},
            },
        }

        data = await self._call(
            "POST",
            "/engagements/v1/engagements",
            payload,
            operation="log_activity",
        )

        engagement_id = str(data.get("engagement", {}).get("id", ""))
        logger.info(
            "Logged HubSpot activity: prospect_id=%s type=%s engagement_id=%s",
            activity.get("prospect_id"),
            activity.get("type"),
            engagement_id,
        )
        return engagement_id

    async def write_brief(
        self,
        brief: HiringSignalBrief | CompetitorGapBrief,
        hs_contact_id: str = "",
    ) -> str:
        """
        Write a HiringSignalBrief or CompetitorGapBrief as a HubSpot note.

        Records enrichment timestamps alongside the serialised brief (Req 10.3).

        Args:
            brief: A validated ``HiringSignalBrief`` or ``CompetitorGapBrief``
                instance.
            hs_contact_id: HubSpot contact ID to associate the note with.

        Returns:
            The HubSpot engagement ID string for the created note.
        """
        if isinstance(brief, HiringSignalBrief):
            brief_type = "HiringSignalBrief"
            enriched_at = brief.last_enriched_at
            company_id = brief.company_id
        else:
            brief_type = "CompetitorGapBrief"
            enriched_at = brief.generated_at
            company_id = brief.company_id

        associations: dict[str, Any] = {}
        if hs_contact_id:
            associations["contactIds"] = [int(hs_contact_id)]

        payload = {
            "engagement": {
                "active": True,
                "type": "NOTE",
                "timestamp": _iso_to_epoch_ms(enriched_at),
            },
            "associations": associations,
            "metadata": {
                "body": json.dumps(brief.model_dump(), default=str, indent=2),
                "brief_type": brief_type,
                "company_id": company_id,
                "enriched_at": enriched_at,
                "schema_version": brief.schema_version,
            },
        }

        data = await self._call(
            "POST",
            "/engagements/v1/engagements",
            payload,
            operation=f"write_brief:{brief_type}",
        )

        engagement_id = str(data.get("engagement", {}).get("id", ""))
        logger.info(
            "Wrote %s to HubSpot: company_id=%s engagement_id=%s",
            brief_type,
            company_id,
            engagement_id,
        )
        return engagement_id

    async def write_booking(self, booking: Booking) -> str:
        """
        Write a confirmed Cal.com booking record to HubSpot.

        Records ``cal_event_id``, prospect segment, and ``HiringSignalBrief``
        reference (Req 10.3, 9.5).

        Args:
            booking: The confirmed ``Booking`` dataclass from the Booking Agent.

        Returns:
            The HubSpot engagement ID string for the created meeting record.
        """
        payload = {
            "engagement": {
                "active": True,
                "type": "MEETING",
                "timestamp": int(time.time() * 1000),
            },
            "associations": {},
            "metadata": {
                "body": (
                    f"Discovery call booked via Cal.com.\n"
                    f"Event ID: {booking.cal_event_id}\n"
                    f"Segment: {booking.segment.value}\n"
                    f"Brief ref: {booking.brief_ref}"
                ),
                "cal_event_id": booking.cal_event_id,
                "prospect_id": booking.prospect_id,
                "icp_segment": booking.segment.value,
                "hiring_signal_brief_ref": booking.brief_ref,
            },
        }

        data = await self._call(
            "POST",
            "/engagements/v1/engagements",
            payload,
            operation="write_booking",
        )

        engagement_id = str(data.get("engagement", {}).get("id", ""))
        logger.info(
            "Wrote booking to HubSpot: prospect_id=%s cal_event_id=%s engagement_id=%s",
            booking.prospect_id,
            booking.cal_event_id,
            engagement_id,
        )
        return engagement_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _map_activity_type(activity_type: str) -> str:
    """
    Map an internal activity type string to a HubSpot engagement type.

    Args:
        activity_type: Internal type string (e.g. ``"outbound_email"``).

    Returns:
        HubSpot engagement type string (e.g. ``"EMAIL"``).
    """
    mapping = {
        "outbound_email": "EMAIL",
        "inbound_email": "EMAIL",
        "outbound_sms": "NOTE",
        "inbound_sms": "NOTE",
        "outbound_voice": "CALL",
        "inbound_voice": "CALL",
        "booking_failure": "NOTE",
    }
    return mapping.get(activity_type, "NOTE")


def _iso_to_epoch_ms(iso_str: str) -> int:
    """
    Convert an ISO 8601 datetime string to a Unix epoch millisecond integer.

    Falls back to the current time when the string is empty or unparseable.

    Args:
        iso_str: ISO 8601 datetime string (e.g. ``"2025-05-01T14:00:00Z"``).

    Returns:
        Unix epoch time in milliseconds.
    """
    if not iso_str:
        return int(time.time() * 1000)
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return int(dt.astimezone(timezone.utc).timestamp() * 1000)
    except ValueError:
        return int(time.time() * 1000)
