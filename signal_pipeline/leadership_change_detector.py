"""
LeadershipChangeDetector — scans Crunchbase ODM `leadership_hire` events for
CTO or VP Engineering appointments within the last 90 days.

Req 2.4: Check Crunchbase and public press releases for a CTO or VP Engineering
         appointment in the last 90 days.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Role detection patterns
# ---------------------------------------------------------------------------

_CTO_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bCTO\b", re.IGNORECASE),
    re.compile(r"\bChief Technology Officer\b", re.IGNORECASE),
    re.compile(r"\bChief Technical Officer\b", re.IGNORECASE),
]

_VP_ENG_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bVP (of )?Engineering\b", re.IGNORECASE),
    re.compile(r"\bVice President (of )?Engineering\b", re.IGNORECASE),
    re.compile(r"\bHead of Engineering\b", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class LeadershipChangeResult:
    """Outcome of a leadership change detection scan."""

    detected: bool                  # True if CTO/VP Eng found in window
    role: Optional[str]             # Normalized role: "CTO" or "VP Engineering"
    appointment_date: Optional[str] # YYYY-MM-DD
    confidence: Optional[str]       # "high" | "medium" | "low" | None
    source_url: Optional[str]       # Link from Crunchbase record
    label: Optional[str]            # Original label text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_role(label: str) -> Optional[str]:
    """
    Return the normalized role string if the label matches a CTO or VP Eng
    pattern, otherwise return None.

    Args:
        label: The headline/description text from a leadership_hire event.

    Returns:
        "CTO", "VP Engineering", or None.
    """
    for pattern in _CTO_PATTERNS:
        if pattern.search(label):
            return "CTO"
    for pattern in _VP_ENG_PATTERNS:
        if pattern.search(label):
            return "VP Engineering"
    return None


def _parse_date(value: str) -> Optional[date]:
    """
    Parse a YYYY-MM-DD date string; return None on failure.

    Args:
        value: Raw date string from the event record.
    """
    if not value or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _assign_confidence(event_date: Optional[date], role: Optional[str]) -> str:
    """
    Assign a confidence level based on date and role availability.

    - "high"   — date present and role clearly identified
    - "medium" — date present but role inferred from partial match
    - "low"    — date missing or role ambiguous

    Args:
        event_date: Parsed appointment date, or None if unavailable.
        role: Normalized role string, or None if not identified.
    """
    if event_date is not None and role is not None:
        return "high"
    if event_date is not None:
        return "medium"
    return "low"


def _null_result() -> LeadershipChangeResult:
    """Return a no-match result with all optional fields set to None."""
    return LeadershipChangeResult(
        detected=False,
        role=None,
        appointment_date=None,
        confidence=None,
        source_url=None,
        label=None,
    )


# ---------------------------------------------------------------------------
# LeadershipChangeDetector
# ---------------------------------------------------------------------------

class LeadershipChangeDetector:
    """
    Scans raw Crunchbase `leadership_hire` events for CTO or VP Engineering
    appointments within a 90-day window.

    Never fabricates values — returns detected=False when no match is found.
    """

    WINDOW_DAYS: int = 90

    def detect(
        self,
        raw_leadership_hire: list[dict],
        reference_date: Optional[date] = None,
    ) -> LeadershipChangeResult:
        """
        Scan raw_leadership_hire events for CTO/VP Eng appointments within
        the last 90 days.

        Returns the most recent matching event, or a result with
        detected=False when none found.

        Args:
            raw_leadership_hire: List of leadership hire dicts from Crunchbase ODM.
                Each dict may contain: key_event_date, label, link, uuid.
            reference_date: Date to measure the 90-day window from.
                            Defaults to today (UTC).

        Returns:
            LeadershipChangeResult with the most recent CTO/VP Eng appointment,
            or detected=False if none found within the window.
        """
        if not raw_leadership_hire:
            return _null_result()

        ref = reference_date or date.today()
        window_start = ref - timedelta(days=self.WINDOW_DAYS)

        candidates: list[tuple[date, str, LeadershipChangeResult]] = []

        for event in raw_leadership_hire:
            if not isinstance(event, dict):
                continue

            label: str = event.get("label", "") or ""
            role = _detect_role(label)
            if role is None:
                continue

            raw_date = event.get("key_event_date", "") or ""
            event_date = _parse_date(raw_date)

            if event_date is None or event_date < window_start or event_date > ref:
                logger.debug(
                    "Leadership hire event outside window or unparseable date: %r", raw_date
                )
                continue

            confidence = _assign_confidence(event_date, role)
            result = LeadershipChangeResult(
                detected=True,
                role=role,
                appointment_date=event_date.isoformat(),
                confidence=confidence,
                source_url=event.get("link") or None,
                label=label or None,
            )
            candidates.append((event_date, label, result))

        if not candidates:
            return _null_result()

        # Return the most recent matching event
        candidates.sort(key=lambda t: t[0], reverse=True)
        return candidates[0][2]
