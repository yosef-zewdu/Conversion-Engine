"""
Run 20+ synthetic prospect interactions and print p50/p95 latency stats.

Usage:
    uv run scripts/run_synthetic_latency.py

No real API calls are made — all external I/O is mocked.
Results are written to eval/latency_report.json.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from config.models import Prospect, ProspectState, Segment
from observability.latency_recorder import record_latency_stats
from signal_pipeline.models import CompetitorGapBrief, FundingEvent, HiringSignalBrief, TechStack
from signal_pipeline.pipeline_runner import PipelineRunner


def _make_prospect(i: int) -> Prospect:
    return Prospect(
        prospect_id=f"synth-{i:03d}",
        company_id=f"co-{i:03d}",
        contact_name=f"Contact {i}",
        email=f"contact{i}@synth.example",
        phone=None,
        timezone="America/New_York",
        preferred_channel="email",
        current_state=ProspectState.COLD,
        outbound_attempt_count=0,
        segment=None,
        hiring_signal_brief_ref=None,
    )


def _make_brief(i: int) -> HiringSignalBrief:
    # Alternate between segment-qualifying and unqualified briefs
    funding = None
    if i % 4 == 0:
        funding = FundingEvent(
            round_type="Series A",
            amount_usd=15_000_000.0,
            close_date="2026-01-15",
            confidence="high",
        )
    return HiringSignalBrief(
        schema_version="1.0",
        company_id=f"co-{i:03d}",
        company_name=f"Synth Corp {i}",
        last_enriched_at=datetime.now(timezone.utc).isoformat(),
        crunchbase_id=f"cb-{i:03d}",
        bench_summary_version=datetime.now(timezone.utc).isoformat(),
        bench_mismatch=False,
        tech_stack=TechStack(
            languages=["python"],
            ml_tools=["pytorch"],
            data_tools=["dbt"],
            confidence="medium",
        ),
        funding_event=funding,
        ai_maturity_score=2,
        ai_maturity_confidence="medium",
        job_post_count=6,
        job_post_velocity_60d=3.5,
        job_post_confidence="high",
    )


def _make_gap_brief(i: int) -> CompetitorGapBrief:
    return CompetitorGapBrief(
        schema_version="1.0",
        company_id=f"co-{i:03d}",
        generated_at=datetime.now(timezone.utc).isoformat(),
        prospect_ai_maturity_score=2,
        sector_percentile=60.0,
        peer_count=5,
        peers=[],
        gaps=[],
    )


def _make_mock_crm() -> MagicMock:
    crm = MagicMock()
    crm.upsert_contact = AsyncMock(return_value="hs-001")
    crm.log_activity = AsyncMock(return_value="hs-002")
    crm.write_brief = AsyncMock(return_value="hs-003")
    crm.write_booking = AsyncMock(return_value="hs-004")
    return crm


async def run_all(n: int = 25) -> list[float]:
    latencies: list[float] = []
    mock_crm = _make_mock_crm()
    runner = PipelineRunner(crm_writer=mock_crm)

    for i in range(n):
        brief = _make_brief(i)
        gap = _make_gap_brief(i)
        prospect = _make_prospect(i)

        with patch.object(runner, "_enrich", new=AsyncMock(return_value=(brief, gap))):
            t0 = time.monotonic()
            result = await runner.run(prospect)
            elapsed = time.monotonic() - t0

        latencies.append(elapsed)
        status = result.segment.value if result.segment else "error"
        print(f"  [{i+1:02d}/{n}] prospect={prospect.prospect_id}  segment={status}  {elapsed*1000:.1f}ms")

    return latencies


def main() -> None:
    n = 25
    print(f"\nRunning {n} synthetic prospect interactions...\n")
    latencies = asyncio.run(run_all(n))

    stats = record_latency_stats(latencies, label="synthetic_interactions")
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "n_interactions": n,
        "p50_ms": round(stats["p50_seconds"] * 1000, 2),
        "p95_ms": round(stats["p95_seconds"] * 1000, 2),
        "min_ms": round(min(latencies) * 1000, 2),
        "max_ms": round(max(latencies) * 1000, 2),
        "all_latencies_ms": [round(l * 1000, 2) for l in latencies],
    }

    out = Path("eval/latency_report.json")
    out.write_text(json.dumps(report, indent=2))

    print(f"\n{'='*50}")
    print(f"  n          : {n}")
    print(f"  p50        : {report['p50_ms']} ms")
    print(f"  p95        : {report['p95_ms']} ms")
    print(f"  min        : {report['min_ms']} ms")
    print(f"  max        : {report['max_ms']} ms")
    print(f"  written to : {out}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
