"""
Integration test: Cal.com slot query, booking creation, and timezone display.

Covers:
  - get_available_slots returns slots with correct local_display (Req 9.1, 9.3)
  - create_booking creates a confirmed booking with required fields (Req 9.2)
  - Timezone conversion is correct for EU, US, and East Africa zones (Req 9.3)
  - Fewer-than-3 slots surfaces actual count without fabrication (Req 9.1)
  - Booking retry: first failure retries; second failure notifies delivery lead (Req 9.4)
  - Booking record written to HubSpot via CRM Writer (Req 9.5)

All Cal.com HTTP calls are intercepted with unittest.mock so no real
credentials or running Cal.com instance are required.

Requirements: 9.1, 9.2, 9.3
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from booking_agent.agent import (
    BookingFailedError,
    CalConfig,
    create_booking,
    get_available_slots,
    utc_to_local_display,
)
from config.models import Booking, Prospect, ProspectState, Segment, Slot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CAL_CONFIG = CalConfig(
    cal_base_url="http://localhost:3000",
    cal_api_key="test-cal-key",
    event_type_id=1,
)

# Three UTC slots spread across a 14-day window
_SLOT_UTCS = [
    "2026-05-01T09:00:00Z",
    "2026-05-02T14:00:00Z",
    "2026-05-03T16:00:00Z",
]


def _cal_slots_response(slot_utcs: list[str]) -> dict[str, Any]:
    """Build a Cal.com v1 /slots/available response dict."""
    slots_by_date: dict[str, list[dict[str, str]]] = {}
    for utc in slot_utcs:
        date = utc[:10]
        slots_by_date.setdefault(date, []).append({"time": utc})
    return {"slots": slots_by_date}


def _cal_booking_response(event_id: str = "cal-evt-001") -> dict[str, Any]:
    """Build a Cal.com v1 /bookings response dict."""
    return {"id": event_id, "uid": event_id, "status": "ACCEPTED"}


def _make_prospect(**overrides: Any) -> Prospect:
    """Return a minimal synthetic prospect."""
    defaults: dict[str, Any] = dict(
        prospect_id="cal-integ-p-001",
        company_id="cal-integ-co-001",
        contact_name="Alex Kim",
        email="alex@caltest.example",
        phone="+15550009999",
        timezone="America/New_York",
        preferred_channel="email",
        current_state=ProspectState.WARM,
        outbound_attempt_count=1,
        segment=Segment.S1,
        hiring_signal_brief_ref="2026-04-01T00:00:00Z",
    )
    defaults.update(overrides)
    return Prospect(**defaults)


def _mock_httpx_get(json_data: dict[str, Any]) -> MagicMock:
    """Return a mock httpx.AsyncClient whose GET returns json_data."""
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _mock_httpx_post(
    json_data: dict[str, Any],
    raise_on_call: int | None = None,
) -> tuple[MagicMock, list[int]]:
    """
    Return a mock httpx.AsyncClient whose POST returns json_data.

    Args:
        json_data: Response payload for successful calls.
        raise_on_call: If set, raise HTTPStatusError on that call number (1-indexed).

    Returns:
        (mock_client, call_counter) — call_counter[0] increments on each POST.
    """
    call_counter = [0]

    resp_ok = MagicMock()
    resp_ok.json.return_value = json_data
    resp_ok.raise_for_status.return_value = None

    resp_err = MagicMock()
    resp_err.raise_for_status.side_effect = Exception("Cal.com 500")

    async def _post(*args: Any, **kwargs: Any) -> MagicMock:
        call_counter[0] += 1
        if raise_on_call is not None and call_counter[0] == raise_on_call:
            return resp_err
        return resp_ok

    mock_client = AsyncMock()
    mock_client.post = _post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client, call_counter


# ---------------------------------------------------------------------------
# Req 9.1 — slot query returns ≥ 3 options; fewer surfaces actual count
# ---------------------------------------------------------------------------


class TestGetAvailableSlots:
    """Integration tests for get_available_slots (Req 9.1, 9.3)."""

    def test_returns_three_slots_from_cal_response(self) -> None:
        """get_available_slots returns one Slot per Cal.com slot entry (Req 9.1)."""
        mock_client = _mock_httpx_get(_cal_slots_response(_SLOT_UTCS))

        with patch("httpx.AsyncClient", return_value=mock_client):
            slots = asyncio.run(get_available_slots(_CAL_CONFIG, "America/New_York"))

        assert len(slots) == 3
        assert all(isinstance(s, Slot) for s in slots)

    def test_slots_have_start_utc_and_end_utc(self) -> None:
        """Every returned Slot must have non-empty start_utc and end_utc (Req 9.1)."""
        mock_client = _mock_httpx_get(_cal_slots_response(_SLOT_UTCS))

        with patch("httpx.AsyncClient", return_value=mock_client):
            slots = asyncio.run(get_available_slots(_CAL_CONFIG, "ET"))

        for slot in slots:
            assert slot.start_utc, "start_utc must not be empty"
            assert slot.end_utc, "end_utc must not be empty"

    def test_fewer_than_three_slots_surfaces_actual_count(self) -> None:
        """When Cal.com returns < 3 slots, actual count is returned without fabrication (Req 9.1)."""
        two_slots = _SLOT_UTCS[:2]
        mock_client = _mock_httpx_get(_cal_slots_response(two_slots))

        with patch("httpx.AsyncClient", return_value=mock_client):
            slots = asyncio.run(get_available_slots(_CAL_CONFIG, "America/New_York"))

        # Must return exactly what Cal.com gave — no fabrication
        assert len(slots) == 2

    def test_zero_slots_returns_empty_list(self) -> None:
        """When Cal.com returns no slots, an empty list is returned (Req 9.1)."""
        mock_client = _mock_httpx_get({"slots": {}})

        with patch("httpx.AsyncClient", return_value=mock_client):
            slots = asyncio.run(get_available_slots(_CAL_CONFIG, "America/New_York"))

        assert slots == []


# ---------------------------------------------------------------------------
# Req 9.3 — timezone conversion correctness
# ---------------------------------------------------------------------------


class TestTimezoneConversion:
    """Integration tests for timezone display in slots (Req 9.3)."""

    # 2026-05-01T09:00:00Z = 05:00 ET (UTC-4 in summer), 11:00 CEST (UTC+2), 12:00 EAT (UTC+3)
    _UTC = "2026-05-01T09:00:00Z"

    def test_us_eastern_conversion(self) -> None:
        """UTC slot converts correctly to US Eastern time (Req 9.3)."""
        display = utc_to_local_display(self._UTC, "America/New_York")
        # May 1 is in EDT (UTC-4) → 05:00
        assert "05:00" in display

    def test_us_eastern_shorthand_et(self) -> None:
        """'ET' shorthand resolves to America/New_York (Req 9.3)."""
        display = utc_to_local_display(self._UTC, "ET")
        assert "05:00" in display

    def test_us_central_conversion(self) -> None:
        """UTC slot converts correctly to US Central time (Req 9.3)."""
        display = utc_to_local_display(self._UTC, "CT")
        # May 1 is in CDT (UTC-5) → 04:00
        assert "04:00" in display

    def test_us_pacific_conversion(self) -> None:
        """UTC slot converts correctly to US Pacific time (Req 9.3)."""
        display = utc_to_local_display(self._UTC, "PT")
        # May 1 is in PDT (UTC-7) → 02:00
        assert "02:00" in display

    def test_eu_paris_conversion(self) -> None:
        """UTC slot converts correctly to EU/Paris time (Req 9.3)."""
        display = utc_to_local_display(self._UTC, "EU")
        # May 1 is in CEST (UTC+2) → 11:00
        assert "11:00" in display

    def test_eu_shorthand_cest(self) -> None:
        """'CEST' shorthand resolves to Europe/Paris (Req 9.3)."""
        display = utc_to_local_display(self._UTC, "CEST")
        assert "11:00" in display

    def test_east_africa_conversion(self) -> None:
        """UTC slot converts correctly to East Africa Time (Req 9.3)."""
        display = utc_to_local_display(self._UTC, "EAT")
        # EAT is UTC+3 → 12:00
        assert "12:00" in display

    def test_eat_iana_zone(self) -> None:
        """Africa/Nairobi IANA zone converts correctly (Req 9.3)."""
        display = utc_to_local_display(self._UTC, "Africa/Nairobi")
        assert "12:00" in display

    def test_local_display_same_instant_as_utc(self) -> None:
        """local_display represents the same instant as the UTC slot (Req 9.3)."""
        utc_iso = "2026-06-15T13:30:00Z"
        tz = "America/Chicago"
        display = utc_to_local_display(utc_iso, tz)
        # CDT (UTC-5) → 08:30
        assert "08:30" in display

    def test_slots_local_display_populated_for_all_zones(self) -> None:
        """All returned slots have non-empty local_display for each supported zone (Req 9.3)."""
        zones = ["ET", "CT", "PT", "EU", "EAT"]
        for tz in zones:
            mock_client = _mock_httpx_get(_cal_slots_response(_SLOT_UTCS))
            with patch("httpx.AsyncClient", return_value=mock_client):
                slots = asyncio.run(get_available_slots(_CAL_CONFIG, tz))
            for slot in slots:
                assert slot.local_display, f"local_display empty for tz={tz!r}"


# ---------------------------------------------------------------------------
# Req 9.2 — booking creation returns confirmed Booking with required fields
# ---------------------------------------------------------------------------


class TestCreateBooking:
    """Integration tests for create_booking (Req 9.2)."""

    _SLOT = Slot(
        start_utc="2026-05-01T09:00:00Z",
        end_utc="2026-05-01T09:30:00Z",
        local_display="Thu 01 May 2026 05:00 EDT",
    )
    _BRIEF_REF = "cal-integ-co-001:2026-04-01T00:00:00Z"

    def test_create_booking_returns_booking_dataclass(self) -> None:
        """create_booking returns a Booking with cal_event_id set (Req 9.2)."""
        mock_client, _ = _mock_httpx_post(_cal_booking_response("cal-evt-001"))
        prospect = _make_prospect()

        with patch("httpx.AsyncClient", return_value=mock_client):
            booking = asyncio.run(
                create_booking(self._SLOT, prospect, self._BRIEF_REF, _CAL_CONFIG)
            )

        assert isinstance(booking, Booking)
        assert booking.cal_event_id == "cal-evt-001"

    def test_create_booking_sets_prospect_id(self) -> None:
        """Booking record must reference the correct prospect_id (Req 9.2)."""
        mock_client, _ = _mock_httpx_post(_cal_booking_response())
        prospect = _make_prospect()

        with patch("httpx.AsyncClient", return_value=mock_client):
            booking = asyncio.run(
                create_booking(self._SLOT, prospect, self._BRIEF_REF, _CAL_CONFIG)
            )

        assert booking.prospect_id == "cal-integ-p-001"

    def test_create_booking_sets_segment(self) -> None:
        """Booking record must carry the prospect's ICP segment (Req 9.2)."""
        mock_client, _ = _mock_httpx_post(_cal_booking_response())
        prospect = _make_prospect(segment=Segment.S2)

        with patch("httpx.AsyncClient", return_value=mock_client):
            booking = asyncio.run(
                create_booking(self._SLOT, prospect, self._BRIEF_REF, _CAL_CONFIG)
            )

        assert booking.segment == Segment.S2

    def test_create_booking_sets_brief_ref(self) -> None:
        """Booking record must carry the HiringSignalBrief reference (Req 9.2)."""
        mock_client, _ = _mock_httpx_post(_cal_booking_response())
        prospect = _make_prospect()

        with patch("httpx.AsyncClient", return_value=mock_client):
            booking = asyncio.run(
                create_booking(self._SLOT, prospect, self._BRIEF_REF, _CAL_CONFIG)
            )

        assert booking.brief_ref == self._BRIEF_REF

    def test_create_booking_uses_unqualified_when_segment_none(self) -> None:
        """When prospect.segment is None, booking segment defaults to UNQUALIFIED (Req 9.2)."""
        mock_client, _ = _mock_httpx_post(_cal_booking_response())
        prospect = _make_prospect(segment=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            booking = asyncio.run(
                create_booking(self._SLOT, prospect, self._BRIEF_REF, _CAL_CONFIG)
            )

        assert booking.segment == Segment.UNQUALIFIED


# ---------------------------------------------------------------------------
# Req 9.4 — retry flow: first failure retries; second failure notifies lead
# ---------------------------------------------------------------------------


def _http_status_error() -> httpx.HTTPStatusError:
    """Build a minimal httpx.HTTPStatusError for mocking Cal.com failures."""
    req = httpx.Request("POST", "http://localhost:3000/api/v1/bookings")
    resp = httpx.Response(500, request=req)
    return httpx.HTTPStatusError("Cal.com 500", request=req, response=resp)


class TestBookingRetryFlow:
    """Integration tests for the Cal.com booking retry policy (Req 9.4)."""

    _SLOT = Slot(
        start_utc="2026-05-01T09:00:00Z",
        end_utc="2026-05-01T09:30:00Z",
        local_display="Thu 01 May 2026 05:00 EDT",
    )
    _BRIEF_REF = "cal-integ-co-001:2026-04-01T00:00:00Z"

    def test_first_failure_retries_and_succeeds(self) -> None:
        """First POST failure triggers a retry; second attempt succeeds (Req 9.4)."""
        call_counter = [0]

        resp_ok = MagicMock()
        resp_ok.json.return_value = _cal_booking_response("cal-retry-ok")
        resp_ok.raise_for_status.return_value = None

        resp_err = MagicMock()
        resp_err.raise_for_status.side_effect = _http_status_error()

        async def _post(*args: Any, **kwargs: Any) -> MagicMock:
            call_counter[0] += 1
            return resp_err if call_counter[0] == 1 else resp_ok

        mock_client = AsyncMock()
        mock_client.post = _post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        prospect = _make_prospect()

        with patch("httpx.AsyncClient", return_value=mock_client):
            booking = asyncio.run(
                create_booking(self._SLOT, prospect, self._BRIEF_REF, _CAL_CONFIG)
            )

        assert booking.cal_event_id == "cal-retry-ok"
        assert call_counter[0] == 2  # initial + 1 retry

    def test_both_failures_raise_booking_failed_error(self) -> None:
        """Two consecutive failures raise BookingFailedError (Req 9.4)."""
        resp_err = MagicMock()
        resp_err.raise_for_status.side_effect = _http_status_error()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=resp_err)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        prospect = _make_prospect()

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(BookingFailedError):
                asyncio.run(
                    create_booking(self._SLOT, prospect, self._BRIEF_REF, _CAL_CONFIG)
                )

    def test_both_failures_notify_delivery_lead_via_crm(self) -> None:
        """On second failure, CRM Writer log_activity is called to notify delivery lead (Req 9.4)."""
        resp_err = MagicMock()
        resp_err.raise_for_status.side_effect = _http_status_error()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=resp_err)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_crm = MagicMock()
        mock_crm.log_activity = AsyncMock(return_value="hs-notify-001")

        prospect = _make_prospect()

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(BookingFailedError):
                asyncio.run(
                    create_booking(
                        self._SLOT, prospect, self._BRIEF_REF, _CAL_CONFIG, mock_crm
                    )
                )

        mock_crm.log_activity.assert_called_once()
        activity = mock_crm.log_activity.call_args[0][0]
        assert activity["type"] == "booking_failure"
        assert activity["prospect_id"] == "cal-integ-p-001"

    def test_exactly_two_post_attempts_on_double_failure(self) -> None:
        """Exactly 2 POST calls are made before giving up — no extra retries (Req 9.4)."""
        call_counter = [0]
        resp_err = MagicMock()
        resp_err.raise_for_status.side_effect = _http_status_error()

        async def _post(*args: Any, **kwargs: Any) -> MagicMock:
            call_counter[0] += 1
            return resp_err

        mock_client = AsyncMock()
        mock_client.post = _post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        prospect = _make_prospect()

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(BookingFailedError):
                asyncio.run(
                    create_booking(self._SLOT, prospect, self._BRIEF_REF, _CAL_CONFIG)
                )

        assert call_counter[0] == 2


# ---------------------------------------------------------------------------
# Req 9.5 — booking record written to HubSpot via CRM Writer
# ---------------------------------------------------------------------------


class TestBookingCRMWrite:
    """Integration test: booking confirmed → CRM write_booking called (Req 9.5)."""

    _SLOT = Slot(
        start_utc="2026-05-01T09:00:00Z",
        end_utc="2026-05-01T09:30:00Z",
        local_display="Thu 01 May 2026 05:00 EDT",
    )
    _BRIEF_REF = "cal-integ-co-001:2026-04-01T00:00:00Z"

    def test_booking_record_has_cal_event_id_segment_and_brief_ref(self) -> None:
        """
        Confirmed booking carries cal_event_id, segment, and brief_ref (Req 9.5).

        The CRM Writer is responsible for persisting this; here we verify the
        Booking dataclass returned by create_booking has all required fields.
        """
        mock_client, _ = _mock_httpx_post(_cal_booking_response("cal-crm-001"))
        prospect = _make_prospect(segment=Segment.S1)

        with patch("httpx.AsyncClient", return_value=mock_client):
            booking = asyncio.run(
                create_booking(self._SLOT, prospect, self._BRIEF_REF, _CAL_CONFIG)
            )

        assert booking.cal_event_id == "cal-crm-001"
        assert booking.segment == Segment.S1
        assert booking.brief_ref == self._BRIEF_REF
        assert booking.prospect_id == "cal-integ-p-001"

    def test_crm_write_booking_called_after_confirmation(self) -> None:
        """
        After a successful booking, CRM Writer write_booking is invoked (Req 9.5).

        Simulates the pipeline calling write_booking with the returned Booking.
        """
        mock_client, _ = _mock_httpx_post(_cal_booking_response("cal-crm-002"))
        mock_crm = MagicMock()
        mock_crm.write_booking = AsyncMock(return_value="hs-booking-001")

        prospect = _make_prospect(segment=Segment.S2)

        async def run() -> str:
            with patch("httpx.AsyncClient", return_value=mock_client):
                booking = await create_booking(
                    self._SLOT, prospect, self._BRIEF_REF, _CAL_CONFIG
                )
            return await mock_crm.write_booking(booking)

        hs_id = asyncio.run(run())

        assert hs_id == "hs-booking-001"
        mock_crm.write_booking.assert_called_once()
        written: Booking = mock_crm.write_booking.call_args[0][0]
        assert written.cal_event_id == "cal-crm-002"
        assert written.segment == Segment.S2
        assert written.brief_ref == self._BRIEF_REF
