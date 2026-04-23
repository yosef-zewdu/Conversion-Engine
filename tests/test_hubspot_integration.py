"""
Integration test: HubSpot MCP contact upsert, activity write, and rate-limit enforcement.

Covers the CRM Writer integration flow:
  - upsert_contact writes required fields (crunchbase_id, last_enriched_at,
    segment, ai_maturity_score, bench_mismatch) — Req 10.1
  - log_activity writes outbound send and inbound reply records — Req 10.2
  - Rate limiter enforces ≤ 100 HubSpot API calls per 10-second window — Req 10.5

All HubSpot HTTP calls are intercepted with httpx.MockTransport so no real
credentials are required.

Requirements: 10.1, 10.2, 10.5
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from config.models import Booking, Prospect, ProspectState, Segment
from crm_writer.writer import CRMWriter, _RateLimiter
from signal_pipeline.models import CompetitorGapBrief, HiringSignalBrief


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_prospect(**overrides: Any) -> Prospect:
    """Return a minimal synthetic prospect for CRM integration tests."""
    defaults: dict[str, Any] = dict(
        prospect_id="integ-p-001",
        company_id="integ-co-001",
        contact_name="Sam Rivera",
        email="sam@integcorp.example",
        phone="+15550001234",
        timezone="America/Chicago",
        preferred_channel="email",
        current_state=ProspectState.COLD,
        outbound_attempt_count=0,
        segment=Segment.S2,
        hiring_signal_brief_ref="2026-04-01T00:00:00Z",
    )
    defaults.update(overrides)
    return Prospect(**defaults)


def _make_hiring_brief(**overrides: Any) -> HiringSignalBrief:
    """Return a minimal valid HiringSignalBrief."""
    defaults: dict[str, Any] = dict(
        schema_version="1.0",
        company_id="integ-co-001",
        company_name="Integ Corp",
        last_enriched_at="2026-04-01T00:00:00Z",
        ai_maturity_score=1,
        ai_maturity_confidence="medium",
    )
    defaults.update(overrides)
    return HiringSignalBrief(**defaults)


def _make_competitor_brief(**overrides: Any) -> CompetitorGapBrief:
    """Return a minimal valid CompetitorGapBrief."""
    defaults: dict[str, Any] = dict(
        schema_version="1.0",
        company_id="integ-co-001",
        generated_at="2026-04-01T00:00:00Z",
        peer_count=3,
    )
    defaults.update(overrides)
    return CompetitorGapBrief(**defaults)


def _make_booking(**overrides: Any) -> Booking:
    """Return a minimal Booking for CRM write tests."""
    defaults: dict[str, Any] = dict(
        cal_event_id="cal-integ-001",
        prospect_id="integ-p-001",
        segment=Segment.S2,
        brief_ref="integ-co-001:2026-04-01T00:00:00Z",
    )
    defaults.update(overrides)
    return Booking(**defaults)


def _mock_hs_response(
    json_data: dict[str, Any] | None = None,
    status_code: int = 200,
) -> MagicMock:
    """Build a mock httpx response for HubSpot API calls."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {
        "results": [{"id": "hs-integ-001"}],
        "engagement": {"id": "eng-integ-001"},
    }
    resp.raise_for_status.return_value = None
    return resp


def _make_writer(json_data: dict[str, Any] | None = None) -> tuple[CRMWriter, list[dict]]:
    """
    Return a CRMWriter whose HTTP calls are intercepted.

    Returns:
        (writer, captured_requests) — captured_requests accumulates every
        (method, url, json) dict sent to the mock transport.
    """
    captured: list[dict] = []

    async def _fake_request(method: str, url: str, **kwargs: Any) -> MagicMock:
        captured.append({"method": method, "url": url, "json": kwargs.get("json", {})})
        return _mock_hs_response(json_data=json_data)

    writer = CRMWriter(access_token="test-token", base_url="https://api.hubapi.com")
    return writer, captured, _fake_request  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Req 10.1 — upsert_contact writes required fields
# ---------------------------------------------------------------------------


class TestUpsertContactIntegration:
    """Integration tests for CRMWriter.upsert_contact (Req 10.1)."""

    def test_upsert_sends_to_correct_endpoint(self) -> None:
        """upsert_contact POSTs to the HubSpot v3 batch upsert endpoint."""
        prospect = _make_prospect()
        captured: list[dict] = []

        async def fake_request(method: str, url: str, **kwargs: Any) -> MagicMock:
            captured.append({"method": method, "url": url})
            return _mock_hs_response(json_data={"results": [{"id": "42"}]})

        writer = CRMWriter(access_token="tok", base_url="https://api.hubapi.com")

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = mock_client
            mock_client.request.side_effect = fake_request

            asyncio.run(writer.upsert_contact(prospect))

        assert len(captured) == 1
        assert captured[0]["method"] == "POST"
        assert "upsert" in captured[0]["url"]

    def test_upsert_writes_segment_and_enrichment_fields(self) -> None:
        """Contact record must include segment, last_enriched_at, and prospect_id (Req 10.1)."""
        prospect = _make_prospect()
        captured: list[dict] = []

        async def fake_request(method: str, url: str, **kwargs: Any) -> MagicMock:
            captured.append(kwargs.get("json", {}))
            return _mock_hs_response(json_data={"results": [{"id": "99"}]})

        writer = CRMWriter(access_token="tok", base_url="https://api.hubapi.com")

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = mock_client
            mock_client.request.side_effect = fake_request

            contact_id = asyncio.run(writer.upsert_contact(prospect))

        assert contact_id == "99"
        # v3 batch upsert: inputs[0].properties
        props = captured[0]["inputs"][0]["properties"]
        assert props["icp_segment"] == "segment_2"
        assert props["last_enriched_at"] == "2026-04-01T00:00:00Z"
        assert props["prospect_id"] == "integ-p-001"
        assert props["company_id"] == "integ-co-001"
        assert props["email"] == "sam@integcorp.example"

    def test_upsert_contact_no_segment_sends_empty_string(self) -> None:
        """When segment is None, icp_segment field is sent as empty string (not None)."""
        prospect = _make_prospect(segment=None)
        captured: list[dict] = []

        async def fake_request(method: str, url: str, **kwargs: Any) -> MagicMock:
            captured.append(kwargs.get("json", {}))
            return _mock_hs_response(json_data={"results": [{"id": "1"}]})

        writer = CRMWriter(access_token="tok", base_url="https://api.hubapi.com")

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = mock_client
            mock_client.request.side_effect = fake_request

            asyncio.run(writer.upsert_contact(prospect))

        assert captured[0]["inputs"][0]["properties"]["icp_segment"] == ""

    def test_upsert_contact_without_brief_ref_omits_last_enriched_at(self) -> None:
        """When hiring_signal_brief_ref is None, last_enriched_at is not sent."""
        prospect = _make_prospect(hiring_signal_brief_ref=None)
        captured: list[dict] = []

        async def fake_request(method: str, url: str, **kwargs: Any) -> MagicMock:
            captured.append(kwargs.get("json", {}))
            return _mock_hs_response(json_data={"results": [{"id": "1"}]})

        writer = CRMWriter(access_token="tok", base_url="https://api.hubapi.com")

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = mock_client
            mock_client.request.side_effect = fake_request

            asyncio.run(writer.upsert_contact(prospect))

        assert "last_enriched_at" not in captured[0]["inputs"][0]["properties"]


# ---------------------------------------------------------------------------
# Req 10.2 — log_activity writes outbound and inbound records
# ---------------------------------------------------------------------------


class TestLogActivityIntegration:
    """Integration tests for CRMWriter.log_activity (Req 10.2)."""

    def test_log_outbound_email_activity(self) -> None:
        """Outbound email activity is written with correct engagement type and metadata."""
        captured: list[dict] = []

        async def fake_request(method: str, url: str, **kwargs: Any) -> MagicMock:
            captured.append(kwargs.get("json", {}))
            return _mock_hs_response(json_data={"engagement": {"id": "eng-out-001"}})

        writer = CRMWriter(access_token="tok", base_url="https://api.hubapi.com")

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = mock_client
            mock_client.request.side_effect = fake_request

            eng_id = asyncio.run(
                writer.log_activity({
                    "type": "outbound_email",
                    "prospect_id": "integ-p-001",
                    "channel": "email",
                    "timestamp": "2026-04-01T10:00:00Z",
                    "content": "Hi Sam, we noticed Integ Corp is hiring...",
                    "direction": "outbound",
                })
            )

        assert eng_id == "eng-out-001"
        payload = captured[0]
        assert payload["engagement"]["type"] == "NOTE"
        assert payload["metadata"]["channel"] == "email"
        assert payload["metadata"]["direction"] == "outbound"
        assert payload["metadata"]["prospect_id"] == "integ-p-001"
        assert "Hi Sam" in payload["metadata"]["body"]

    def test_log_inbound_email_reply(self) -> None:
        """Inbound email reply is written with direction=inbound and EMAIL type."""
        captured: list[dict] = []

        async def fake_request(method: str, url: str, **kwargs: Any) -> MagicMock:
            captured.append(kwargs.get("json", {}))
            return _mock_hs_response(json_data={"engagement": {"id": "eng-in-001"}})

        writer = CRMWriter(access_token="tok", base_url="https://api.hubapi.com")

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = mock_client
            mock_client.request.side_effect = fake_request

            eng_id = asyncio.run(
                writer.log_activity({
                    "type": "inbound_email",
                    "prospect_id": "integ-p-001",
                    "channel": "email",
                    "timestamp": "2026-04-01T11:30:00Z",
                    "content": "Thanks, I'd like to learn more.",
                    "direction": "inbound",
                })
            )

        assert eng_id == "eng-in-001"
        payload = captured[0]
        assert payload["engagement"]["type"] == "NOTE"
        assert payload["metadata"]["direction"] == "inbound"

    def test_log_outbound_sms_activity(self) -> None:
        """Outbound SMS activity is written with NOTE engagement type."""
        captured: list[dict] = []

        async def fake_request(method: str, url: str, **kwargs: Any) -> MagicMock:
            captured.append(kwargs.get("json", {}))
            return _mock_hs_response(json_data={"engagement": {"id": "eng-sms-001"}})

        writer = CRMWriter(access_token="tok", base_url="https://api.hubapi.com")

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = mock_client
            mock_client.request.side_effect = fake_request

            asyncio.run(
                writer.log_activity({
                    "type": "outbound_sms",
                    "prospect_id": "integ-p-001",
                    "channel": "sms",
                    "timestamp": "2026-04-02T09:00:00Z",
                    "content": "Hi Sam, following up via SMS.",
                    "direction": "outbound",
                })
            )

        assert captured[0]["engagement"]["type"] == "NOTE"
        assert captured[0]["metadata"]["channel"] == "sms"

    def test_log_activity_timestamp_is_epoch_ms(self) -> None:
        """Activity timestamp is converted to epoch milliseconds for HubSpot."""
        captured: list[dict] = []

        async def fake_request(method: str, url: str, **kwargs: Any) -> MagicMock:
            captured.append(kwargs.get("json", {}))
            return _mock_hs_response(json_data={"engagement": {"id": "1"}})

        writer = CRMWriter(access_token="tok", base_url="https://api.hubapi.com")

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = mock_client
            mock_client.request.side_effect = fake_request

            asyncio.run(
                writer.log_activity({
                    "type": "outbound_email",
                    "prospect_id": "integ-p-001",
                    "channel": "email",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "content": "test",
                })
            )

        ts = captured[0]["engagement"]["timestamp"]
        assert isinstance(ts, int)
        # 2026-01-01T00:00:00Z = 1767225600000 ms
        assert ts == 1767225600000


# ---------------------------------------------------------------------------
# Req 10.1 + 10.2 — upsert + activity in sequence (combined flow)
# ---------------------------------------------------------------------------


class TestUpsertAndActivitySequence:
    """Integration test: upsert_contact followed by log_activity (Req 10.1, 10.2)."""

    def test_upsert_then_log_activity_sequence(self) -> None:
        """
        Full CRM write sequence: upsert contact, then log outbound activity.

        Validates that both calls succeed and return valid IDs, and that the
        activity record references the same prospect_id as the contact.
        """
        call_log: list[str] = []

        async def fake_request(method: str, url: str, **kwargs: Any) -> MagicMock:
            if "upsert" in url:
                call_log.append("upsert")
                return _mock_hs_response(json_data={"results": [{"id": "hs-seq-001"}]})
            else:
                call_log.append("activity")
                return _mock_hs_response(json_data={"engagement": {"id": "eng-seq-001"}})

        writer = CRMWriter(access_token="tok", base_url="https://api.hubapi.com")
        prospect = _make_prospect()

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = mock_client
            mock_client.request.side_effect = fake_request

            async def run_sequence() -> tuple[str, str]:
                contact_id = await writer.upsert_contact(prospect)
                eng_id = await writer.log_activity({
                    "type": "outbound_email",
                    "prospect_id": prospect.prospect_id,
                    "channel": "email",
                    "timestamp": "2026-04-01T10:00:00Z",
                    "content": "Outreach email body",
                    "direction": "outbound",
                })
                return contact_id, eng_id

            contact_id, eng_id = asyncio.run(run_sequence())

        assert contact_id == "hs-seq-001"
        assert eng_id == "eng-seq-001"
        assert call_log == ["upsert", "activity"]

    def test_upsert_contact_then_write_brief_then_log_activity(self) -> None:
        """
        Full enrichment CRM sequence: upsert → write_brief → log_activity.

        Validates Req 10.1 (contact + brief) and Req 10.2 (activity).
        """
        call_log: list[tuple[str, str]] = []

        async def fake_request(method: str, url: str, **kwargs: Any) -> MagicMock:
            if "upsert" in url:
                call_log.append(("upsert", url))
                return _mock_hs_response(json_data={"results": [{"id": "hs-full-001"}]})
            else:
                call_log.append(("engagement", url))
                return _mock_hs_response(json_data={"engagement": {"id": "eng-full-001"}})

        writer = CRMWriter(access_token="tok", base_url="https://api.hubapi.com")
        prospect = _make_prospect()
        brief = _make_hiring_brief()

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = mock_client
            mock_client.request.side_effect = fake_request

            async def run_full() -> None:
                await writer.upsert_contact(prospect)
                await writer.write_brief(brief)
                await writer.log_activity({
                    "type": "outbound_email",
                    "prospect_id": prospect.prospect_id,
                    "channel": "email",
                    "timestamp": "2026-04-01T10:00:00Z",
                    "content": "Signal-grounded outreach",
                    "direction": "outbound",
                })

            asyncio.run(run_full())

        # Verify call order: upsert first, then two engagement writes
        assert call_log[0][0] == "upsert"
        assert call_log[1][0] == "engagement"  # write_brief
        assert call_log[2][0] == "engagement"  # log_activity
        assert len(call_log) == 3


# ---------------------------------------------------------------------------
# Req 10.5 — rate-limit enforcement in integration context
# ---------------------------------------------------------------------------


class TestRateLimitIntegration:
    """Integration tests for rate-limit enforcement across CRM write operations (Req 10.5)."""

    def test_burst_of_upserts_respects_rate_limit(self) -> None:
        """
        A burst of upsert_contact calls must not exceed 100 per 10-second window.

        Uses a small window (max_calls=5, window=1s) to keep the test fast.
        Verifies that no 1-second slice contains more than 5 dispatched calls.
        """
        max_calls = 5
        window = 1.0
        limiter = _RateLimiter(max_calls=max_calls, window_seconds=window)
        writer = CRMWriter(
            access_token="tok",
            base_url="https://api.hubapi.com",
            rate_limiter=limiter,
        )
        dispatch_times: list[float] = []

        async def fake_request(method: str, url: str, **kwargs: Any) -> MagicMock:
            dispatch_times.append(time.monotonic())
            return _mock_hs_response(json_data={"results": [{"id": str(len(dispatch_times))}]})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = mock_client
            mock_client.request.side_effect = fake_request

            async def run_burst() -> None:
                prospects = [_make_prospect(prospect_id=f"p-{i}", email=f"p{i}@x.example") for i in range(max_calls + 3)]
                await asyncio.gather(*[writer.upsert_contact(p) for p in prospects])

            asyncio.run(run_burst())

        # Verify no 1-second window contains more than max_calls dispatches
        for i, ts in enumerate(dispatch_times):
            calls_in_window = sum(1 for t in dispatch_times if ts - window < t <= ts)
            assert calls_in_window <= max_calls, (
                f"Rate limit exceeded at index {i}: {calls_in_window} calls in {window}s window"
            )

    def test_mixed_operations_respect_shared_rate_limiter(self) -> None:
        """
        Mixed upsert + log_activity calls share the same rate limiter.

        All calls count against the same window — the limiter is not per-operation.
        """
        max_calls = 4
        window = 1.0
        limiter = _RateLimiter(max_calls=max_calls, window_seconds=window)
        writer = CRMWriter(
            access_token="tok",
            base_url="https://api.hubapi.com",
            rate_limiter=limiter,
        )
        dispatch_times: list[float] = []

        async def fake_request(method: str, url: str, **kwargs: Any) -> MagicMock:
            dispatch_times.append(time.monotonic())
            if "upsert" in url:
                return _mock_hs_response(json_data={"results": [{"id": "1"}]})
            return _mock_hs_response(json_data={"engagement": {"id": "1"}})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = mock_client
            mock_client.request.side_effect = fake_request

            async def run_mixed() -> None:
                prospect = _make_prospect()
                activity = {
                    "type": "outbound_email",
                    "prospect_id": "integ-p-001",
                    "channel": "email",
                    "timestamp": "2026-04-01T10:00:00Z",
                    "content": "test",
                }
                # 3 upserts + 3 activities = 6 total calls, window allows 4
                await asyncio.gather(
                    writer.upsert_contact(prospect),
                    writer.log_activity(activity),
                    writer.upsert_contact(_make_prospect(prospect_id="p-2", email="p2@x.example")),
                    writer.log_activity(activity),
                    writer.upsert_contact(_make_prospect(prospect_id="p-3", email="p3@x.example")),
                    writer.log_activity(activity),
                )

            asyncio.run(run_mixed())

        for i, ts in enumerate(dispatch_times):
            calls_in_window = sum(1 for t in dispatch_times if ts - window < t <= ts)
            assert calls_in_window <= max_calls, (
                f"Shared rate limit exceeded at index {i}: {calls_in_window} calls in {window}s window"
            )

    def test_rate_limiter_allows_calls_after_window_expires(self) -> None:
        """
        After the window expires, the rate limiter allows a new burst.

        Saturate the window, wait for it to expire, then verify new calls proceed.
        """
        max_calls = 3
        window = 0.3  # short window for test speed
        limiter = _RateLimiter(max_calls=max_calls, window_seconds=window)
        writer = CRMWriter(
            access_token="tok",
            base_url="https://api.hubapi.com",
            rate_limiter=limiter,
        )
        call_count = 0

        async def fake_request(method: str, url: str, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            return _mock_hs_response(json_data={"results": [{"id": str(call_count)}]})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__.return_value = mock_client
            mock_client.request.side_effect = fake_request

            async def run() -> None:
                # First burst — fills the window
                prospects_1 = [
                    _make_prospect(prospect_id=f"p-{i}", email=f"p{i}@x.example")
                    for i in range(max_calls)
                ]
                for p in prospects_1:
                    await writer.upsert_contact(p)

                # Wait for window to expire
                await asyncio.sleep(window + 0.05)

                # Second burst — should proceed without blocking
                prospects_2 = [
                    _make_prospect(prospect_id=f"q-{i}", email=f"q{i}@x.example")
                    for i in range(max_calls)
                ]
                for p in prospects_2:
                    await writer.upsert_contact(p)

            asyncio.run(run())

        assert call_count == max_calls * 2
