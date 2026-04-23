"""
Tests for the observability module.

Covers:
- LLM call tracing (Property 20)
- Budget guard enforcement (Req 11.5)
- p50/p95 latency computation (Req 11.4)
- Invoice summary generation (Req 11.3, 11.6)
- Cost-quality violation flagging (Req 11.3)

Requirements: 11.1, 11.3, 11.4, 11.5, 11.6
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from observability.invoice import (
    InvoiceSummary,
    ProspectCostRecord,
    _aggregate_traces,
    generate_invoice_summary,
)
from observability.latency_recorder import compute_percentiles, record_latency_stats
from observability.llm_tracer import (
    LLMCallRecord,
    _BUDGET_LIMIT_USD,
    acknowledge_budget_exceeded,
    guard_llm_call,
    is_budget_exceeded,
    reset_budget_state,
    trace_llm_call,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_budget():
    """Reset budget state before each test."""
    reset_budget_state()
    yield
    reset_budget_state()


# ---------------------------------------------------------------------------
# LLM Tracer — unit tests
# ---------------------------------------------------------------------------


class TestLLMTracer:
    """Tests for LLM call tracing and budget guard."""

    def test_trace_llm_call_returns_record(self):
        """trace_llm_call returns a populated LLMCallRecord."""
        with patch("observability.llm_tracer._emit_llm_trace", return_value="trace-123"):
            record = trace_llm_call(
                model="openrouter/qwen/qwen3",
                prompt_tokens=100,
                completion_tokens=50,
                latency_seconds=1.5,
                cost_usd=0.01,
                component="test",
            )

        assert isinstance(record, LLMCallRecord)
        assert record.model == "openrouter/qwen/qwen3"
        assert record.prompt_tokens == 100
        assert record.completion_tokens == 50
        assert record.latency_seconds == 1.5
        assert record.cost_usd == 0.01
        assert record.trace_id == "trace-123"

    def test_trace_llm_call_accumulates_spend(self):
        """Multiple calls accumulate total spend."""
        with patch("observability.llm_tracer._emit_llm_trace", return_value=None):
            trace_llm_call(
                model="m", prompt_tokens=0, completion_tokens=0,
                latency_seconds=0.1, cost_usd=5.0,
            )
            trace_llm_call(
                model="m", prompt_tokens=0, completion_tokens=0,
                latency_seconds=0.1, cost_usd=7.0,
            )

        from observability.llm_tracer import get_total_llm_spend
        assert get_total_llm_spend() == pytest.approx(12.0)

    def test_budget_not_exceeded_initially(self):
        """Budget is not exceeded at startup."""
        assert not is_budget_exceeded()

    def test_budget_exceeded_after_threshold(self):
        """Budget is exceeded after spend crosses $20."""
        with patch("observability.llm_tracer._emit_llm_trace", return_value=None):
            trace_llm_call(
                model="m", prompt_tokens=0, completion_tokens=0,
                latency_seconds=0.1, cost_usd=_BUDGET_LIMIT_USD,
            )
        assert is_budget_exceeded()

    def test_budget_guard_raises_when_exceeded(self):
        """guard_llm_call raises RuntimeError when budget is exceeded."""
        with patch("observability.llm_tracer._emit_llm_trace", return_value=None):
            trace_llm_call(
                model="m", prompt_tokens=0, completion_tokens=0,
                latency_seconds=0.1, cost_usd=_BUDGET_LIMIT_USD,
            )
        with pytest.raises(RuntimeError, match="LLM budget exceeded"):
            guard_llm_call()

    def test_acknowledge_clears_budget_guard(self):
        """acknowledge_budget_exceeded allows calls to resume."""
        with patch("observability.llm_tracer._emit_llm_trace", return_value=None):
            trace_llm_call(
                model="m", prompt_tokens=0, completion_tokens=0,
                latency_seconds=0.1, cost_usd=_BUDGET_LIMIT_USD,
            )
        assert is_budget_exceeded()
        acknowledge_budget_exceeded()
        assert not is_budget_exceeded()
        # guard should not raise
        guard_llm_call()

    def test_trace_without_langfuse_keys_does_not_crash(self):
        """trace_llm_call succeeds even when Langfuse keys are absent."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")}
        with patch.dict(os.environ, env, clear=True):
            record = trace_llm_call(
                model="m", prompt_tokens=10, completion_tokens=5,
                latency_seconds=0.5, cost_usd=0.001,
            )
        assert record.trace_id is None


# ---------------------------------------------------------------------------
# Property 20: LLM Call Observability Coverage
# ---------------------------------------------------------------------------


# Feature: conversion-engine, Property 20: LLM Call Observability Coverage
@given(
    model=st.text(min_size=1, max_size=80),
    prompt_tokens=st.integers(min_value=0, max_value=100_000),
    completion_tokens=st.integers(min_value=0, max_value=100_000),
    latency_seconds=st.floats(min_value=0.0, max_value=300.0, allow_nan=False),
    cost_usd=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
@settings(max_examples=100)
def test_llm_call_record_always_has_required_fields(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_seconds: float,
    cost_usd: float,
) -> None:
    """Every LLM call record contains model, token counts, and latency (Req 11.1)."""
    reset_budget_state()
    with patch("observability.llm_tracer._emit_llm_trace", return_value="t-id"):
        record = trace_llm_call(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_seconds=latency_seconds,
            cost_usd=cost_usd,
        )
    assert record.model == model
    assert record.prompt_tokens == prompt_tokens
    assert record.completion_tokens == completion_tokens
    assert record.latency_seconds == latency_seconds
    reset_budget_state()


# ---------------------------------------------------------------------------
# Latency recorder — unit tests
# ---------------------------------------------------------------------------


class TestLatencyRecorder:
    """Tests for p50/p95 latency computation."""

    def test_empty_list_returns_zeros(self):
        """compute_percentiles returns zeros for empty input."""
        result = compute_percentiles([])
        assert result == {"p50": 0.0, "p95": 0.0}

    def test_single_value(self):
        """Single value produces p50 == p95 == that value."""
        result = compute_percentiles([2.5])
        assert result["p50"] == pytest.approx(2.5)
        assert result["p95"] == pytest.approx(2.5)

    def test_known_distribution(self):
        """p50 and p95 are correct for a known sorted distribution."""
        # 100 values: 1..100
        latencies = list(range(1, 101))
        result = compute_percentiles(latencies)
        # p50 of 1..100 = 50.5
        assert result["p50"] == pytest.approx(50.5, abs=0.1)
        # p95 of 1..100 = 95.05
        assert result["p95"] == pytest.approx(95.05, abs=0.5)

    def test_record_latency_stats_returns_dict(self):
        """record_latency_stats returns a dict with expected keys."""
        stats = record_latency_stats([1.0, 2.0, 3.0], label="test")
        assert "p50_seconds" in stats
        assert "p95_seconds" in stats
        assert stats["count"] == 3
        assert stats["label"] == "test"

    @given(latencies=st.lists(
        st.floats(min_value=0.0, max_value=1000.0, allow_nan=False),
        min_size=1,
        max_size=500,
    ))
    @settings(max_examples=100)
    def test_p50_lte_p95(self, latencies: list[float]) -> None:
        """p50 is always <= p95 for any non-empty list."""
        result = compute_percentiles(latencies)
        assert result["p50"] <= result["p95"]


# ---------------------------------------------------------------------------
# Invoice generator — unit tests
# ---------------------------------------------------------------------------


class TestInvoiceGenerator:
    """Tests for invoice summary generation."""

    def _make_llm_trace(
        self,
        prospect_id: str,
        cost_usd: float,
        trace_id: str = "t1",
    ) -> dict:
        return {
            "id": trace_id,
            "name": "llm_call.nurture_sequencer",
            "metadata": {
                "prospect_id": prospect_id,
                "cost_usd": cost_usd,
                "model": "qwen3",
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "latency_seconds": 1.0,
            },
        }

    def _make_enrich_trace(self, prospect_id: str, trace_id: str = "t2") -> dict:
        return {
            "id": trace_id,
            "name": "signal_pipeline.enrich",
            "metadata": {
                "prospect_id": prospect_id,
                "latency_ms": 500.0,
                "result_count": 3,
            },
        }

    def test_aggregate_llm_traces(self):
        """LLM traces are aggregated into per-prospect cost records."""
        traces = [
            self._make_llm_trace("p1", 0.05, "t1"),
            self._make_llm_trace("p1", 0.03, "t2"),
            self._make_llm_trace("p2", 0.10, "t3"),
        ]
        records = _aggregate_traces(traces)
        assert "p1" in records
        assert records["p1"].llm_cost_usd == pytest.approx(0.08)
        assert records["p2"].llm_cost_usd == pytest.approx(0.10)

    def test_aggregate_enrich_traces_adds_api_cost(self):
        """Enrichment traces add API cost to the prospect record."""
        traces = [self._make_enrich_trace("p1", "t1")]
        records = _aggregate_traces(traces)
        assert records["p1"].api_cost_usd == pytest.approx(0.001)

    def test_cost_quality_violation_flagged(self):
        """Prospects with total cost > $8 are flagged as violations."""
        traces = [self._make_llm_trace("p1", 9.0, "t1")]
        summary = generate_invoice_summary(
            traces=traces,
            qualified_prospect_ids={"p1"},
            output_path="/tmp/test_invoice.json",
        )
        assert summary.cost_quality_violations == 1
        assert summary.per_prospect_breakdown[0].cost_quality_violation is True

    def test_no_violation_below_threshold(self):
        """Prospects with total cost <= $8 are not flagged."""
        traces = [self._make_llm_trace("p1", 3.0, "t1")]
        summary = generate_invoice_summary(
            traces=traces,
            output_path="/tmp/test_invoice_ok.json",
        )
        assert summary.cost_quality_violations == 0

    def test_cost_per_qualified_lead_computed(self):
        """cost_per_qualified_lead_usd is total cost / qualified count."""
        traces = [
            self._make_llm_trace("p1", 2.0, "t1"),
            self._make_llm_trace("p2", 4.0, "t2"),
        ]
        summary = generate_invoice_summary(
            traces=traces,
            qualified_prospect_ids={"p1"},
            output_path="/tmp/test_invoice_cpl.json",
        )
        # total = 6.0, qualified = 1 → cost_per_lead = 6.0
        assert summary.cost_per_qualified_lead_usd == pytest.approx(6.0)

    def test_invoice_written_to_disk(self, tmp_path: Path):
        """generate_invoice_summary writes a valid JSON file."""
        traces = [self._make_llm_trace("p1", 1.0, "t1")]
        out = tmp_path / "invoice_summary.json"
        generate_invoice_summary(traces=traces, output_path=out)

        assert out.exists()
        data = json.loads(out.read_text())
        assert "total_llm_cost_usd" in data
        assert "per_prospect_breakdown" in data
        assert isinstance(data["per_prospect_breakdown"], list)

    def test_invoice_to_dict_structure(self):
        """InvoiceSummary.to_dict() produces the expected JSON structure."""
        rec = ProspectCostRecord(
            prospect_id="p1",
            llm_cost_usd=1.5,
            api_cost_usd=0.01,
            qualified=True,
        )
        summary = InvoiceSummary(
            total_llm_cost_usd=1.5,
            total_api_cost_usd=0.01,
            total_prospects_processed=1,
            qualified_leads=1,
            cost_per_qualified_lead_usd=1.51,
            per_prospect_breakdown=[rec],
        )
        d = summary.to_dict()
        assert d["total_llm_cost_usd"] == 1.5
        assert len(d["per_prospect_breakdown"]) == 1
        assert d["per_prospect_breakdown"][0]["prospect_id"] == "p1"
        assert d["per_prospect_breakdown"][0]["qualified"] is True
