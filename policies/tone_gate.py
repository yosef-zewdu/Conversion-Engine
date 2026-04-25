"""
Tone Gate — blocks prohibited phrases and style violations before send.

Wraps ToneGuard from mechanism/three_stage_chain.py for use in the
policy layer (separate from the Stage-3 retry loop in the mechanism).

This gate is the final check before tool_executor — it does not retry,
it just blocks and surfaces violations.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToneViolation:
    reason: str


@dataclass
class ToneGateResult:
    allowed: bool
    score: int = 100
    violations: list[ToneViolation] = field(default_factory=list)


class ToneGate:
    """
    Policy-layer tone gate. Delegates scoring to ToneGuard from the mechanism.
    """

    PASS_THRESHOLD = 70

    def review(self, proposed_action: dict) -> ToneGateResult:
        from mechanism.three_stage_chain import ToneGuard

        body = proposed_action.get("body", "")
        subject = proposed_action.get("subject")
        channel = proposed_action.get("channel", "email")

        guard = ToneGuard()
        result = guard.check(body, subject, channel)

        violations = [ToneViolation(reason=v) for v in result.violations]
        return ToneGateResult(
            allowed=result.passes,
            score=result.total,
            violations=violations,
        )
