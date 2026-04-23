"""
Send a test SMS via Africa's Talking sandbox to verify connectivity.

Usage:
    uv run python -m scripts.test_sms --phone +251960039108

The sandbox doesn't deliver to real phones — it shows in the AT simulator at:
  https://simulator.africastalking.com/

Requires AT_API_KEY and AT_USERNAME in .env
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

AT_API_KEY = os.environ.get("AT_API_KEY", "").strip()
AT_USERNAME = os.environ.get("AT_USERNAME", "sandbox").strip()

if not AT_API_KEY:
    print("ERROR: AT_API_KEY not set in .env")
    sys.exit(1)


def send_sms(phone: str, message: str) -> None:
    """Send an SMS via Africa's Talking sandbox API."""
    url = "https://api.sandbox.africastalking.com/version1/messaging"
    headers = {
        "apiKey": AT_API_KEY,
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "username": AT_USERNAME,
        "to": phone,
        "message": message,
    }

    resp = httpx.post(url, headers=headers, data=data, timeout=15)

    if resp.status_code == 201:
        result = resp.json()
        recipients = result.get("SMSMessageData", {}).get("Recipients", [])
        if recipients:
            r = recipients[0]
            print(f"SMS queued successfully")
            print(f"  number    : {r.get('number')}")
            print(f"  status    : {r.get('status')}")
            print(f"  messageId : {r.get('messageId')}")
            print(f"  cost      : {r.get('cost')}")
            print()
            print("View it in the AT simulator:")
            print("  https://simulator.africastalking.com/")
            print("  (log in with your sandbox credentials, check the SMS inbox)")
        else:
            print(f"Sent but no recipient data: {result}")
    else:
        print(f"ERROR {resp.status_code}: {resp.text}")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Test Africa's Talking SMS sandbox")
    parser.add_argument(
        "--phone",
        default=os.environ.get("STAFF_SINK_PHONE", "").strip(),
        help="Phone number in E.164 format e.g. +251900009198",
    )
    parser.add_argument(
        "--message",
        default=(
            "[Conversion Engine demo] Hi, this is a test SMS from the "
            "Tenacious outreach system. Segment: segment_1. Draft: true."
        ),
    )
    args = parser.parse_args()

    if not args.phone:
        print("ERROR: provide --phone or set STAFF_SINK_PHONE in .env")
        sys.exit(1)

    print(f"\nSending SMS via Africa's Talking sandbox...")
    print(f"  to      : {args.phone}")
    print(f"  message : {args.message[:60]}...")
    print()
    send_sms(args.phone, args.message)


if __name__ == "__main__":
    main()
