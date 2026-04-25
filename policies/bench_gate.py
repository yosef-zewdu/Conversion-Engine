"""
Bench Gate — enforces bench availability before capacity commitments.

Rules (architecture spec §13):
  - bench_mismatch=True → no capacity commitment
  - required_count > available_count → route to human
  - stack absent → do not claim capability
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BenchViolation:
    reason: str


@dataclass
class BenchGateResult:
    allowed: bool
    requires_human: bool = False
    violations: list[BenchViolation] = field(default_factory=list)


_CAPACITY_PHRASES = [
    "we have engineers available",
    "we can provide",
    "we have capacity",
    "engineers ready",
    "available immediately",
    "can start",
    "headcount available",
]


class BenchGate:
    def review(
        self,
        proposed_action: dict,
        bench_mismatch: bool,
        bench_summary: dict | None = None,
    ) -> BenchGateResult:
        violations: list[BenchViolation] = []
        requires_human = False

        body = proposed_action.get("body", "").lower()

        # Hard block: bench_mismatch means we cannot commit to any capacity
        if bench_mismatch:
            for phrase in _CAPACITY_PHRASES:
                if phrase in body:
                    violations.append(BenchViolation(
                        reason=f"bench_mismatch=True; cannot use capacity claim '{phrase}'"
                    ))

        # If bench_summary provided, check required vs available
        if bench_summary:
            for stack in bench_summary.get("stacks", []):
                if stack.get("committed"):
                    continue
                available = stack.get("available_count", 0) or 0
                if available == 0 and stack.get("name", "").lower() in body:
                    violations.append(BenchViolation(
                        reason=f"no bench engineers available for stack '{stack.get('name')}'"
                    ))
                    requires_human = True

        return BenchGateResult(
            allowed=len(violations) == 0,
            requires_human=requires_human,
            violations=violations,
        )
