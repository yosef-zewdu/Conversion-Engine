"""
Latency recorder for the Conversion Engine.

Computes p50 and p95 latency from a list of latency samples.
Reads from Langfuse trace data when available.

Requirements: 11.4
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def compute_percentiles(latencies: list[float]) -> dict[str, float]:
    """Compute p50 and p95 latency from a list of latency values.

    Args:
        latencies: List of latency values in seconds (or milliseconds — caller
            is responsible for consistent units).

    Returns:
        Dict with keys ``"p50"`` and ``"p95"`` containing the respective
        percentile values.  Returns ``{"p50": 0.0, "p95": 0.0}`` for an
        empty list.
    """
    if not latencies:
        return {"p50": 0.0, "p95": 0.0}

    sorted_vals = sorted(latencies)
    n = len(sorted_vals)

    def _percentile(p: float) -> float:
        idx = (p / 100.0) * (n - 1)
        lower = int(idx)
        upper = min(lower + 1, n - 1)
        frac = idx - lower
        return sorted_vals[lower] + frac * (sorted_vals[upper] - sorted_vals[lower])

    return {
        "p50": _percentile(50),
        "p95": _percentile(95),
    }


def fetch_langfuse_latencies(
    trace_name_prefix: str | None = None,
    limit: int = 200,
) -> list[float]:
    """Fetch wall-clock latencies from Langfuse traces.

    Queries the Langfuse API for recent traces and extracts latency values
    from their metadata.  Falls back to an empty list when Langfuse is
    unavailable or keys are not configured.

    Args:
        trace_name_prefix: Optional prefix to filter trace names
            (e.g. ``"llm_call."``).  When ``None``, all traces are fetched.
        limit: Maximum number of traces to fetch.

    Returns:
        List of latency values in seconds extracted from trace metadata.
    """
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    base_url = os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

    if not public_key or not secret_key:
        logger.warning("Langfuse keys not configured — cannot fetch latencies.")
        return []

    try:
        from langfuse import Langfuse

        lf = Langfuse(public_key=public_key, secret_key=secret_key, host=base_url)
        traces = lf.get_traces(limit=limit)
        latencies: list[float] = []

        for trace in getattr(traces, "data", []):
            name = getattr(trace, "name", "") or ""
            if trace_name_prefix and not name.startswith(trace_name_prefix):
                continue
            meta = getattr(trace, "metadata", {}) or {}
            # Try latency_seconds first, then latency_ms converted
            if "latency_seconds" in meta:
                latencies.append(float(meta["latency_seconds"]))
            elif "latency_ms" in meta:
                latencies.append(float(meta["latency_ms"]) / 1000.0)

        return latencies
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch Langfuse latencies: %s", exc)
        return []


def record_latency_stats(
    latencies: list[float],
    label: str = "all",
) -> dict[str, Any]:
    """Compute and log p50/p95 latency statistics.

    Args:
        latencies: List of latency values in seconds.
        label: Human-readable label for the log message.

    Returns:
        Dict with ``"label"``, ``"count"``, ``"p50_seconds"``, and
        ``"p95_seconds"`` keys.
    """
    stats = compute_percentiles(latencies)
    result = {
        "label": label,
        "count": len(latencies),
        "p50_seconds": stats["p50"],
        "p95_seconds": stats["p95"],
    }
    logger.info(
        "Latency stats [%s]: n=%d p50=%.3fs p95=%.3fs",
        label,
        result["count"],
        result["p50_seconds"],
        result["p95_seconds"],
    )
    return result
