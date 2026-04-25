"""
Bench-to-Brief Match — compares prospect tech stack against BenchSummary.

Sets bench_mismatch=True when zero available engineers exist for any stack
in the prospect's tech profile. Records the bench_summary version timestamp.

Uses the authoritative seed/bench_summary.json (richer schema with per-stack
engineer counts and availability notes). Falls back to the simpler
data/bench_summary.json if the seed file is missing.

Requirements: 7.1, 7.2, 7.3
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from signal_pipeline.models import TechStack

logger = logging.getLogger(__name__)

# Authoritative seed file — richer schema with per-stack details
_SEED_BENCH_PATH = (
    Path(__file__).parent.parent
    / "data"
    / "tenacious_sales_data"
    / "seed"
    / "bench_summary.json"
)
# Fallback to the simpler runtime file
_FALLBACK_BENCH_PATH = Path(__file__).parent.parent / "data" / "bench_summary.json"
_DEFAULT_BENCH_PATH = _SEED_BENCH_PATH if _SEED_BENCH_PATH.exists() else _FALLBACK_BENCH_PATH


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class BenchMatchResult:
    """
    Result of a Bench-to-Brief match operation.

    Args:
        bench_mismatch: True when no available engineers match the prospect stack.
        matched_stacks: Stacks with at least one available engineer.
        bench_summary_version: Version timestamp from the bench summary file.
    """

    bench_mismatch: bool
    matched_stacks: list[str] = field(default_factory=list)
    bench_summary_version: str = ""


# ---------------------------------------------------------------------------
# BenchToBriefMatch
# ---------------------------------------------------------------------------


class BenchToBriefMatch:
    """
    Compares a prospect's tech stack against the bench summary availability.

    Loads bench_summary.json from disk on each match call to reflect the
    latest capacity data. Sets bench_mismatch=True when no overlap exists
    between the prospect's stack and engineers with count > 0.
    """

    def __init__(self, bench_path: Path = _DEFAULT_BENCH_PATH) -> None:
        """
        Initialise with an optional custom path to bench_summary.json.

        Args:
            bench_path: Path to the bench summary JSON file.
        """
        self._bench_path = bench_path

    def _load(self) -> dict:
        """
        Load bench summary from disk. Tries seed file first, falls back to
        the simpler runtime file.

        Returns:
            Parsed bench summary dict, or {} on failure.
        """
        for path in [self._bench_path, _FALLBACK_BENCH_PATH]:
            try:
                with path.open(encoding="utf-8") as fh:
                    data = json.load(fh)
                logger.debug("Loaded bench_summary from %s", path)
                return data
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to load bench_summary from %s: %s", path, exc)
        return {}

    def _build_available_set(self, bench_summary: dict) -> tuple[dict[str, int], str]:
        """
        Build a case-insensitive mapping of stack -> available engineer count.

        Handles both schema variants:
        - Seed schema: {"stacks": {"python": {"total": 7, "available": 6, ...}, ...}}
        - Runtime schema: {"available_engineers": {"python": 4, ...}}

        Args:
            bench_summary: Parsed bench summary JSON.

        Returns:
            Tuple of (stack -> count mapping, version string).
        """
        version: str = bench_summary.get("as_of", bench_summary.get("version", ""))

        bench_available: dict[str, int] = {}

        # Seed schema: stacks is a dict of stack -> object with junior/mid/senior counts
        stacks = bench_summary.get("stacks")
        if isinstance(stacks, dict):
            for stack_name, stack_data in stacks.items():
                if not isinstance(stack_data, dict):
                    continue
                # Sum across seniority tiers — but respect "committed" exclusions
                # (e.g. fullstack_nestjs is committed through Q3 2026)
                note = stack_data.get("note", "")
                if "committed" in note.lower():
                    # Engineers on paid engagements aren't bench-available
                    continue
                total = (
                    stack_data.get("junior", 0)
                    + stack_data.get("mid", 0)
                    + stack_data.get("senior", 0)
                )
                if total > 0:
                    bench_available[stack_name.lower()] = total
                    # Also index individual skill names so we can match "langchain" etc.
                    for skill in stack_data.get("skills", []):
                        bench_available[skill.lower()] = total
            return bench_available, version

        # Fallback: simple available_engineers dict
        available: dict = bench_summary.get("available_engineers", {})
        for k, v in available.items():
            if isinstance(v, int) and v > 0:
                bench_available[k.lower()] = v
        return bench_available, version

    def match(
        self,
        tech_stack: Optional[TechStack],
        bench_summary: Optional[dict] = None,
    ) -> BenchMatchResult:
        """
        Compare prospect tech stack against bench summary availability.

        Checks tech_stack.languages, tech_stack.ml_tools, and tech_stack.data_tools
        against the bench, including individual skill names from the seed schema.
        Sets bench_mismatch=True when no overlap exists.

        Args:
            tech_stack: Prospect's TechStack, or None if unavailable.
            bench_summary: Optional pre-loaded bench summary dict. When None,
                the file is loaded from disk.

        Returns:
            BenchMatchResult with mismatch flag, matched stacks, and version.
        """
        if bench_summary is None:
            bench_summary = self._load()

        bench_available, version = self._build_available_set(bench_summary)

        if tech_stack is None:
            return BenchMatchResult(
                bench_mismatch=False,
                matched_stacks=[],
                bench_summary_version=version,
            )

        prospect_stacks: set[str] = set()
        for lang in tech_stack.languages:
            prospect_stacks.add(lang.lower())
        for tool in tech_stack.ml_tools:
            prospect_stacks.add(tool.lower())
        for tool in tech_stack.data_tools:
            prospect_stacks.add(tool.lower())

        if not prospect_stacks:
            return BenchMatchResult(
                bench_mismatch=False,
                matched_stacks=[],
                bench_summary_version=version,
            )

        matched = [s for s in prospect_stacks if s in bench_available]
        bench_mismatch = len(matched) == 0

        return BenchMatchResult(
            bench_mismatch=bench_mismatch,
            matched_stacks=sorted(matched),
            bench_summary_version=version,
        )
