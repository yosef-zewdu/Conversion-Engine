"""
OutboundMessage dataclass for the Nurture Sequencer.

Every message dispatched by the Nurture Sequencer carries this metadata.
``draft`` is ALWAYS ``True`` — this is the object that Req 1.4 and Property 2
test against.

Requirements: 1.4
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OutboundMessage:
    """
    A single outbound message produced by the Nurture Sequencer.

    Args:
        prospect_id: UUID of the prospect this message targets.
        channel: Delivery channel — one of ``"email"``, ``"sms"``, or ``"voice"``.
        content: Body text of the message.
        subject: Email subject line; ``None`` for SMS/voice.
        draft: ALWAYS ``True``.  Never set to ``False``.
        sent_at: ISO 8601 datetime string representing when the message was composed.
        metadata: Arbitrary additional metadata dict.
    """

    prospect_id: str
    channel: str  # "email" | "sms" | "voice"
    content: str
    subject: Optional[str]
    draft: bool  # ALWAYS True — never set to False
    sent_at: str  # ISO 8601 datetime
    metadata: dict = field(default_factory=dict)
