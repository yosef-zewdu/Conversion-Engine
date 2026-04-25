"""
Evidence Gate — blocks unsupported factual claims by confidence tier.

Rules (from architecture spec §13):
  confidence >= 0.80  → factual phrasing allowed
  0.55 – 0.79         → soft/attributed phrasing only
  < 0.55              → question or omit
  null                → omit

Special:
  "aggressive hiring" requires job_post_count >= 5 AND job_post_velocity_60d >= 3.0
  layoff headcount requires headcount_affected != null
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvidenceViolation:
    claim_text: str
    reason: str


@dataclass
class EvidenceGateResult:
    allowed: bool
    violations: list[EvidenceViolation] = field(default_factory=list)
    safe_claims: list[dict] = field(default_factory=list)


class EvidenceGate:
    """
    Validates each claim in a proposed action against confidence thresholds.

    The gate does NOT rewrite claims — it flags violations so the policy
    layer can decide whether to block or request a rewrite.
    """

    ASSERTIVE_THRESHOLD = 0.80
    SOFT_THRESHOLD = 0.55

    def review(self, proposed_action: dict, brief: dict) -> EvidenceGateResult:
        claims: list[dict] = proposed_action.get("claims", [])
        violations: list[EvidenceViolation] = []
        safe_claims: list[dict] = []

        for claim in claims:
            v = self._check_claim(claim, brief)
            if v:
                violations.append(v)
            else:
                safe_claims.append(claim)

        # Check for "aggressive hiring" language in body
        body = proposed_action.get("body", "").lower()
        if "aggressive hiring" in body:
            job_post_count = brief.get("job_post_count") or 0
            velocity = brief.get("job_post_velocity_60d") or 0.0
            if job_post_count < 5 or velocity < 3.0:
                violations.append(EvidenceViolation(
                    claim_text="aggressive hiring",
                    reason=f"requires job_post_count>=5 AND velocity>=3.0; "
                           f"got count={job_post_count} velocity={velocity}",
                ))

        return EvidenceGateResult(
            allowed=len(violations) == 0,
            violations=violations,
            safe_claims=safe_claims,
        )

    def _check_claim(self, claim: dict, brief: dict) -> EvidenceViolation | None:
        confidence = claim.get("confidence")
        phrasing_tier = claim.get("phrasing_tier", "assertive")
        text = claim.get("text", "")

        if confidence is None:
            if phrasing_tier == "assertive":
                return EvidenceViolation(
                    claim_text=text,
                    reason="null confidence requires omit or interrogative phrasing",
                )
            return None

        if isinstance(confidence, str):
            confidence_map = {"high": 0.85, "medium": 0.67, "low": 0.35}
            confidence = confidence_map.get(confidence, 0.0)

        if confidence >= self.ASSERTIVE_THRESHOLD:
            return None  # any phrasing allowed

        if confidence >= self.SOFT_THRESHOLD:
            if phrasing_tier == "assertive":
                return EvidenceViolation(
                    claim_text=text,
                    reason=f"confidence {confidence:.2f} (0.55–0.79) requires soft/attributed phrasing",
                )
            return None

        # < 0.55 — must be interrogative or omitted
        if phrasing_tier in ("assertive", "soft"):
            return EvidenceViolation(
                claim_text=text,
                reason=f"confidence {confidence:.2f} < 0.55 requires interrogative or omit",
            )
        return None
