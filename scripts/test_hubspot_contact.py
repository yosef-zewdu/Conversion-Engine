"""
Test HubSpot Contacts API by creating a test contact.

Usage:
    python scripts/test_hubspot_contact.py

Requires HUBSPOT_ACCESS_TOKEN in .env
"""

import sys
import httpx
from dotenv import load_dotenv
import os

load_dotenv()

TOKEN = os.getenv("HUBSPOT_ACCESS_TOKEN", "").strip()

if not TOKEN:
    print("ERROR: HUBSPOT_ACCESS_TOKEN is not set in .env")
    print("Follow scripts/setup_hubspot_mcp.md to create a Private App and copy the token.")
    sys.exit(1)

url = "https://api.hubapi.com/crm/v3/objects/contacts"
headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}
payload = {
    "properties": {
        "email": "test@tenacious-sandbox.dev",
        "firstname": "Test",
        "lastname": "Contact",
    }
}

try:
    response = httpx.post(url, headers=headers, json=payload, timeout=10)
    response.raise_for_status()
    data = response.json()
    contact_id = data.get("id")
    hs_object_id = data.get("properties", {}).get("hs_object_id")
    print(f"Contact created successfully.")
    print(f"  id:           {contact_id}")
    print(f"  hs_object_id: {hs_object_id}")
except httpx.HTTPStatusError as e:
    print(f"ERROR: HubSpot API returned {e.response.status_code}")
    try:
        detail = e.response.json()
        print(f"  message: {detail.get('message', e.response.text)}")
    except Exception:
        print(f"  body: {e.response.text}")
    sys.exit(1)
except httpx.RequestError as e:
    print(f"ERROR: Network error — {e}")
    sys.exit(1)
