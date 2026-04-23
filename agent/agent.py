"""
ConversationAgent — public entry point for the LangGraph agent.

This module is the single import the webhook handler uses.
All logic lives in graph.py; this file is the stable public API.

Usage::

    from agent.agent import ConversationAgent

    agent = ConversationAgent()
    result = await agent.handle(prospect_dict, inbound_text, channel="email")
"""
from __future__ import annotations

import logging
from typing import Any

from agent.graph import handle_inbound

logger = logging.getLogger(__name__)


class ConversationAgent:
    """Thin wrapper around the LangGraph conversation graph.

    Provides a stable public API for the webhook handler and tests.
    State is persisted per prospect_id via LangGraph's MemorySaver.
    """

    async def handle(
        self,
        prospect_dict: dict[str, Any],
        inbound_text: str,
        channel: str = "email",
        cal_event_id: str | None = None,
    ) -> dict[str, Any]:
        """Handle one inbound message turn for a prospect.

        Args:
            prospect_dict: Prospect as dict — must include prospect_id and email.
            inbound_text: Raw text of the inbound message.
            channel: Channel the message arrived on ("email" | "sms").
            cal_event_id: Cal.com booking UID when triggered by BOOKING_CREATED.

        Returns:
            Final graph state with reply_text, hs_contact_id, segment, etc.
        """
        logger.info(
            "ConversationAgent.handle: prospect_id=%s channel=%s cal_event_id=%s",
            prospect_dict.get("prospect_id"),
            channel,
            cal_event_id,
        )
        return await handle_inbound(prospect_dict, inbound_text, channel, cal_event_id)
