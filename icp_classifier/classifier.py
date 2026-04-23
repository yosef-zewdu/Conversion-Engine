"""
ICP Classifier — stateless classify(brief, config) -> SegmentResult.

Evaluates all four ICP segment definitions in priority order and returns the
highest-confidence match. Falls back to unqualified with abstained=True when
no segment exceeds the configured abstention threshold.

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

# Priority order: lower number = higher priority
_SEGMENT_PRIORITY: dict[Segment, int] = {
    Segment.S1: 1,
    Segment.S2: 2,
    Segment.S3: 3,
    Segment.S4: 4,
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
            Defaults to ICP_ABSTENTION_THRESHOLD env var or 0.4.
        employee_min: Prospect's minimum employee count from firmographic data.
        employee_max: Prospect's maximum employee count from firmographic data.
    """

    abstention_threshold: float = field(
        default_factory=lambda: float(os.environ.get("ICP_ABSTENTION_THRESHOLD", "0.4"))
    )
    employee_min: Optional[int] = None
    employee_max: Optional[int] = None


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


def _score_segment_1(brief: HiringSignalBrief) -> Optional[tuple[float, list[str]]]:
    """
    Score Segment 1: Series A/B funding $5–30M in last 6 months (180 days).

    Args:
        brief: HiringSignalBrief to evaluate.

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
    if days is None or days > 180:
        return None

    confidence = _CONFIDENCE_MAP.get(fe.confidence, 0.4)
    signals = [
        "funding_event.round_type",
        "funding_event.amount_usd",
        "funding_event.close_date",
    ]
    return confidence, signals


def _score_segment_2(
    brief: HiringSignalBrief, config: ClassifierConfig
) -> Optional[tuple[float, list[str]]]:
    """
    Score Segment 2: 200–2,000 employees + layoff/restructure in last 120 days.

    Args:
        brief: HiringSignalBrief to evaluate.
        config: ClassifierConfig with optional employee count bounds.

    Returns:
        Tuple of (confidence, signals_used) if qualifies, else None.
    """
    le = brief.layoff_event
    if le is None:
        return None

    emp_min = config.employee_min
    emp_max = config.employee_max

    # Disqualify when employee count is clearly outside [200, 2000]
    if emp_min is not None and emp_min > 2000:
        return None
    if emp_max is not None and emp_max < 200:
        return None

    base_confidence = _LAYOFF_CONFIDENCE_MAP.get(le.confidence, 0.4)

    # Lower confidence when employee count is unknown
    if emp_min is None and emp_max is None:
        confidence = min(base_confidence, 0.5)
    else:
        confidence = base_confidence

    signals = ["layoff_event.event_date", "employee_count"]
    return confidence, signals


def _score_segment_3(brief: HiringSignalBrief) -> Optional[tuple[float, list[str]]]:
    """
    Score Segment 3: New CTO/VP Eng appointment in last 90 days.

    Args:
        brief: HiringSignalBrief to evaluate.

    Returns:
        Tuple of (confidence, signals_used) if qualifies, else None.
    """
    lc = brief.leadership_change
    if lc is None:
        return None

    confidence = _LEADERSHIP_CONFIDENCE_MAP.get(lc.confidence, 0.4)
    signals = ["leadership_change.role", "leadership_change.appointment_date"]
    return confidence, signals


def _score_segment_4(brief: HiringSignalBrief) -> Optional[tuple[float, list[str]]]:
    """
    Score Segment 4: Capability gap signal + AI maturity score >= 2.

    Args:
        brief: HiringSignalBrief to evaluate.

    Returns:
        Tuple of (confidence, signals_used) if qualifies, else None.
    """
    score = brief.ai_maturity_score
    if score is None or score < 2:
        return None

    ts = brief.tech_stack
    if ts is None or not ts.ml_tools:
        return None

    confidence = _AI_MATURITY_CONFIDENCE_MAP.get(score, 0.65)
    signals = ["ai_maturity_score", "tech_stack.ml_tools"]
    return confidence, signals


# ---------------------------------------------------------------------------
# classify()
# ---------------------------------------------------------------------------


def classify(brief: HiringSignalBrief, config: ClassifierConfig) -> SegmentResult:
    """
    Classify a prospect into one of four ICP segments or unqualified.

    Evaluates all segments, picks the highest-confidence one (ties broken by
    priority S1 > S2 > S3 > S4). Returns unqualified with abstained=True when
    no segment exceeds the abstention threshold.

    Args:
        brief: HiringSignalBrief containing all enriched signals.
        config: ClassifierConfig with threshold and optional employee bounds.

    Returns:
        SegmentResult with segment, confidence, signals_used, and abstained flag.
    """
    candidates: list[tuple[Segment, float, list[str]]] = []

    s1 = _score_segment_1(brief)
    if s1 is not None:
        candidates.append((Segment.S1, s1[0], s1[1]))

    s2 = _score_segment_2(brief, config)
    if s2 is not None:
        candidates.append((Segment.S2, s2[0], s2[1]))

    s3 = _score_segment_3(brief)
    if s3 is not None:
        candidates.append((Segment.S3, s3[0], s3[1]))

    s4 = _score_segment_4(brief)
    if s4 is not None:
        candidates.append((Segment.S4, s4[0], s4[1]))

    # Filter by abstention threshold
    qualified = [
        (seg, conf, sigs)
        for seg, conf, sigs in candidates
        if conf > config.abstention_threshold
    ]

    if not qualified:
        return SegmentResult(
            segment=Segment.UNQUALIFIED,
            confidence=0.0,
            signals_used=[],
            abstained=True,
        )

    # Pick highest confidence; break ties by segment priority (lower = higher priority)
    best = max(
        qualified,
        key=lambda x: (x[1], -_SEGMENT_PRIORITY[x[0]]),
    )

    return SegmentResult(
        segment=best[0],
        confidence=best[1],
        signals_used=best[2],
        abstained=False,
    )
