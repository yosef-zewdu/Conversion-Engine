"""
Bench-to-Brief Match — compares prospect tech stack against BenchSummary.

Sets bench_mismatch=True when zero available engineers exist for any stack
in the prospect's tech profile. Records the bench_summary version timestamp.

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

_DEFAULT_BENCH_PATH = Path(__file__).parent.parent / "data" / "bench_summary.json"


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
        Load bench summary from disk.

        Returns:
            Parsed bench summary dict, or {} on failure.
        """
        try:
            with self._bench_path.open(encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to load bench_summary from %s: %s", self._bench_path, exc
            )
            return {}

    def match(
        self,
        tech_stack: Optional[TechStack],
        bench_summary: Optional[dict] = None,
    ) -> BenchMatchResult:
        """
        Compare prospect tech stack against bench summary availability.

        Checks tech_stack.languages and tech_stack.ml_tools against the
        available_engineers keys (case-insensitive). Sets bench_mismatch=True
        when no overlap exists with stacks that have count > 0.

        Args:
            tech_stack: Prospect's TechStack, or None if unavailable.
            bench_summary: Optional pre-loaded bench summary dict. When None,
                the file is loaded from disk.

        Returns:
            BenchMatchResult with mismatch flag, matched stacks, and version.
        """
        if bench_summary is None:
            bench_summary = self._load()

        version: str = bench_summary.get("version", "")
        available: dict = bench_summary.get("available_engineers", {})

        # Build set of stacks with at least one available engineer (case-insensitive)
        bench_available: dict[str, int] = {
            k.lower(): v for k, v in available.items() if isinstance(v, int) and v > 0
        }

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
