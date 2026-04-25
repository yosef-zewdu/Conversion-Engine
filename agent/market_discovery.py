"""
Market Discovery Agent — finds candidate companies from public/snapshot data.

Architecture spec §3:
  - Filters by size, funding, geography, stage
  - Preserves Crunchbase IDs
  - Attaches source references
  - Never fabricates company facts

Input:  CampaignConfig dict
Output: {"candidate_accounts": [...], "status": "ok" | "no_candidates_found"}

This agent is mostly deterministic. No LLM calls are made here — filtering
is pure data transformation from the existing Crunchbase/layoff snapshots.
"""
from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Resolve relative to this file so the path is always correct regardless of CWD
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_CB_PATH = os.path.join(_PROJECT_ROOT, "data", "crunchbase-companies-information.csv")
_DEFAULT_LAYOFFS_PATH = os.path.join(_PROJECT_ROOT, "data", "layoffs_data.csv")


class MarketDiscoveryAgent:
    """
    Finds candidate accounts by scanning snapshot data against CampaignConfig filters.

    All accounts must carry source_refs and a candidate_reason.
    Returns empty candidate_accounts (never raises) when no matches found.
    """

    def __init__(
        self,
        crunchbase_path: str = _DEFAULT_CB_PATH,
        layoffs_path: str = _DEFAULT_LAYOFFS_PATH,
    ) -> None:
        self._cb_path = crunchbase_path
        self._layoffs_path = layoffs_path

    def discover(self, campaign_config: dict) -> dict:
        """
        Run discovery against campaign filters.

        Args:
            campaign_config: Dict with keys:
              company_size.min_employees, company_size.max_employees,
              funding.rounds, funding.min_amount_usd, funding.max_amount_usd,
              funding.within_days, signals_required.min_engineering_roles,
              signals_required.min_ai_maturity_score

        Returns:
            {"candidate_accounts": [...], "status": "ok" | "no_candidates_found",
             "reason": str | None}
        """
        size_cfg = campaign_config.get("company_size", {})
        funding_cfg = campaign_config.get("funding", {})
        signals_cfg = campaign_config.get("signals_required", {})

        min_employees = size_cfg.get("min_employees", 0)
        max_employees = size_cfg.get("max_employees", 10_000)
        allowed_rounds = [r.lower() for r in funding_cfg.get("rounds", [])]
        min_amount = funding_cfg.get("min_amount_usd", 0)
        max_amount = funding_cfg.get("max_amount_usd", float("inf"))
        within_days = funding_cfg.get("within_days", 180)
        min_eng_roles = signals_cfg.get("min_engineering_roles", 0)

        candidates: list[dict] = []

        # Scan Layoffs (if requested or if scanning for distressed startups)
        min_layoff_pct = signals_cfg.get("min_layoff_percentage", 0)
        layoff_candidates = []
        if min_layoff_pct > 0 or "S2" in campaign_config.get("target_segments", []):
            layoff_candidates = self._scan_layoffs(min_layoff_pct)
        
        # Scan Crunchbase ODM
        cb_candidates = self._scan_crunchbase(
            min_employees, max_employees, allowed_rounds, min_amount, max_amount, within_days
        )

        # Prioritize layoff candidates if S2 is targeted
        if "S2" in campaign_config.get("target_segments", []):
            candidates.extend(layoff_candidates)
            candidates.extend(cb_candidates)
        else:
            candidates.extend(cb_candidates)
            candidates.extend(layoff_candidates)

        # Deduplicate by crunchbase_id
        seen: set[str] = set()
        unique: list[dict] = []
        for c in candidates:
            cid = c.get("crunchbase_id", "")
            if cid not in seen:
                seen.add(cid)
                unique.append(c)

        if not unique:
            return {
                "candidate_accounts": [],
                "status": "no_candidates_found",
                "reason": "No accounts matched campaign filters",
            }

        return {
            "candidate_accounts": unique,
            "status": "ok",
            "reason": None,
        }

    def _scan_layoffs(self, min_pct: float) -> list[dict]:
        """Scan Layoffs CSV for candidates."""
        results: list[dict] = []
        csv_files = self._find_csv_files(self._layoffs_path)
        
        for csv_file in csv_files:
            try:
                with open(csv_file, encoding="utf-8", errors="replace") as f:
                    reader = csv.DictReader(f)
                    for i, row in enumerate(reader):
                        # Percentage filter
                        raw_pct = row.get("Percentage") or "0"
                        raw_count = row.get("Laid_Off_Count") or "0"
                        try:
                            pct = float(raw_pct)
                        except ValueError:
                            pct = 0.0
                        
                        try:
                            count = int(float(raw_count))
                        except ValueError:
                            count = 0

                        # Qualify if pct >= min_pct OR absolute count is high (e.g. > 50)
                        if pct < min_pct and count < 50:
                            continue
                            
                        company_name = row.get("Company") or ""
                        if not company_name:
                            continue
                            
                        # Use a synthetic ID since we don't have UUIDs in layoffs CSV
                        # The enricher will try to find the real Crunchbase record by name
                        results.append({
                            "company_name": company_name,
                            "crunchbase_id": f"layoff_{i+2}", 
                            "industry": row.get("Industry", ""),
                            "employee_count": 0,
                            "employee_band": "Unknown",
                            "country": row.get("Country", ""),
                            "homepage_url": "",
                            "last_funding_round": "",
                            "funding_total_usd": 0.0,
                            "candidate_reason": f"Significant layoff detected ({pct*100:.0f}%)",
                            "source_refs": {
                                "layoffs": f"{os.path.basename(csv_file)}:row_{i+2}"
                            },
                        })
            except Exception as exc:
                logger.warning("Skipping layoff file %s: %s", csv_file, exc)
        return results

    def _scan_crunchbase(
        self,
        min_emp: int,
        max_emp: int,
        allowed_rounds: list[str],
        min_amount: float,
        max_amount: float,
        within_days: int,
    ) -> list[dict]:
        """Scan Crunchbase ODM CSV files for matching companies."""
        results: list[dict] = []
        now = datetime.now(timezone.utc)

        csv_files = self._find_csv_files(self._cb_path)
        logger.warning("MarketDiscoveryAgent scanning crunchbase for candidates at path: %s", self._cb_path)
        logger.warning("Found %d CSV files: %s", len(csv_files), csv_files)
        
        for csv_file in csv_files:
            try:
                with open(csv_file, encoding="utf-8", errors="replace") as f:
                    reader = csv.DictReader(f)
                    for i, row in enumerate(reader):
                        candidate = self._row_to_candidate(
                            row, csv_file, i + 2,
                            min_emp, max_emp, allowed_rounds, min_amount, max_amount, within_days, now
                        )
                        if candidate:
                            results.append(candidate)
            except Exception as exc:
                logger.warning("Skipping %s: %s", csv_file, exc)

        return results

    def _row_to_candidate(
        self,
        row: dict,
        csv_file: str,
        row_num: int,
        min_emp: int,
        max_emp: int,
        allowed_rounds: list[str],
        min_amount: float,
        max_amount: float,
        within_days: int,
        now: datetime,
    ) -> dict | None:
        """Convert a Crunchbase row (real schema) to a candidate account if it passes filters."""
        import json as _json

        # ── Employee count ───────────────────────────────────────────────────
        # The real CSV has a plain integer column `num_employees` or a range like '501-1000'
        raw_emp = str(row.get("num_employees") or row.get("num_employee_profiles") or "").strip()
        emp_count = 0
        if raw_emp:
            try:
                if "-" in raw_emp:
                    # Take the middle or lower of the range to be safe
                    parts = raw_emp.split("-")
                    emp_count = int(parts[0].replace(",", "").strip())
                elif "+" in raw_emp:
                    emp_count = int(raw_emp.replace("+", "").replace(",", "").strip())
                else:
                    emp_count = int(raw_emp.replace(",", "").strip())
            except ValueError:
                emp_count = 0

        # Only apply employee gate when we actually have data (missing = include for scraper to resolve)
        if emp_count > 0:
            if min_emp > 0 and emp_count < min_emp:
                return None
            if max_emp < 10_000 and emp_count > max_emp:
                return None

        # ── Funding ──────────────────────────────────────────────────────────
        # `funding_rounds` is a JSON object: {"last_funding_type": "...", "value": {"value_usd": ...}}
        funding_json = row.get("funding_rounds") or "{}"
        try:
            funding_data = _json.loads(funding_json) if isinstance(funding_json, str) else funding_json
        except (_json.JSONDecodeError, TypeError):
            funding_data = {}

        last_round = str(
            funding_data.get("last_funding_type")
            or row.get("last_funding_type")
            or ""
        ).lower().replace("_", " ")

        amount_usd = 0.0
        val_block = funding_data.get("value") or {}
        if isinstance(val_block, dict):
            amount_usd = float(val_block.get("value_usd") or 0)
        if amount_usd == 0:
            try:
                amount_usd = float(str(row.get("total_funding_usd") or "0").replace(",", "") or 0)
            except ValueError:
                amount_usd = 0.0

        if allowed_rounds:
            if not any(r in last_round for r in allowed_rounds):
                return None

        if amount_usd < min_amount or amount_usd > max_amount:
            return None

        # ── Build candidate ──────────────────────────────────────────────────
        company_name = row.get("name") or row.get("legal_name") or ""
        crunchbase_id = row.get("uuid") or row.get("id") or f"cb_{row_num}"
        industry = row.get("industries") or row.get("category_list") or ""
        country = row.get("country_code") or ""
        homepage = row.get("website") or row.get("url") or ""

        return {
            "company_name": company_name,
            "crunchbase_id": crunchbase_id,
            "industry": industry,
            "employee_count": emp_count,
            "employee_band": f"{emp_count}",
            "country": country,
            "homepage_url": homepage,
            "last_funding_round": last_round,
            "funding_total_usd": amount_usd,
            "candidate_reason": self._build_reason(last_round, emp_count, allowed_rounds),
            "source_refs": {
                "crunchbase": f"{os.path.basename(csv_file)}:row_{row_num}"
            },
        }

    def _build_reason(self, last_round: str, emp_count: int, allowed_rounds: list[str]) -> str:
        parts = []
        if last_round:
            parts.append(last_round)
        if emp_count:
            parts.append(f"{emp_count} employees")
        return " + ".join(parts) if parts else "matched campaign filters"

    @staticmethod
    def _find_csv_files(base_path: str) -> list[str]:
        """Find CSV file or recursively find all CSV files in directory."""
        from pathlib import Path
        p = Path(base_path)
        if p.is_file() and p.suffix.lower() == ".csv":
            return [str(p)]
        
        import glob
        return glob.glob(os.path.join(base_path, "**", "*.csv"), recursive=True)
