import asyncio
import json
import logging
import os
from pathlib import Path
from agent.agent import ConversationAgent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dispatch_outreach")

async def dispatch():
    queue_path = Path("runs/outreach_queue.jsonl")
    if not queue_path.exists():
        logger.error("No outreach queue found at %s", queue_path)
        return

    agent = ConversationAgent()
    
    # Read the queue
    with open(queue_path, "r") as f:
        prospects = [json.loads(line) for line in f if line.strip()]

    if not prospects:
        logger.info("Outreach queue is empty.")
        return

    logger.info("Processing %d prospects from queue...", len(prospects))

    for p in prospects:
        email = p.get("email")
        company = p.get("company_name")
        logger.info("Starting outreach for %s (%s)", company, email)
        
        try:
            # Trigger the ConversationAgent with an empty inbound
            # This will trigger the 'First Outreach' logic in the graph
            result = await agent.handle(
                prospect_dict=p,
                inbound_text="",  # Empty triggers outbound start
                channel=p.get("preferred_channel", "email")
            )
            
            logger.info("Outreach draft generated for %s: %s", company, result.get("segment"))
            if result.get("reply_text"):
                logger.info("Reply text preview: %s...", result.get("reply_text")[:100])
            
        except Exception as e:
            logger.error("Failed to seed outreach for %s: %s", company, e)

if __name__ == "__main__":
    asyncio.run(dispatch())
