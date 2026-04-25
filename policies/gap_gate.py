"""
Gap Gate — controls competitor gap language in proposed outbound.

Rules (architecture spec §13):
  - If all gaps are low confidence → omit gap language
  - If peer_count < 5 → do not say "leading companies in your sector"
  - If segment != S4 → avoid strong gap framing unless explicitly allowed
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GapViolation:
    reason: str


@dataclass
class GapGateResult:
    allowed: bool
    violations: list[GapViolation] = field(default_factory=list)


class GapGate:
    def review(
        self,
        proposed_action: dict,
        gap_brief: dict | None,
        segment: str | None,
    ) -> GapGateResult:
        violations: list[GapViolation] = []

        if gap_brief is None:
            return GapGateResult(allowed=True)

        body = proposed_action.get("body", "").lower()

        # Broad sector language requires peer_count >= 5
        peer_count = gap_brief.get("peer_count", 0) or 0
        broad_phrases = [
            "leading companies in your sector",
            "top companies in your sector",
            "sector leaders",
            "industry leaders",
            "competitors are",
        ]
        for phrase in broad_phrases:
            if phrase in body and peer_count < 5:
                violations.append(GapViolation(
                    reason=f"peer_count={peer_count} < 5; cannot use '{phrase}'"
                ))

        # All-low-confidence gap check
        gaps = gap_brief.get("gaps", [])
        gap_phrases = ["gap", "behind", "lacking", "capability"]
        body_has_gap_language = any(p in body for p in gap_phrases)
        if body_has_gap_language and gaps:
            all_low = all(g.get("confidence", "low") == "low" for g in gaps)
            if all_low:
                violations.append(GapViolation(
                    reason="all gap confidences are low; gap language must be omitted"
                ))

        # Segment gate — strong gap framing reserved for S4
        strong_gap_phrases = ["you are behind", "falling behind", "your team lacks"]
        for phrase in strong_gap_phrases:
            if phrase in body and segment not in ("segment_4", "s4"):
                violations.append(GapViolation(
                    reason=f"'{phrase}' is strong gap framing; only allowed for Segment 4"
                ))

        return GapGateResult(allowed=len(violations) == 0, violations=violations)
