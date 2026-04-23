"""
Invoice Summary Generator for the Conversion Engine.

Aggregates per-prospect LLM token costs and enrichment API costs from
Langfuse traces and writes ``invoice_summary.json``.

This file is required for evidence graph verification of all cost claims
in ``memo.pdf``.

Requirements: 11.3, 11.6
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cost-quality violation threshold (Req 11.3)
# ---------------------------------------------------------------------------

_COST_QUALITY_VIOLATION_USD = 8.0
_COST_TARGET_USD = 5.0

# ---------------------------------------------------------------------------
# Output path
# ---------------------------------------------------------------------------

_DEFAULT_OUTPUT_PATH = Path("invoice_summary.json")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class ProspectCostRecord:
    """Per-prospect cost breakdown.

    Attributes:
        prospect_id: UUID of the prospect.
        llm_cost_usd: Total LLM token cost for this prospect.
        api_cost_usd: Total enrichment API cost for this prospect.
        qualified: Whether the prospect was classified as a qualified lead.
        cost_quality_violation: True when total cost > $8 (Req 11.3).
        trace_ids: List of Langfuse trace IDs associated with this prospect.
    """

    prospect_id: str
    llm_cost_usd: float = 0.0
    api_cost_usd: float = 0.0
    qualified: bool = False
    cost_quality_violation: bool = False
    trace_ids: list[str] = field(default_factory=list)

    @property
    def total_cost_usd(self) -> float:
        """Total cost (LLM + API) for this prospect."""
        return self.llm_cost_usd + self.api_cost_usd


@dataclass
class InvoiceSummary:
    """Aggregated cost summary across all prospects.

    Attributes:
        total_llm_cost_usd: Sum of all LLM costs.
        total_api_cost_usd: Sum of all enrichment API costs.
        total_prospects_processed: Total number of prospects processed.
        qualified_leads: Number of prospects classified as qualified leads.
        cost_per_qualified_lead_usd: Average cost per qualified lead.
        cost_quality_violations: Number of prospects exceeding the $8 threshold.
        per_prospect_breakdown: List of per-prospect cost records.
    """

    total_llm_cost_usd: float = 0.0
    total_api_cost_usd: float = 0.0
    total_prospects_processed: int = 0
    qualified_leads: int = 0
    cost_per_qualified_lead_usd: float = 0.0
    cost_quality_violations: int = 0
    per_prospect_breakdown: list[ProspectCostRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict.

        Returns:
            Dict representation suitable for ``json.dumps``.
        """
        return {
            "total_llm_cost_usd": self.total_llm_cost_usd,
            "total_api_cost_usd": self.total_api_cost_usd,
            "total_prospects_processed": self.total_prospects_processed,
            "qualified_leads": self.qualified_leads,
            "cost_per_qualified_lead_usd": self.cost_per_qualified_lead_usd,
            "cost_quality_violations": self.cost_quality_violations,
            "per_prospect_breakdown": [
                {
                    "prospect_id": r.prospect_id,
                    "llm_cost_usd": r.llm_cost_usd,
                    "api_cost_usd": r.api_cost_usd,
                    "total_cost_usd": r.total_cost_usd,
                    "qualified": r.qualified,
                    "cost_quality_violation": r.cost_quality_violation,
                    "trace_ids": r.trace_ids,
                }
                for r in self.per_prospect_breakdown
            ],
        }


# ---------------------------------------------------------------------------
# Langfuse trace fetcher
# ---------------------------------------------------------------------------


def _fetch_traces_from_langfuse(limit: int = 500) -> list[dict[str, Any]]:
    """Fetch raw traces from Langfuse.

    Args:
        limit: Maximum number of traces to retrieve.

    Returns:
        List of trace dicts with at minimum ``name``, ``metadata``, and ``id``
        keys.  Returns an empty list when Langfuse is unavailable.
    """
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    base_url = os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

    if not public_key or not secret_key:
        logger.warning("Langfuse keys not configured — cannot fetch traces.")
        return []

    try:
        from langfuse import Langfuse

        lf = Langfuse(public_key=public_key, secret_key=secret_key, host=base_url)
        response = lf.get_traces(limit=limit)
        traces = []
        for t in getattr(response, "data", []):
            traces.append(
                {
                    "id": getattr(t, "id", None),
                    "name": getattr(t, "name", ""),
                    "metadata": getattr(t, "metadata", {}) or {},
                    "input": getattr(t, "input", {}),
                    "output": getattr(t, "output", {}),
                }
            )
        return traces
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch traces from Langfuse: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Core aggregation logic
# ---------------------------------------------------------------------------


def _aggregate_traces(
    traces: list[dict[str, Any]],
) -> dict[str, ProspectCostRecord]:
    """Aggregate trace data into per-prospect cost records.

    Handles two trace types:
    - ``llm_call.*`` — LLM call traces with ``cost_usd`` in metadata.
    - ``signal_pipeline.enrich`` — enrichment traces (API cost approximated
      as $0.001 per run as a placeholder; real costs come from actual API
      billing).

    Args:
        traces: List of raw trace dicts from Langfuse.

    Returns:
        Dict mapping ``prospect_id`` → ``ProspectCostRecord``.
    """
    records: dict[str, ProspectCostRecord] = {}

    for trace in traces:
        name: str = trace.get("name", "")
        meta: dict[str, Any] = trace.get("metadata", {}) or {}
        trace_id: str = str(trace.get("id", ""))

        prospect_id: str = str(meta.get("prospect_id") or "unknown")

        if prospect_id not in records:
            records[prospect_id] = ProspectCostRecord(prospect_id=prospect_id)

        rec = records[prospect_id]
        if trace_id:
            rec.trace_ids.append(trace_id)

        if name.startswith("llm_call."):
            cost = float(meta.get("cost_usd", 0.0))
            rec.llm_cost_usd += cost

        elif name == "signal_pipeline.enrich":
            # Enrichment API cost: approximate $0.001 per run
            # (Playwright + Crunchbase lookups; real billing tracked externally)
            rec.api_cost_usd += 0.001

        elif name == "crm_write_failure:":
            # Failed CRM writes don't add cost but are tracked
            pass

    return records


def generate_invoice_summary(
    traces: list[dict[str, Any]] | None = None,
    qualified_prospect_ids: set[str] | None = None,
    output_path: Path | str = _DEFAULT_OUTPUT_PATH,
) -> InvoiceSummary:
    """Generate ``invoice_summary.json`` from Langfuse traces.

    Fetches traces from Langfuse when ``traces`` is ``None``.  Aggregates
    per-prospect LLM and API costs, flags cost-quality violations (> $8),
    and writes the summary to ``output_path``.

    Args:
        traces: Pre-fetched list of trace dicts.  When ``None``, traces are
            fetched from Langfuse automatically.
        qualified_prospect_ids: Set of prospect IDs that were classified as
            qualified leads.  Used to compute cost-per-qualified-lead.
        output_path: Path to write ``invoice_summary.json``.

    Returns:
        The populated ``InvoiceSummary`` dataclass.
    """
    if traces is None:
        traces = _fetch_traces_from_langfuse()

    qualified_ids = qualified_prospect_ids or set()
    per_prospect = _aggregate_traces(traces)

    total_llm = 0.0
    total_api = 0.0
    violations = 0
    qualified_count = 0

    for prospect_id, rec in per_prospect.items():
        rec.qualified = prospect_id in qualified_ids
        rec.cost_quality_violation = rec.total_cost_usd > _COST_QUALITY_VIOLATION_USD

        total_llm += rec.llm_cost_usd
        total_api += rec.api_cost_usd

        if rec.cost_quality_violation:
            violations += 1
            logger.warning(
                "Cost-quality violation: prospect_id=%s total_cost=$%.4f (threshold=$%.2f)",
                prospect_id,
                rec.total_cost_usd,
                _COST_QUALITY_VIOLATION_USD,
            )

        if rec.qualified:
            qualified_count += 1

    cost_per_lead = (
        (total_llm + total_api) / qualified_count if qualified_count > 0 else 0.0
    )

    summary = InvoiceSummary(
        total_llm_cost_usd=total_llm,
        total_api_cost_usd=total_api,
        total_prospects_processed=len(per_prospect),
        qualified_leads=qualified_count,
        cost_per_qualified_lead_usd=cost_per_lead,
        cost_quality_violations=violations,
        per_prospect_breakdown=list(per_prospect.values()),
    )

    # Write to disk
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary.to_dict(), indent=2))
    logger.info("invoice_summary.json written to %s", output)

    return summary
