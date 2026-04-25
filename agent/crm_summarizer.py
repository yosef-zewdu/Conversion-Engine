"""
CRM Summary Agent — converts current lead state into structured HubSpot fields
and timeline notes.

Architecture spec §12:
  Input:  AgentState, ToolResults, TraceRefs
  Output: contact_properties dict + timeline_note dict

Invariants:
  - CRM writes bypass outbound kill switch (they are internal records)
  - Every CRM write includes trace_id
  - Do not log failed drafts as sent messages
  - HubSpot records include source refs or brief refs

LLM is optional (summary sentence only). Defaults to deterministic construction.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class CRMSummarizerAgent:
    """
    Converts AgentState and tool results into HubSpot contact properties
    and a timeline note.

    Deterministic by default. LLM used only when OPENROUTER_API_KEY is set
    and a summary sentence is requested.
    """

    def summarize(
        self,
        state: dict[str, Any],
        tool_results: list[dict],
        trace_refs: list[str],
    ) -> dict:
        """
        Build CRM payload from current agent state.

        Args:
            state: Current AgentState dict.
            tool_results: List of tool execution result dicts.
            trace_refs: List of trace_id strings for this interaction.

        Returns:
            {"contact_properties": {...}, "timeline_note": {...}}
        """
        brief = state.get("hiring_signal_brief") or {}
        gap_brief = state.get("competitor_gap_brief") or {}
        segment = state.get("segment") or "unqualified"
        bench_mismatch = state.get("bench_mismatch", False)
        lifecycle_stage = state.get("lifecycle_stage", "lead")
        trace_id = trace_refs[0] if trace_refs else state.get("trace_id", "")
        now = datetime.now(timezone.utc).isoformat()

        # Derive risk flags
        risk_flags = self._build_risk_flags(state, brief, gap_brief)

        contact_properties = {
            "icp_segment": segment,
            "icp_confidence": brief.get("icp_confidence", 0.0),
            "ai_maturity_score": brief.get("ai_maturity_score"),
            "last_enriched_at": brief.get("last_enriched_at", now),
            "lead_status": self._map_lifecycle_to_hs_status(lifecycle_stage),
            "risk_flags": "; ".join(risk_flags) if risk_flags else "none",
            "bench_mismatch": str(bench_mismatch).lower(),
            "trace_id": trace_id,
            "source_refs": self._build_source_refs(brief),
            "tenacious_status": "draft",  # Invariant: always draft
        }

        # Add hiring velocity if available
        hv = brief.get("hiring_velocity") or {}
        if hv.get("open_roles_today") is not None:
            contact_properties["open_engineering_roles"] = hv["open_roles_today"]
        if brief.get("job_post_count") is not None:
            contact_properties["job_post_count"] = brief["job_post_count"]

        # Timeline note
        event_description = self._build_event_description(state, tool_results)
        timeline_note = {
            "title": self._build_note_title(state),
            "body": event_description,
            "trace_refs": trace_refs,
            "created_at": now,
            "tenacious_status": "draft",
        }

        return {
            "contact_properties": contact_properties,
            "timeline_note": timeline_note,
        }

    def _map_lifecycle_to_hs_status(self, lifecycle_stage: str) -> str:
        mapping = {
            "cold": "new",
            "contacted": "open",
            "replied": "in_progress",
            "warm": "in_progress",
            "booking": "open_deal",
            "dormant": "unqualified",
            "opted_out": "unqualified",
            "escalated": "in_progress",
        }
        return mapping.get(lifecycle_stage, "new")

    def _build_risk_flags(
        self,
        state: dict,
        brief: dict,
        gap_brief: dict,
    ) -> list[str]:
        flags: list[str] = []

        # Gap confidence flags
        gaps = gap_brief.get("gaps", []) if gap_brief else []
        low_gap = [g for g in gaps if g.get("confidence") == "low"]
        if low_gap:
            flags.append("low_confidence_gap_claim_avoided")

        # Bench mismatch flag
        if state.get("bench_mismatch"):
            flags.append("bench_mismatch_capacity_not_committed")

        # Small peer set flag
        peer_count = gap_brief.get("peer_count", 0) if gap_brief else 0
        if 0 < peer_count < 5:
            flags.append(f"small_peer_set_{peer_count}")

        # Honesty flags from enrichment pipeline
        for hf in brief.get("honesty_flags", []):
            flags.append(hf)

        return flags

    def _build_source_refs(self, brief: dict) -> str:
        refs: list[str] = []
        funding = brief.get("funding_event") or {}
        if funding.get("source_ref"):
            refs.append(funding["source_ref"])
        layoff = brief.get("layoff_event") or {}
        if layoff.get("source_ref"):
            refs.append(layoff["source_ref"])
        leadership = brief.get("leadership_change") or {}
        if leadership.get("source_ref"):
            refs.append(leadership["source_ref"])
        return "; ".join(refs) if refs else "no_source_refs"

    def _build_note_title(self, state: dict) -> str:
        channel = state.get("channel", "email")
        intent = state.get("classified_intent", "")
        lifecycle = state.get("lifecycle_stage", "")

        if intent == "interested_with_correction":
            return "Prospect replied with correction; agent updated"
        if intent in ("interested_positive",):
            return f"Signal-grounded {channel} outreach sent — positive reply"
        if intent == "chooses_slot":
            return "Prospect selected discovery call slot"
        if intent == "unsubscribe":
            return "Prospect opted out"
        if state.get("reply_text"):
            return f"Signal-grounded {channel} outreach sent"
        return "Lead state updated"

    def _build_event_description(
        self,
        state: dict,
        tool_results: list[dict],
    ) -> str:
        parts: list[str] = []

        intent = state.get("classified_intent", "")
        if intent:
            parts.append(f"Intent classified: {intent}.")

        need = state.get("prospect_need", "")
        if need and need != "unspecified":
            parts.append(f"Prospect need: {need}.")

        do_not_repeat = state.get("do_not_repeat_claims", [])
        if do_not_repeat:
            parts.append(f"Claims to avoid: {', '.join(do_not_repeat)}.")

        segment = state.get("segment")
        if segment:
            parts.append(f"ICP segment: {segment}.")

        for tr in tool_results:
            if tr.get("status") == "success":
                parts.append(f"Tool {tr.get('tool_name')} executed successfully.")

        if not parts:
            parts.append("Lead state updated by ConversationAgent.")

        return " ".join(parts)
