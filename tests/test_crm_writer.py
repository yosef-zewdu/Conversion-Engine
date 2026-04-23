"""
Tests for CRM Writer — rate limiting, retry backoff, and write operations.

Properties tested (optional):
  18 — CRM Retry Bounded Exponential Backoff
  19 — HubSpot Rate Limit Enforcement

Requirements: 10.1, 10.2, 10.3, 10.4, 10.5
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.models import Booking, Prospect, ProspectState, Segment
from crm_writer.writer import CRMWriter, _RateLimiter, _iso_to_epoch_ms, _map_activity_type
from signal_pipeline.models import CompetitorGapBrief, HiringSignalBrief


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_prospect(**kwargs: Any) -> Prospect:
    defaults = dict(
        prospect_id="p-001",
        company_id="co-001",
        contact_name="Alex Smith",
        email="alex@example.com",
        phone=None,
        timezone="America/New_York",
        preferred_channel="email",
        current_state=ProspectState.COLD,
        outbound_attempt_count=0,
        segment=Segment.S1,
        hiring_signal_brief_ref="2025-05-01T00:00:00Z",
    )
    defaults.update(kwargs)
    return Prospect(**defaults)


def _make_hiring_brief(**kwargs: Any) -> HiringSignalBrief:
    defaults = dict(
        schema_version="1.0",
        company_id="co-001",
        company_name="Acme Corp",
        last_enriched_at="2025-05-01T00:00:00Z",
        ai_maturity_score=2,
        ai_maturity_confidence="medium",
    )
    defaults.update(kwargs)
    return HiringSignalBrief(**defaults)


def _make_competitor_brief(**kwargs: Any) -> CompetitorGapBrief:
    defaults = dict(
        schema_version="1.0",
        company_id="co-001",
        generated_at="2025-05-01T00:00:00Z",
        peer_count=5,
    )
    defaults.update(kwargs)
    return CompetitorGapBrief(**defaults)


def _make_booking(**kwargs: Any) -> Booking:
    defaults = dict(
        cal_event_id="cal-123",
        prospect_id="p-001",
        segment=Segment.S1,
        brief_ref="co-001:2025-05-01T00:00:00Z",
    )
    defaults.update(kwargs)
    return Booking(**defaults)


def _mock_response(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {"results": [{"id": "hs-001"}], "engagement": {"id": "eng-001"}}
    if status_code >= 400:
        import httpx
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=MagicMock()
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# Unit tests — helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_iso_to_epoch_ms_valid(self) -> None:
        ms = _iso_to_epoch_ms("2025-01-01T00:00:00Z")
        assert ms == 1735689600000

    def test_iso_to_epoch_ms_empty_returns_current(self) -> None:
        before = int(time.time() * 1000)
        ms = _iso_to_epoch_ms("")
        after = int(time.time() * 1000)
        assert before <= ms <= after

    def test_iso_to_epoch_ms_invalid_returns_current(self) -> None:
        before = int(time.time() * 1000)
        ms = _iso_to_epoch_ms("not-a-date")
        after = int(time.time() * 1000)
        assert before <= ms <= after

    def test_map_activity_type_known(self) -> None:
        assert _map_activity_type("outbound_email") == "NOTE"
        assert _map_activity_type("outbound_voice") == "CALL"
        assert _map_activity_type("outbound_sms") == "NOTE"

    def test_map_activity_type_unknown_defaults_to_note(self) -> None:
        assert _map_activity_type("unknown_type") == "NOTE"


# ---------------------------------------------------------------------------
# Unit tests — rate limiter
# ---------------------------------------------------------------------------


class TestRateLimiter:
    def test_allows_calls_within_limit(self) -> None:
        limiter = _RateLimiter(max_calls=5, window_seconds=10.0)

        async def run() -> None:
            for _ in range(5):
                await limiter.acquire()

        asyncio.run(run())

    def test_blocks_when_window_saturated(self) -> None:
        """Saturate the window and verify the next acquire waits."""
        limiter = _RateLimiter(max_calls=2, window_seconds=1.0)

        async def run() -> float:
            await limiter.acquire()
            await limiter.acquire()
            start = time.monotonic()
            await limiter.acquire()  # should wait ~1s
            return time.monotonic() - start

        elapsed = asyncio.run(run())
        # Should have waited at least 0.5s (generous lower bound for CI)
        assert elapsed >= 0.5

    # Feature: conversion-engine, Property 19: HubSpot Rate Limit Enforcement
    def test_burst_never_exceeds_rate_limit(self) -> None:
        """
        Simulate a burst of writes and verify the rate limiter never allows
        more than max_calls timestamps within any window_seconds window.
        """
        max_calls = 10
        window = 1.0
        limiter = _RateLimiter(max_calls=max_calls, window_seconds=window)

        async def run() -> list[float]:
            timestamps: list[float] = []
            for _ in range(max_calls + 5):
                await limiter.acquire()
                timestamps.append(time.monotonic())
            return timestamps

        timestamps = asyncio.run(run())

        # Verify no window_seconds slice contains more than max_calls entries
        for i, ts in enumerate(timestamps):
            window_calls = sum(1 for t in timestamps if ts - window < t <= ts)
            assert window_calls <= max_calls, (
                f"Rate limit exceeded: {window_calls} calls in window at index {i}"
            )


# ---------------------------------------------------------------------------
# Unit tests — CRMWriter operations
# ---------------------------------------------------------------------------


class TestCRMWriterUpsertContact:
    def test_upsert_contact_returns_id(self) -> None:
        writer = CRMWriter(access_token="test-token", base_url="https://api.hubapi.com")
        prospect = _make_prospect()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.request.return_value = _mock_response(
                json_data={"results": [{"id": "42"}]}
            )

            result = asyncio.run(
                writer.upsert_contact(prospect)
            )

        assert result == "42"

    def test_upsert_contact_includes_required_fields(self) -> None:
        writer = CRMWriter(access_token="test-token", base_url="https://api.hubapi.com")
        prospect = _make_prospect()
        captured: list[dict] = []

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def capture_request(method: str, url: str, **kwargs: Any) -> MagicMock:
                captured.append(kwargs.get("json", {}))
                return _mock_response(json_data={"results": [{"id": "1"}]})

            mock_client.request.side_effect = capture_request

            asyncio.run(writer.upsert_contact(prospect))

        assert captured
        # v3 batch upsert: {"inputs": [{"idProperty": "email", "id": ..., "properties": {...}}]}
        props = captured[0]["inputs"][0]["properties"]
        assert props["email"] == "alex@example.com"
        assert props["prospect_id"] == "p-001"
        assert props["icp_segment"] == "segment_1"


class TestCRMWriterLogActivity:
    def test_log_activity_returns_engagement_id(self) -> None:
        writer = CRMWriter(access_token="test-token", base_url="https://api.hubapi.com")
        activity = {
            "type": "outbound_email",
            "prospect_id": "p-001",
            "channel": "email",
            "timestamp": "2025-05-01T10:00:00Z",
            "content": "Hello from Tenacious",
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.request.return_value = _mock_response(
                json_data={"engagement": {"id": "eng-999"}}
            )

            result = asyncio.run(
                writer.log_activity(activity)
            )

        assert result == "eng-999"

    def test_log_activity_maps_email_type(self) -> None:
        writer = CRMWriter(access_token="test-token", base_url="https://api.hubapi.com")
        captured: list[dict] = []

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def capture(method: str, url: str, **kwargs: Any) -> MagicMock:
                captured.append(kwargs.get("json", {}))
                return _mock_response(json_data={"engagement": {"id": "1"}})

            mock_client.request.side_effect = capture

            asyncio.run(
                writer.log_activity({
                    "type": "outbound_email",
                    "prospect_id": "p-001",
                    "channel": "email",
                    "timestamp": "2025-05-01T10:00:00Z",
                    "content": "test",
                })
            )

        assert captured[0]["engagement"]["type"] == "NOTE"


class TestCRMWriterWriteBrief:
    def test_write_hiring_brief_returns_id(self) -> None:
        writer = CRMWriter(access_token="test-token", base_url="https://api.hubapi.com")
        brief = _make_hiring_brief()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.request.return_value = _mock_response(
                json_data={"engagement": {"id": "eng-brief-1"}}
            )

            result = asyncio.run(writer.write_brief(brief))

        assert result == "eng-brief-1"

    def test_write_competitor_brief_returns_id(self) -> None:
        writer = CRMWriter(access_token="test-token", base_url="https://api.hubapi.com")
        brief = _make_competitor_brief()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.request.return_value = _mock_response(
                json_data={"engagement": {"id": "eng-brief-2"}}
            )

            result = asyncio.run(writer.write_brief(brief))

        assert result == "eng-brief-2"

    def test_write_brief_includes_enrichment_timestamp(self) -> None:
        writer = CRMWriter(access_token="test-token", base_url="https://api.hubapi.com")
        brief = _make_hiring_brief(last_enriched_at="2025-05-01T12:00:00Z")
        captured: list[dict] = []

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def capture(method: str, url: str, **kwargs: Any) -> MagicMock:
                captured.append(kwargs.get("json", {}))
                return _mock_response(json_data={"engagement": {"id": "1"}})

            mock_client.request.side_effect = capture

            asyncio.run(writer.write_brief(brief))

        assert captured[0]["metadata"]["enriched_at"] == "2025-05-01T12:00:00Z"
        assert captured[0]["metadata"]["brief_type"] == "HiringSignalBrief"


class TestCRMWriterWriteBooking:
    def test_write_booking_returns_id(self) -> None:
        writer = CRMWriter(access_token="test-token", base_url="https://api.hubapi.com")
        booking = _make_booking()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.request.return_value = _mock_response(
                json_data={"engagement": {"id": "eng-booking-1"}}
            )

            result = asyncio.run(
                writer.write_booking(booking)
            )

        assert result == "eng-booking-1"

    def test_write_booking_includes_cal_event_id(self) -> None:
        writer = CRMWriter(access_token="test-token", base_url="https://api.hubapi.com")
        booking = _make_booking(cal_event_id="cal-xyz-789")
        captured: list[dict] = []

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def capture(method: str, url: str, **kwargs: Any) -> MagicMock:
                captured.append(kwargs.get("json", {}))
                return _mock_response(json_data={"engagement": {"id": "1"}})

            mock_client.request.side_effect = capture

            asyncio.run(writer.write_booking(booking))

        meta = captured[0]["metadata"]
        assert meta["cal_event_id"] == "cal-xyz-789"
        assert meta["icp_segment"] == "segment_1"
        assert "hiring_signal_brief_ref" in meta


# ---------------------------------------------------------------------------
# Retry and failure tests
# ---------------------------------------------------------------------------


class TestCRMWriterRetry:
    # Feature: conversion-engine, Property 18: CRM Retry Bounded Exponential Backoff
    def test_retries_exactly_three_times_then_raises(self) -> None:
        """
        On persistent failure, CRMWriter retries exactly 3 times total
        (attempts 1, 2, 3) then raises the last exception.
        """
        import httpx

        writer = CRMWriter(access_token="test-token", base_url="https://api.hubapi.com")
        call_count = 0

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def always_fail(method: str, url: str, **kwargs: Any) -> MagicMock:
                nonlocal call_count
                call_count += 1
                raise httpx.RequestError("connection refused")

            mock_client.request.side_effect = always_fail

            with patch("crm_writer.writer._emit_failure_to_langfuse") as mock_emit:
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    with pytest.raises(httpx.RequestError):
                        asyncio.run(
                            writer.upsert_contact(_make_prospect())
                        )

                mock_emit.assert_called_once()

        assert call_count == 3, f"Expected 3 attempts, got {call_count}"

    def test_emits_payload_to_langfuse_on_final_failure(self) -> None:
        """After all retries fail, the payload is emitted to Langfuse."""
        import httpx

        writer = CRMWriter(access_token="test-token", base_url="https://api.hubapi.com")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.request.side_effect = httpx.RequestError("timeout")

            with patch("crm_writer.writer._emit_failure_to_langfuse") as mock_emit:
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    with pytest.raises(httpx.RequestError):
                        asyncio.run(
                            writer.upsert_contact(_make_prospect())
                        )

            mock_emit.assert_called_once()
            call_args = mock_emit.call_args
            assert call_args[0][0] == "upsert_contact"
            assert "payload" in call_args[0][1]

    def test_succeeds_on_second_attempt(self) -> None:
        """Verify recovery: first attempt fails, second succeeds."""
        import httpx

        writer = CRMWriter(access_token="test-token", base_url="https://api.hubapi.com")
        call_count = 0

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def fail_then_succeed(method: str, url: str, **kwargs: Any) -> MagicMock:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise httpx.RequestError("transient error")
                return _mock_response(json_data={"results": [{"id": "recovered-id"}]})

            mock_client.request.side_effect = fail_then_succeed

            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = asyncio.run(
                    writer.upsert_contact(_make_prospect())
                )

        assert result == "recovered-id"
        assert call_count == 2

    def test_backoff_delays_are_correct(self) -> None:
        """Verify sleep is called with 1s, 2s delays between the 3 attempts."""
        import httpx

        writer = CRMWriter(access_token="test-token", base_url="https://api.hubapi.com")
        sleep_calls: list[float] = []

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.request.side_effect = httpx.RequestError("error")

            async def record_sleep(delay: float) -> None:
                sleep_calls.append(delay)

            with patch("asyncio.sleep", side_effect=record_sleep):
                with patch("crm_writer.writer._emit_failure_to_langfuse"):
                    with pytest.raises(httpx.RequestError):
                        asyncio.run(
                            writer.upsert_contact(_make_prospect())
                        )

        # Delays between attempt 1→2 and 2→3: 1s and 2s
        assert sleep_calls == [1.0, 2.0], f"Expected [1.0, 2.0], got {sleep_calls}"
