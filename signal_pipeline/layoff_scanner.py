"""
LayoffScanner — looks up layoff events for a company in the layoffs.fyi CSV dataset.

Req 2.2: Layoffs.fyi CSV lookup, 120-day window; record event_date,
         headcount_affected, percentage_cut.
"""
from __future__ import annotations

import csv
import difflib
import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class LayoffScanResult:
    """Represents a single layoff event found within the 120-day window."""

    event_date: str                       # YYYY-MM-DD
    headcount_affected: Optional[int]     # Laid_Off_Count — may be None
    percentage_cut: Optional[float]       # Percentage (0.0–1.0) — may be None
    company_name: str                     # Matched company name from CSV
    source_url: Optional[str] = None      # Source article URL


# ---------------------------------------------------------------------------
# CSV loader (lazy, cached at module level)
# ---------------------------------------------------------------------------

_cache: Optional[list[dict]] = None
_cache_loaded: bool = False


def _find_csv(base_path: str) -> Optional[Path]:
    """Return a .csv file path from base_path (file or directory)."""
    p = Path(base_path)
    if p.is_file() and p.suffix.lower() == ".csv":
        return p
    if p.is_dir():
        csvs = list(p.glob("*.csv"))
        if csvs:
            return csvs[0]
    return None


def _load_csv() -> Optional[list[dict]]:
    """Load the layoffs CSV once and cache it in module-level state."""
    global _cache, _cache_loaded
    if _cache_loaded:
        return _cache

    _cache_loaded = True
    env_path = os.environ.get("LAYOFFS_CSV_PATH")
    if not env_path:
        logger.warning(
            "LAYOFFS_CSV_PATH is not set — LayoffScanner will return empty results."
        )
        return None

    csv_path = _find_csv(env_path)
    if csv_path is None:
        logger.warning(
            "No CSV file found at LAYOFFS_CSV_PATH=%r — LayoffScanner will return empty results.",
            env_path,
        )
        return None

    try:
        with csv_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            _cache = list(reader)
        logger.info("Loaded %d layoff records from %s", len(_cache), csv_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load layoffs CSV from %s: %s", csv_path, exc)
        _cache = None

    return _cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_int(value: str) -> Optional[int]:
    """Parse an integer string; return None if empty or non-numeric."""
    if not value or not value.strip():
        return None
    try:
        return int(float(value.strip()))
    except (ValueError, TypeError):
        return None


def _parse_float(value: str) -> Optional[float]:
    """Parse a float string; return None if empty or non-numeric."""
    if not value or not value.strip():
        return None
    try:
        return float(value.strip())
    except (ValueError, TypeError):
        return None


def _parse_date(value: str) -> Optional[date]:
    """Parse a YYYY-MM-DD date string; return None on failure."""
    if not value or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# LayoffScanner
# ---------------------------------------------------------------------------

class LayoffScanner:
    """
    Scans the layoffs.fyi CSV for layoff events within a 120-day window.

    Lookup is by company name using fuzzy matching (difflib).  Returns a list
    of LayoffScanResult objects — one per matching event.  Returns an empty
    list when no record is found or the data source is unavailable.
    """

    WINDOW_DAYS: int = 120

    def _records(self) -> list[dict]:
        """Return all loaded CSV rows, or an empty list if unavailable."""
        return _load_csv() or []

    def scan(
        self,
        company_name: str,
        cutoff: float = 0.6,
        reference_date: Optional[date] = None,
    ) -> list[LayoffScanResult]:
        """
        Return layoff events for *company_name* within the last 120 days.

        Args:
            company_name: The company name to look up (fuzzy matched).
            cutoff: Minimum similarity score for fuzzy name matching (0–1).
            reference_date: Date to measure the 120-day window from.
                            Defaults to today (UTC).

        Returns:
            A list of LayoffScanResult objects, sorted by event_date descending.
            Empty list when no match or data unavailable.
        """
        records = self._records()
        if not records:
            return []

        ref = reference_date or date.today()
        window_start = ref - timedelta(days=self.WINDOW_DAYS)

        # Collect all company names for fuzzy matching
        all_names = [r.get("Company", "") for r in records]
        matches = difflib.get_close_matches(company_name, all_names, n=5, cutoff=cutoff)
        if not matches:
            logger.debug("No layoffs.fyi match for company_name=%r", company_name)
            return []

        matched_names_set = set(matches)
        results: list[LayoffScanResult] = []

        for row in records:
            if row.get("Company", "") not in matched_names_set:
                continue

            event_date = _parse_date(row.get("Date", ""))
            if event_date is None:
                continue
            if event_date < window_start:
                continue

            results.append(
                LayoffScanResult(
                    event_date=event_date.isoformat(),
                    headcount_affected=_parse_int(row.get("Laid_Off_Count", "")),
                    percentage_cut=_parse_float(row.get("Percentage", "")),
                    company_name=row.get("Company", ""),
                    source_url=row.get("Source") or None,
                )
            )

        # Most recent first
        results.sort(key=lambda r: r.event_date, reverse=True)
        return results

    def most_recent(
        self,
        company_name: str,
        cutoff: float = 0.6,
        reference_date: Optional[date] = None,
    ) -> Optional[LayoffScanResult]:
        """
        Return the single most recent layoff event within the 120-day window,
        or None if no event is found.

        Args:
            company_name: The company name to look up.
            cutoff: Fuzzy match similarity threshold.
            reference_date: Reference date for the 120-day window.
        """
        events = self.scan(company_name, cutoff=cutoff, reference_date=reference_date)
        return events[0] if events else None
