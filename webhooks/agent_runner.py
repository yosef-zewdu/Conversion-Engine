import logging
from typing import Any
from agent.agent import ConversationAgent

logger = logging.getLogger(__name__)

# Module-level agent singleton
_agent = ConversationAgent()

async def run_agent(
    prospect: dict[str, Any],
    inbound_text: str,
    channel: str,
    cal_event_id: str | None = None,
) -> dict[str, Any] | None:
    try:
        result = await _agent.handle(prospect, inbound_text, channel, cal_event_id)
        logger.info(
            "Agent completed: prospect_id=%s segment=%s destination=%s hs_id=%s",
            prospect.get("prospect_id"),
            result.get("segment"),
            result.get("destination"),
            result.get("hs_contact_id"),
        )
        return result
    except Exception as exc:
        logger.error("Agent failed for prospect_id=%s: %s", prospect.get("prospect_id"), exc)
        return None
