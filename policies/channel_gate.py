"""
Channel Gate — enforces email-first, SMS-only-after-reply sequencing.

Rules (architecture spec §13):
  - First outbound is always email
  - SMS allowed only if email_reply_count >= 1 OR prospect explicitly asks for SMS
  - STOP/UNSUB always wins → mark opted_out

Channel hierarchy: email → SMS (warm only) → voice (discovery call, human)
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChannelViolation:
    reason: str


@dataclass
class ChannelGateResult:
    allowed: bool
    corrected_channel: str | None = None
    violations: list[ChannelViolation] = field(default_factory=list)


class ChannelGate:
    def review(
        self,
        proposed_action: dict,
        email_reply_count: int,
        prospect_asked_for_sms: bool = False,
        opted_out: bool = False,
    ) -> ChannelGateResult:
        violations: list[ChannelViolation] = []
        channel = proposed_action.get("channel", "email")
        action_type = proposed_action.get("action_type", "")

        # STOP/UNSUB hard block on all outbound
        if opted_out and action_type in ("send_email", "send_sms"):
            violations.append(ChannelViolation(
                reason="prospect has opted out; no outbound allowed"
            ))
            return ChannelGateResult(allowed=False, violations=violations)

        # SMS before any email reply is a sequencing violation
        if channel == "sms" and not prospect_asked_for_sms:
            if email_reply_count < 1:
                violations.append(ChannelViolation(
                    reason=f"SMS requires email_reply_count>=1; got {email_reply_count}"
                ))
                return ChannelGateResult(
                    allowed=False,
                    corrected_channel="email",
                    violations=violations,
                )

        return ChannelGateResult(allowed=True)
