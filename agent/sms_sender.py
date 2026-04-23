"""
Africa's Talking SMS sender — used by the agent graph for outbound SMS dispatch.

Reads AT_API_KEY and AT_USERNAME from environment. Falls back to sandbox
endpoint unless AT_SANDBOX=false is set.
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

_SANDBOX_URL = "https://api.sandbox.africastalking.com/version1/messaging"
_PROD_URL = "https://api.africastalking.com/version1/messaging"


def _endpoint() -> str:
    use_sandbox = os.environ.get("AT_SANDBOX", "true").strip().lower()
    return _SANDBOX_URL if use_sandbox != "false" else _PROD_URL


async def send_sms(phone: str, message: str) -> None:
    """Send an outbound SMS via Africa's Talking.

    Args:
        phone: Recipient phone number in E.164 format (e.g. +251960039108).
        message: Message body (max 160 chars for a single SMS segment).

    Raises:
        RuntimeError: If AT_API_KEY is not set.
        httpx.HTTPStatusError: If the API returns a non-2xx status.
    """
    api_key = os.environ.get("AT_API_KEY", "").strip()
    username = os.environ.get("AT_USERNAME", "sandbox").strip()

    if not api_key:
        raise RuntimeError(
            "AT_API_KEY is not set — cannot send SMS via Africa's Talking."
        )

    url = _endpoint()
    headers = {
        "apiKey": api_key,
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "username": username,
        "to": phone,
        "message": message,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, headers=headers, data=data)

    if resp.status_code not in (200, 201):
        logger.error(
            "SMS send failed: status=%d body=%s phone=%s",
            resp.status_code, resp.text[:200], phone,
        )
        resp.raise_for_status()

    result = resp.json()
    recipients = result.get("SMSMessageData", {}).get("Recipients", [])
    if recipients:
        r = recipients[0]
        logger.info(
            "SMS sent: phone=%s status=%s messageId=%s cost=%s",
            r.get("number"), r.get("status"), r.get("messageId"), r.get("cost"),
        )
    else:
        logger.warning("SMS send: no recipient data in response: %s", result)
