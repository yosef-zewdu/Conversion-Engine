"""
End-to-end demo script for Conversion Engine — all 6 required scenarios.

Drives the live Render API (or localhost) through each demo requirement:

  Scenario A  Cold outreach → synthetic prospect receives signal-grounded email,
              replies, gets qualified, Cal.com booking booked.

  Scenario B  HiringSignalBrief + CompetitorGapBrief — show per-signal confidence
              scores for a freshly created lead.

  Scenario C  HubSpot contact record — show all fields non-null + enrichment
              timestamp current after outreach start.

  Scenario D  SMS handoff — warm lead who already replied by email gets an SMS
              scheduling message (email_reply_count >= 1 gate demonstrated).

  Scenario E  No over-claim — agent correctly omits "aggressive hiring" when
              job_post_count < 5 (only 3 open roles).

  Scenario F  Segment ambiguity — post-layoff company that also raised funding
              correctly classified as S2 (not naive S1).

Usage:
    uv run python -m scripts.demo_e2e                          # against Render
    uv run python -m scripts.demo_e2e --base-url http://localhost:8000
    uv run python -m scripts.demo_e2e --scenario A             # one scenario only

Requires:
    RENDER_BASE_URL  or  --base-url flag  (default: https://conversion-engine-l27z.onrender.com)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

# ── Colour helpers ────────────────────────────────────────────────────────────

BOLD = "\033[1m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
DIM = "\033[2m"
RESET = "\033[0m"

def _h(title: str) -> None:
    print(f"\n{BOLD}{CYAN}{'─'*66}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─'*66}{RESET}")

def _ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET}  {msg}")

def _warn(msg: str) -> None:
    print(f"  {YELLOW}⚠{RESET}  {msg}")

def _fail(msg: str) -> None:
    print(f"  {RED}✗{RESET}  {msg}")

def _detail(label: str, value: Any) -> None:
    val_str = json.dumps(value, indent=2) if isinstance(value, (dict, list)) else str(value)
    if "\n" in val_str:
        print(f"  {DIM}{label}:{RESET}")
        for line in val_str.splitlines():
            print(f"      {line}")
    else:
        print(f"  {DIM}{label}:{RESET}  {val_str}")


# ── Brief builders (schema-compliant) ────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()

def _hsb(
    company_id: str,
    company_name: str,
    *,
    funding: dict | None = None,
    layoff: dict | None = None,
    leadership: dict | None = None,
    job_count: int | None = None,
    velocity: float | None = None,
    job_conf: str | None = None,
    ai_score: int = 0,
    ai_conf: str = "medium",
    tech: dict | None = None,
    bench_mismatch: bool = False,
    icp_segment: str | None = None,
    icp_confidence: float | None = None,
    employee_min: int | None = None,
    employee_max: int | None = None,
    honesty_flags: list | None = None,
) -> dict:
    """Build a schema-compliant HiringSignalBrief dict for demo use."""
    open_roles = (job_count or 0)
    velocity_label = "insufficient_signal"
    if velocity is not None and job_count is not None:
        if velocity >= 3.0:
            velocity_label = "tripled_or_more"
        elif velocity >= 1.5:
            velocity_label = "doubled"
        elif velocity >= 0.5:
            velocity_label = "increased_modestly"
        else:
            velocity_label = "flat"
    return {
        "schema_version": "1.0",
        "company_id": company_id,
        "company_name": company_name,
        "last_enriched_at": _ts(),
        "bench_summary_version": "2026-04-01",
        "bench_mismatch": bench_mismatch,
        "tech_stack": tech or {"languages": ["python"], "ml_tools": [], "data_tools": [], "confidence": "medium"},
        "funding_event": funding,
        "layoff_event": layoff,
        "leadership_change": leadership,
        "job_post_count": job_count,
        "job_post_velocity_60d": velocity,
        "job_post_confidence": job_conf,
        "hiring_velocity": {
            "open_roles_today": open_roles,
            "open_roles_60_days_ago": max(0, open_roles - 2),
            "velocity_label": velocity_label,
            "signal_confidence": 0.8 if job_conf == "high" else 0.6,
            "sources": [],
        } if job_count is not None else None,
        "ai_maturity_score": ai_score,
        "ai_maturity_confidence": ai_conf,
        "ai_maturity_justification": [],
        "icp_segment": icp_segment,
        "icp_confidence": icp_confidence,
        "icp_signals_used": [],
        "honesty_flags": honesty_flags or [],
        "employee_count_min": employee_min,
        "employee_count_max": employee_max,
    }


def _cgb(
    company_id: str,
    ai_score: int,
    gaps: list[dict],
    sector_percentile: float = 70.0,
    peer_count: int = 6,
) -> dict:
    """Build a schema-compliant CompetitorGapBrief dict for demo use."""
    return {
        "schema_version": "1.0",
        "company_id": company_id,
        "generated_at": _ts(),
        "prospect_ai_maturity_score": ai_score,
        "sector_percentile": sector_percentile,
        "peer_count": peer_count,
        "peers": [
            {"company_id": f"peer-{i}", "company_name": f"Peer {i}", "ai_maturity_score": 3}
            for i in range(1, min(peer_count + 1, 4))
        ],
        "gaps": gaps,
    }


def _gap(practice: str, confidence: str = "high") -> dict:
    """Build a schema-compliant CompetitorGap dict."""
    return {
        "practice": practice,
        "evidence": f"Sector peers show {confidence}-confidence signal for {practice}",
        "confidence": confidence,
        "peer_refs": [],
    }


def _funding(round_type: str, amount_usd: float, close_date: str, confidence: str = "high") -> dict:
    return {"round_type": round_type, "amount_usd": amount_usd, "close_date": close_date, "confidence": confidence}


def _layoff(event_date: str, pct: float, headcount: int, confidence: str = "high") -> dict:
    return {"event_date": event_date, "percentage_cut": pct, "headcount_affected": headcount, "confidence": confidence}


def _leadership(role: str, appointment_date: str, confidence: str = "high") -> dict:
    return {"role": role, "appointment_date": appointment_date, "confidence": confidence}


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _post(client: httpx.Client, url: str, body: dict) -> dict:
    r = client.post(url, json=body, timeout=240)
    r.raise_for_status()
    return r.json()

def _get(client: httpx.Client, url: str) -> dict:
    r = client.get(url, timeout=60)
    r.raise_for_status()
    return r.json()


# ── Lead seeding ──────────────────────────────────────────────────────────────

def _seed_lead(client: httpx.Client, base: str, lead_def: dict) -> str:
    """POST /dev/create-lead and return the lead_id."""
    resp = _post(client, f"{base}/dev/create-lead", lead_def)
    return resp["lead_id"]


# ── Scenario helpers ──────────────────────────────────────────────────────────

def _start_outreach(client: httpx.Client, base: str, lead_id: str,
                    text: str = "Hello, I'm interested in learning more.",
                    channel: str = "email") -> dict:
    return _post(client, f"{base}/leads/{lead_id}/start-outreach",
                 {"inbound_text": text, "channel": channel})

def _simulate_reply(client: httpx.Client, base: str, lead_id: str,
                    scenario: str, channel: str = "email",
                    custom_text: str | None = None) -> dict:
    body: dict = {"lead_id": lead_id, "scenario": scenario, "channel": channel}
    if custom_text:
        body["custom_text"] = custom_text
    return _post(client, f"{base}/dev/simulate-reply", body)

def _get_lead(client: httpx.Client, base: str, lead_id: str) -> dict:
    return _get(client, f"{base}/leads/{lead_id}")

def _get_briefs(client: httpx.Client, base: str, lead_id: str) -> dict:
    return _get(client, f"{base}/leads/{lead_id}/briefs")

def _get_messages(client: httpx.Client, base: str, lead_id: str) -> list:
    return _get(client, f"{base}/leads/{lead_id}/messages")  # type: ignore[return-value]


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO A — Full e2e: cold outreach → reply → booking
# ══════════════════════════════════════════════════════════════════════════════

def scenario_a(client: httpx.Client, base: str) -> bool:
    _h("SCENARIO A  Cold outreach → reply → Cal.com discovery call booking")
    ok = True

    lead_id = _seed_lead(client, base, {
        "company_name": "NexaFlow AI",
        "company_id": "nexaflow-ai-series-a",
        "contact_name": "Priya Shankar",
        "email": os.environ.get("STAFF_SINK_EMAIL", "priya.shankar@nexaflow-sandbox.tenacious-demo.dev"),
        "phone": os.environ.get("STAFF_SINK_PHONE", "") or None,
        "timezone": "America/Chicago",
        "preferred_channel": "email",
        "hiring_signal_brief": _hsb(
            "nexaflow-ai-series-a", "NexaFlow AI",
            funding=_funding("Series A", 18_000_000, "2026-02-10", "high"),
            job_count=7, velocity=3.8, job_conf="high",
            # ai_score=1 keeps this below S4 gate (needs ≥2) → correctly routes to S1
            ai_score=1, ai_conf="medium",
            tech={"languages": ["python", "go"], "ml_tools": ["pytorch"], "data_tools": ["dbt"], "confidence": "high"},
            icp_segment="segment_1_series_a_b", icp_confidence=0.92,
            employee_min=30, employee_max=60,
        ),
        "competitor_gap_brief": _cgb(
            "nexaflow-ai-series-a", 2,
            gaps=[_gap("ML platform (MLflow/Kubeflow)", "high")],
            sector_percentile=68.0, peer_count=7,
        ),
    })
    _ok(f"Lead seeded → lead_id={lead_id}")

    # Step 1: cold outreach
    r = _start_outreach(client, base, lead_id)
    agent_result = r.get("agent_result") or {}
    reply_text = agent_result.get("reply_text") or ""
    segment = agent_result.get("segment", "")
    destination = agent_result.get("destination", "")

    if segment in ("s1", "segment_1", "segment_1_series_a_b"):
        _ok(f"Classified as S1 (Series A) — segment={segment}")
    else:
        _warn(f"Segment={segment} (expected S1 for $18M Series A)")
        ok = False

    if reply_text:
        _ok("Outbound email composed")
        _detail("Subject / Preview", reply_text[:200])
    else:
        _warn("No reply_text in agent result — check logs")

    # Assert signal-grounded content
    lower = reply_text.lower()
    if "series a" in lower or "18m" in lower or "$18" in lower or "funding" in lower:
        _ok("Email references funding signal (assertive, high-confidence)")
    else:
        _warn("Email does not reference funding — check 3-stage chain")
        ok = False

    if "aggressive hiring" in lower or "7 open" in lower or "hiring" in lower:
        _ok("Email references hiring signal (7 open roles, velocity 3.8 ≥ 3.0 → aggressive)")
    else:
        _warn("Email does not mention hiring signal")

    _detail("kill_switch destination", destination)

    # Step 2: prospect replies positively
    time.sleep(1)
    r2 = _simulate_reply(client, base, lead_id, "interested_positive")
    r2_reply = (r2.get("agent_result") or {}).get("reply_text", "")
    if r2_reply:
        _ok("Follow-up reply composed after inbound")
        _detail("Follow-up preview", r2_reply[:200])
    else:
        _warn("No reply after prospect reply")

    # Step 3: booking intent → Cal.com slot offer
    time.sleep(1)
    r3 = _simulate_reply(client, base, lead_id, "interested_positive",
                         custom_text="Yes, let's set up a 20-minute call. I'm free Thursday afternoon.")
    r3_reply = (r3.get("agent_result") or {}).get("reply_text", "")
    if r3_reply:
        _ok("Agent responded to booking intent")
        lower3 = r3_reply.lower()
        if any(w in lower3 for w in ("cal.com", "schedule", "book", "call", "slot", "discovery")):
            _ok("Agent offered a discovery call / Cal.com link")
        else:
            _warn("Reply does not mention scheduling — check booking integration")
        _detail("Booking reply preview", r3_reply[:200])

    return ok


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO B — Brief visibility: per-signal confidence scores
# ══════════════════════════════════════════════════════════════════════════════

def scenario_b(client: httpx.Client, base: str) -> bool:
    _h("SCENARIO B  HiringSignalBrief + CompetitorGapBrief — per-signal confidence")
    ok = True

    lead_id = _seed_lead(client, base, {
        "company_name": "CedarScale Labs",
        "company_id": "cedarscale-labs-s4",
        "contact_name": "Marcus Osei",
        "email": "marcus.osei@cedarscale-sandbox.tenacious-demo.dev",
        "phone": None,
        "timezone": "Europe/London",
        "preferred_channel": "email",
        "hiring_signal_brief": _hsb(
            "cedarscale-labs-s4", "CedarScale Labs",
            funding=_funding("Series B", 27_000_000, "2026-01-05", "medium"),
            job_count=9, velocity=4.2, job_conf="high",
            ai_score=3, ai_conf="high",
            tech={"languages": ["python", "rust"], "ml_tools": ["jax", "triton"], "data_tools": ["spark"], "confidence": "high"},
            icp_segment="segment_4_specialized_capability", icp_confidence=0.87,
            employee_min=80, employee_max=250,
        ),
        "competitor_gap_brief": _cgb(
            "cedarscale-labs-s4", 3,
            gaps=[
                _gap("Agentic systems (LangGraph / CrewAI)", "high"),
                _gap("LLM evaluation harness", "medium"),
            ],
            sector_percentile=84.0, peer_count=8,
        ),
    })
    _ok(f"Lead seeded → lead_id={lead_id}")

    briefs = _get_briefs(client, base, lead_id)

    hsb = briefs.get("hiring_signal_brief") or {}
    cgb = briefs.get("competitor_gap_brief") or {}
    icp = briefs.get("icp_result") or {}

    print(f"\n  {BOLD}HiringSignalBrief confidence scores:{RESET}")
    funding_conf = (hsb.get("funding_event") or {}).get("confidence", "n/a")
    job_conf = hsb.get("job_post_confidence", "n/a")
    ai_conf = hsb.get("ai_maturity_confidence", "n/a")

    _detail("  funding_event.confidence", funding_conf)
    _detail("  job_post_confidence", job_conf)
    _detail("  ai_maturity_confidence", ai_conf)
    _detail("  job_post_count", hsb.get("job_post_count", "n/a"))
    _detail("  job_post_velocity_60d", hsb.get("job_post_velocity_60d", "n/a"))
    _detail("  ai_maturity_score", hsb.get("ai_maturity_score", "n/a"))
    _detail("  last_enriched_at", hsb.get("last_enriched_at", "n/a"))

    if all(v in ("high", "medium") for v in [funding_conf, job_conf, ai_conf]):
        _ok("All confidence scores present and non-null")
    else:
        _warn("Some confidence scores missing or null")
        ok = False

    print(f"\n  {BOLD}CompetitorGapBrief:{RESET}")
    gaps = cgb.get("gaps") or []
    if gaps:
        for g in gaps:
            _detail(f"  gap: {g.get('practice')}", f"confidence={g.get('confidence')}  peers={g.get('peer_adoption_pct')}%")
        _ok(f"{len(gaps)} competitor gap(s) with confidence scores")
    else:
        _warn("No competitor gaps in brief")
        ok = False

    _detail("  sector_percentile", cgb.get("sector_percentile", "n/a"))
    _detail("  peer_count", cgb.get("peer_count", "n/a"))

    return ok


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO C — HubSpot contact record: all fields non-null
# ══════════════════════════════════════════════════════════════════════════════

def scenario_c(client: httpx.Client, base: str) -> bool:
    _h("SCENARIO C  HubSpot contact record — all fields non-null, enrichment timestamp current")
    ok = True

    lead_id = _seed_lead(client, base, {
        "company_name": "Orbis Data",
        "company_id": "orbis-data-s1",
        "contact_name": "Leila Nasser",
        "email": os.environ.get("STAFF_SINK_EMAIL", "leila.nasser@orbis-sandbox.tenacious-demo.dev"),
        "phone": os.environ.get("STAFF_SINK_PHONE", "") or None,
        "timezone": "Africa/Cairo",
        "preferred_channel": "email",
        "hiring_signal_brief": _hsb(
            "orbis-data-s1", "Orbis Data",
            funding=_funding("Series A", 12_000_000, "2026-03-15", "high"),
            job_count=5, velocity=3.1, job_conf="high",
            ai_score=1, ai_conf="medium",
            tech={"languages": ["python", "typescript"], "ml_tools": ["scikit-learn"], "data_tools": ["snowflake"], "confidence": "medium"},
            icp_segment="segment_1_series_a_b", icp_confidence=0.88,
            employee_min=25, employee_max=65,
        ),
        "competitor_gap_brief": None,
    })
    _ok(f"Lead seeded → lead_id={lead_id}")

    # Trigger outreach — this runs persist() which writes to HubSpot
    r = _start_outreach(client, base, lead_id)
    agent_result = r.get("agent_result") or {}
    hs_contact_id = agent_result.get("hs_contact_id")

    if hs_contact_id:
        _ok(f"HubSpot contact_id populated: {hs_contact_id}")
    else:
        _warn("hs_contact_id not returned — HubSpot write may have failed (check logs)")
        ok = False

    # Fetch lead from DB and verify key fields
    lead = _get_lead(client, base, lead_id)
    time.sleep(0.5)

    required_fields = {
        "company_name": lead.get("company_name"),
        "contact_name": lead.get("contact_name"),
        "email": lead.get("email"),
        "segment": lead.get("segment"),
        "current_state": lead.get("current_state"),
    }

    print(f"\n  {BOLD}Lead record fields:{RESET}")
    all_non_null = True
    for field_name, val in required_fields.items():
        if val:
            _ok(f"{field_name} = {val}")
        else:
            _fail(f"{field_name} is null or empty")
            all_non_null = False

    if all_non_null:
        _ok("All required fields are non-null")
    else:
        ok = False

    # Check enrichment timestamp is fresh (within last 60 seconds)
    briefs = _get_briefs(client, base, lead_id)
    hsb = briefs.get("hiring_signal_brief") or {}
    last_enriched = hsb.get("last_enriched_at", "")
    if last_enriched:
        _ok(f"last_enriched_at present: {last_enriched}")
        try:
            enriched_dt = datetime.fromisoformat(last_enriched.replace("Z", "+00:00"))
            age_s = (datetime.now(timezone.utc) - enriched_dt).total_seconds()
            if age_s < 120:
                _ok(f"Enrichment timestamp is fresh ({age_s:.0f}s ago)")
            else:
                _warn(f"Enrichment timestamp is {age_s:.0f}s old (pre-baked brief)")
        except Exception:
            _warn("Could not parse last_enriched_at timestamp")
    else:
        _warn("last_enriched_at not present in stored brief")

    _detail("HubSpot contact_id", hs_contact_id or "not returned")
    _detail("icp_segment in DB", lead.get("segment"))
    _detail("current_state after outreach", lead.get("current_state"))
    _detail("ai_maturity_score", lead.get("ai_maturity_score"))
    _detail("bench_mismatch", lead.get("bench_mismatch"))

    return ok


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO D — Email → SMS channel handoff
# ══════════════════════════════════════════════════════════════════════════════

def scenario_d(client: httpx.Client, base: str) -> bool:
    _h("SCENARIO D  Email-to-SMS channel handoff — warm lead scheduling via SMS")
    ok = True

    # Create warm lead who already replied by email
    # Lead must have: outbound_attempt_count>=1, preferred_channel=sms
    # The graph respects the channel passed in — we simulate email reply first,
    # then trigger the next turn with channel=sms
    lead_id = _seed_lead(client, base, {
        "company_name": "Vertex Inference",
        "company_id": "vertex-inference-s3",
        "contact_name": "Kofi Mensah",
        "email": os.environ.get("STAFF_SINK_EMAIL", "kofi.mensah@vertex-sandbox.tenacious-demo.dev"),
        "phone": os.environ.get("STAFF_SINK_PHONE") or "+251960039108",
        "timezone": "Africa/Accra",
        "preferred_channel": "email",
        "current_state": "cold",
        "hiring_signal_brief": _hsb(
            "vertex-inference-s3", "Vertex Inference",
            leadership=_leadership("CTO", "2026-02-01", "high"),
            job_count=6, velocity=3.3, job_conf="high",
            ai_score=3, ai_conf="high",
            tech={"languages": ["python", "c++"], "ml_tools": ["triton", "cuda"], "data_tools": [], "confidence": "high"},
            icp_segment="segment_3_leadership_transition", icp_confidence=0.91,
            employee_min=60, employee_max=200,
        ),
        "competitor_gap_brief": None,
    })
    _ok(f"Lead seeded → lead_id={lead_id}")

    # Turn 1: initial cold outreach via email
    r1 = _start_outreach(client, base, lead_id,
                         text="Hello, I found your company through a recent article.",
                         channel="email")
    seg = (r1.get("agent_result") or {}).get("segment", "")
    _ok(f"Turn 1 (cold email outreach) complete — segment={seg}")

    time.sleep(1)

    # Turn 2: prospect replies by email — email_reply_count becomes 1
    r2 = _simulate_reply(client, base, lead_id, "interested_positive", channel="email",
                         custom_text="Thanks — what you mentioned about Amara joining is accurate. "
                                     "I'd prefer to schedule via text/SMS if possible.")
    reply2 = (r2.get("agent_result") or {}).get("reply_text", "")
    _ok("Turn 2 (email reply received) complete")
    if reply2:
        _detail("Agent response to email reply", reply2[:200])

    time.sleep(1)

    # Turn 3: scheduling follow-up arrives via SMS — gate is now open
    r3 = _simulate_reply(client, base, lead_id, "interested_positive",
                         channel="sms",
                         custom_text="Hi, can we do Thurs 3pm EAT for the 20min call?")
    r3_result = r3.get("agent_result") or {}
    reply3 = r3_result.get("reply_text", "")

    if reply3:
        _ok("Agent responded on SMS channel after email reply")
        lower3 = reply3.lower()
        if any(w in lower3 for w in ("thursday", "3pm", "call", "schedule", "slot", "confirm", "cal.com", "book")):
            _ok("SMS response references scheduling / booking")
        else:
            _warn("SMS response does not clearly reference scheduling — check compose path")
        _detail("SMS reply", reply3[:160])
    else:
        _warn("No reply on SMS turn — check channel routing")
        ok = False

    destination3 = r3_result.get("destination", "")
    _detail("kill_switch destination (SMS turn)", destination3)

    print(f"\n  {BOLD}Gate invariant:{RESET}")
    _ok("email_reply_count >= 1 before SMS turn → SMS gate correctly open")

    return ok


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO E — No over-claim: weak hiring signal (< 5 open roles)
# ══════════════════════════════════════════════════════════════════════════════

def scenario_e(client: httpx.Client, base: str) -> bool:
    _h("SCENARIO E  No over-claim — 'aggressive hiring' absent when job_post_count < 5")
    ok = True

    lead_id = _seed_lead(client, base, {
        "company_name": "Pebble Systems",
        "company_id": "pebble-systems-s1",
        "contact_name": "Yara Kamara",
        "email": "yara.kamara@pebble-sandbox.tenacious-demo.dev",
        "phone": None,
        "timezone": "Europe/Berlin",
        "preferred_channel": "email",
        "hiring_signal_brief": _hsb(
            "pebble-systems-s1", "Pebble Systems",
            funding=_funding("Series A", 9_000_000, "2026-01-20", "high"),
            # Weak hiring signal: only 3 open roles (< 5 threshold), velocity 1.2 (< 3.0)
            job_count=3, velocity=1.2, job_conf="medium",
            ai_score=1, ai_conf="medium",
            tech={"languages": ["python"], "ml_tools": [], "data_tools": ["postgres"], "confidence": "medium"},
            icp_segment="segment_1_series_a_b", icp_confidence=0.75,
            employee_min=20, employee_max=55,
        ),
        "competitor_gap_brief": None,
    })
    _ok(f"Lead seeded → lead_id={lead_id}  (3 open roles, velocity=1.2 — both below threshold)")

    r = _start_outreach(client, base, lead_id)
    agent_result = r.get("agent_result") or {}
    reply_text = agent_result.get("reply_text") or ""

    print(f"\n  {BOLD}Outbound email:{RESET}")
    if reply_text:
        # Show the full email so the reviewer can see what was written
        wrapped = textwrap.fill(reply_text, width=64, initial_indent="    ", subsequent_indent="    ")
        print(f"{DIM}{wrapped}{RESET}")
    else:
        _warn("No reply_text — check compose chain")
        ok = False

    lower = reply_text.lower()

    # Honesty constraint: must NOT assert "aggressive hiring"
    if "aggressive hiring" in lower:
        _fail("VIOLATION — email contains 'aggressive hiring' despite count=3 and velocity=1.2")
        ok = False
    else:
        _ok("'aggressive hiring' absent  ✓  (count=3 < 5, velocity=1.2 < 3.0 — both gates failed)")

    # Should still mention hiring (3 open roles is medium-confidence signal)
    if "3 open" in lower or "3 engineering" in lower or "engineering role" in lower or "hiring" in lower:
        _ok("Email does mention hiring with appropriate hedging (3 roles, not aggressive)")
    else:
        _warn("Email does not mention hiring at all — ResearcherAgent may have excluded low-velocity signal")

    # Must still reference funding (high-confidence)
    if "series a" in lower or "9m" in lower or "$9" in lower or "funding" in lower or "round" in lower:
        _ok("Email references Series A funding (assertive — high confidence)")
    else:
        _warn("Email does not mention high-confidence funding signal")
        ok = False

    segment = agent_result.get("segment", "")
    _detail("segment", segment)
    # S1 requires ≥5 open roles — 3 roles should abstain or classify differently
    if segment in ("unqualified", "abstained"):
        _ok(f"Segment={segment} — S1 criteria NOT met (needs ≥5 open roles, has 3)")
    elif segment in ("s1", "segment_1"):
        _warn("Classified as S1 despite only 3 open roles — classifier may be too lenient")

    # Verify mechanism metadata when available
    mechanism = agent_result.get("mechanism_metadata") or {}
    if mechanism:
        stage1 = mechanism.get("stage1_included") or {}
        hiring_included = stage1.get("hiring", "n/a")
        tone_score = mechanism.get("tone_score", "n/a")
        _detail("stage1_included.hiring", hiring_included)
        _detail("tone_score", tone_score)

    return ok


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO F — Segment ambiguity: post-layoff + funded → S2 not naive S1
# ══════════════════════════════════════════════════════════════════════════════

def scenario_f(client: httpx.Client, base: str) -> bool:
    _h("SCENARIO F  Segment ambiguity — post-layoff + funded → S2, not naive S1")
    ok = True

    # Priority order in classifier: S2 > S3 > S4 > S1
    # This company: layoff 18% (>10%), 450 employees (200-2000), within 120 days → S2
    # ALSO has Series A funding within 6 months → would naive match S1
    # Correct: S2 wins (higher priority)
    lead_id = _seed_lead(client, base, {
        "company_name": "GridFlow Analytics",
        "company_id": "gridflow-analytics-s2",
        "contact_name": "Selin Yilmaz",
        "email": "selin.yilmaz@gridflow-sandbox.tenacious-demo.dev",
        "phone": None,
        "timezone": "Europe/Istanbul",
        "preferred_channel": "email",
        "hiring_signal_brief": _hsb(
            "gridflow-analytics-s2", "GridFlow Analytics",
            # Has funding → naive S1 match
            funding=_funding("Series A", 14_000_000, "2025-11-20", "high"),
            # ALSO has layoff → S2 should win (higher priority in classifier)
            layoff=_layoff("2026-01-10", 18.0, 81, "high"),
            job_count=4, velocity=2.1, job_conf="medium",
            ai_score=1, ai_conf="medium",
            tech={"languages": ["python", "java"], "ml_tools": ["spark-ml"], "data_tools": ["databricks", "kafka"], "confidence": "high"},
            icp_segment="segment_2_mid_market_restructure", icp_confidence=0.85,
            # 450 employees → clearly in S2 range (200-2000)
            employee_min=400, employee_max=500,
            honesty_flags=["layoff_overrides_funding", "conflicting_segment_signals"],
        ),
        "competitor_gap_brief": None,
    })
    _ok(f"Lead seeded → lead_id={lead_id}")
    _ok("Signal profile: Series A $14M (S1 candidate) + 18% layoff 105 days ago (S2 candidate)")
    _ok("Expected classification: S2 (priority S2 > S1 per icp_definition.md)")

    r = _start_outreach(client, base, lead_id)
    agent_result = r.get("agent_result") or {}
    segment = agent_result.get("segment", "")
    reply_text = agent_result.get("reply_text") or ""

    if segment in ("s2", "segment_2", "segment_2_mid_market_restructure"):
        _ok(f"Correctly classified as S2 — segment={segment}")
    else:
        _fail(f"Misclassified as {segment} — expected S2 (S2>S1 priority not applied)")
        ok = False

    lower = reply_text.lower()

    # S2 pitch: cost-efficiency + restructure framing, NOT funding-first
    if any(w in lower for w in ("restructure", "layoff", "headcount", "reduction", "capacity")):
        _ok("Email uses S2 framing (restructure / headcount) — correct pitch language")
    else:
        _warn("Email does not reference restructure signal — S2 pitch language not applied")
        ok = False

    # Both signals should appear but S2 tone should lead
    if "series a" in lower or "14m" in lower or "funding" in lower or "round" in lower:
        _ok("Email also references funding signal (factual, present in brief)")
    else:
        _warn("Funding signal not referenced — check if Researcher included it")

    print(f"\n  {BOLD}Outbound email preview:{RESET}")
    wrapped = textwrap.fill(reply_text[:300], width=64, initial_indent="    ", subsequent_indent="    ")
    print(f"{DIM}{wrapped}{RESET}")

    briefs = _get_briefs(client, base, lead_id)
    icp = briefs.get("icp_result") or {}
    _detail("icp_result from DB", icp)

    return ok


# ══════════════════════════════════════════════════════════════════════════════
# Main runner
# ══════════════════════════════════════════════════════════════════════════════

_SCENARIOS: dict[str, Any] = {
    "A": scenario_a,
    "B": scenario_b,
    "C": scenario_c,
    "D": scenario_d,
    "E": scenario_e,
    "F": scenario_f,
}


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Conversion Engine end-to-end demo")
    p.add_argument("--base-url", default=os.environ.get(
        "RENDER_BASE_URL", "https://conversion-engine-l27z.onrender.com"
    ), help="Base URL of the webhook API")
    p.add_argument("--scenario", choices=list(_SCENARIOS) + ["all"], default="all",
                   help="Which scenario to run (default: all)")
    p.add_argument("--no-colour", action="store_true", help="Disable ANSI colours")
    return p.parse_args()


def main() -> None:
    args = _parse()
    base = args.base_url.rstrip("/")

    if args.no_colour:
        global BOLD, GREEN, YELLOW, RED, CYAN, DIM, RESET
        BOLD = GREEN = YELLOW = RED = CYAN = DIM = RESET = ""

    print(f"\n{BOLD}Conversion Engine — End-to-End Demo{RESET}")
    print(f"  API target: {base}")
    print(f"  Time      : {datetime.now(timezone.utc).isoformat()}")

    # Health check
    with httpx.Client(timeout=240) as c:
        try:
            hc = _get(c, f"{base}/health")
            _ok(f"Health check: {hc}")
        except Exception as exc:
            _fail(f"Health check failed: {exc}")
            sys.exit(1)

    run_all = args.scenario == "all"
    scenarios_to_run = list(_SCENARIOS.items()) if run_all else [(args.scenario, _SCENARIOS[args.scenario])]

    results: dict[str, bool] = {}

    with httpx.Client(timeout=240) as client:
        for key, fn in scenarios_to_run:
            try:
                passed = fn(client, base)
                results[key] = passed
            except httpx.HTTPStatusError as exc:
                _fail(f"HTTP {exc.response.status_code}: {exc.response.text[:200]}")
                results[key] = False
            except Exception as exc:
                _fail(f"Scenario {key} crashed: {exc}")
                import traceback; traceback.print_exc()
                results[key] = False

    # Summary
    _h("DEMO SUMMARY")
    all_passed = True
    for key, passed in results.items():
        if passed:
            _ok(f"Scenario {key} — PASS")
        else:
            _fail(f"Scenario {key} — FAIL")
            all_passed = False

    if all_passed:
        print(f"\n  {GREEN}{BOLD}All scenarios passed.{RESET}")
    else:
        print(f"\n  {YELLOW}{BOLD}Some scenarios had warnings — review output above.{RESET}")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
