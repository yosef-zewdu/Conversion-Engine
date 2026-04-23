"""
FundingEventFetcher — parses Crunchbase ODM funding_rounds_list data for
funding events within the last 180 days.

Req 2.5: Check Crunchbase for funding events in the last 180 days; record
         round type, amount, and close date.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Round type normalisation map
# ---------------------------------------------------------------------------

_ROUND_TYPE_NORMALISE: dict[str, str] = {
    "seed round": "Seed",
    "series a round": "Series A",
    "series b round": "Series B",
    "series c round": "Series C",
    "series d round": "Series D",
    "series e round": "Series E",
    "series f round": "Series F",
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class FundingEventResult:
    """Represents the outcome of a funding event lookup."""

    detected: bool                  # True if a funding event found in window
    round_type: Optional[str]       # e.g. "Series A", "Series B", "Seed"
    amount_usd: Optional[float]     # from money_raised.value_usd
    close_date: Optional[str]       # YYYY-MM-DD from announced_on
    confidence: Optional[str]       # "high" | "medium" | "low" | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(value: str) -> Optional[date]:
    """Parse a YYYY-MM-DD date string; return None on failure."""
    if not value or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _extract_round_type(title: str) -> Optional[str]:
    """
    Extract and normalise the round type from a funding round title.

    Splits on " - " and takes the first part.  Normalises common variants
    such as "Seed Round" → "Seed" and "Series A Round" → "Series A".

    Args:
        title: The raw title string from the funding round record.

    Returns:
        Normalised round type string, or None if title is empty.
    """
    if not title or not title.strip():
        return None

    raw = title.strip()
    if " - " in raw:
        raw = raw.split(" - ")[0].strip()

    if not raw:
        return None

    normalised = _ROUND_TYPE_NORMALISE.get(raw.lower())
    return normalised if normalised is not None else raw


def _extract_amount_usd(round_dict: dict) -> Optional[float]:
    """
    Extract the USD amount from a funding round dict's money_raised field.

    Returns None if money_raised is absent or value_usd is null/missing.

    Args:
        round_dict: A single funding round dict from funding_rounds_list.
    """
    money_raised = round_dict.get("money_raised")
    if not isinstance(money_raised, dict):
        return None
    value_usd = money_raised.get("value_usd")
    if value_usd is None:
        return None
    try:
        return float(value_usd)
    except (TypeError, ValueError):
        return None


def _assign_confidence(amount_usd: Optional[float], close_date: Optional[str]) -> str:
    """
    Assign a confidence level based on available data.

    Rules:
      - amount_usd present and non-null → "high"
      - amount_usd null but close_date present → "medium"
      - neither present → "low"

    Args:
        amount_usd: The USD amount, or None.
        close_date: The close date string, or None.

    Returns:
        One of "high", "medium", or "low".
    """
    if amount_usd is not None:
        return "high"
    if close_date is not None:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# FundingEventFetcher
# ---------------------------------------------------------------------------

_NO_EVENT = FundingEventResult(
    detected=False,
    round_type=None,
    amount_usd=None,
    close_date=None,
    confidence=None,
)


class FundingEventFetcher:
    """
    Parses a pre-loaded list of Crunchbase funding round dicts and returns
    the most recent funding event within the last 180 days.

    Never fabricates values; missing or null fields are returned as None.
    """

    WINDOW_DAYS: int = 180

    def fetch(
        self,
        raw_funding_rounds: list[dict],
        reference_date: Optional[date] = None,
    ) -> FundingEventResult:
        """
        Return the most recent funding event within the last 180 days.

        Args:
            raw_funding_rounds: List of funding round dicts from
                                FirmographicResult.raw_funding_rounds.
            reference_date: Date to measure the 180-day window from.
                            Defaults to today (UTC).

        Returns:
            FundingEventResult with detected=True and populated fields when a
            matching round is found; detected=False with all fields None when
            no round falls within the window.
        """
        if not raw_funding_rounds:
            return _NO_EVENT

        ref = reference_date or date.today()
        window_start = ref - timedelta(days=self.WINDOW_DAYS)

        candidates: list[tuple[date, dict]] = []
        for round_dict in raw_funding_rounds:
            announced_on = _parse_date(round_dict.get("announced_on", ""))
            if announced_on is None:
                continue
            if announced_on < window_start or announced_on > ref:
                continue
            candidates.append((announced_on, round_dict))

        if not candidates:
            return _NO_EVENT

        # Most recent first
        candidates.sort(key=lambda t: t[0], reverse=True)
        most_recent_date, most_recent_round = candidates[0]

        close_date = most_recent_date.isoformat()
        amount_usd = _extract_amount_usd(most_recent_round)
        round_type = _extract_round_type(most_recent_round.get("title", ""))
        confidence = _assign_confidence(amount_usd, close_date)

        logger.debug(
            "FundingEventFetcher: found round_type=%r amount_usd=%r close_date=%r",
            round_type,
            amount_usd,
            close_date,
        )

        return FundingEventResult(
            detected=True,
            round_type=round_type,
            amount_usd=amount_usd,
            close_date=close_date,
            confidence=confidence,
        )
