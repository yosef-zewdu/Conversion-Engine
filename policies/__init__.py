"""
Policy Gate System — deterministic safety layer between agents and side effects.

Gates:
  evidence_gate    — blocks unsupported factual claims by confidence tier
  gap_gate         — controls competitor gap language
  bench_gate       — enforces bench availability before capacity commitments
  channel_gate     — enforces email-first, SMS-only-after-reply sequencing
  pricing_gate     — prevents premature contract value disclosure
  tone_gate        — blocks prohibited phrases and style violations
  tool_action_gate — ensures no send without policy approval + trace_id

Entry point: EvidenceCalibratedActionPolicy (action_policy.py)
"""
from policies.action_policy import EvidenceCalibratedActionPolicy, PolicyDecision

__all__ = ["EvidenceCalibratedActionPolicy", "PolicyDecision"]
