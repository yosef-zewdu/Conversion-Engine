"""
EvidenceCalibratedActionPolicy — main policy gate orchestrator.

Runs all specialist gates and produces a unified PolicyDecision.
The Tool Executor checks policy_decision.allowed before any side effect.

Architecture spec §13: "Hard deterministic safety layer between agents and side effects."
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from policies.bench_gate import BenchGate
from policies.channel_gate import ChannelGate
from policies.evidence_gate import EvidenceGate
from policies.gap_gate import GapGate
from policies.pricing_gate import PricingGate
from policies.tone_gate import ToneGate
from policies.tool_action_gate import ToolActionGate

POLICY_VERSION = "evidence_calibrated_v1"


@dataclass
class PolicyDecision:
    allowed: bool
    requires_rewrite: bool = False
    requires_human: bool = False
    violations: list[str] = field(default_factory=list)
    safe_action: dict = field(default_factory=dict)
    policy_version: str = POLICY_VERSION
    policy_decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))


class EvidenceCalibratedActionPolicy:
    """
    Orchestrates all policy gates for a proposed action.

    Usage::

        policy = EvidenceCalibratedActionPolicy()
        decision = policy.review(state, proposed_action)
        if decision.allowed:
            tool_executor.execute(proposed_action, decision)
    """

    def __init__(self) -> None:
        self._evidence = EvidenceGate()
        self._gap = GapGate()
        self._bench = BenchGate()
        self._channel = ChannelGate()
        self._pricing = PricingGate()
        self._tone = ToneGate()
        self._tool_action = ToolActionGate()

    def review(self, state: dict[str, Any], proposed_action: dict) -> PolicyDecision:
        """
        Run all gates against the proposed action.

        Args:
            state: Current AgentState as a dict (from graph or orchestrator).
            proposed_action: Action dict conforming to the standard action schema.

        Returns:
            PolicyDecision with allowed flag, violations, and decision ID.
        """
        violations: list[str] = []
        requires_rewrite = False
        requires_human = False

        brief = state.get("hiring_signal_brief") or {}
        gap_brief = state.get("competitor_gap_brief")
        segment = state.get("segment")
        bench_mismatch = state.get("bench_mismatch", False)
        email_reply_count = state.get("email_reply_count", 0)
        opted_out = state.get("opted_out", False)
        destination = state.get("destination", "staff_sink")
        trace_id = state.get("trace_id")
        slot_confirmed = state.get("slot_confirmed", False)
        prospect_asked_sms = state.get("prospect_asked_sms", False)

        # Evidence Gate
        ev = self._evidence.review(proposed_action, brief)
        if not ev.allowed:
            requires_rewrite = True
            for v in ev.violations:
                violations.append(f"evidence: {v.reason}")

        # Gap Gate
        gp = self._gap.review(proposed_action, gap_brief, segment)
        if not gp.allowed:
            requires_rewrite = True
            for v in gp.violations:
                violations.append(f"gap: {v.reason}")

        # Bench Gate
        bn = self._bench.review(proposed_action, bench_mismatch)
        if not bn.allowed:
            requires_rewrite = True
            for v in bn.violations:
                violations.append(f"bench: {v.reason}")
        if bn.requires_human:
            requires_human = True

        # Channel Gate
        ch = self._channel.review(
            proposed_action,
            email_reply_count=email_reply_count,
            prospect_asked_for_sms=prospect_asked_sms,
            opted_out=opted_out,
        )
        if not ch.allowed:
            # Channel violations block outright (not just rewrite)
            for v in ch.violations:
                violations.append(f"channel: {v.reason}")

        # Pricing Gate
        pr = self._pricing.review(proposed_action)
        if not pr.allowed:
            requires_rewrite = True
            for v in pr.violations:
                violations.append(f"pricing: {v.reason}")
        if pr.requires_human:
            requires_human = True

        # Tone Gate (only for outbound messages)
        action_type = proposed_action.get("action_type", "")
        if action_type in ("send_email", "send_sms") and proposed_action.get("body"):
            tn = self._tone.review(proposed_action)
            if not tn.allowed:
                requires_rewrite = True
                for v in tn.violations:
                    violations.append(f"tone: {v.reason}")

        # Build decision — produce ID first so it can be passed to ToolActionGate
        decision_id = str(uuid.uuid4())
        # ToolActionGate — needs the decision ID to verify it was set
        # We pass it only when all other gates passed (so the ID is real)
        effective_id = decision_id if not violations else None
        ta = self._tool_action.review(
            proposed_action,
            policy_decision_id=effective_id,
            trace_id=trace_id,
            destination=destination,
            slot_confirmed=slot_confirmed,
        )
        if not ta.allowed:
            for v in ta.violations:
                violations.append(f"tool_action: {v.reason}")

        allowed = len(violations) == 0

        # Build safe_action: a copy with channel corrected if needed
        safe_action = dict(proposed_action)
        if not ch.allowed and ch.corrected_channel:
            safe_action["channel"] = ch.corrected_channel

        return PolicyDecision(
            allowed=allowed,
            requires_rewrite=requires_rewrite,
            requires_human=requires_human,
            violations=violations,
            safe_action=safe_action if allowed else {},
            policy_version=POLICY_VERSION,
            policy_decision_id=decision_id if allowed else "",
        )
