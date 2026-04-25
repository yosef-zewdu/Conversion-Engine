"""
Pricing Gate — prevents premature contract value disclosure.

Rules (architecture spec §13):
  - Public monthly band may be mentioned
  - Specific total contract value (TCV) not allowed
  - Custom pricing routes to human / scoping call
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class PricingViolation:
    reason: str


@dataclass
class PricingGateResult:
    allowed: bool
    requires_human: bool = False
    violations: list[PricingViolation] = field(default_factory=list)


# Patterns that indicate forbidden TCV disclosure
_TCV_PATTERNS = [
    re.compile(r"\$\s*\d[\d,]+\s*(k|K|M|million|thousand)?\s*(per year|annually|total)", re.I),
    re.compile(r"total contract", re.I),
    re.compile(r"annual contract value", re.I),
    re.compile(r"tcv", re.I),
]

_CUSTOM_PRICING_PHRASES = [
    "custom pricing",
    "custom quote",
    "depends on scope",
    "based on requirements",
]


class PricingGate:
    def review(self, proposed_action: dict) -> PricingGateResult:
        violations: list[PricingViolation] = []
        requires_human = False

        body = proposed_action.get("body", "")

        for pattern in _TCV_PATTERNS:
            if pattern.search(body):
                violations.append(PricingViolation(
                    reason=f"TCV disclosure pattern detected: '{pattern.pattern}'"
                ))

        for phrase in _CUSTOM_PRICING_PHRASES:
            if phrase.lower() in body.lower():
                requires_human = True

        return PricingGateResult(
            allowed=len(violations) == 0,
            requires_human=requires_human,
            violations=violations,
        )
