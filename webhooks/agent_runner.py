import logging
import uuid
from typing import Any
from orchestrators.conversation_orchestrator import ConversationOrchestrator

logger = logging.getLogger(__name__)

async def run_agent(
    prospect_dict: dict[str, Any],
    inbound_text: str,
    channel: str = "email",
    cal_event_id: str | None = None,
) -> dict[str, Any] | None:
    """
    Public entry point to trigger the specialized Conversation Orchestrator.
    This routes the message through the 3-stage mechanism and Policy Gate.
    """
    orchestrator = ConversationOrchestrator()
    
    # Construct the event object the orchestrator expects
    event = {
        "event_id": f"evt_{uuid.uuid4().hex[:8]}",
        "lead_id": prospect_dict.get("prospect_id"),
        "channel": channel,
        "body": inbound_text,
        "event_type": "inbound_message" if inbound_text else "outbound_seed",
        "prospect": prospect_dict,
        # Briefs are usually inside the prospect_dict if pre-enriched
        "briefs": {
            "hiring_signal_brief": prospect_dict.get("hiring_signal_brief"),
            "competitor_gap_brief": prospect_dict.get("competitor_gap_brief"),
        }
    }
    
    try:
        result = await orchestrator.handle_event(event)
        logger.info(
            "Orchestrator completed: prospect_id=%s intent=%s policy_allowed=%s",
            prospect_dict.get("prospect_id"),
            result.get("intent"),
            (result.get("policy_decision") or {}).get("allowed")
        )
        return result
    except Exception as exc:
        logger.error("Orchestrator failed for prospect_id=%s: %s", prospect_dict.get("prospect_id"), exc)
        return None
