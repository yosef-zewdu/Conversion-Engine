"""
Create custom contact properties in HubSpot required by the Conversion Engine.

HubSpot silently ignores unknown properties on upsert — you must create them
first. Run this once against your sandbox before running the demo.

Usage:
    uv run python -m scripts.setup_hubspot_properties

Requires HUBSPOT_ACCESS_TOKEN in .env
"""
from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("HUBSPOT_ACCESS_TOKEN", "").strip()
if not TOKEN:
    print("ERROR: HUBSPOT_ACCESS_TOKEN not set in .env")
    sys.exit(1)

BASE = "https://api.hubapi.com"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}

# Custom properties to create on the Contact object
PROPERTIES = [
    {
        "name": "prospect_id",
        "label": "Prospect ID",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
        "description": "Conversion Engine internal prospect UUID",
    },
    {
        "name": "company_id",
        "label": "Company ID",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
        "description": "Crunchbase company_id",
    },
    {
        "name": "crunchbase_id",
        "label": "Crunchbase ID",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
        "description": "Crunchbase ODM record ID",
    },
    {
        "name": "icp_segment",
        "label": "ICP Segment",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
        "description": "Assigned ICP segment: segment_1/2/3/4 or unqualified",
    },
    {
        "name": "prospect_state",
        "label": "Prospect State",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
        "description": "FSM state: cold/contacted/replied/warm/booking/dormant/opted_out",
    },
    {
        "name": "last_enriched_at",
        "label": "Last Enriched At",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
        "description": "ISO 8601 timestamp of last Signal Pipeline enrichment",
    },
    {
        "name": "ai_maturity_score",
        "label": "AI Maturity Score",
        "type": "number",
        "fieldType": "number",
        "groupName": "contactinformation",
        "description": "AI maturity score 0-3 from Signal Pipeline",
    },
    {
        "name": "bench_mismatch",
        "label": "Bench Mismatch",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "contactinformation",
        "description": "True when no bench engineers match the prospect stack",
        "options": [
            {"label": "True", "value": "true", "displayOrder": 0, "hidden": False},
            {"label": "False", "value": "false", "displayOrder": 1, "hidden": False},
        ],
    },
]


def create_property(prop: dict) -> None:
    url = f"{BASE}/crm/v3/properties/contacts"
    resp = httpx.post(url, headers=HEADERS, json=prop, timeout=10)
    if resp.status_code == 409:
        print(f"  [exists]  {prop['name']}")
    elif resp.status_code in (200, 201):
        print(f"  [created] {prop['name']}")
    else:
        print(f"  [ERROR {resp.status_code}] {prop['name']}: {resp.text[:120]}")


def main() -> None:
    print(f"Creating {len(PROPERTIES)} custom contact properties in HubSpot...\n")
    for prop in PROPERTIES:
        create_property(prop)
    print("\nDone. Re-run the demo script to see fields populated on the contact.")


if __name__ == "__main__":
    main()
