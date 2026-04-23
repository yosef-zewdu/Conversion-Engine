"""
HiringSignalBriefAssembler — merges all sub-component results into a validated
HiringSignalBrief and optional CompetitorGapBrief.

Req 2.8: Signal Pipeline SHALL produce a HiringSignalBrief with all collected
         signals and per-signal confidence.
Req 16.1: Serialize HiringSignalBrief and CompetitorGapBrief as valid JSON
          conforming to their schemas.
Req 16.5: If validation fails, return descriptive error identifying failing
          field; do NOT write to HubSpot or Langfuse.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from signal_pipeline.ai_maturity_scorer import AIMaturityResult
from signal_pipeline.assembler import validate_hiring_signal_brief
from signal_pipeline.firmographic_enricher import FirmographicResult
from signal_pipeline.funding_event_fetcher import FundingEventResult
from signal_pipeline.job_post_scraper import JobPostResult
from signal_pipeline.layoff_scanner import LayoffScanResult
from signal_pipeline.leadership_change_detector import LeadershipChangeResult
from signal_pipeline.models import (
    CompetitorGapBrief,
    FundingEvent,
    HiringSignalBrief,
    LayoffEvent,
    LeadershipChange,
    TechStack,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tech classification keyword sets
# ---------------------------------------------------------------------------

_LANGUAGE_KEYWORDS: frozenset[str] = frozenset(
    [
        "python", "go", "golang", "rust", "java", "typescript",
        "javascript", "ruby", "php", "scala", "swift", "kotlin",
    ]
)

_ML_TOOL_KEYWORDS: frozenset[str] = frozenset(
    [
        "pytorch", "tensorflow", "keras", "scikit-learn", "mlflow",
        "kubeflow", "ray", "feast", "langchain", "openai", "huggingface",
    ]
)

_DATA_TOOL_KEYWORDS: frozenset[str] = frozenset(
    [
        "spark", "kafka", "dbt", "airflow", "flink", "databricks",
        "snowflake", "bigquery", "redshift", "postgres", "mysql", "mongodb",
    ]
)

# Path to bench summary relative to the project root
_BENCH_SUMMARY_PATH = Path(__file__).parent.parent / "data" / "bench_summary.json"


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class BriefValidationError(Exception):
    """
    Raised when HiringSignalBrief or CompetitorGapBrief fails schema validation.

    Callers MUST catch this exception and must NOT write to HubSpot or Langfuse
    when it is raised (Req 16.5).
    """


# ---------------------------------------------------------------------------
# Input dataclass
# ---------------------------------------------------------------------------


@dataclass
class AssemblerInput:
    """All sub-component results required to assemble a HiringSignalBrief."""

    company_id: str
    company_name: str
    firmographic: FirmographicResult
    layoff: Optional[LayoffScanResult]
    job_posts: Optional[JobPostResult]
    leadership: Optional[LeadershipChangeResult]
    funding: Optional[FundingEventResult]
    ai_maturity: Optional[AIMaturityResult]
    competitor_gap: Optional[CompetitorGapBrief]


# ---------------------------------------------------------------------------
# Bench summary loader
# ---------------------------------------------------------------------------


def _load_bench_summary(path: Path = _BENCH_SUMMARY_PATH) -> dict:
    """
    Load and return the bench summary JSON from disk.

    Returns an empty dict if the file is missing or unparseable, logging a
    warning in that case.

    Args:
        path: Filesystem path to bench_summary.json.

    Returns:
        Parsed bench summary dict, or {} on failure.
    """
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load bench_summary.json from %s: %s", path, exc)
        return {}


# ---------------------------------------------------------------------------
# Tech stack helpers
# ---------------------------------------------------------------------------


def _classify_tech(tools: list[str]) -> tuple[list[str], list[str], list[str]]:
    """
    Classify a flat list of tech strings into languages, ML tools, and data tools.

    Each tool is lowercased before matching. A tool may only appear in one
    category (languages checked first, then ML tools, then data tools).

    Args:
        tools: Deduplicated list of tech strings.

    Returns:
        Tuple of (languages, ml_tools, data_tools) — each a sorted list.
    """
    languages: list[str] = []
    ml_tools: list[str] = []
    data_tools: list[str] = []

    for tool in tools:
        lower = tool.lower()
        if lower in _LANGUAGE_KEYWORDS:
            languages.append(lower)
        elif lower in _ML_TOOL_KEYWORDS:
            ml_tools.append(lower)
        elif lower in _DATA_TOOL_KEYWORDS:
            data_tools.append(lower)

    return sorted(languages), sorted(ml_tools), sorted(data_tools)


def _tech_stack_confidence(
    total_tools: int,
    sources_checked: list[str],
) -> Optional[str]:
    """
    Derive tech stack confidence from the number of detected tools and sources.

    Rules:
    - "high"   if ≥ 3 tools detected
    - "medium" if 1–2 tools detected
    - "low"    if 0 tools but sources were checked
    - None     if no data at all

    Args:
        total_tools: Total number of classified tools (languages + ml + data).
        sources_checked: List of source names that were queried.

    Returns:
        One of "high", "medium", "low", or None.
    """
    if total_tools >= 3:
        return "high"
    if total_tools >= 1:
        return "medium"
    if sources_checked:
        return "low"
    return None


def _build_tech_stack(
    firmographic: FirmographicResult,
    job_posts: Optional[JobPostResult],
) -> Optional[TechStack]:
    """
    Build a TechStack from firmographic BuiltWith data and job-post tech signals.

    Merges and deduplicates tech strings from both sources, then classifies
    them into languages, ML tools, and data tools.  Returns None when no
    source data is available.

    Args:
        firmographic: FirmographicResult containing builtwith_tech.
        job_posts: Optional JobPostResult containing tech_signals.

    Returns:
        TechStack instance, or None if no tech data is available.
    """
    raw_tools: list[str] = list(firmographic.builtwith_tech)
    sources_checked: list[str] = []

    if firmographic.builtwith_tech:
        sources_checked.append("builtwith")

    if job_posts is not None:
        raw_tools.extend(job_posts.tech_signals)
        if job_posts.sources_checked:
            sources_checked.extend(job_posts.sources_checked)

    if not raw_tools and not sources_checked:
        return None

    # Deduplicate preserving first occurrence (case-insensitive)
    seen: set[str] = set()
    deduped: list[str] = []
    for tool in raw_tools:
        lower = tool.lower()
        if lower not in seen:
            seen.add(lower)
            deduped.append(lower)

    languages, ml_tools, data_tools = _classify_tech(deduped)
    total = len(languages) + len(ml_tools) + len(data_tools)
    confidence = _tech_stack_confidence(total, sources_checked)

    return TechStack(
        languages=languages,
        ml_tools=ml_tools,
        data_tools=data_tools,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Bench mismatch helper
# ---------------------------------------------------------------------------


def _compute_bench_mismatch(
    tech_stack: Optional[TechStack],
    available_engineers: dict,
) -> Optional[bool]:
    """
    Determine whether the prospect's tech stack has no matching bench capacity.

    Checks languages and ML tools against the available_engineers keys.
    Returns True (mismatch) when no overlap is found, False when at least one
    match exists, and None when tech_stack is unavailable.

    Args:
        tech_stack: Assembled TechStack, or None.
        available_engineers: Dict mapping stack name → engineer count from
                             bench_summary.json.

    Returns:
        True if no bench match, False if at least one match, None if no data.
    """
    if tech_stack is None:
        return None

    prospect_stacks = set(tech_stack.languages) | set(tech_stack.ml_tools)
    if not prospect_stacks:
        return None

    bench_stacks = {k.lower() for k, v in available_engineers.items() if v and v > 0}
    has_match = bool(prospect_stacks & bench_stacks)
    return not has_match


# ---------------------------------------------------------------------------
# Sub-result mapping helpers
# ---------------------------------------------------------------------------


def _build_funding_event(funding: Optional[FundingEventResult]) -> Optional[FundingEvent]:
    """
    Map a FundingEventResult to a FundingEvent model, or None if not detected.

    Args:
        funding: FundingEventResult from FundingEventFetcher, or None.

    Returns:
        FundingEvent if detected=True and round_type/close_date are present,
        otherwise None.
    """
    if funding is None or not funding.detected:
        return None
    if not funding.round_type or not funding.close_date:
        return None
    return FundingEvent(
        round_type=funding.round_type,
        amount_usd=funding.amount_usd,
        close_date=funding.close_date,
        confidence=funding.confidence,
    )


def _build_layoff_event(layoff: Optional[LayoffScanResult]) -> Optional[LayoffEvent]:
    """
    Map a LayoffScanResult to a LayoffEvent model, or None if not provided.

    Args:
        layoff: LayoffScanResult from LayoffScanner, or None.

    Returns:
        LayoffEvent if layoff is provided, otherwise None.
    """
    if layoff is None:
        return None
    return LayoffEvent(
        event_date=layoff.event_date,
        headcount_affected=layoff.headcount_affected,
        percentage_cut=layoff.percentage_cut,
        confidence=None,  # LayoffScanResult has no confidence field; propagate null
    )


def _build_leadership_change(
    leadership: Optional[LeadershipChangeResult],
) -> Optional[LeadershipChange]:
    """
    Map a LeadershipChangeResult to a LeadershipChange model, or None.

    Only maps when detected=True and required fields are present.

    Args:
        leadership: LeadershipChangeResult from LeadershipChangeDetector, or None.

    Returns:
        LeadershipChange if detected=True and role/appointment_date are present,
        otherwise None.
    """
    if leadership is None or not leadership.detected:
        return None
    if not leadership.role or not leadership.appointment_date:
        return None
    return LeadershipChange(
        role=leadership.role,
        appointment_date=leadership.appointment_date,
        confidence=leadership.confidence,
    )


# ---------------------------------------------------------------------------
# HiringSignalBriefAssembler
# ---------------------------------------------------------------------------


class HiringSignalBriefAssembler:
    """
    Merges all sub-component results into a validated HiringSignalBrief and
    optional CompetitorGapBrief.

    Propagates nulls with confidence: null for missing data sources.
    Raises BriefValidationError if schema validation fails, preventing any
    downstream writes to HubSpot or Langfuse (Req 16.5).
    """

    def assemble(
        self, inputs: AssemblerInput
    ) -> tuple[HiringSignalBrief, Optional[CompetitorGapBrief]]:
        """
        Merge all sub-results into validated HiringSignalBrief and CompetitorGapBrief.

        Raises BriefValidationError if validation fails (Req 16.5).

        Args:
            inputs: AssemblerInput containing all sub-component results.

        Returns:
            Tuple of (HiringSignalBrief, CompetitorGapBrief | None).

        Raises:
            BriefValidationError: When the assembled brief fails Pydantic schema
                                  validation. Callers must not write to HubSpot
                                  or Langfuse when this is raised.
        """
        bench = _load_bench_summary()
        bench_version: Optional[str] = bench.get("version")
        available_engineers: dict = bench.get("available_engineers", {})

        tech_stack = _build_tech_stack(inputs.firmographic, inputs.job_posts)
        bench_mismatch = _compute_bench_mismatch(tech_stack, available_engineers)

        funding_event = _build_funding_event(inputs.funding)
        layoff_event = _build_layoff_event(inputs.layoff)
        leadership_change = _build_leadership_change(inputs.leadership)

        job_post_count: Optional[int] = None
        job_post_velocity_60d: Optional[float] = None
        job_post_confidence: Optional[str] = None
        if inputs.job_posts is not None:
            job_post_count = inputs.job_posts.job_post_count
            job_post_velocity_60d = inputs.job_posts.job_post_velocity_60d
            job_post_confidence = inputs.job_posts.job_post_confidence

        ai_maturity_score: Optional[int] = None
        ai_maturity_confidence: Optional[str] = None
        ai_maturity_justification = []
        if inputs.ai_maturity is not None:
            ai_maturity_score = inputs.ai_maturity.score
            ai_maturity_confidence = inputs.ai_maturity.confidence
            ai_maturity_justification = inputs.ai_maturity.justification

        brief_data = {
            "schema_version": "1.0",
            "company_id": inputs.company_id,
            "company_name": inputs.company_name,
            "last_enriched_at": inputs.firmographic.last_enriched_at
            or datetime.now(timezone.utc).isoformat(),
            "crunchbase_id": inputs.firmographic.crunchbase_id,
            "bench_summary_version": bench_version,
            "bench_mismatch": bench_mismatch,
            "tech_stack": tech_stack.model_dump() if tech_stack is not None else None,
            "funding_event": funding_event.model_dump() if funding_event is not None else None,
            "layoff_event": layoff_event.model_dump() if layoff_event is not None else None,
            "job_post_count": job_post_count,
            "job_post_velocity_60d": job_post_velocity_60d,
            "job_post_confidence": job_post_confidence,
            "leadership_change": (
                leadership_change.model_dump() if leadership_change is not None else None
            ),
            "ai_maturity_score": ai_maturity_score,
            "ai_maturity_confidence": ai_maturity_confidence,
            "ai_maturity_justification": [
                e.model_dump() for e in ai_maturity_justification
            ],
            "icp_segment": None,
            "icp_confidence": None,
            "icp_signals_used": [],
        }

        try:
            brief = validate_hiring_signal_brief(brief_data)
        except ValidationError as exc:
            logger.error(
                "HiringSignalBrief validation failed for company_id=%r: %s",
                inputs.company_id,
                exc,
            )
            raise BriefValidationError(str(exc)) from exc

        return brief, inputs.competitor_gap
