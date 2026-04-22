"""
One-shot script to verify Resend connectivity.
Sends a single test email and prints the message ID.

Usage:
    RESEND_TO=you@example.com python scripts/test_resend_email.py

Requires RESEND_API_KEY in .env
"""
import os
import sys
import httpx
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ.get("RESEND_API_KEY", "")
if not API_KEY:
    print("✗ RESEND_API_KEY is not set in .env")
    sys.exit(1)

TO = os.environ.get("RESEND_TO", "")
if not TO:
    print("✗ Set RESEND_TO=your@email.com before running this script")
    sys.exit(1)

payload = {
    "from": "Conversion Engine <onboarding@resend.dev>",
    "to": [TO],
    "subject": "Conversion Engine — Resend connectivity check",
    "html": (
        "<p>This is a test email from the <strong>Conversion Engine</strong> "
        "(task 0.5 verification).</p>"
        "<p>If you received this, Resend is wired up correctly.</p>"
    ),
}

resp = httpx.post(
    "https://api.resend.com/emails",
    headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
    json=payload,
    timeout=15,
)

if resp.status_code == 200:
    data = resp.json()
    print("✓ Email sent successfully")
    print(f"  Message ID : {data.get('id')}")
    print(f"  To         : {TO}")
else:
    print(f"✗ Resend API error {resp.status_code}: {resp.text}")
    sys.exit(1)
