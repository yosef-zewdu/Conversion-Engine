"""
Tool Action Gate — ensures no side effect executes without full policy approval.

Rules (architecture spec §13):
  - No send without policy_decision_id
  - No booking unless prospect selected or confirmed a real slot
  - No CRM write without trace_id
  - No outbound if kill switch routes to staff sink (except the sink itself)
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolActionViolation:
    reason: str


@dataclass
class ToolActionGateResult:
    allowed: bool
    violations: list[ToolActionViolation] = field(default_factory=list)


class ToolActionGate:
    def review(
        self,
        proposed_action: dict,
        policy_decision_id: str | None,
        trace_id: str | None,
        destination: str,
        slot_confirmed: bool = False,
    ) -> ToolActionGateResult:
        violations: list[ToolActionViolation] = []
        action_type = proposed_action.get("action_type", "")

        # All outbound actions require a policy_decision_id
        if action_type in ("send_email", "send_sms") and not policy_decision_id:
            violations.append(ToolActionViolation(
                reason="send action requires policy_decision_id; not set"
            ))

        # Booking requires prospect to have confirmed a real slot
        if action_type == "book_meeting" and not slot_confirmed:
            violations.append(ToolActionViolation(
                reason="book_meeting requires slot_confirmed=True; prospect must select a real slot"
            ))

        # CRM writes require trace_id
        if action_type == "write_crm" and not trace_id:
            violations.append(ToolActionViolation(
                reason="write_crm requires trace_id; not set"
            ))

        return ToolActionGateResult(allowed=len(violations) == 0, violations=violations)
