"""
LangGraph conversation agent for the Conversion Engine.

Graph nodes:
  enrich        → run signal pipeline, produce 3 briefs
  classify      → ICP classifier + bench match
  check_command → handle STOP / HELP / UNSUB before LLM
  llm           → OpenRouter call with 9 HubSpot MCP tools
  tools         → execute MCP tool calls
  kill_switch   → route to staff sink or real prospect
  send_email    → dispatch via Resend
  persist       → write final state to HubSpot

State is persisted via LangGraph's MemorySaver (in-process) keyed by
prospect_id, so multi-turn conversations pick up where they left off.

Requirements: 1.1–1.5, 2.1–2.8, 6.1–6.5, 8.1–8.7, 10.1–10.5, 11.1
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from agent.tool_executor import execute_tool
from agent.tools import HUBSPOT_TOOLS
from config.kill_switch import get_outbound_destination
from config.models import Destination, Prospect, ProspectState, Segment
from signal_pipeline.models import CompetitorGapBrief, HiringSignalBrief

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------


class AgentState(TypedDict):
    """Shared state threaded through every graph node.

    Args:
        messages: Full conversation history (LangGraph managed).
        prospect: The Prospect dataclass being processed.
        hiring_signal_brief: Enrichment brief, set after enrich node.
        competitor_gap_brief: Gap brief, set after enrich node.
        segment: ICP segment assigned by classifier.
        bench_mismatch: True when bench has no engineers for prospect stack.
        hs_contact_id: HubSpot contact ID, set after first CRM write.
        inbound_text: Raw text of the inbound message triggering this turn.
        channel: Channel the inbound arrived on ("email" | "sms").
        command: Detected command ("stop" | "help" | "unsub" | None).
        reply_text: Final reply text to send to the prospect.
        destination: Kill switch routing result.
        cal_event_id: Cal.com booking UID when triggered by BOOKING_CREATED.
        error: Error message if a node fails.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    prospect: Optional[dict]          # Prospect serialised as dict
    hiring_signal_brief: Optional[dict]
    competitor_gap_brief: Optional[dict]
    segment: Optional[str]
    bench_mismatch: bool
    hs_contact_id: Optional[str]
    inbound_text: str
    channel: str
    command: Optional[str]
    reply_text: Optional[str]
    destination: Optional[str]
    cal_event_id: Optional[str]
    error: Optional[str]
    # Act IV mechanism outputs
    policy_decision: Optional[dict]   # PolicyDecision as dict, set after policy_review node
    mechanism_metadata: Optional[dict]  # ToneGuard score + stage1 include flags
    icp_result: Optional[dict]         # Full classification result (segment, confidence, signals_used)


# ---------------------------------------------------------------------------
# LLM client (OpenRouter)
# ---------------------------------------------------------------------------

def _build_llm() -> ChatOpenAI:
    """Build the OpenRouter-backed ChatOpenAI client with HubSpot tools bound.

    Returns:
        ChatOpenAI instance with tools bound.
    """
    llm = ChatOpenAI(
        model=os.environ.get("OPENROUTER_MODEL", "qwen/qwen3-235b-a22b"),
        openai_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        openai_api_base="https://openrouter.ai/api/v1",
        temperature=0.3,
    )
    return llm.bind_tools(HUBSPOT_TOOLS)


# ---------------------------------------------------------------------------
# Node: enrich
# ---------------------------------------------------------------------------

async def node_enrich(state: AgentState) -> dict:
    """Run the signal pipeline to produce enrichment briefs.

    Calls PipelineRunner._enrich() on the prospect. On failure, sets
    state.error and returns empty briefs so downstream nodes can degrade
    gracefully.

    Skips Playwright scraping for synthetic prospects (company_id contains
    "test", "demo", or "synthetic") to keep smoke tests fast.

    Args:
        state: Current graph state.

    Returns:
        Partial state update with hiring_signal_brief and competitor_gap_brief.
    """
    from signal_pipeline.pipeline_runner import PipelineRunner

    prospect_dict = state.get("prospect") or {}
    company_id = prospect_dict.get("company_id", "")

    # Use pre-baked briefs when supplied (demo / campaign queue path)
    pre_baked_brief = prospect_dict.get("hiring_signal_brief")
    pre_baked_gap = prospect_dict.get("competitor_gap_brief")
    if pre_baked_brief:
        logger.info("Enrichment: using pre-baked brief for prospect_id=%s", prospect_dict.get("prospect_id"))
        return {
            "hiring_signal_brief": pre_baked_brief,
            "competitor_gap_brief": pre_baked_gap,
        }

    # Skip heavy scraping for synthetic/test prospects
    _SYNTHETIC_MARKERS = ("test", "demo", "synthetic", "sandbox", "fake")
    is_synthetic = any(m in company_id.lower() for m in _SYNTHETIC_MARKERS)

    try:
        prospect = _dict_to_prospect(prospect_dict)
        runner = PipelineRunner()

        if is_synthetic:
            # Lightweight enrichment: skip Playwright scraping
            from signal_pipeline.firmographic_enricher import FirmographicEnricher
            from signal_pipeline.hiring_signal_brief_assembler import AssemblerInput, HiringSignalBriefAssembler
            from signal_pipeline.ai_maturity_scorer import AIMaturityScorer, AIMaturityInput

            firmographic = FirmographicEnricher().enrich_by_name(company_id)
            ai_maturity = AIMaturityScorer().score(AIMaturityInput())
            assembler_input = AssemblerInput(
                company_id=company_id,
                company_name=prospect_dict.get("contact_name", company_id),
                firmographic=firmographic,
                layoff=None,
                job_posts=None,
                leadership=None,
                funding=None,
                ai_maturity=ai_maturity,
                competitor_gap=None,
            )
            brief, gap_brief = HiringSignalBriefAssembler().assemble(assembler_input)
        else:
            brief, gap_brief = await runner._enrich(prospect)

        logger.info("Enrichment complete for prospect_id=%s synthetic=%s", prospect.prospect_id, is_synthetic)
        return {
            "hiring_signal_brief": brief.model_dump(mode="json"),
            "competitor_gap_brief": gap_brief.model_dump(mode="json") if gap_brief else None,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Enrichment failed: %s — continuing with empty briefs", exc)
        return {
            "hiring_signal_brief": None,
            "competitor_gap_brief": None,
            "error": f"enrichment_failed: {exc}",
        }


# ---------------------------------------------------------------------------
# Node: classify
# ---------------------------------------------------------------------------

async def node_classify(state: AgentState) -> dict:
    """Run ICP classifier and bench match on the enriched brief.

    Args:
        state: Current graph state.

    Returns:
        Partial state update with segment and bench_mismatch.
    """
    from icp_classifier.classifier import ClassifierConfig, classify
    from icp_classifier.bench_match import BenchToBriefMatch
    from signal_pipeline.models import HiringSignalBrief

    brief_dict = state.get("hiring_signal_brief")
    if not brief_dict:
        return {"segment": "unqualified", "bench_mismatch": False}

    try:
        brief = HiringSignalBrief(**brief_dict)
        segment_result = classify(brief, ClassifierConfig())
        bench_result = BenchToBriefMatch().match(brief.tech_stack)
        logger.info(
            "Classified: segment=%s confidence=%.2f bench_mismatch=%s",
            segment_result.segment.value,
            segment_result.confidence,
            bench_result.bench_mismatch,
        )
        return {
            "segment": segment_result.segment.value,
            "bench_mismatch": bench_result.bench_mismatch,
            "icp_result": {
                "segment": segment_result.segment.value,
                "confidence": segment_result.confidence,
                "signals_used": segment_result.signals_used,
                "decision": "qualified" if not segment_result.abstained else "abstained",
            },
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Classification failed: %s", exc)
        return {"segment": "unqualified", "bench_mismatch": False, "icp_result": None}


# ---------------------------------------------------------------------------
# Node: check_command
# ---------------------------------------------------------------------------

async def node_check_command(state: AgentState) -> dict:
    """Detect STOP / HELP / UNSUB commands before the LLM sees the message.

    Args:
        state: Current graph state.

    Returns:
        Partial state update with command and optionally reply_text.
    """
    from nurture_sequencer.state_machine import is_opt_out

    text = (state.get("inbound_text") or "").strip().upper()

    if is_opt_out(text) or text in ("STOP", "UNSUB", "UNSUBSCRIBE", "CANCEL"):
        logger.info("Opt-out command detected")
        return {
            "command": "stop",
            "reply_text": "You have been unsubscribed. You will receive no further messages.",
        }

    if text in ("HELP", "INFO", "?"):
        return {
            "command": "help",
            "reply_text": (
                "Tenacious Consulting — reply STOP to unsubscribe. "
                "For questions email hello@tenacious.co"
            ),
        }

    return {"command": None}


# ---------------------------------------------------------------------------
# Node: llm
# ---------------------------------------------------------------------------

async def node_llm(state: AgentState) -> dict:
    """Call the LLM with the full conversation context and HubSpot tools.

    Checks the budget guard before calling. Records token usage after.
    Emits a Langfuse trace via the @observe decorator.

    Args:
        state: Current graph state.

    Returns:
        Partial state update with new AI message appended to messages.
    """
    from config.budget_guard import BudgetExceededError, get_budget_guard

    guard = get_budget_guard()

    try:
        guard.check()
    except BudgetExceededError as exc:
        logger.error("Budget guard blocked LLM call: %s", exc)
        fallback = AIMessage(
            content=(
                "Our automated system has reached its usage limit for today. "
                "A Tenacious team member will follow up with you shortly."
            )
        )
        return {"messages": [fallback], "error": f"budget_exceeded: ${exc.total_usd:.4f}"}

    llm = _build_llm()
    system_prompt = _build_system_prompt(state)
    messages = [SystemMessage(content=system_prompt)] + list(state.get("messages") or [])

    # Langfuse Traceability (Req 11.1)
    callbacks = []
    try:
        from langfuse.callback import CallbackHandler
        handler = CallbackHandler(
            public_key=os.environ.get("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.environ.get("LANGFUSE_SECRET_KEY"),
            host=os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
        )
        callbacks.append(handler)
    except Exception as exc:
        logger.warning("Langfuse callback initialization failed: %s", exc)

    try:
        import time
        t0 = time.monotonic()
        
        # Invoke LLM with Langfuse callbacks
        response = await llm.ainvoke(messages, config={"callbacks": callbacks})
        
        latency_ms = int((time.monotonic() - t0) * 1000)

        usage = getattr(response, "usage_metadata", None) or {}
        prompt_tokens = getattr(usage, "input_tokens", None) or (usage.get("input_tokens") if isinstance(usage, dict) else 0) or 0
        completion_tokens = getattr(usage, "output_tokens", None) or (usage.get("output_tokens") if isinstance(usage, dict) else 0) or 0
        model = os.environ.get("OPENROUTER_MODEL", "_default")
        guard.record(prompt_tokens, completion_tokens, model)

        logger.info(
            "LLM: latency=%dms prompt_tokens=%d completion_tokens=%d budget=$%.4f",
            latency_ms, prompt_tokens, completion_tokens, guard.total_usd,
        )
        return {"messages": [response]}
    except Exception as exc:  # noqa: BLE001
        logger.error("LLM call failed: %s", exc)
        fallback = AIMessage(content="I'll have a team member follow up with you shortly.")
        return {"messages": [fallback], "error": f"llm_failed: {exc}"}


# ---------------------------------------------------------------------------
# Node: tools
# ---------------------------------------------------------------------------

async def node_tools(state: AgentState) -> dict:
    """Execute HubSpot MCP tool calls requested by the LLM.

    Iterates over all tool_calls in the last AI message, executes each
    via tool_executor, and appends ToolMessage results so the LLM can
    continue its reasoning.

    Args:
        state: Current graph state.

    Returns:
        Partial state update with ToolMessage results appended to messages.
    """
    messages = state.get("messages") or []
    last_msg = messages[-1] if messages else None

    if not isinstance(last_msg, AIMessage) or not last_msg.tool_calls:
        return {}

    tool_messages: list[ToolMessage] = []
    for tc in last_msg.tool_calls:
        result = await execute_tool(tc["name"], tc["args"])
        tool_messages.append(
            ToolMessage(content=result, tool_call_id=tc["id"])
        )
        logger.info("Tool %s executed: result_len=%d", tc["name"], len(result))

    return {"messages": tool_messages}


# ---------------------------------------------------------------------------
# Node: kill_switch
# ---------------------------------------------------------------------------

async def node_kill_switch(state: AgentState) -> dict:
    """Apply kill switch routing — staff sink or real prospect.

    Args:
        state: Current graph state.

    Returns:
        Partial state update with destination.
    """
    destination = get_outbound_destination()
    logger.info("Kill switch: destination=%s", destination.value)
    return {"destination": destination.value}


# ---------------------------------------------------------------------------
# Node: send_email
# ---------------------------------------------------------------------------

async def node_send_email(state: AgentState) -> dict:
    """Send the reply via the appropriate channel (email via Resend, SMS via Africa's Talking).

    Extracts the final text reply from the last AI message, applies the
    kill switch, and dispatches via the channel that initiated this turn.

    Args:
        state: Current graph state.

    Returns:
        Partial state update with reply_text set.
    """
    import re
    import resend  # type: ignore[import]
    from agent.sms_sender import send_sms

    messages = state.get("messages") or []
    reply_text = state.get("reply_text")

    # Extract reply from last AI message if not already set
    if not reply_text:
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content:
                reply_text = str(msg.content)
                break

    # Strip any leaked tool schema tags the LLM may have included
    if reply_text:
        reply_text = re.sub(r"<tools>.*?</tools>", "", reply_text, flags=re.DOTALL).strip()
        reply_text = re.sub(r"\{\"type\":\s*\"function\".*?\}\s*$", "", reply_text, flags=re.DOTALL).strip()

    reply_text = (reply_text or "").strip()
    if not reply_text:
        reply_text = "Thank you for your message. A team member will follow up shortly."

    prospect_dict = state.get("prospect") or {}
    channel = state.get("channel", "email")
    destination = state.get("destination", Destination.STAFF_SINK.value)

    # Route to staff sink when kill switch is not live
    if destination == Destination.STAFF_SINK.value:
        sink_email = os.environ.get("STAFF_SINK_EMAIL", prospect_dict.get("email", ""))
        sink_phone = os.environ.get("STAFF_SINK_PHONE", "")
        if channel == "sms" and sink_phone:
            logger.info("Kill switch: routing SMS to staff sink %s", sink_phone)
            try:
                await send_sms(sink_phone, reply_text[:160])
                logger.info("SMS (staff sink) sent to %s", sink_phone)
            except Exception as exc:  # noqa: BLE001
                logger.warning("SMS send to staff sink failed: %s", exc)
        else:
            logger.info("Kill switch: routing email to staff sink %s", sink_email)
            try:
                resend.api_key = os.environ.get("RESEND_API_KEY", "")
                resend_from = os.environ.get("RESEND_FROM", "onboarding@resend.dev")
                resend.Emails.send({
                    "from": resend_from,
                    "reply_to": [resend_from],
                    "to": [sink_email],
                    "subject": f"[Lead: {prospect_dict.get('prospect_id', '')}] Re: Your inquiry — Tenacious Consulting",
                    "text": reply_text,
                    "tags": [{"name": "prospect_id", "value": prospect_dict.get("prospect_id", "")}],
                })
                logger.info("Email (staff sink) sent to %s", sink_email)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Email send to staff sink failed: %s", exc)
        return {"reply_text": reply_text}

    # Live routing to real prospect
    if channel == "sms":
        to_phone = prospect_dict.get("phone", "")
        if not to_phone:
            logger.warning("SMS channel but prospect has no phone — falling back to email")
            channel = "email"
        else:
            try:
                await send_sms(to_phone, reply_text[:160])
                logger.info("SMS sent to %s", to_phone)
            except Exception as exc:  # noqa: BLE001
                logger.warning("SMS send failed: %s", exc)
            return {"reply_text": reply_text}

    # Email dispatch (default)
    to_email = prospect_dict.get("email", "")
    prospect_id = prospect_dict.get("prospect_id", "")
    resend_from = os.environ.get("RESEND_FROM", "onboarding@resend.dev")
    try:
        resend.api_key = os.environ.get("RESEND_API_KEY", "")
        resend.Emails.send({
            "from": resend_from,
            "reply_to": [resend_from],
            "to": [to_email],
            "subject": f"[Lead: {prospect_id}] Re: Your inquiry — Tenacious Consulting",
            "text": reply_text,
            "tags": [{"name": "prospect_id", "value": prospect_id}],
        })
        logger.info("Email sent to %s", to_email)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Email send failed: %s", exc)

    return {"reply_text": reply_text}


# ---------------------------------------------------------------------------
# Node: persist
# ---------------------------------------------------------------------------

async def node_persist(state: AgentState) -> dict:
    """Write conversation state to HubSpot via MCP tools.

    Upserts the contact, logs the inbound message, and logs the outbound
    reply. Uses tool_executor directly (not via LLM) for deterministic writes.

    Args:
        state: Current graph state.

    Returns:
        Partial state update with hs_contact_id.
    """
    prospect_dict = state.get("prospect") or {}
    email = prospect_dict.get("email", "")
    contact_id = state.get("hs_contact_id", "")

    # 1. Upsert contact — skip if LLM already created it (contact_id in messages)
    if email and not contact_id:
        # Check if LLM already created/found the contact in the tool loop
        for msg in (state.get("messages") or []):
            if hasattr(msg, "content") and isinstance(msg.content, str):
                try:
                    data = json.loads(msg.content)
                    results = data.get("results", [])
                    if results and results[0].get("id"):
                        contact_id = str(results[0]["id"])
                        break
                except (json.JSONDecodeError, AttributeError):
                    pass

    if email and not contact_id:
        try:
            result = await execute_tool("hubspot_upsert_contact", {
                "email": email,
                "firstname": prospect_dict.get("contact_name", "").split()[0] if prospect_dict.get("contact_name") else "",
                "lastname": " ".join(prospect_dict.get("contact_name", "").split()[1:]),
                "company": prospect_dict.get("company_id", ""),
            })
            data = json.loads(result) if isinstance(result, str) else result
            results = data.get("results", [])
            if results:
                contact_id = str(results[0].get("id", ""))
        except Exception as exc:  # noqa: BLE001
            logger.warning("CRM upsert failed: %s", exc)

    if not contact_id:
        # Try to find existing contact
        try:
            result = await execute_tool("hubspot_get_contact", {"email": email})
            data = json.loads(result) if isinstance(result, str) else result
            results = data.get("results", [])
            if results:
                contact_id = str(results[0].get("id", ""))
        except Exception as exc:  # noqa: BLE001
            logger.warning("CRM contact lookup failed: %s", exc)

    now = datetime.now(timezone.utc).isoformat()

    # 2. Log inbound message
    if contact_id and state.get("inbound_text"):
        try:
            await execute_tool("hubspot_log_activity", {
                "contact_id": contact_id,
                "channel": state.get("channel", "email"),
                "direction": "inbound",
                "content": state.get("inbound_text", ""),
                "timestamp": now,
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("CRM inbound log failed: %s", exc)

    # 3. Log outbound reply
    if contact_id and state.get("reply_text"):
        try:
            await execute_tool("hubspot_log_activity", {
                "contact_id": contact_id,
                "channel": "email",
                "direction": "outbound",
                "content": state.get("reply_text", ""),
                "timestamp": now,
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("CRM outbound log failed: %s", exc)

    # 4. Write brief if available
    if contact_id and state.get("hiring_signal_brief"):
        try:
            await execute_tool("hubspot_write_brief", {
                "contact_id": contact_id,
                "brief_type": "HiringSignalBrief",
                "brief_json": json.dumps(state["hiring_signal_brief"]),
                "enriched_at": now,
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("CRM brief write failed: %s", exc)

    # 5. Write booking record when triggered by a Cal.com BOOKING_CREATED event
    cal_event_id = state.get("cal_event_id")
    if contact_id and cal_event_id:
        brief = state.get("hiring_signal_brief") or {}
        brief_ref = f"{(state.get('prospect') or {}).get('company_id', '')}@{now}"
        try:
            await execute_tool("hubspot_write_booking", {
                "contact_id": contact_id,
                "cal_event_id": cal_event_id,
                "icp_segment": state.get("segment") or "unqualified",
                "brief_ref": brief_ref,
                "start_utc": now,
            })
            logger.info(
                "Booking written to HubSpot: cal_event_id=%s contact_id=%s",
                cal_event_id, contact_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("CRM booking write failed: %s", exc)

    return {"hs_contact_id": contact_id or None}


# ---------------------------------------------------------------------------
# Node: compose_outbound
# ---------------------------------------------------------------------------

async def node_compose_outbound(state: AgentState) -> dict:
    """Run the 3-stage mechanism chain to produce a draft outbound message.

    Researcher → Closer → ToneGuard. Populates reply_text and mechanism_metadata.
    Falls back to the LLM node when no hiring_signal_brief is available (e.g.
    enrichment failed) so the graph degrades gracefully.

    Args:
        state: Current graph state.

    Returns:
        Partial state update with reply_text and mechanism_metadata.
    """
    from mechanism.three_stage_chain import compose_outbound_chain
    from signal_pipeline.models import HiringSignalBrief, CompetitorGapBrief

    brief_dict = state.get("hiring_signal_brief")
    if not brief_dict:
        # No brief — fall through to LLM node for a generic reply
        return {}

    prospect_dict = state.get("prospect") or {}
    gap_dict = state.get("competitor_gap_brief")
    channel = state.get("channel", "email")

    try:
        prospect = _dict_to_prospect(prospect_dict)
        brief = HiringSignalBrief(**brief_dict)
        gap_brief = CompetitorGapBrief(**gap_dict) if gap_dict else None

        outbound = compose_outbound_chain(prospect, brief, gap_brief, channel)

        logger.info(
            "Mechanism chain complete: tone_score=%d violations=%s",
            outbound.metadata.get("tone_score", 0),
            outbound.metadata.get("tone_violations", []),
        )
        return {
            "reply_text": outbound.content,
            "mechanism_metadata": outbound.metadata,
        }

    except Exception as exc:
        logger.warning("Mechanism chain failed (%s) — continuing to LLM node", exc)
        return {}


# ---------------------------------------------------------------------------
# Node: policy_review
# ---------------------------------------------------------------------------

async def node_policy_review(state: AgentState) -> dict:
    """Run EvidenceCalibratedActionPolicy on the draft reply.

    Blocks the send if the policy gate rejects the draft. Sets policy_decision
    in state so the send_email node and persist node can include the decision ID.

    Args:
        state: Current graph state.

    Returns:
        Partial state update with policy_decision (and cleared reply_text if blocked).
    """
    from policies.action_policy import EvidenceCalibratedActionPolicy

    reply_text = state.get("reply_text")
    if not reply_text:
        # Nothing to review
        return {"policy_decision": None}

    channel = state.get("channel", "email")
    prospect = state.get("prospect") or {}

    proposed_action = {
        "action_type": f"send_{channel}",
        "lead_id": prospect.get("prospect_id", ""),
        "channel": channel,
        "subject": state.get("mechanism_metadata", {}).get("subject", "") if state.get("mechanism_metadata") else "",
        "body": reply_text,
        "claims": [],
        "requires_policy_review": True,
    }

    policy = EvidenceCalibratedActionPolicy()
    decision = policy.review(dict(state), proposed_action)

    decision_dict = {
        "allowed": decision.allowed,
        "requires_rewrite": decision.requires_rewrite,
        "requires_human": decision.requires_human,
        "violations": decision.violations,
        "policy_version": decision.policy_version,
        "policy_decision_id": decision.policy_decision_id,
    }

    if not decision.allowed:
        logger.warning(
            "PolicyGate blocked outbound for %s: %s",
            prospect.get("prospect_id"),
            decision.violations,
        )
        # Clear reply_text so send_email node sends nothing to prospect
        return {
            "policy_decision": decision_dict,
            "reply_text": None,
            "error": f"policy_blocked: {'; '.join(decision.violations)}",
        }

    logger.info("PolicyGate approved outbound (id=%s)", decision.policy_decision_id)
    return {"policy_decision": decision_dict}


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------

def route_after_command(state: AgentState) -> Literal["compose_outbound", "kill_switch"]:
    """Route to kill_switch if a command was detected, else to compose_outbound.

    Args:
        state: Current graph state.

    Returns:
        Next node name.
    """
    if state.get("command"):
        return "kill_switch"
    return "compose_outbound"


def route_after_compose(state: AgentState) -> Literal["policy_review", "llm"]:
    """Route to policy_review if mechanism produced a draft, else to llm.

    When the mechanism chain succeeds (reply_text is set), the draft goes
    directly to policy_review — the LLM is bypassed for cost and consistency.
    When no brief was available, fall through to the LLM node for a generic reply.

    Args:
        state: Current graph state.

    Returns:
        Next node name.
    """
    if state.get("reply_text"):
        return "policy_review"
    return "llm"


def route_after_llm(state: AgentState) -> Literal["tools", "policy_review"]:
    """Route to tools if LLM made tool calls, else to policy_review.

    Args:
        state: Current graph state.

    Returns:
        Next node name.
    """
    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        return "tools"
    return "policy_review"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_graph() -> Any:
    """Build and compile the LangGraph conversation graph.

    Returns:
        Compiled LangGraph app with MemorySaver checkpointing.
    """
    builder = StateGraph(AgentState)

    builder.add_node("enrich", node_enrich)
    builder.add_node("classify", node_classify)
    builder.add_node("check_command", node_check_command)
    builder.add_node("compose_outbound", node_compose_outbound)
    builder.add_node("policy_review", node_policy_review)
    builder.add_node("llm", node_llm)
    builder.add_node("tools", node_tools)
    builder.add_node("kill_switch", node_kill_switch)
    builder.add_node("send_email", node_send_email)
    builder.add_node("persist", node_persist)

    # Edges — target graph:
    # enrich → classify → check_command → compose_outbound → policy_review
    #        → kill_switch → send_email → persist
    # LLM fallback: compose_outbound → llm → policy_review (when no brief)
    builder.add_edge(START, "enrich")
    builder.add_edge("enrich", "classify")
    builder.add_edge("classify", "check_command")
    builder.add_conditional_edges("check_command", route_after_command)
    builder.add_conditional_edges("compose_outbound", route_after_compose)
    builder.add_conditional_edges("llm", route_after_llm)
    builder.add_edge("tools", "llm")          # tool results loop back to LLM
    builder.add_edge("policy_review", "kill_switch")
    builder.add_edge("kill_switch", "send_email")
    builder.add_edge("send_email", "persist")
    builder.add_edge("persist", END)

    memory = MemorySaver()
    return builder.compile(checkpointer=memory)


# Module-level compiled graph
_graph = None


def get_graph() -> Any:
    """Return the module-level compiled graph singleton.

    Returns:
        Compiled LangGraph app.
    """
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def handle_inbound(
    prospect_dict: dict,
    inbound_text: str,
    channel: str = "email",
    cal_event_id: Optional[str] = None,
) -> dict:
    """Handle one inbound message turn for a prospect.

    Args:
        prospect_dict: Prospect serialised as dict (must include prospect_id, email).
        inbound_text: Raw text of the inbound message.
        channel: Channel the message arrived on ("email" | "sms").
        cal_event_id: Cal.com booking UID when triggered by BOOKING_CREATED.

    Returns:
        Final graph state dict with reply_text, hs_contact_id, segment, etc.
    """
    graph = get_graph()
    prospect_id = prospect_dict.get("prospect_id", "unknown")

    initial_state: AgentState = {
        "messages": [HumanMessage(content=inbound_text)],
        "prospect": prospect_dict,
        "hiring_signal_brief": None,
        "competitor_gap_brief": None,
        "segment": None,
        "bench_mismatch": False,
        "hs_contact_id": None,
        "inbound_text": inbound_text,
        "channel": channel,
        "command": None,
        "reply_text": None,
        "destination": None,
        "cal_event_id": cal_event_id,
        "error": None,
        "policy_decision": None,
        "mechanism_metadata": None,
        "icp_result": None,
    }

    config = {"configurable": {"thread_id": prospect_id}}
    final_state = await graph.ainvoke(initial_state, config=config)
    return final_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dict_to_prospect(d: dict) -> Any:
    """Reconstruct a Prospect dataclass from a dict.

    Args:
        d: Dict representation of a Prospect.

    Returns:
        Prospect dataclass instance.
    """
    from config.models import Prospect, ProspectState, Segment

    p = Prospect(
        prospect_id=d.get("prospect_id", "unknown"),
        company_id=d.get("company_id", "unknown"),
        contact_name=d.get("contact_name", ""),
        email=d.get("email", ""),
        phone=d.get("phone"),
        timezone=d.get("timezone", "UTC"),
        preferred_channel=d.get("preferred_channel", "email"),
        current_state=ProspectState(d.get("current_state", "cold")),
        outbound_attempt_count=d.get("outbound_attempt_count", 0),
        segment=Segment(d.get("segment")) if d.get("segment") else None,
        hiring_signal_brief_ref=d.get("hiring_signal_brief_ref"),
    )
    if d.get("company_name"):
        object.__setattr__(p, "company_name", d.get("company_name"))
    return p


_SEGMENT_PITCH_LANGUAGE: dict[str, str] = {
    # Official pitch language per icp_definition.md
    "s1": (
        "This is a Series A/B company in growth mode. Lead with speed to capability: "
        "'We help early-stage engineering teams scale their AI function faster than direct hire.' "
        "Reference their specific open roles and funding round to ground the pitch. "
        "Subject lines: 'Context: [Company] engineering velocity' or 'Question: scaling your AI team'."
    ),
    "segment_1": (  # legacy key
        "This is a Series A/B company in growth mode. Lead with speed to capability: "
        "'We help early-stage engineering teams scale their AI function faster than direct hire.' "
        "Reference their specific open roles and funding round to ground the pitch."
    ),
    "segment_1_series_a_b": (
        "This is a Series A/B company in growth mode. Lead with speed to capability: "
        "'We help early-stage engineering teams scale their AI function faster than direct hire.' "
        "Reference their specific open roles and funding round to ground the pitch. "
        "Subject lines: 'Context: [Company] engineering velocity' or 'Question: scaling your AI team'."
    ),
    "s2": (
        "This is a mid-market company post-restructure. Lead with cost-efficiency and flexibility: "
        "'We embed senior engineers on a month-to-month basis — no severance risk, no headcount approval.' "
        "Acknowledge the restructure with care; do not imply their team is inadequate. "
        "Subject lines: 'Context: engineering capacity after [Company] restructure'."
    ),
    "segment_2": (
        "This is a mid-market company post-restructure. Lead with cost-efficiency and flexibility. "
        "Acknowledge the restructure with care; do not imply their team is inadequate."
    ),
    "segment_2_mid_market_restructure": (
        "This is a mid-market company post-restructure. Lead with cost-efficiency and flexibility: "
        "'We embed senior engineers on a month-to-month basis — no severance risk, no headcount approval.' "
        "Acknowledge the restructure with care; do not imply their team is inadequate. "
        "Subject lines: 'Context: engineering capacity after [Company] restructure'."
    ),
    "s3": (
        "This is a leadership-transition prospect. Lead with the new CTO/VP Eng's mandate: "
        "'New engineering leaders often need to move fast in the first 90 days. "
        "We give them a force-multiplier without waiting for headcount approvals.' "
        "Reference the specific leadership change found in the brief. "
        "Subject lines: 'Request: 15 minutes with [New Leader Name]'."
    ),
    "segment_3": (
        "This is a leadership-transition prospect. Lead with the new CTO/VP Eng's mandate. "
        "Reference the specific leadership change found in the brief."
    ),
    "segment_3_leadership_transition": (
        "This is a leadership-transition prospect. Lead with the new CTO/VP Eng's mandate: "
        "'New engineering leaders often need to move fast in the first 90 days. "
        "We give them a force-multiplier without waiting for headcount approvals.' "
        "Reference the specific leadership change found in the brief. "
        "Subject lines: 'Request: 15 minutes with [New Leader Name]'."
    ),
    "s4": (
        "This is a specialized-capability gap prospect. Lead with the specific AI/ML gap identified: "
        "reference the competitor gap finding and what peers in their sector are already doing. "
        "Frame as a research finding, not a critique: "
        "'Our sector scan shows X% of comparable companies now have a dedicated [capability] function.' "
        "Only reference gaps with medium or high confidence. "
        "Subject lines: 'Context: [Company] AI capability vs. sector peers'."
    ),
    "segment_4": (
        "This is a specialized-capability gap prospect. Lead with the specific AI/ML gap identified. "
        "Only reference gaps with medium or high confidence."
    ),
    "segment_4_specialized_capability": (
        "This is a specialized-capability gap prospect. Lead with the specific AI/ML gap identified: "
        "reference the competitor gap finding and what peers in their sector are already doing. "
        "Frame as a research finding, not a critique. "
        "Only reference gaps with medium or high confidence. "
        "Subject lines: 'Context: [Company] AI capability vs. sector peers'."
    ),
}

_SIGNATURE_TEMPLATE = """
[First name]
[Title, e.g., Research Partner]
Tenacious Intelligence Corporation
gettenacious.com
""".strip()


def _build_system_prompt(state: AgentState) -> str:
    """Build the LLM system prompt from briefs, style guide, and honesty rules.

    Args:
        state: Current graph state.

    Returns:
        System prompt string.
    """
    brief = state.get("hiring_signal_brief") or {}
    gap = state.get("competitor_gap_brief") or {}
    segment = state.get("segment") or "unqualified"
    bench_mismatch = state.get("bench_mismatch", False)

    # Segment-specific pitch guidance from icp_definition.md
    pitch_guidance = _SEGMENT_PITCH_LANGUAGE.get(
        segment,
        "No specific segment matched. Send a brief exploratory email without strong claims.",
    )

    # Honesty flags from brief — agent must respect these
    honesty_flags = brief.get("honesty_flags", [])
    flags_block = ""
    if honesty_flags:
        flags_block = (
            "\n## Honesty Flags (from enrichment pipeline — MUST respect)\n"
            + "\n".join(f"- {f}" for f in honesty_flags)
            + "\n"
        )

    # Load style guide
    style_guide_path = os.path.join(os.path.dirname(__file__), "style_guide.md")
    try:
        with open(style_guide_path) as f:
            style_guide = f.read()
    except FileNotFoundError:
        style_guide = "Be professional, direct, and grounded in data."

    # Derive hiring velocity language from official fields (open_roles_today preferred)
    hv = brief.get("hiring_velocity") or {}
    open_roles_today = hv.get("open_roles_today", brief.get("job_post_count"))
    velocity_label = hv.get("velocity_label", "insufficient_signal")
    hv_confidence = hv.get("signal_confidence", 0.0)

    prompt = f"""You are a sales development agent for Tenacious Consulting and Outsourcing.
Your job is to qualify prospects and book discovery calls.

## Style Guide
{style_guide}

## Signature (use verbatim in every outreach email)
{_SIGNATURE_TEMPLATE}

## Prospect Context
- ICP Segment: {segment}
- Bench mismatch: {bench_mismatch} (if True, do NOT commit to specific staffing capacity)

## Segment-Specific Pitch Guidance
{pitch_guidance}
{flags_block}
## Hiring Signal Brief
{json.dumps(brief, indent=2) if brief else "Not yet enriched."}

## Competitor Gap Brief
{json.dumps(gap, indent=2) if gap else "Not available."}

## Honesty Rules (CRITICAL — violations are grading disqualifiers)
- Only assert claims when the brief shows confidence: "medium" or "high"
- For confidence: "low" or null — use interrogative phrasing ("we noticed signals suggesting...")
- Hiring velocity: use `hiring_velocity.velocity_label` to determine phrasing:
  - "tripled_or_more" or "doubled" → assertive: "Your engineering team has grown significantly"
  - "increased_modestly" → attribution: "Signals suggest modest hiring growth"
  - "flat" or "declined" → omit or ask
  - "insufficient_signal" → ask rather than assert; never invent a velocity claim
- Never reference competitor gaps unless gap confidence is "medium" or "high"
- Never commit to staffing capacity if bench_mismatch is True
- Segment 4 (specialized capability): only pitch if the prospect's ai_maturity_score >= 2

## HubSpot Tools
You have access to 9 HubSpot tools. Use them to:
1. Look up the contact (hubspot_get_contact) — do this first
2. Upsert the contact with enrichment data (hubspot_upsert_contact) — use the contact_id from step 1
3. Create a deal when the prospect qualifies (hubspot_create_deal)

IMPORTANT:
- Do NOT call hubspot_log_activity or hubspot_write_brief — these are handled automatically.
- Only call tools when you have a valid contact_id from hubspot_get_contact or hubspot_upsert_contact.
- Do NOT include tool schemas, JSON, or <tools> tags in your text reply to the prospect.
- Your reply to the prospect must be plain conversational text only.
- All outbound content is marked draft=true automatically.
- Cold email body: max 120 words. Warm follow-up: max 200 words. One CTA per message.
- Subject line must start with: Request: / Follow-up: / Context: / Question:
- Never use: "I hope this finds you well", "Just following up", "Circling back",
  "top talent", "world-class", "rockstar", "ninja", "bench" (in prospect-facing copy).
"""
    return prompt
