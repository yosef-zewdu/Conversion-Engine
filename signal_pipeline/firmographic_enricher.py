"""
FirmographicEnricher — looks up a company in the Crunchbase ODM CSV dataset.

Req 2.1: Retrieve firmographic data from Crunchbase ODM sample; produce enrichment
         record with crunchbase_id and last_enriched_at.
Req 2.6: If no record found, set crunchbase_id: null, all firmographic fields to null,
         never fabricate values.
"""
from __future__ import annotations

import csv
import difflib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Employee band → (min, max) mapping
# ---------------------------------------------------------------------------

EMPLOYEE_BAND_MAP: dict[str, tuple[int, Optional[int]]] = {
    "1-10": (1, 10),
    "11-50": (11, 50),
    "51-100": (51, 100),
    "101-250": (101, 250),
    "251-500": (251, 500),
    "501-1000": (501, 1000),
    "1001-5000": (1001, 5000),
    "5001-10000": (5001, 10000),
    "10001+": (10001, None),
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class FirmographicResult:
    crunchbase_id: Optional[str] = None
    company_name: Optional[str] = None
    sectors: list[str] = field(default_factory=list)
    employee_band: Optional[str] = None
    employee_count_min: Optional[int] = None
    employee_count_max: Optional[int] = None
    funding_stage: Optional[str] = None
    ipo_status: Optional[str] = None
    website: Optional[str] = None
    about: Optional[str] = None
    full_description: Optional[str] = None
    country_code: Optional[str] = None
    founded_date: Optional[str] = None
    builtwith_tech: list[str] = field(default_factory=list)
    siftery_products: list[str] = field(default_factory=list)
    raw_funding_rounds: list[dict] = field(default_factory=list)
    raw_leadership_hire: list[dict] = field(default_factory=list)
    raw_layoff: list[dict] = field(default_factory=list)
    last_enriched_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _null_result() -> FirmographicResult:
    """Return a fully-null result (Req 2.6)."""
    return FirmographicResult(last_enriched_at=_now_iso())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_json_list(raw: str) -> list:
    """Safely parse a JSON field that should be a list; return [] on failure."""
    if not raw or raw.strip() in ("", "null"):
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def _extract_names(items: list, key: str = "value") -> list[str]:
    """Extract string values from a list of dicts by key, skipping missing/non-str."""
    result = []
    for item in items:
        if isinstance(item, dict):
            val = item.get(key)
            if isinstance(val, str) and val:
                result.append(val)
    return result


def _parse_employee_band(band: str) -> tuple[Optional[int], Optional[int]]:
    if not band:
        return None, None
    return EMPLOYEE_BAND_MAP.get(band, (None, None))


def _infer_funding_stage(investment_stage: str, funding_rounds: list[dict]) -> Optional[str]:
    """Use investment_stage if set; otherwise infer from the latest funding round title."""
    if investment_stage and investment_stage.strip():
        return investment_stage.strip()
    if not funding_rounds:
        return None
    # Sort by announced_on descending, pick latest
    def _date_key(r: dict) -> str:
        return r.get("announced_on", "") or ""
    latest = max(funding_rounds, key=_date_key, default=None)
    if latest is None:
        return None
    title: str = latest.get("title", "") or ""
    # Extract stage from title like "Series A - CompanyName"
    if " - " in title:
        stage_part = title.split(" - ")[0].strip()
        if stage_part:
            return stage_part
    return title.strip() or None


def _or_none(value: str) -> Optional[str]:
    """Return None for empty/whitespace strings."""
    return value.strip() if value and value.strip() else None


# ---------------------------------------------------------------------------
# CSV loader (lazy, cached at module level)
# ---------------------------------------------------------------------------

_cache: Optional[list[dict]] = None
_cache_loaded: bool = False


def _find_csv(base_path: str) -> Optional[Path]:
    """Find a .csv file in base_path (directory) or treat base_path as a file."""
    p = Path(base_path)
    if p.is_file() and p.suffix.lower() == ".csv":
        return p
    if p.is_dir():
        csvs = list(p.glob("*.csv"))
        if csvs:
            return csvs[0]
    return None


def _load_csv() -> Optional[list[dict]]:
    global _cache, _cache_loaded
    if _cache_loaded:
        return _cache

    _cache_loaded = True
    # Use env var if set, otherwise fall back to the project-bundled data file
    env_path = os.environ.get("CRUNCHBASE_ODM_PATH")
    _project_root = Path(__file__).parent.parent
    _default_csv = _project_root / "data" / "crunchbase-companies-information.csv"

    if env_path:
        candidate = Path(env_path)
        # If the env var points to a valid CSV file, use it; otherwise use the project default
        if candidate.is_file() and candidate.suffix.lower() == ".csv":
            resolved_path: Path = candidate
        elif candidate.is_dir():
            # Look for any CSV inside that dir
            csvs = list(candidate.glob("**/*.csv"))
            resolved_path = csvs[0] if csvs else _default_csv
        else:
            logger.warning(
                "CRUNCHBASE_ODM_PATH=%r does not exist — falling back to project data/", env_path
            )
            resolved_path = _default_csv
    else:
        resolved_path = _default_csv

    csv_path = _find_csv(str(resolved_path))
    if csv_path is None:
        logger.warning(
            "No CSV file found at %r — FirmographicEnricher will return null results.",
            resolved_path,
        )
        return None

    try:
        with csv_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            _cache = list(reader)
        logger.info("Loaded %d Crunchbase records from %s", len(_cache), csv_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load Crunchbase CSV from %s: %s", csv_path, exc)
        _cache = None

    return _cache


# ---------------------------------------------------------------------------
# FirmographicEnricher
# ---------------------------------------------------------------------------

class FirmographicEnricher:
    """
    Looks up a company in the Crunchbase ODM CSV dataset and returns a
    FirmographicResult.  Never fabricates values (Req 2.6).
    """

    def _records(self) -> list[dict]:
        return _load_csv() or []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enrich_by_name(self, company_name: str, cutoff: float = 0.6) -> FirmographicResult:
        """Fuzzy-match company_name against the 'name' column."""
        records = self._records()
        if not records:
            return _null_result()

        names = [r.get("name", "") for r in records]
        matches = difflib.get_close_matches(company_name, names, n=1, cutoff=cutoff)
        if not matches:
            logger.debug("No Crunchbase match for company_name=%r", company_name)
            return _null_result()

        matched_name = matches[0]
        record = next((r for r in records if r.get("name") == matched_name), None)
        if record is None:
            return _null_result()

        return self._build_result(record)

    def enrich_by_id(self, company_id: str) -> FirmographicResult:
        """Exact match on the 'uuid' or 'id' column."""
        records = self._records()
        if not records:
            return _null_result()

        record = next(
            (r for r in records if r.get("uuid") == company_id or r.get("id") == company_id),
            None,
        )
        if record is None:
            logger.debug("No Crunchbase record for company_id=%r", company_id)
            return _null_result()

        return self._build_result(record)

    # ------------------------------------------------------------------
    # Internal builder
    # ------------------------------------------------------------------

    def _build_result(self, row: dict) -> FirmographicResult:
        # --- JSON fields ---
        industries = _parse_json_list(row.get("industries", ""))
        funding_rounds = _parse_json_list(row.get("funding_rounds_list", ""))
        leadership_hire = _parse_json_list(row.get("leadership_hire", ""))
        layoff = _parse_json_list(row.get("layoff", ""))
        builtwith_raw = _parse_json_list(row.get("builtwith_tech", ""))
        siftery_raw = _parse_json_list(row.get("siftery_products", ""))

        # --- Sectors ---
        sectors = _extract_names(industries, key="value")

        # --- Employee band ---
        band = _or_none(row.get("num_employees", ""))
        emp_min, emp_max = _parse_employee_band(band) if band else (None, None)

        # --- Funding stage ---
        funding_stage = _infer_funding_stage(
            row.get("investment_stage", ""), funding_rounds
        )

        # --- Tech lists ---
        # builtwith_tech items may be dicts with a "name" key or plain strings
        builtwith_tech: list[str] = []
        for item in builtwith_raw:
            if isinstance(item, str) and item:
                builtwith_tech.append(item)
            elif isinstance(item, dict):
                val = item.get("name") or item.get("value") or item.get("tag")
                if isinstance(val, str) and val:
                    builtwith_tech.append(val)

        siftery_products: list[str] = []
        for item in siftery_raw:
            if isinstance(item, str) and item:
                siftery_products.append(item)
            elif isinstance(item, dict):
                val = item.get("name") or item.get("value")
                if isinstance(val, str) and val:
                    siftery_products.append(val)

        return FirmographicResult(
            crunchbase_id=_or_none(row.get("id", "")),
            company_name=_or_none(row.get("name", "")),
            sectors=sectors,
            employee_band=band,
            employee_count_min=emp_min,
            employee_count_max=emp_max,
            funding_stage=funding_stage,
            ipo_status=_or_none(row.get("ipo_status", "")),
            website=_or_none(row.get("website", "")),
            about=_or_none(row.get("about", "")),
            full_description=_or_none(row.get("full_description", "")),
            country_code=_or_none(row.get("country_code", "")),
            founded_date=_or_none(row.get("founded_date", "")),
            builtwith_tech=builtwith_tech,
            siftery_products=siftery_products,
            raw_funding_rounds=funding_rounds,
            raw_leadership_hire=leadership_hire,
            raw_layoff=layoff,
            last_enriched_at=_now_iso(),
        )
