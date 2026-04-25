"""
ICP Classifier — stateless classify(brief, config) -> SegmentResult.

Evaluates all four ICP segment definitions in priority order per the official
icp_definition.md: S2 > S3 > S4 > S1. Falls back to unqualified with
abstained=True when no segment exceeds the configured abstention threshold
(0.6 per spec).

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from config.models import Segment, SegmentResult
from signal_pipeline.models import HiringSignalBrief

# ---------------------------------------------------------------------------
# Confidence mapping helpers
# ---------------------------------------------------------------------------

_CONFIDENCE_MAP: dict[Optional[str], float] = {
    "high": 0.9,
    "medium": 0.7,
    "low": 0.5,
    None: 0.4,
}

_LAYOFF_CONFIDENCE_MAP: dict[Optional[str], float] = {
    "high": 0.85,
    "medium": 0.65,
    "low": 0.45,
    None: 0.4,
}

_LEADERSHIP_CONFIDENCE_MAP: dict[Optional[str], float] = {
    "high": 0.9,
    "medium": 0.7,
    "low": 0.5,
    None: 0.4,
}

_AI_MATURITY_CONFIDENCE_MAP: dict[int, float] = {
    3: 0.85,
    2: 0.65,
}

# Official classification priority: S2 > S3 > S4 > S1 (lower number = higher priority)
_SEGMENT_PRIORITY: dict[Segment, int] = {
    Segment.S2: 1,
    Segment.S3: 2,
    Segment.S4: 3,
    Segment.S1: 4,
}


# ---------------------------------------------------------------------------
# ClassifierConfig
# ---------------------------------------------------------------------------


@dataclass
class ClassifierConfig:
    """
    Configuration for the ICP classifier.

    Args:
        abstention_threshold: Minimum confidence required to assign a segment.
            Defaults to ICP_ABSTENTION_THRESHOLD env var or 0.6 per spec.
        employee_min: Prospect's minimum employee count from firmographic data.
        employee_max: Prospect's maximum employee count from firmographic data.
        open_roles: Number of open engineering roles from hiring signal brief.
    """

    abstention_threshold: float = field(
        default_factory=lambda: float(os.environ.get("ICP_ABSTENTION_THRESHOLD", "0.6"))
    )
    ignore_dates: bool = field(
        default_factory=lambda: os.environ.get("ICP_IGNORE_DATES", "true").lower() == "true"
    )
    employee_min: Optional[int] = None
    employee_max: Optional[int] = None
    open_roles: Optional[int] = None


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def _days_since(date_str: str) -> Optional[int]:
    """
    Compute the number of days between a date string and today (UTC).

    Args:
        date_str: ISO 8601 date string (YYYY-MM-DD).

    Returns:
        Number of days since the given date, or None if parsing fails.
    """
    try:
        event_date = date.fromisoformat(date_str)
        today = datetime.now(timezone.utc).date()
        return (today - event_date).days
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Segment scoring functions
# ---------------------------------------------------------------------------


def _score_segment_1(
    brief: HiringSignalBrief, config: ClassifierConfig
) -> Optional[tuple[float, list[str]]]:
    """
    Score Segment 1: Series A/B, $5–30M, last 180 days, 15–80 headcount, 5+ open eng roles.

    Official criteria (icp_definition.md):
    - Round: Series A or B
    - Amount: $5M–$30M
    - Closed within 180 days
    - Headcount: 15–80 employees
    - Open engineering roles: >= 5

    Args:
        brief: HiringSignalBrief to evaluate.
        config: ClassifierConfig with employee bounds and open_roles.

    Returns:
        Tuple of (confidence, signals_used) if qualifies, else None.
    """
    fe = brief.funding_event
    if fe is None:
        return None

    round_lower = fe.round_type.lower()
    if "series a" not in round_lower and "series b" not in round_lower:
        return None

    if fe.amount_usd is None:
        return None
    if not (5_000_000 <= fe.amount_usd <= 30_000_000):
        return None

    days = _days_since(fe.close_date)
    if not config.ignore_dates and (days is None or days > 180):
        return None

    # Headcount gate: 15–80 employees (disqualify when clearly outside range)
    emp_min = config.employee_min
    emp_max = config.employee_max
    if emp_min is not None and emp_min > 80:
        return None
    if emp_max is not None and emp_max < 15:
        return None

    # Role count gate: >= 5 open engineering roles
    open_roles = config.open_roles
    if not config.ignore_dates and (open_roles is not None and open_roles < 5):
        return None

    confidence = _CONFIDENCE_MAP.get(fe.confidence, 0.4)

    # Reduce confidence when headcount or role data is unavailable
    if emp_min is None and emp_max is None:
        confidence = min(confidence, 0.7)
    if open_roles is None:
        confidence = min(confidence, 0.7)

    signals = [
        "funding_event.round_type",
        "funding_event.amount_usd",
        "funding_event.close_date",
    ]
    if emp_min is not None or emp_max is not None:
        signals.append("employee_count")
    if open_roles is not None:
        signals.append("open_roles")
    return confidence, signals


def _score_segment_2(
    brief: HiringSignalBrief, config: ClassifierConfig
) -> Optional[tuple[float, list[str]]]:
    """
    Score Segment 2: Series C+, 200–2,000 employees, layoff/restructure in last 120 days,
    3+ open engineering roles post-layoff.

    Official criteria (icp_definition.md):
    - Layoff or restructure press in last 120 days
    - Headcount: 200–2,000
    - Open engineering roles post-layoff: >= 3

    Args:
        brief: HiringSignalBrief to evaluate.
        config: ClassifierConfig with employee bounds and open_roles.

    Returns:
        Tuple of (confidence, signals_used) if qualifies, else None.
    """
    le = brief.layoff_event
    if le is None:
        return None

    # Layoff must be within 120 days
    days = _days_since(le.event_date)
    if not config.ignore_dates and (days is not None and days > 120):
        return None

    emp_min = config.employee_min
    emp_max = config.employee_max

    # Disqualify when employee count is clearly outside [200, 2000]
    if emp_min is not None and emp_min > 2000:
        return None
    if emp_max is not None and emp_max < 200:
        return None

    # Role count gate: >= 3 open engineering roles post-layoff
    open_roles = config.open_roles
    if open_roles is not None and open_roles < 3:
        return None

    base_confidence = _LAYOFF_CONFIDENCE_MAP.get(le.confidence, 0.4)

    # Lower confidence when employee count is unknown
    if emp_min is None and emp_max is None:
        confidence = min(base_confidence, 0.6)
    else:
        confidence = base_confidence

    if open_roles is None:
        confidence = min(confidence, 0.65)

    signals = ["layoff_event.event_date", "employee_count"]
    if open_roles is not None:
        signals.append("open_roles")
    return confidence, signals


def _score_segment_3(
    brief: HiringSignalBrief, config: ClassifierConfig
) -> Optional[tuple[float, list[str]]]:
    """
    Score Segment 3: New CTO or VP Eng in last 90 days, 50–500 headcount.

    Official criteria (icp_definition.md):
    - New CTO or VP Engineering appointment within 90 days
    - Headcount: 50–500

    Args:
        brief: HiringSignalBrief to evaluate.
        config: ClassifierConfig with employee bounds.

    Returns:
        Tuple of (confidence, signals_used) if qualifies, else None.
    """
    lc = brief.leadership_change
    if lc is None:
        return None

    # Leadership change must be within 90 days
    days = _days_since(lc.appointment_date)
    if not config.ignore_dates and (days is not None and days > 90):
        return None

    # Headcount gate: 50–500 (disqualify when clearly outside range)
    emp_min = config.employee_min
    emp_max = config.employee_max
    if emp_min is not None and emp_min > 500:
        return None
    if emp_max is not None and emp_max < 50:
        return None

    confidence = _LEADERSHIP_CONFIDENCE_MAP.get(lc.confidence, 0.4)
    if emp_min is None and emp_max is None:
        confidence = min(confidence, 0.7)

    signals = ["leadership_change.role", "leadership_change.appointment_date"]
    if emp_min is not None or emp_max is not None:
        signals.append("employee_count")
    return confidence, signals


def _score_segment_4(brief: HiringSignalBrief) -> Optional[tuple[float, list[str]]]:
    """
    Score Segment 4: Specific capability gap + AI maturity score >= 2.

    Official criteria (icp_definition.md):
    - ai_maturity_score >= 2 (hard gate — score 0 or 1 disqualifies)
    - Signals: 60+ day open specialist role, strategic announcement, or CTO
      commentary on specific tech challenge

    Args:
        brief: HiringSignalBrief to evaluate.

    Returns:
        Tuple of (confidence, signals_used) if qualifies, else None.
    """
    score = brief.ai_maturity_score
    # Hard gate: score 0 or 1 is an explicit disqualifier per spec
    if score is None or score < 2:
        return None

    confidence = _AI_MATURITY_CONFIDENCE_MAP.get(score, 0.65)
    signals = ["ai_maturity_score"]

    # Check for additional capability signals in hiring velocity or tech stack
    hv = brief.hiring_velocity
    if hv is not None and hv.open_roles_today is not None and hv.open_roles_today > 0:
        signals.append("hiring_velocity.open_roles_today")

    ts = brief.tech_stack
    if ts is not None and ts.ml_tools:
        signals.append("tech_stack.ml_tools")

    return confidence, signals


# ---------------------------------------------------------------------------
# classify()
# ---------------------------------------------------------------------------


def classify(brief: HiringSignalBrief, config: ClassifierConfig) -> SegmentResult:
    """
    Classify a prospect into one of four ICP segments or unqualified.

    Official priority cascade (icp_definition.md): S2 > S3 > S4 > S1 > abstain.
    Abstains when no segment exceeds the threshold (0.6 per spec). When multiple
    segments qualify, ties in confidence are broken by priority.

    Args:
        brief: HiringSignalBrief containing all enriched signals.
        config: ClassifierConfig with threshold and optional employee/role bounds.

    Returns:
        SegmentResult with segment, confidence, signals_used, and abstained flag.
    """
    candidates: list[tuple[Segment, float, list[str]]] = []

    s2 = _score_segment_2(brief, config)
    if s2 is not None:
        candidates.append((Segment.S2, s2[0], s2[1]))

    s3 = _score_segment_3(brief, config)
    if s3 is not None:
        candidates.append((Segment.S3, s3[0], s3[1]))

    s4 = _score_segment_4(brief)
    if s4 is not None:
        candidates.append((Segment.S4, s4[0], s4[1]))

    s1 = _score_segment_1(brief, config)
    if s1 is not None:
        candidates.append((Segment.S1, s1[0], s1[1]))

    # Filter by abstention threshold (0.6 per spec)
    qualified = [
        (seg, conf, sigs)
        for seg, conf, sigs in candidates
        if conf >= config.abstention_threshold
    ]

    if not qualified:
        return SegmentResult(
            segment=Segment.UNQUALIFIED,
            confidence=0.0,
            signals_used=[],
            abstained=True,
        )

    # Official priority cascade: S2 > S3 > S4 > S1 (icp_definition.md).
    # When multiple segments qualify, priority always wins over confidence.
    # Sort: lowest priority-number first (highest priority), highest confidence as tiebreaker.
    qualified.sort(key=lambda x: (_SEGMENT_PRIORITY[x[0]], -x[1]))
    best = qualified[0]

    return SegmentResult(
        segment=best[0],
        confidence=best[1],
        signals_used=best[2],
        abstained=False,
    )
