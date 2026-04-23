"""
Booking Agent — Cal.com integration for discovery call scheduling.

Provides slot querying and booking creation against a self-hosted Cal.com
instance, with timezone conversion, minimum-slot enforcement, and a
one-retry policy on booking failure.

Requirements: 9.1, 9.2, 9.3, 9.4, 9.5
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx

from config.models import Booking, Prospect, Segment, Slot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Timezone support
# ---------------------------------------------------------------------------

# Canonical IANA zone names for each supported region.
# Callers may pass any IANA zone string; these are the documented defaults.
SUPPORTED_ZONES: dict[str, str] = {
    # EU
    "EU": "Europe/Paris",
    "CET": "Europe/Paris",
    "CEST": "Europe/Paris",
    "Europe/Paris": "Europe/Paris",
    "Europe/Berlin": "Europe/Berlin",
    "Europe/London": "Europe/London",
    # US
    "US": "America/New_York",
    "ET": "America/New_York",
    "CT": "America/Chicago",
    "PT": "America/Los_Angeles",
    "America/New_York": "America/New_York",
    "America/Chicago": "America/Chicago",
    "America/Los_Angeles": "America/Los_Angeles",
    # East Africa
    "EAT": "Africa/Nairobi",
    "Africa/Nairobi": "Africa/Nairobi",
}

_SLOT_DISPLAY_FMT = "%a %d %b %Y %H:%M %Z"


def resolve_iana_zone(tz: str) -> str:
    """
    Resolve a region shorthand or IANA zone string to a canonical IANA name.

    Falls back to the raw ``tz`` value when not found in the lookup table,
    allowing any valid IANA zone string to pass through.

    Args:
        tz: A region shorthand (e.g. ``"EU"``, ``"ET"``) or IANA zone string
            (e.g. ``"America/New_York"``).

    Returns:
        A canonical IANA timezone string.
    """
    return SUPPORTED_ZONES.get(tz, tz)


def utc_to_local_display(utc_iso: str, tz: str) -> str:
    """
    Convert a UTC ISO 8601 datetime string to a human-readable local string.

    Args:
        utc_iso: UTC datetime in ISO 8601 format (e.g. ``"2025-05-01T14:00:00Z"``).
        tz: IANA timezone string or supported region shorthand.

    Returns:
        A formatted string representing the same instant in the prospect's
        local timezone (e.g. ``"Thu 01 May 2025 16:00 CEST"``).
    """
    iana = resolve_iana_zone(tz)
    zone = ZoneInfo(iana)
    # Parse — handle both "Z" suffix and "+00:00" offset
    utc_str = utc_iso.replace("Z", "+00:00")
    dt_utc = datetime.fromisoformat(utc_str).astimezone(timezone.utc)
    dt_local = dt_utc.astimezone(zone)
    return dt_local.strftime(_SLOT_DISPLAY_FMT)


# ---------------------------------------------------------------------------
# CalConfig
# ---------------------------------------------------------------------------


@dataclass
class CalConfig:
    """
    Configuration for the Cal.com REST API client.

    Attributes:
        cal_base_url: Base URL of the self-hosted Cal.com instance
            (e.g. ``"http://localhost:3000"``).
        cal_api_key: API key for authenticating Cal.com requests.
        event_type_id: Cal.com event type ID to query/book.
    """

    cal_base_url: str
    cal_api_key: str
    event_type_id: int = 1

    @classmethod
    def from_env(cls) -> CalConfig:
        """Build a ``CalConfig`` from environment variables."""
        return cls(
            cal_base_url=os.environ.get("CAL_BASE_URL", "https://api.cal.eu"),
            cal_api_key=os.environ["CAL_API_KEY"],
            event_type_id=int(os.environ.get("CAL_EVENT_TYPE_ID", "268206")),
        )


# ---------------------------------------------------------------------------
# CRMWriter protocol — allows the booking agent to notify the delivery lead
# without a hard dependency on crm_writer (task 8 may not be implemented yet).
# ---------------------------------------------------------------------------


class CRMWriterProtocol(Protocol):
    """Minimal interface the Booking Agent needs from the CRM Writer."""

    async def log_activity(self, activity: dict[str, Any]) -> str:
        """Log an activity record; returns the HubSpot activity ID."""
        ...


class _NullCRMWriter:
    """
    No-op CRM writer used when no real CRMWriter is available.

    Logs a warning instead of writing to HubSpot.
    """

    async def log_activity(self, activity: dict[str, Any]) -> str:
        """
        Log a warning and return a placeholder ID.

        Args:
            activity: The activity payload that would have been written.

        Returns:
            A placeholder string ``"noop"``.
        """
        logger.warning(
            "CRMWriter not available — activity not written to HubSpot: %s",
            activity,
        )
        return "noop"


# ---------------------------------------------------------------------------
# Slot helpers
# ---------------------------------------------------------------------------

_MIN_SLOTS = 3


def _parse_slots(raw_slots: list[dict[str, Any]], tz: str) -> list[Slot]:
    """
    Convert raw Cal.com slot dicts to ``Slot`` dataclass instances.

    Args:
        raw_slots: List of slot dicts from the Cal.com API response.
        tz: Prospect timezone (IANA or shorthand) for ``local_display``.

    Returns:
        List of ``Slot`` instances with ``local_display`` set.
    """
    slots: list[Slot] = []
    for raw in raw_slots:
        start = raw.get("start", raw.get("time", ""))
        # Cal.com v1 slots endpoint returns {"time": "..."} per slot
        # Derive end time: assume 30-minute slots when not provided
        end = raw.get("endTime", "")
        if not end and start:
            dt_start = datetime.fromisoformat(start.replace("Z", "+00:00"))
            from datetime import timedelta
            dt_end = dt_start + timedelta(minutes=30)
            end = dt_end.isoformat()
        slots.append(
            Slot(
                start_utc=start,
                end_utc=end,
                local_display=utc_to_local_display(start, tz) if start else "",
            )
        )
    return slots


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_available_slots(cal_config: CalConfig, tz: str) -> list[Slot]:
    """
    Query Cal.com for available booking slots and return them in prospect tz.

    Enforces the minimum-3-slots rule (Req 9.1): returns the actual list
    from Cal.com without fabrication. Callers should check ``len(slots) < 3``
    and surface the actual count to the prospect rather than fabricating slots.

    Args:
        cal_config: Cal.com connection configuration.
        tz: Prospect timezone — IANA string or supported shorthand
            (``"EU"``, ``"ET"``, ``"CT"``, ``"PT"``, ``"EAT"``).

    Returns:
        List of ``Slot`` instances converted to the prospect's local timezone.
        May contain fewer than 3 items when Cal.com has limited availability;
        callers MUST NOT fabricate additional slots.
    """
    today = datetime.now(timezone.utc).date()
    # Query a 14-day window to maximise slot availability
    from datetime import timedelta
    end_date = today + timedelta(days=14)

    params = {
        "apiKey": cal_config.cal_api_key,
        "eventTypeId": cal_config.event_type_id,
        "startTime": today.isoformat(),
        "endTime": end_date.isoformat(),
        "timeZone": resolve_iana_zone(tz),
    }

    # Cal.com v2 API
    url = f"{cal_config.cal_base_url}/v2/slots"
    headers = {
        "cal-api-key": cal_config.cal_api_key,
        "cal-api-version": "2024-09-04",
    }
    params = {
        "eventTypeId": cal_config.event_type_id,
        "start": f"{today.isoformat()}T00:00:00Z",
        "end": f"{end_date.isoformat()}T23:59:59Z",
        "timeZone": resolve_iana_zone(tz),
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()

    # Cal.com v2 actual response: {"data": {"YYYY-MM-DD": [{"start": "..."}, ...], ...}}
    # The data dict maps dates directly to slot lists (no nested "slots" key)
    raw_slots: list[dict[str, Any]] = []
    slots_data = data.get("data", data)
    if isinstance(slots_data, dict):
        for val in slots_data.values():
            if isinstance(val, list):
                raw_slots.extend(val)
            elif isinstance(val, dict):
                # nested slots key fallback
                for day_slots in val.values():
                    if isinstance(day_slots, list):
                        raw_slots.extend(day_slots)

    slots = _parse_slots(raw_slots, tz)

    if len(slots) < _MIN_SLOTS:
        logger.warning(
            "Cal.com returned only %d slot(s) — fewer than the minimum %d. "
            "Surfacing actual count; no fabrication.",
            len(slots),
            _MIN_SLOTS,
        )

    return slots


async def create_booking(
    slot: Slot,
    prospect: Prospect,
    brief_ref: str,
    cal_config: CalConfig | None = None,
    crm_writer: CRMWriterProtocol | None = None,
) -> Booking:
    """
    Create a confirmed booking in Cal.com for the given slot and prospect.

    Implements a one-retry policy (Req 9.4):
    - First failure → retry once.
    - Second failure → notify delivery lead via CRM Writer and inform prospect
      by raising ``BookingFailedError``.

    Args:
        slot: The ``Slot`` selected by the prospect.
        prospect: The ``Prospect`` dataclass with contact details.
        brief_ref: Reference string combining ``company_id`` and
            ``last_enriched_at`` from the ``HiringSignalBrief``.
        cal_config: Cal.com configuration. Falls back to ``CalConfig.from_env()``
            when ``None``.
        crm_writer: CRM Writer instance for delivery-lead notification.
            Falls back to a no-op writer when ``None``.

    Returns:
        A ``Booking`` dataclass with the confirmed Cal.com event ID.

    Raises:
        BookingFailedError: When both the initial attempt and the single retry
            fail. The delivery lead has been notified via CRM before raising.
    """
    if cal_config is None:
        cal_config = CalConfig.from_env()
    if crm_writer is None:
        crm_writer = _NullCRMWriter()

    payload = {
        "eventTypeId": cal_config.event_type_id,
        "start": slot.start_utc,
        "attendee": {
            "name": prospect.contact_name,
            "email": prospect.email,
            "timeZone": resolve_iana_zone(prospect.timezone),
            "language": "en",
        },
        "metadata": {
            "prospect_id": prospect.prospect_id,
            "brief_ref": brief_ref,
        },
    }

    url = f"{cal_config.cal_base_url}/v2/bookings"
    headers = {
        "cal-api-key": cal_config.cal_api_key,
        "cal-api-version": "2024-08-13",
    }

    last_exc: Exception | None = None

    for attempt in range(2):  # attempt 0 = first try, attempt 1 = retry
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()

            cal_event_id = str(
                data.get("data", {}).get("uid")
                or data.get("data", {}).get("id")
                or data.get("uid")
                or data.get("id")
                or data.get("bookingId", "")
            )
            segment = prospect.segment or Segment.UNQUALIFIED

            logger.info(
                "Booking created: cal_event_id=%s prospect_id=%s attempt=%d",
                cal_event_id,
                prospect.prospect_id,
                attempt + 1,
            )

            return Booking(
                cal_event_id=cal_event_id,
                prospect_id=prospect.prospect_id,
                segment=segment,
                brief_ref=brief_ref,
            )

        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            last_exc = exc
            logger.warning(
                "Booking attempt %d failed for prospect %s: %s",
                attempt + 1,
                prospect.prospect_id,
                exc,
            )
            if attempt == 0:
                logger.info("Retrying booking for prospect %s…", prospect.prospect_id)
                # fall through to second attempt

    # Both attempts failed — notify delivery lead and inform prospect (Req 9.4)
    logger.error(
        "Both booking attempts failed for prospect %s. Notifying delivery lead.",
        prospect.prospect_id,
    )

    await crm_writer.log_activity(
        {
            "type": "booking_failure",
            "prospect_id": prospect.prospect_id,
            "brief_ref": brief_ref,
            "slot_start_utc": slot.start_utc,
            "message": (
                "Booking creation failed after 1 retry. "
                "Please follow up manually to schedule the discovery call."
            ),
        }
    )

    raise BookingFailedError(
        f"Cal.com booking failed after 1 retry for prospect "
        f"{prospect.prospect_id!r}. Delivery lead notified. "
        f"Inform prospect that a human will follow up to schedule. "
        f"Last error: {last_exc}"
    )


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class BookingFailedError(RuntimeError):
    """
    Raised when Cal.com booking creation fails after the single allowed retry.

    Callers should catch this and inform the prospect that a human will
    follow up to schedule the discovery call.
    """
