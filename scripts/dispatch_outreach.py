import asyncio
import json
import logging
import os
from pathlib import Path
from agent.agent import ConversationAgent

from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dispatch_outreach")

# Load environment variables for Langfuse/OpenRouter/HubSpot
load_dotenv()

from sqlalchemy import select
from db.session import AsyncSessionLocal
from db.models import Lead, Event

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dispatch_outreach")

# Load environment variables for Langfuse/OpenRouter/HubSpot
load_dotenv()

async def dispatch():
    async with AsyncSessionLocal() as db:
        # 1. Fetch leads in 'cold' state
        result = await db.execute(
            select(Lead).where(Lead.current_state == "cold")
        )
        leads = result.scalars().all()

        if not leads:
            logger.info("No cold leads found in the database. Queue is empty.")
            return

        logger.info("Processing %d cold leads from the database...", len(leads))


        for lead in leads:
            logger.info("Starting outreach for %s (%s)", lead.company_name, lead.email)
            
            try:
                # Convert Lead ORM model to the prospect dict the agent expects
                from webhooks.utils import lead_to_prospect_dict
                prospect_dict = lead_to_prospect_dict(lead)

                # Trigger the ConversationAgent via the run_agent bridge
                from webhooks.agent_runner import run_agent
                result = await run_agent(
                    prospect_dict=prospect_dict,
                    inbound_text="",  # Empty triggers outbound start
                    channel=lead.preferred_channel or "email"
                )
                
                if result:
                    logger.info("Outreach draft generated for %s: %s", lead.company_name, result.get("intent"))
                    
                    # 2. Update lead state to avoid duplicate dispatch
                    lead.current_state = "contacted"
                
                # Log an event
                outbound_event = Event(
                    lead_id=lead.id,
                    event_type="outbound_dispatched",
                    payload={"channel": lead.preferred_channel}
                )
                db.add(outbound_event)
                
            except Exception as e:
                logger.error("Failed to seed outreach for %s: %s", lead.company_name, e)

        # Commit all state updates
        await db.commit()
        logger.info("Finished processing all leads.")

if __name__ == "__main__":
    asyncio.run(dispatch())
