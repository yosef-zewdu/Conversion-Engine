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
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Classification failed: %s", exc)
        return {"segment": "unqualified", "bench_mismatch": False}


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

    # Emit Langfuse trace for this LLM call (Req 11.1)
    lf_trace = None
    try:
        from langfuse import Langfuse
        lf = Langfuse(
            public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
            secret_key=os.environ.get("LANGFUSE_SECRET_KEY", ""),
            host=os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
        )
        lf_trace = lf.trace(
            name="llm_call",
            input={"messages_count": len(messages)},
            metadata={"model": os.environ.get("OPENROUTER_MODEL", ""), "node": "node_llm"},
        )
    except Exception:
        pass

    try:
        import time
        t0 = time.monotonic()
        response = await llm.ainvoke(messages)
        latency_ms = int((time.monotonic() - t0) * 1000)

        usage = getattr(response, "usage_metadata", None) or {}
        prompt_tokens = getattr(usage, "input_tokens", None) or (usage.get("input_tokens") if isinstance(usage, dict) else 0) or 0
        completion_tokens = getattr(usage, "output_tokens", None) or (usage.get("output_tokens") if isinstance(usage, dict) else 0) or 0
        model = os.environ.get("OPENROUTER_MODEL", "_default")
        guard.record(prompt_tokens, completion_tokens, model)

        # Update Langfuse trace with output
        if lf_trace:
            try:
                lf_trace.update(
                    output={"content_len": len(str(response.content)), "latency_ms": latency_ms},
                    metadata={"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
                )
                lf.flush()
            except Exception:
                pass

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
                resend.Emails.send({
                    "from": os.environ.get("RESEND_FROM", "onboarding@resend.dev"),
                    "to": [sink_email],
                    "subject": "Re: Your inquiry — Tenacious Consulting",
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
    try:
        resend.api_key = os.environ.get("RESEND_API_KEY", "")
        resend.Emails.send({
            "from": os.environ.get("RESEND_FROM", "onboarding@resend.dev"),
            "to": [to_email],
            "subject": "Re: Your inquiry — Tenacious Consulting",
            "text": reply_text,
            "tags": [{"name": "prospect_id", "value": prospect_dict.get("prospect_id", "")}],
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
# Routing functions
# ---------------------------------------------------------------------------

def route_after_command(state: AgentState) -> Literal["llm", "kill_switch"]:
    """Route to kill_switch if a command was detected, else to llm.

    Args:
        state: Current graph state.

    Returns:
        Next node name.
    """
    if state.get("command"):
        return "kill_switch"
    return "llm"


def route_after_llm(state: AgentState) -> Literal["tools", "kill_switch"]:
    """Route to tools if LLM made tool calls, else to kill_switch.

    Args:
        state: Current graph state.

    Returns:
        Next node name.
    """
    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        return "tools"
    return "kill_switch"


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
    builder.add_node("llm", node_llm)
    builder.add_node("tools", node_tools)
    builder.add_node("kill_switch", node_kill_switch)
    builder.add_node("send_email", node_send_email)
    builder.add_node("persist", node_persist)

    # Edges
    builder.add_edge(START, "enrich")
    builder.add_edge("enrich", "classify")
    builder.add_edge("classify", "check_command")
    builder.add_conditional_edges("check_command", route_after_command)
    builder.add_conditional_edges("llm", route_after_llm)
    builder.add_edge("tools", "llm")          # tool results loop back to LLM
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

    return Prospect(
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

    # Load style guide
    style_guide_path = os.path.join(os.path.dirname(__file__), "style_guide.md")
    try:
        with open(style_guide_path) as f:
            style_guide = f.read()
    except FileNotFoundError:
        style_guide = "Be professional, direct, and grounded in data."

    prompt = f"""You are a sales development agent for Tenacious Consulting and Outsourcing.
Your job is to qualify prospects and book discovery calls.

## Style Guide
{style_guide}

## Prospect Context
- ICP Segment: {segment}
- Bench mismatch: {bench_mismatch} (if True, do NOT commit to specific staffing capacity)

## Hiring Signal Brief
{json.dumps(brief, indent=2) if brief else "Not yet enriched."}

## Competitor Gap Brief
{json.dumps(gap, indent=2) if gap else "Not available."}

## Honesty Rules (CRITICAL)
- Only assert claims when the brief shows confidence: "medium" or "high"
- For confidence: "low" or null — use interrogative phrasing ("we noticed signals suggesting...")
- Never claim "aggressive hiring" unless job_post_count >= 5 AND job_post_velocity_60d >= 3.0
- Never reference competitor gaps unless gap confidence is "medium" or "high"
- Never commit to staffing capacity if bench_mismatch is True

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
"""
    return prompt
