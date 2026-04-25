"""
Reply Handler Agent — classifies inbound replies and decides the next action.

Architecture spec §10:
  Input:  latest_inbound, conversation_history, lead_state, briefs
  Output: intent, next_action, state_update, draft_context

Supported intents (from spec):
  interested_positive, interested_with_correction, asks_for_pricing,
  asks_for_capacity, asks_for_sms, chooses_slot, not_interested,
  unsubscribe, confused, hostile, unknown

Routing:
  unsubscribe       → mark_opted_out
  asks_pricing      → pricing_gate → human/scoping
  asks_capacity     → bench_gate → human if specific staffing requested
  asks_sms          → channel_gate → enable SMS only if warm
  chooses_slot      → scheduler
  interested/*      → mechanism chain reply
  unknown           → ask clarifying question
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Deterministic keyword patterns ───────────────────────────────────────────

_OPT_OUT_PATTERNS = re.compile(
    r"\b(stop|unsubscribe|unsub|cancel|opt.?out|remove me|no more|do not contact)\b",
    re.I,
)
_PRICING_PATTERNS = re.compile(
    r"\b(price|pricing|cost|rate|fee|how much|budget|quote|invoice|contract)\b",
    re.I,
)
_CAPACITY_PATTERNS = re.compile(
    r"\b(capacity|headcount|how many|staffing|bench|engineers available|available now)\b",
    re.I,
)
_SMS_PATTERNS = re.compile(
    r"\b(text me|sms|whatsapp|message me|reach me by text|phone)\b",
    re.I,
)
_SLOT_PATTERNS = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r"|[0-9]{1,2}\s*(am|pm)|[0-9]{1,2}:[0-9]{2}"
    r"|morning|afternoon|evening|works for me|that works|confirmed)\b",
    re.I,
)
_CORRECTION_PATTERNS = re.compile(
    r"\b(actually|not quite|that'?s? not right|we don'?t|we aren'?t|"
    r"you'?re wrong|correction|our issue is|our problem is|incorrect)\b",
    re.I,
)
_NOT_INTERESTED_PATTERNS = re.compile(
    r"\b(not interested|no thanks|pass|don'?t need|we'?re good|"
    r"already have|happy with|no need|not for us)\b",
    re.I,
)
_HOSTILE_PATTERNS = re.compile(
    r"\b(spam|stop spamming|report|complaint|inappropriate|harassment|"
    r"block|legal action|cease and desist)\b",
    re.I,
)
_POSITIVE_PATTERNS = re.compile(
    r"\b(interested|tell me more|sounds good|great|love to|would like|"
    r"yes|open to|curious|let'?s talk|happy to)\b",
    re.I,
)


@dataclass
class ReplyHandlerOutput:
    intent: str
    next_action: str
    state_update: dict = field(default_factory=dict)
    draft_context: dict = field(default_factory=dict)
    requires_human: bool = False


class ReplyHandlerAgent:
    """
    Classifies inbound reply intent and determines next action.

    Classification is primarily keyword-based (deterministic) for speed
    and auditability. Falls back to LLM classification for ambiguous messages
    only when OPENROUTER_API_KEY is present.
    """

    def classify(
        self,
        latest_inbound: dict,
        conversation_history: list[dict],
        lead_state: dict,
        briefs: dict,
    ) -> ReplyHandlerOutput:
        """
        Classify an inbound reply.

        Args:
            latest_inbound: {"channel": "email"|"sms", "body": str}
            conversation_history: List of prior message dicts
            lead_state: Current prospect/lead state dict
            briefs: {"hiring_signal_brief": dict, "competitor_gap_brief": dict}

        Returns:
            ReplyHandlerOutput with intent and next_action.
        """
        body = latest_inbound.get("body", "")
        channel = latest_inbound.get("channel", "email")

        # Hard-coded fast paths (deterministic, no LLM)
        if _OPT_OUT_PATTERNS.search(body):
            return ReplyHandlerOutput(
                intent="unsubscribe",
                next_action="mark_opted_out",
                state_update={"opted_out": True},
                draft_context={},
            )

        if _HOSTILE_PATTERNS.search(body):
            return ReplyHandlerOutput(
                intent="hostile",
                next_action="escalate_human",
                requires_human=True,
                state_update={"lifecycle_stage": "escalated"},
                draft_context={"tone": "de-escalate"},
            )

        if _PRICING_PATTERNS.search(body):
            return ReplyHandlerOutput(
                intent="asks_for_pricing",
                next_action="pricing_gate",
                requires_human=True,
                state_update={"prospect_need": "pricing_info"},
                draft_context={"mention_public_band": True, "route_to_scoping": True},
            )

        if _CAPACITY_PATTERNS.search(body):
            bench_mismatch = briefs.get("hiring_signal_brief", {}).get("bench_mismatch", False)
            return ReplyHandlerOutput(
                intent="asks_for_capacity",
                next_action="bench_gate" if bench_mismatch else "send_email_reply",
                requires_human=bench_mismatch,
                state_update={"prospect_need": "staffing_capacity"},
                draft_context={"avoid_capacity_commitment": bench_mismatch},
            )

        if _SMS_PATTERNS.search(body):
            email_reply_count = lead_state.get("email_reply_count", 0)
            sms_allowed = email_reply_count >= 1
            return ReplyHandlerOutput(
                intent="asks_for_sms",
                next_action="channel_gate",
                state_update={"prospect_asked_sms": True, "sms_allowed": sms_allowed},
                draft_context={"channel": "sms" if sms_allowed else "email"},
            )

        if _SLOT_PATTERNS.search(body):
            return ReplyHandlerOutput(
                intent="chooses_slot",
                next_action="scheduler",
                state_update={"lifecycle_stage": "booking"},
                draft_context={"extract_slot": True},
            )

        if _NOT_INTERESTED_PATTERNS.search(body):
            return ReplyHandlerOutput(
                intent="not_interested",
                next_action="no_action",
                state_update={"lifecycle_stage": "dormant"},
                draft_context={},
            )

        if _CORRECTION_PATTERNS.search(body):
            do_not_repeat = self._extract_corrected_claims(body, briefs)
            prospect_need = self._extract_stated_need(body)
            return ReplyHandlerOutput(
                intent="interested_with_correction",
                next_action="send_email_reply",
                state_update={
                    "prospect_need": prospect_need,
                    "do_not_repeat_claims": do_not_repeat,
                    "lifecycle_stage": "warm",
                },
                draft_context={
                    "acknowledge_correction": True,
                    "avoid_gap_claim": True,
                    "do_not_repeat_claims": do_not_repeat,
                },
            )

        if _POSITIVE_PATTERNS.search(body):
            return ReplyHandlerOutput(
                intent="interested_positive",
                next_action="send_email_reply",
                state_update={"lifecycle_stage": "warm"},
                draft_context={"tone": "warm", "suggest_call": True},
            )

        # Ambiguous — try LLM if available, else unknown
        if os.environ.get("OPENROUTER_API_KEY"):
            try:
                return self._llm_classify(body, conversation_history, lead_state)
            except Exception as exc:
                logger.warning("LLM intent classification failed: %s", exc)

        return ReplyHandlerOutput(
            intent="unknown",
            next_action="send_email_reply",
            state_update={},
            draft_context={"ask_clarifying_question": True},
        )

    def _extract_corrected_claims(self, body: str, briefs: dict) -> list[str]:
        """Identify which prior claims the prospect is correcting."""
        corrected: list[str] = []
        gap_brief = briefs.get("competitor_gap_brief") or {}
        gaps = gap_brief.get("gaps", [])
        for gap in gaps:
            practice = gap.get("practice", "").lower()
            if practice and practice in body.lower():
                corrected.append(f"gap claim: {practice}")
        if re.search(r"\b(ml platform|machine learning|ai platform)\b", body, re.I):
            corrected.append("ml platform gap claim")
        return corrected

    def _extract_stated_need(self, body: str) -> str:
        """Extract the prospect's self-described need from correction text."""
        patterns = [
            (re.compile(r"our (issue|problem|challenge|need) is ([^.!?]+)", re.I), 2),
            (re.compile(r"we need ([^.!?]+)", re.I), 1),
            (re.compile(r"our focus is ([^.!?]+)", re.I), 1),
        ]
        for pattern, group in patterns:
            m = pattern.search(body)
            if m:
                return m.group(group).strip()
        return "unspecified"

    def _llm_classify(
        self,
        body: str,
        conversation_history: list[dict],
        lead_state: dict,
    ) -> ReplyHandlerOutput:
        """LLM fallback for genuinely ambiguous messages."""
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model=os.environ.get("OPENROUTER_MODEL", "qwen/qwen3-235b-a22b"),
            openai_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
            openai_api_base="https://openrouter.ai/api/v1",
            temperature=0.0,
        )
        system = (
            "Classify this inbound sales reply into exactly one of these intents:\n"
            "interested_positive, interested_with_correction, asks_for_pricing, "
            "asks_for_capacity, asks_for_sms, chooses_slot, not_interested, "
            "unsubscribe, confused, hostile, unknown\n"
            "Reply with ONLY the intent label, nothing else."
        )
        response = llm.invoke([
            SystemMessage(content=system),
            HumanMessage(content=f"Reply body: {body}"),
        ])
        intent = str(response.content).strip().lower()

        # Map LLM intent to next_action
        intent_to_action = {
            "interested_positive": "send_email_reply",
            "interested_with_correction": "send_email_reply",
            "asks_for_pricing": "pricing_gate",
            "asks_for_capacity": "bench_gate",
            "asks_for_sms": "channel_gate",
            "chooses_slot": "scheduler",
            "not_interested": "no_action",
            "unsubscribe": "mark_opted_out",
            "confused": "send_email_reply",
            "hostile": "escalate_human",
            "unknown": "send_email_reply",
        }
        action = intent_to_action.get(intent, "send_email_reply")
        return ReplyHandlerOutput(
            intent=intent,
            next_action=action,
            draft_context={"ask_clarifying_question": intent in ("confused", "unknown")},
        )
