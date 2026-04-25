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
    HiringVelocity,
    LayoffEvent,
    LeadershipChange,
    TechStack,
    VelocityLabel,
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

# Authoritative seed bench summary; falls back to legacy path
_SEED_BENCH_PATH = (
    Path(__file__).parent.parent
    / "data" / "tenacious_sales_data" / "seed" / "bench_summary.json"
)
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


def _load_bench_summary() -> dict:
    """Load bench summary JSON, preferring the authoritative seed file."""
    for path in (_SEED_BENCH_PATH, _BENCH_SUMMARY_PATH):
        try:
            with path.open(encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            continue
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load bench_summary from %s: %s", path, exc)
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
# HiringVelocity builder
# ---------------------------------------------------------------------------


def _compute_velocity_label(today: int, days_ago: int) -> VelocityLabel:
    if days_ago == 0:
        return "insufficient_signal" if today == 0 else "tripled_or_more"
    ratio = today / days_ago
    if ratio >= 3.0:
        return "tripled_or_more"
    if ratio >= 1.8:
        return "doubled"
    if ratio >= 1.1:
        return "increased_modestly"
    if ratio >= 0.9:
        return "flat"
    return "declined"


def _build_hiring_velocity(job_posts: Optional["JobPostResult"]) -> Optional[HiringVelocity]:  # type: ignore[name-defined]
    """Build HiringVelocity from JobPostResult. Returns None when no job-post data."""
    if job_posts is None or job_posts.job_post_count is None:
        return None

    today = job_posts.job_post_count
    # job_post_velocity_60d is always None in current scraper (no historical snapshot)
    days_ago = 0
    velocity_label: VelocityLabel = "insufficient_signal"
    signal_confidence = 0.3  # low confidence since we have no 60-day baseline

    if job_posts.job_post_velocity_60d is not None:
        days_ago = int(job_posts.job_post_velocity_60d)
        velocity_label = _compute_velocity_label(today, days_ago)
        signal_confidence = {"high": 0.85, "medium": 0.65, "low": 0.4}.get(
            job_posts.job_post_confidence or "low", 0.4
        )

    sources = [s for s in (job_posts.sources_checked or []) if s in {
        "builtin", "wellfound", "linkedin_public", "company_careers_page"
    }]

    return HiringVelocity(
        open_roles_today=today,
        open_roles_60_days_ago=days_ago,
        velocity_label=velocity_label,
        signal_confidence=signal_confidence,
        sources=sources,
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

    bench_stacks: set[str] = set()
    for k, v in available_engineers.items():
        # Seed schema: v is a dict with junior/mid/senior counts
        if isinstance(v, dict):
            total = sum(
                v.get(lvl, 0) or 0 for lvl in ("junior", "mid", "senior")
            )
            if total > 0:
                bench_stacks.add(k.lower())
                for skill in v.get("skills", []):
                    bench_stacks.add(skill.lower())
        elif isinstance(v, int) and v > 0:
            bench_stacks.add(k.lower())
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


def _layoff_confidence(
    headcount_affected: Optional[int],
    percentage_cut: Optional[float],
) -> Optional[str]:
    """Derive confidence for a layoff event from available quantitative data.

    Rules:
    - "high"   if both headcount and percentage are present
    - "medium" if exactly one quantitative field is present
    - "low"    if neither is present but an event was detected
    """
    has_headcount = headcount_affected is not None
    has_pct = percentage_cut is not None
    if has_headcount and has_pct:
        return "high"
    if has_headcount or has_pct:
        return "medium"
    return "low"


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
        confidence=_layoff_confidence(layoff.headcount_affected, layoff.percentage_cut),
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
        bench_version: Optional[str] = bench.get("as_of")
        available_engineers: dict = bench.get("stacks", bench.get("available_engineers", {}))

        tech_stack = _build_tech_stack(inputs.firmographic, inputs.job_posts)
        bench_mismatch = _compute_bench_mismatch(tech_stack, available_engineers)

        funding_event = _build_funding_event(inputs.funding)
        layoff_event = _build_layoff_event(inputs.layoff)
        leadership_change = _build_leadership_change(inputs.leadership)

        hiring_velocity = _build_hiring_velocity(inputs.job_posts)

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
            "hiring_velocity": hiring_velocity.model_dump() if hiring_velocity is not None else None,
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
            "employee_count_min": inputs.firmographic.employee_count_min,
            "employee_count_max": inputs.firmographic.employee_count_max,
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
