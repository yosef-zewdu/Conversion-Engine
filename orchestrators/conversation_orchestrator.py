"""
Conversation Orchestrator — handles one inbound event through the full agent pipeline.

Architecture spec §16: Target graph:
  load_state → load_history → check_command → classify_intent → route_by_intent
  → compose_chain / scheduler / escalation → policy_review → tool_executor
  → write_crm → write_trace → update_state

Invariants:
  - Graph handles one event, then exits
  - STOP/HELP/UNSUB fast path avoids LLM
  - Prospect-facing text goes through mechanism and policy gate
  - CRM write and trace write always happen when safe

This orchestrator is the target architecture. The existing agent/graph.py
(enrich → classify → check_command → llm → tools → kill_switch → send_email → persist)
is the current production graph; this orchestrator is the planned successor that
wires the mechanism chain and policy gate into the conversation flow.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

class ConversationOrchestrator:
    """
    Full conversation pipeline for one inbound event.

    This wraps the LangGraph agent but adds:
      1. ReplyHandlerAgent intent classification
      2. 3-stage mechanism chain for outbound drafting
      3. PolicyGate review before send
      4. CRM summarizer write with trace_id
      5. Trace write to evidence graph

    Usage::

        orchestrator = ConversationOrchestrator()
        result = await orchestrator.handle_event(event)
    """

    async def handle_event(self, event: dict) -> dict:
        """
        Handle one inbound event.

        Args:
            event: Normalized event dict with keys:
              event_id, lead_id, conversation_id, event_type,
              channel, body, prospect (dict), briefs (dict)

        Returns:
            Result dict with reply_text, intent, policy_decision, crm_write_status,
            trace_id.
        """
        trace_id = f"tr_{uuid.uuid4().hex[:12]}"
        event_type = event.get("event_type", "unknown")
        channel = event.get("channel", "email")
        body = event.get("body", "")
        prospect = event.get("prospect") or {}
        briefs = event.get("briefs") or {}

        logger.info(
            "ConversationOrchestrator: trace=%s event_type=%s channel=%s",
            trace_id, event_type, channel,
        )

        # ── Step 1: Load state ────────────────────────────────────────────
        state = self._load_state(prospect, briefs, event)
        state["trace_id"] = trace_id

        # ── Step 2: check_command — fast path before LLM ─────────────────
        command_result = self._check_command(body)
        if command_result:
            return await self._handle_command(command_result, state, trace_id)

        # ── Step 3: classify_intent ───────────────────────────────────────
        from agent.reply_handler import ReplyHandlerAgent

        handler = ReplyHandlerAgent()
        reply_output = handler.classify(
            latest_inbound={"channel": channel, "body": body},
            conversation_history=state.get("conversation_history", []),
            lead_state=state,
            briefs=briefs,
        )
        state["classified_intent"] = reply_output.intent
        state.update(reply_output.state_update)

        logger.info("Intent classified: %s → %s", reply_output.intent, reply_output.next_action)

        # ── Step 4: route by intent ───────────────────────────────────────
        if reply_output.intent == "unsubscribe":
            return self._opt_out_response(state, trace_id)

        if reply_output.requires_human or reply_output.next_action == "escalate_human":
            return await self._escalate_human(state, reply_output, trace_id)

        if reply_output.next_action == "scheduler":
            return await self._handle_scheduling(body, state, trace_id)

        if reply_output.next_action == "no_action":
            return self._no_action_response(state, trace_id)

        # ── Step 5: compose outbound via 3-stage mechanism chain ──────────
        draft_result = self._compose_via_mechanism(state, briefs, channel)

        # ── Step 6: policy_review ─────────────────────────────────────────
        proposed_action = {
            "action_type": "send_email" if channel == "email" else "send_sms",
            "lead_id": prospect.get("prospect_id", ""),
            "channel": channel,
            "subject": draft_result.get("subject"),
            "body": draft_result.get("content", ""),
            "claims": draft_result.get("claims", []),
        }

        from policies.action_policy import EvidenceCalibratedActionPolicy

        policy = EvidenceCalibratedActionPolicy()
        decision = policy.review(state, proposed_action)

        if not decision.allowed:
            logger.warning(
                "Policy blocked action: %s violations=%s",
                reply_output.intent,
                decision.violations,
            )
            if decision.requires_human:
                return await self._escalate_human(state, reply_output, trace_id)

            # Fallback: no outbound, just write CRM
            crm_status = await self._write_crm(state, [], trace_id)
            return {
                "trace_id": trace_id,
                "intent": reply_output.intent,
                "reply_text": None,
                "policy_decision": {
                    "allowed": False,
                    "violations": decision.violations,
                },
                "crm_write_status": crm_status,
                "action_taken": "policy_blocked",
            }

        # ── Step 7: tool_executor — send ──────────────────────────────────
        tool_results = await self._execute_send(
            decision.safe_action or proposed_action,
            decision,
            state,
        )

        # ── Step 8: write_crm ─────────────────────────────────────────────
        crm_status = await self._write_crm(state, tool_results, trace_id)

        # ── Step 9: write_trace ───────────────────────────────────────────
        self._write_trace(trace_id, state, decision, tool_results)

        return {
            "trace_id": trace_id,
            "intent": reply_output.intent,
            "reply_text": (decision.safe_action or proposed_action).get("body"),
            "policy_decision": {
                "allowed": decision.allowed,
                "policy_decision_id": decision.policy_decision_id,
                "violations": decision.violations,
            },
            "crm_write_status": crm_status,
            "action_taken": "sent",
            "prospect": state.get("prospect"),
            "segment": state.get("segment"),
            "destination": state.get("destination"),
            "briefs": briefs,
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    def _load_state(self, prospect: dict, briefs: dict, event: dict) -> dict:
        return {
            "prospect": prospect,
            "hiring_signal_brief": briefs.get("hiring_signal_brief"),
            "competitor_gap_brief": briefs.get("competitor_gap_brief"),
            "segment": prospect.get("segment"),
            "bench_mismatch": (briefs.get("hiring_signal_brief") or {}).get("bench_mismatch", False),
            "channel": event.get("channel", "email"),
            "inbound_text": event.get("body", ""),
            "conversation_history": event.get("conversation_history", []),
            "email_reply_count": prospect.get("email_reply_count", 0),
            "sms_allowed": False,
            "lifecycle_stage": prospect.get("current_state", "cold"),
            "opted_out": False,
            "destination": "staff_sink",
        }

    def _check_command(self, body: str) -> str | None:
        text = body.strip().upper()
        if text in ("STOP", "UNSUB", "UNSUBSCRIBE", "CANCEL", "OPT OUT"):
            return "stop"
        if text in ("HELP", "INFO", "?"):
            return "help"
        return None

    async def _handle_command(
        self, command: str, state: dict, trace_id: str
    ) -> dict:
        if command == "stop":
            reply = "You have been unsubscribed. You will receive no further messages."
            action = "mark_opted_out"
        else:
            reply = "Tenacious Consulting — reply STOP to unsubscribe. Email hello@tenacious.co"
            action = "help_sent"

        await self._write_crm(state, [], trace_id)
        return {
            "trace_id": trace_id,
            "intent": command,
            "reply_text": reply,
            "action_taken": action,
            "policy_decision": {"allowed": True, "violations": []},
        }

    def _opt_out_response(self, state: dict, trace_id: str) -> dict:
        return {
            "trace_id": trace_id,
            "intent": "unsubscribe",
            "reply_text": "You have been unsubscribed.",
            "action_taken": "mark_opted_out",
            "policy_decision": {"allowed": True, "violations": []},
        }

    def _no_action_response(self, state: dict, trace_id: str) -> dict:
        return {
            "trace_id": trace_id,
            "intent": state.get("classified_intent", "not_interested"),
            "reply_text": None,
            "action_taken": "no_action",
            "policy_decision": {"allowed": True, "violations": []},
        }

    async def _escalate_human(
        self, state: dict, reply_output: Any, trace_id: str
    ) -> dict:
        logger.info("Escalating to human: intent=%s", getattr(reply_output, "intent", ""))
        await self._write_crm(state, [], trace_id)
        return {
            "trace_id": trace_id,
            "intent": getattr(reply_output, "intent", "escalated"),
            "reply_text": None,
            "action_taken": "escalate_human",
            "policy_decision": {"allowed": True, "violations": [], "requires_human": True},
        }

    async def _handle_scheduling(self, body: str, state: dict, trace_id: str) -> dict:
        try:
            from booking_agent.agent import BookingAgent

            agent = BookingAgent()
            prospect = state.get("prospect") or {}
            result = await agent.handle_slot_selection(
                lead_id=prospect.get("prospect_id", ""),
                inbound_text=body,
                prospect_timezone=prospect.get("timezone", "UTC"),
            )
            status = result.get("status", "")
            if status == "booked":
                reply_text = (
                    f"Your discovery call has been confirmed for {result.get('slot_local', 'your requested time')}. "
                    f"We look forward to speaking with you!"
                )
            elif status == "no_slot_found":
                reply_text = (
                    "Thanks for your reply! We couldn't find an exact match for that time. "
                    "Could you share a couple of alternative times that work for you?"
                )
            else:
                reply_text = (
                    "We encountered an issue booking your slot. "
                    "A team member will follow up shortly to confirm manually."
                )
            return {
                "trace_id": trace_id,
                "intent": "chooses_slot",
                "reply_text": reply_text,
                "action_taken": f"booking_{status}",
                "booking_result": result,
                "policy_decision": {"allowed": True, "violations": []},
            }
        except Exception as exc:
            logger.warning("Scheduling failed: %s", exc)
            return {
                "trace_id": trace_id,
                "intent": "chooses_slot",
                "reply_text": "A team member will follow up shortly to confirm your discovery call.",
                "action_taken": "booking_failed",
                "error": str(exc),
                "policy_decision": {"allowed": True, "violations": []},
            }

    def _compose_via_mechanism(
        self, state: dict, briefs: dict, channel: str
    ) -> dict:
        """Run 3-stage mechanism chain. Falls back to LangGraph agent output."""
        try:
            from mechanism.three_stage_chain import compose_outbound_chain
            from signal_pipeline.models import HiringSignalBrief, CompetitorGapBrief
            from config.models import Prospect, ProspectState, Segment

            brief_dict = briefs.get("hiring_signal_brief") or {}
            gap_dict = briefs.get("competitor_gap_brief")
            prospect_dict = state.get("prospect") or {}

            if not brief_dict:
                logger.warning("_compose_via_mechanism: no hiring_signal_brief — using LLM fallback")
                return {"content": None, "subject": None, "claims": []}

            brief = HiringSignalBrief(**brief_dict)
            gap_brief = CompetitorGapBrief(**gap_dict) if gap_dict else None
            prospect = Prospect(
                prospect_id=prospect_dict.get("prospect_id", "unknown"),
                company_id=prospect_dict.get("company_id", "unknown"),
                contact_name=prospect_dict.get("contact_name", ""),
                email=prospect_dict.get("email", ""),
                phone=prospect_dict.get("phone"),
                timezone=prospect_dict.get("timezone", "UTC"),
                preferred_channel=prospect_dict.get("preferred_channel", "email"),
                current_state=ProspectState(prospect_dict.get("current_state", "cold")),
                outbound_attempt_count=prospect_dict.get("outbound_attempt_count", 0),
                segment=Segment(prospect_dict.get("segment")) if prospect_dict.get("segment") else None,
            )

            msg = compose_outbound_chain(prospect, brief, gap_brief, channel)
            return {
                "content": msg.content,
                "subject": msg.subject,
                "claims": [],
                "mechanism": "3-stage-chain",
                "tone_score": (msg.metadata or {}).get("tone_score", 100),
            }

        except Exception as exc:
            logger.warning("Mechanism chain failed, returning None draft: %s", exc)
            return {"content": None, "subject": None, "claims": []}

    async def _execute_send(
        self,
        action: dict,
        decision: Any,
        state: dict,
    ) -> list[dict]:
        """Execute the approved action via the appropriate channel tool."""
        from config.kill_switch import get_outbound_destination
        from config.models import Destination

        destination = get_outbound_destination()
        channel = action.get("channel", "email")
        body = (action.get("body") or "").strip()
        if not body:
            body = "Thank you for your interest. A member of the Tenacious team will be in touch shortly."
        prospect = state.get("prospect") or {}

        # Kill switch routing
        if destination == Destination.STAFF_SINK:
            import os
            sink = os.environ.get("STAFF_SINK_EMAIL", "")
            to = sink if sink else prospect.get("email", "")
        else:
            to = prospect.get("email", "") if channel == "email" else prospect.get("phone", "")

        tool_result: dict = {
            "tool_name": f"send_{channel}",
            "status": "skipped",
            "destination": destination.value,
        }

        try:
            if channel == "email" and to:
                import resend
                import os

                resend.api_key = os.environ.get("RESEND_API_KEY", "")
                resend_from = os.environ.get("RESEND_FROM", "onboarding@resend.dev")
                prospect_id = prospect.get("prospect_id", "")
                logger.info("_execute_send: sending email to=%s from=%s body_len=%d", to, resend_from, len(body))
                resend.Emails.send({
                    "from": resend_from,
                    "reply_to": [resend_from],
                    "to": [to],
                    "subject": f"[Lead: {prospect_id}] {action.get('subject') or 'Re: Your inquiry — Tenacious Consulting'}",
                    "text": body,
                    "tags": [{"name": "prospect_id", "value": prospect_id}],
                    "headers": {"X-Tenacious-Status": "draft"},
                })
                logger.info("_execute_send: email sent successfully to=%s", to)
                tool_result["status"] = "success"
                tool_result["to"] = to
            elif channel == "sms" and to:
                from agent.sms_sender import send_sms

                await send_sms(to, body[:160])
                tool_result["status"] = "success"
                tool_result["to"] = to
        except Exception as exc:
            logger.warning("Send failed: %s", exc)
            tool_result["status"] = "failed"
            tool_result["error"] = str(exc)

        return [tool_result]

    async def _write_crm(
        self, state: dict, tool_results: list[dict], trace_id: str
    ) -> str:
        """Write CRM summary via CRMSummarizerAgent."""
        try:
            from agent.crm_summarizer import CRMSummarizerAgent

            summarizer = CRMSummarizerAgent()
            crm_payload = summarizer.summarize(state, tool_results, [trace_id])

            # CRM writes bypass kill switch — internal records only
            from crm_writer.writer import CRMWriter
            from config.models import Prospect, ProspectState, Segment

            writer = CRMWriter()
            prospect_dict = state.get("prospect") or {}
            
            # Reconstruct Prospect object for the writer
            prospect = Prospect(
                prospect_id=prospect_dict.get("prospect_id", "unknown"),
                company_id=prospect_dict.get("company_id", "unknown"),
                contact_name=prospect_dict.get("contact_name", ""),
                email=prospect_dict.get("email", ""),
                phone=prospect_dict.get("phone"),
                timezone=prospect_dict.get("timezone", "UTC"),
                preferred_channel=prospect_dict.get("preferred_channel", "email"),
                current_state=ProspectState(state.get("lifecycle_stage", "cold")),
                outbound_attempt_count=prospect_dict.get("outbound_attempt_count", 0),
                segment=Segment(state.get("segment")) if state.get("segment") else None,
            )

            # 1. Upsert contact with new properties (segment, maturity, etc.)
            hs_id = await writer.upsert_contact(prospect)
            
            # 2. Log the activity (the reply or the seed outreach)
            if tool_results:
                for tr in tool_results:
                    await writer.log_activity({
                        "type": tr.get("tool_name", "outbound_email"),
                        "prospect_id": prospect.prospect_id,
                        "channel": state.get("channel", "email"),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "content": crm_payload["timeline_note"]["body"],
                        "direction": "outbound"
                    }, hs_contact_id=hs_id)

            # 3. Write briefs if they've changed
            hiring_brief = state.get("hiring_signal_brief")
            if hiring_brief:
                from signal_pipeline.models import HiringSignalBrief
                await writer.write_brief(HiringSignalBrief(**hiring_brief), hs_contact_id=hs_id)

            return "ok"
        except Exception as exc:
            logger.warning("CRM write failed: %s", exc, exc_info=True)
            return f"failed: {exc}"

    def _write_trace(
        self,
        trace_id: str,
        state: dict,
        decision: Any,
        tool_results: list[dict],
    ) -> None:
        """Append trace record to evidence graph."""
        try:
            import json
            from pathlib import Path

            trace = {
                "trace_id": trace_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "segment": state.get("segment"),
                "intent": state.get("classified_intent"),
                "policy_allowed": decision.allowed,
                "policy_violations": decision.violations,
                "tool_results": tool_results,
                "lead_id": (state.get("prospect") or {}).get("prospect_id"),
            }

            evidence_path = Path("evidence_graph/traces.jsonl")
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            with open(evidence_path, "a") as f:
                f.write(json.dumps(trace) + "\n")
        except Exception as exc:
            logger.warning("Trace write failed: %s", exc)
