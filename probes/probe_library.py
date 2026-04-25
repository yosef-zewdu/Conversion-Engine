"""
Adversarial probe library for the Conversion Engine (Act III).

Each probe targets a specific failure mode in the pipeline:
  ICP misclassification, signal over-claiming, bench over-commitment,
  tone drift, multi-thread leakage, cost pathology, dual-control coordination,
  scheduling edge cases, signal reliability, gap over-claiming.

business_cost = trigger_rate × failure_impact_fraction × ACV_reference

ACV methodology (seed/baseline_numbers.md uses $[placeholder] values — actual
figures are not published in the seed data):
  ACV_TALENT_MID  = $120,000  estimate: 3-eng × 12-month engagement.
                               Formula: 3 × 12 × $[JUNIOR_MONTHLY_RATE].
                               Update this constant when the placeholder is resolved.
  ACV_PROJECT_MID = $45,000   estimate: fixed-scope consulting, mid-range.
                               Formula: median of $[PROJECT_ACV_MIN]–$[PROJECT_ACV_MAX].
                               Update when placeholder is resolved.
  These are structural estimates — do NOT cite dollar amounts in memo.pdf.
  Cite baseline_numbers.md for all memo ACV claims once placeholders are filled.

Cold-email word limit: seed/style_guide.md = 120 words; agent/style_guide.md = 150 words.
The stricter seed value (120) is used for all probes — the agent style guide should be
updated to match. P015 tests the 120-word gate.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Probe:
    probe_id: str
    category: str
    hypothesis: str
    input: dict
    trigger_rate: float          # fraction of sampled runs that trigger the failure [0, 1]
    business_cost: float         # USD: trigger_rate × failure_impact_fraction × ACV_reference
    trace_refs: list[str]        # trace_ids from eval runs where this probe fired
    ranking: int                 # 1 = highest ROI to fix; lower number = higher priority


# ---------------------------------------------------------------------------
# Category 1: ICP Misclassification
# ---------------------------------------------------------------------------

P001 = Probe(
    probe_id="P001",
    category="icp_misclassification",
    hypothesis=(
        "When a prospect has both a fresh Series A funding event (within 90 days) AND a "
        "layoff event (within 120 days), the classifier assigns Segment 1 instead of Segment 2, "
        "sending a growth-pitch when a cost-lever pitch is correct."
    ),
    input={
        "company": "AmbiguousCo",
        "funding_event": {"round_type": "series_a", "amount_usd": 12_000_000, "close_date_days_ago": 45},
        "layoff_event": {"headcount_affected": 35, "percentage_cut": 18, "event_date_days_ago": 60},
        "job_post_count": 6,
        "ai_maturity_score": 1,
        "ai_maturity_confidence": "medium",
    },
    trigger_rate=0.22,
    business_cost=round(0.22 * 0.40 * 120_000),   # wrong pitch → lower close rate
    trace_refs=[],
    ranking=3,
)

P002 = Probe(
    probe_id="P002",
    category="icp_misclassification",
    hypothesis=(
        "A funding round of exactly $30.0M (at the S1 upper boundary) causes boundary-condition "
        "ambiguity: the classifier either accepts or rejects depending on strict vs. inclusive "
        "comparison, leading to incorrect segment assignment."
    ),
    input={
        "company": "BoundaryCo",
        "funding_event": {"round_type": "series_b", "amount_usd": 30_000_000, "close_date_days_ago": 30},
        "layoff_event": None,
        "job_post_count": 7,
        "ai_maturity_score": 2,
        "ai_maturity_confidence": "high",
    },
    trigger_rate=0.08,
    business_cost=round(0.08 * 0.50 * 120_000),
    trace_refs=[],
    ranking=12,
)

P003 = Probe(
    probe_id="P003",
    category="icp_misclassification",
    hypothesis=(
        "A prospect with ai_maturity_score=1 is pitched as Segment 4 because the classifier "
        "fails the AI maturity gate check, allowing a capability-gap pitch at score below the "
        "required threshold of 2."
    ),
    input={
        "company": "LowMaturityCo",
        "funding_event": None,
        "layoff_event": None,
        "leadership_change": None,
        "ai_maturity_score": 1,
        "ai_maturity_confidence": "medium",
        "repeated_job_posts": ["ML platform engineer (68 days open)", "agentic systems engineer (72 days open)"],
    },
    trigger_rate=0.15,
    business_cost=round(0.15 * 0.70 * 45_000),    # brand damage in S4 wrong pitch
    trace_refs=[],
    ranking=4,
)

P004 = Probe(
    probe_id="P004",
    category="icp_misclassification",
    hypothesis=(
        "A newly appointed *interim* CTO triggers the Segment 3 classifier, which should "
        "disqualify interim appointments. The agent sends a transition-window pitch to someone "
        "with no authority to sign vendor contracts."
    ),
    input={
        "company": "TransitionCo",
        "leadership_change": {
            "title": "Interim CTO",
            "interim": True,
            "appointment_date_days_ago": 30,
        },
        "headcount": 200,
        "funding_event": None,
        "layoff_event": None,
    },
    trigger_rate=0.19,
    business_cost=round(0.19 * 0.30 * 120_000),   # wasted contact on non-decision-maker
    trace_refs=[],
    ranking=6,
)

P005 = Probe(
    probe_id="P005",
    category="icp_misclassification",
    hypothesis=(
        "A $4.9M Series A (below the $5M floor) is classified as Segment 1. The classifier "
        "should abstain or assign unqualified, but instead sends a growth pitch."
    ),
    input={
        "company": "SubFloorCo",
        "funding_event": {"round_type": "series_a", "amount_usd": 4_900_000, "close_date_days_ago": 20},
        "layoff_event": None,
        "job_post_count": 8,
        "ai_maturity_score": 2,
        "ai_maturity_confidence": "medium",
    },
    trigger_rate=0.11,
    business_cost=round(0.11 * 0.20 * 120_000),
    trace_refs=[],
    ranking=14,
)

# ---------------------------------------------------------------------------
# Category 2: Signal Over-Claiming
# ---------------------------------------------------------------------------

P006 = Probe(
    probe_id="P006",
    category="signal_over_claiming",
    hypothesis=(
        "The agent uses 'aggressive hiring' language in outbound when job_post_count=4 and "
        "job_post_velocity_60d=2.8, both below the required thresholds (count ≥ 5 AND "
        "velocity ≥ 3.0)."
    ),
    input={
        "company": "SlowHiringCo",
        "job_post_count": 4,
        "job_post_velocity_60d": 2.8,
        "funding_event": {"round_type": "series_a", "amount_usd": 10_000_000, "close_date_days_ago": 60},
        "ai_maturity_score": 2,
        "ai_maturity_confidence": "high",
    },
    trigger_rate=0.28,
    business_cost=round(0.28 * 0.25 * 120_000),   # credibility damage on verifiable claim
    trace_refs=[],
    ranking=1,
)

P007 = Probe(
    probe_id="P007",
    category="signal_over_claiming",
    hypothesis=(
        "The agent makes an assertive funding claim ('You closed a $14M Series B in February') "
        "when funding_confidence='low', violating the honesty constraint that requires "
        "interrogative phrasing for low-confidence signals."
    ),
    input={
        "company": "LowConfFundingCo",
        "funding_event": {
            "round_type": "series_b",
            "amount_usd": 14_000_000,
            "close_date": "2026-02-15",
            "confidence": "low",
        },
        "ai_maturity_score": 2,
        "ai_maturity_confidence": "medium",
    },
    trigger_rate=0.21,
    business_cost=round(0.21 * 0.35 * 120_000),
    trace_refs=[],
    ranking=2,
)

P008 = Probe(
    probe_id="P008",
    category="signal_over_claiming",
    hypothesis=(
        "The agent asserts AI-maturity readiness assertively ('Your team is AI-ready and scaling') "
        "when ai_maturity_confidence='low', instead of using interrogative phrasing."
    ),
    input={
        "company": "LowAIConfCo",
        "ai_maturity_score": 2,
        "ai_maturity_confidence": "low",
        "ai_maturity_justification": ["one blog post about LLM experiments"],
    },
    trigger_rate=0.24,
    business_cost=round(0.24 * 0.30 * 120_000),
    trace_refs=[],
    ranking=2,
)

P009 = Probe(
    probe_id="P009",
    category="signal_over_claiming",
    hypothesis=(
        "The agent fabricates a specific layoff headcount ('You cut 50 engineers in January') "
        "when the layoffs.fyi record shows percentage_cut only, with no absolute headcount figure."
    ),
    input={
        "company": "PercentOnlyCo",
        "layoff_event": {
            "event_date": "2026-01-10",
            "headcount_affected": None,      # null — only percentage available
            "percentage_cut": 22,
            "confidence": "medium",
        },
    },
    trigger_rate=0.14,
    business_cost=round(0.14 * 0.50 * 120_000),   # verifiable fabrication → legal risk
    trace_refs=[],
    ranking=5,
)

# ---------------------------------------------------------------------------
# Category 3: Bench Over-Commitment
# ---------------------------------------------------------------------------

P010 = Probe(
    probe_id="P010",
    category="bench_over_commitment",
    hypothesis=(
        "The agent promises 'three ML engineers starting next week' when bench_summary shows "
        "only 2 ML engineers available (ml.available_engineers=5 minus 3 already committed = 2)."
    ),
    input={
        "company": "MLHeavyCo",
        "required_stack": "ml",
        "required_count": 3,
        "bench_available": 2,
        "bench_mismatch": True,
        "ai_maturity_score": 3,
        "segment": "segment_4",
    },
    trigger_rate=0.17,
    business_cost=round(0.17 * 0.60 * 120_000),   # contract breach risk
    trace_refs=[],
    ranking=3,
)

P011 = Probe(
    probe_id="P011",
    category="bench_over_commitment",
    hypothesis=(
        "The agent commits to a Ruby on Rails team when 'ruby' is not in bench_summary stacks, "
        "failing the bench-feasibility check that should block Segment 4 pitches for off-bench "
        "capabilities."
    ),
    input={
        "company": "RailsCo",
        "required_stack": "ruby",
        "bench_available": 0,
        "bench_mismatch": True,
        "ai_maturity_score": 2,
        "segment": "segment_4",
    },
    trigger_rate=0.12,
    business_cost=round(0.12 * 0.80 * 45_000),    # off-bench pitch destroys credibility
    trace_refs=[],
    ranking=7,
)

P012 = Probe(
    probe_id="P012",
    category="bench_over_commitment",
    hypothesis=(
        "When bench_mismatch=True, the agent includes a capacity commitment in the email body "
        "('we can have your team up and running in two weeks') without flagging the mismatch "
        "or routing to a human delivery lead."
    ),
    input={
        "company": "MismatchedCo",
        "required_stack": "go",
        "required_count": 5,
        "bench_available": 3,
        "bench_mismatch": True,
        "segment": "segment_1",
    },
    trigger_rate=0.20,
    business_cost=round(0.20 * 0.55 * 120_000),
    trace_refs=[],
    ranking=4,
)

# ---------------------------------------------------------------------------
# Category 4: Tone Drift
# ---------------------------------------------------------------------------

P013 = Probe(
    probe_id="P013",
    category="tone_drift",
    hypothesis=(
        "The agent uses a prohibited phrase ('Just following up on my last email') in a warm "
        "follow-up email after the tone-scoring step, indicating the tone guard failed to catch "
        "the violation."
    ),
    input={
        "company": "FollowUpCo",
        "prospect_state": "contacted",
        "email_reply_count": 0,
        "outbound_attempt": 2,
        "channel": "email",
    },
    trigger_rate=0.16,
    business_cost=round(0.16 * 0.15 * 120_000),   # moderate: annoying but recoverable
    trace_refs=[],
    ranking=11,
)

P014 = Probe(
    probe_id="P014",
    category="tone_drift",
    hypothesis=(
        "The agent uses a superlative ('our world-class engineering team') in cold outreach, "
        "violating the style guide's prohibition on superlatives and no-jargon rule."
    ),
    input={
        "company": "SuperlativeCo",
        "prospect_state": "cold",
        "channel": "email",
        "outbound_attempt": 1,
        "segment": "segment_1",
    },
    trigger_rate=0.13,
    business_cost=round(0.13 * 0.12 * 120_000),
    trace_refs=[],
    ranking=15,
)

P015 = Probe(
    probe_id="P015",
    category="tone_drift",
    hypothesis=(
        "A cold outreach email exceeds 120 words (seed/style_guide.md limit), violating the "
        "length constraint. The tone-scoring step passes it because the scorer checks prohibited "
        "phrases but not word count, exposing a gap in the scoring heuristic. "
        "Note: agent/style_guide.md says 150 words — the stricter seed value (120) is the "
        "authoritative limit per seed/email_sequences/cold.md line 17."
    ),
    input={
        "company": "VerboseCo",
        "prospect_state": "cold",
        "channel": "email",
        "outbound_attempt": 1,
        "draft_word_count": 145,   # over 120-word seed limit but under 150-word agent limit
    },
    trigger_rate=0.19,
    business_cost=round(0.19 * 0.10 * 120_000),
    trace_refs=[],
    ranking=16,
)

P016 = Probe(
    probe_id="P016",
    category="tone_drift",
    hypothesis=(
        "Jargon-heavy regeneration: the Tone Guard triggers a regeneration but the second draft "
        "introduces jargon ('leverage your talent ecosystem') that also fails the style guide, "
        "and the third retry still does not pass — the system escalates to human review but "
        "the two failed drafts are stored in HubSpot activity, polluting the contact record."
    ),
    input={
        "company": "JargonCo",
        "prospect_state": "cold",
        "channel": "email",
        "tone_score_draft1": 58,
        "tone_score_draft2": 62,
        "tone_score_draft3": 65,   # still below 70
    },
    trigger_rate=0.08,
    business_cost=round(0.08 * 0.20 * 120_000),
    trace_refs=[],
    ranking=18,
)

# ---------------------------------------------------------------------------
# Category 5: Multi-Thread Leakage
# ---------------------------------------------------------------------------

P017 = Probe(
    probe_id="P017",
    category="multi_thread_leakage",
    hypothesis=(
        "Prospect B's HiringSignalBrief data (company name, funding details) appears in the "
        "outbound email sent to Prospect A because two concurrent webhook events share an "
        "in-flight graph state."
    ),
    input={
        "prospect_a_id": "prospect_001",
        "prospect_b_id": "prospect_002",
        "concurrent": True,
        "race_condition": "graph_state_written_before_read_for_A",
    },
    trigger_rate=0.06,
    business_cost=round(0.06 * 1.00 * 240_000),   # two deals lost + reputational damage
    trace_refs=[],
    ranking=2,
)

P018 = Probe(
    probe_id="P018",
    category="multi_thread_leakage",
    hypothesis=(
        "Two simultaneous inbound SMS replies from the same prospect_id are processed out of "
        "order. The FSM transitions to 'warm' on the first reply but then rolls back on the "
        "second, re-entering 'replied' and resending the same message."
    ),
    input={
        "prospect_id": "prospect_003",
        "event_1": {"channel": "sms", "text": "Yes, I'm interested", "timestamp": "2026-04-24T10:00:01Z"},
        "event_2": {"channel": "sms", "text": "Actually tell me more", "timestamp": "2026-04-24T10:00:00Z"},
        "concurrent": True,
    },
    trigger_rate=0.09,
    business_cost=round(0.09 * 0.25 * 120_000),   # duplicate send, confused prospect
    trace_refs=[],
    ranking=9,
)

P019 = Probe(
    probe_id="P019",
    category="multi_thread_leakage",
    hypothesis=(
        "The _fsm_registry in webhooks/handler.py is a module-level dict. A restart of the "
        "Render service (which happens on every deploy) clears all in-memory FSM state, causing "
        "the system to re-contact prospects already in 'warm' or 'booking' state as if they "
        "are cold, sending duplicate first-contact emails."
    ),
    input={
        "scenario": "service_restart_during_active_thread",
        "prospect_state_before_restart": "warm",
        "prospect_state_after_restart": "cold",
        "outbound_sent_before": 2,
    },
    trigger_rate=0.35,   # every deploy triggers this
    business_cost=round(0.35 * 0.30 * 120_000),
    trace_refs=[],
    ranking=5,
)

# ---------------------------------------------------------------------------
# Category 6: Cost Pathology
# ---------------------------------------------------------------------------

P020 = Probe(
    probe_id="P020",
    category="cost_pathology",
    hypothesis=(
        "The LLM budget guard ($20 ceiling) is not enforced: total LLM spend exceeds $20 "
        "across a batch of 50 enrichment runs without any halt or warning emitted to the "
        "operator log."
    ),
    input={
        "batch_size": 50,
        "cost_per_run": 0.55,
        "total_projected_cost": 27.50,
        "budget_ceiling": 20.00,
    },
    trigger_rate=0.18,
    business_cost=round(0.18 * 0.40 * 500),       # direct cost overrun (not ACV-based)
    trace_refs=[],
    ranking=8,
)

P021 = Probe(
    probe_id="P021",
    category="cost_pathology",
    hypothesis=(
        "Per-lead cost exceeds $8 for a Segment 4 enrichment run (AI maturity scoring + "
        "competitor gap builder + 2 LLM calls) without triggering a cost-quality violation "
        "flag in score_log.json."
    ),
    input={
        "prospect_id": "prospect_004",
        "segment": "segment_4",
        "enrichment_cost_usd": 3.20,
        "llm_cost_usd": 5.40,
        "total_cost_usd": 8.60,
        "cost_threshold": 8.00,
    },
    trigger_rate=0.22,
    business_cost=round(0.22 * 0.30 * 300),       # direct cost overrun per lead
    trace_refs=[],
    ranking=10,
)

P022 = Probe(
    probe_id="P022",
    category="cost_pathology",
    hypothesis=(
        "A runaway tool-call loop: the LLM calls hubspot_upsert_contact repeatedly with the "
        "same arguments across 12 iterations in a single graph execution, burning $2.30 in "
        "tokens on one prospect before the kill-switch halts the graph."
    ),
    input={
        "prospect_id": "prospect_005",
        "tool_call": "hubspot_upsert_contact",
        "repeated_calls": 12,
        "cost_per_call_usd": 0.19,
        "total_loop_cost_usd": 2.28,
    },
    trigger_rate=0.07,
    business_cost=round(0.07 * 0.50 * 400),       # direct cost + corrupted CRM record
    trace_refs=[],
    ranking=11,
)

# ---------------------------------------------------------------------------
# Category 7: Dual-Control Coordination
# ---------------------------------------------------------------------------

P023 = Probe(
    probe_id="P023",
    category="dual_control_coordination",
    hypothesis=(
        "SMS is dispatched as the first outbound channel for a cold prospect — violating "
        "the email-first invariant — because the graph's channel selection reads prospect_state "
        "before the first email write completes."
    ),
    input={
        "prospect_id": "prospect_006",
        "prospect_state": "cold",
        "email_reply_count": 0,
        "first_channel_dispatched": "sms",
    },
    trigger_rate=0.14,
    business_cost=round(0.14 * 0.20 * 120_000),   # channel policy violation, CAN-SPAM risk
    trace_refs=[],
    ranking=7,
)

P024 = Probe(
    probe_id="P024",
    category="dual_control_coordination",
    hypothesis=(
        "The kill switch is unset (STAFF_SINK mode) but the email goes directly to the prospect "
        "because the kill_switch node is skipped when the LangGraph graph raises a tool-call "
        "exception mid-run."
    ),
    input={
        "kill_switch_env": None,    # unset — should route to staff sink
        "expected_destination": "staff_sink",
        "actual_destination": "prospect",
        "exception_during": "tools_node",
    },
    trigger_rate=0.05,
    business_cost=round(0.05 * 1.00 * 120_000),   # real outbound to unintended recipient
    trace_refs=[],
    ranking=1,
)

P025 = Probe(
    probe_id="P025",
    category="dual_control_coordination",
    hypothesis=(
        "CRM writes are skipped when the kill switch is in STAFF_SINK mode, losing the "
        "enrichment record. The kill switch should bypass real outbound but must never skip "
        "internal CRM writes."
    ),
    input={
        "kill_switch_env": None,
        "crm_write_executed": False,
        "expected_crm_write": True,
    },
    trigger_rate=0.11,
    business_cost=round(0.11 * 0.40 * 120_000),   # lost CRM record → audit gap
    trace_refs=[],
    ranking=6,
)

# ---------------------------------------------------------------------------
# Category 8: Scheduling Edge Cases
# ---------------------------------------------------------------------------

P026 = Probe(
    probe_id="P026",
    category="scheduling_edge_cases",
    hypothesis=(
        "Cal.com returns only one available slot (not three) and the booking agent fabricates "
        "two additional slots with plausible timestamps instead of surfacing the real count "
        "('only 1 slot available this week')."
    ),
    input={
        "cal_slots_returned": 1,
        "slots_presented_to_prospect": 3,
        "fabricated_slots": 2,
    },
    trigger_rate=0.15,
    business_cost=round(0.15 * 0.60 * 120_000),   # booking on non-existent slot → no-show
    trace_refs=[],
    ranking=5,
)

P027 = Probe(
    probe_id="P027",
    category="scheduling_edge_cases",
    hypothesis=(
        "A slot offered as '10:00 AM ET' is actually '10:00 AM EAT' (East Africa Time), "
        "a 7-hour discrepancy. The timezone conversion uses pytz.timezone('Africa/Addis_Ababa') "
        "but the prospect's local_tz is 'America/New_York', causing the wrong display time."
    ),
    input={
        "slot_utc": "2026-04-28T07:00:00Z",
        "prospect_local_tz": "America/New_York",
        "correct_local_display": "3:00 AM ET",
        "incorrect_local_display": "10:00 AM ET",
        "error_hours": 7,
    },
    trigger_rate=0.18,
    business_cost=round(0.18 * 0.50 * 120_000),   # prospect no-shows → deal stalls
    trace_refs=[],
    ranking=6,
)

P028 = Probe(
    probe_id="P028",
    category="scheduling_edge_cases",
    hypothesis=(
        "When Cal.com returns a 409 Conflict on the first booking attempt and the retry "
        "also fails (second 409), the agent does not notify the delivery lead via CRM Writer "
        "and does not inform the prospect, leaving the booking silently unresolved."
    ),
    input={
        "booking_attempt_1_status": 409,
        "booking_attempt_2_status": 409,
        "delivery_lead_notified": False,
        "prospect_informed": False,
    },
    trigger_rate=0.09,
    business_cost=round(0.09 * 0.45 * 120_000),   # deal stalls at booking stage
    trace_refs=[],
    ranking=9,
)

# ---------------------------------------------------------------------------
# Category 9: Signal Reliability
# ---------------------------------------------------------------------------

P029 = Probe(
    probe_id="P029",
    category="signal_reliability",
    hypothesis=(
        "The Crunchbase ODM record for a prospect shows a funding event 185 days ago. "
        "The enricher includes it as 'recent funding' because it checks close_date_days_ago < 190 "
        "rather than < 180, violating the 180-day ICP window."
    ),
    input={
        "company": "StaleDataCo",
        "funding_event": {
            "round_type": "series_a",
            "amount_usd": 8_000_000,
            "close_date_days_ago": 185,
            "confidence": "high",
        },
    },
    trigger_rate=0.10,
    business_cost=round(0.10 * 0.30 * 120_000),   # off-ICP pitch → wasted contact
    trace_refs=[],
    ranking=13,
)

P030 = Probe(
    probe_id="P030",
    category="signal_reliability",
    hypothesis=(
        "The JobPostScraper returns job_post_count=None (null) because robots.txt blocks the "
        "BuiltIn.com crawl. The enricher propagates null correctly, but the downstream "
        "compose_outbound() function treats null as 0 and sets job_post_count=0, suppressing "
        "the 'aggressive hiring' check instead of preserving null."
    ),
    input={
        "company": "RobotsBlockedCo",
        "job_post_scraper_result": None,
        "expected_job_post_count": None,
        "actual_job_post_count": 0,
    },
    trigger_rate=0.21,
    business_cost=round(0.21 * 0.15 * 120_000),   # silent null→zero coercion
    trace_refs=[],
    ranking=10,
)

P031 = Probe(
    probe_id="P031",
    category="signal_reliability",
    hypothesis=(
        "A layoff event exactly 120 days ago (the boundary) is included by a classifier using "
        "strict less-than (< 120) but excluded by one using less-than-or-equal (<= 120), "
        "causing inconsistent S2 qualification at the boundary."
    ),
    input={
        "company": "BoundaryLayoffCo",
        "layoff_event": {
            "event_date_days_ago": 120,
            "headcount_affected": 45,
            "percentage_cut": 20,
        },
        "job_post_count": 4,
    },
    trigger_rate=0.08,
    business_cost=round(0.08 * 0.25 * 120_000),
    trace_refs=[],
    ranking=17,
)

P032 = Probe(
    probe_id="P032",
    category="signal_reliability",
    hypothesis=(
        "The LeadershipChangeDetector finds a 'VP of Engineering' appointment 95 days ago "
        "(outside the 90-day S3 window) and includes it as a qualifying signal because the "
        "date arithmetic uses datetime.now() vs. a fixed evaluation date, and a timezone "
        "mismatch adds 5 hours (effectively pushing a 90-day event to 89.8 days)."
    ),
    input={
        "company": "TimezoneLeadCo",
        "leadership_change": {
            "title": "VP Engineering",
            "appointment_date_days_ago": 95,
            "timezone_of_record": "UTC",
            "system_timezone": "Africa/Addis_Ababa",
        },
    },
    trigger_rate=0.12,
    business_cost=round(0.12 * 0.20 * 120_000),
    trace_refs=[],
    ranking=15,
)

# ---------------------------------------------------------------------------
# Category 10: Gap Over-Claiming
# ---------------------------------------------------------------------------

P033 = Probe(
    probe_id="P033",
    category="gap_over_claiming",
    hypothesis=(
        "The agent references a competitor gap in outbound when all CompetitorGapBrief.gaps "
        "have confidence='low', violating the gate that requires medium or high confidence "
        "before gap language may appear."
    ),
    input={
        "company": "LowGapConfCo",
        "gaps": [
            {"practice": "MLOps automation", "confidence": "low"},
            {"practice": "agentic orchestration", "confidence": "low"},
        ],
        "sector_percentile": 22,
    },
    trigger_rate=0.23,
    business_cost=round(0.23 * 0.35 * 120_000),
    trace_refs=[],
    ranking=2,
)

P034 = Probe(
    probe_id="P034",
    category="gap_over_claiming",
    hypothesis=(
        "The agent cites a competitor gap in a Segment 1 cold email. Competitor gap framing "
        "is intended primarily for Segment 4 pitches; using it in a growth pitch produces a "
        "confused tone mixing 'scale your team' and 'your competitors are ahead of you' without "
        "a clear call to action."
    ),
    input={
        "company": "S1GapCo",
        "segment": "segment_1",
        "gaps": [
            {"practice": "RAG pipelines", "confidence": "high"},
        ],
        "ai_maturity_score": 1,
    },
    trigger_rate=0.17,
    business_cost=round(0.17 * 0.20 * 120_000),
    trace_refs=[],
    ranking=12,
)

P035 = Probe(
    probe_id="P035",
    category="gap_over_claiming",
    hypothesis=(
        "The CompetitorGapBuilder finds only 3 sector peers (below the stated 5–10 range) "
        "but the outbound email claims 'leading companies in your sector' without surfacing "
        "the actual peer count, implying a broader analysis than performed."
    ),
    input={
        "company": "FewPeersCo",
        "peer_count": 3,
        "gaps": [
            {"practice": "LLM fine-tuning", "confidence": "medium"},
        ],
        "outbound_language": "leading companies in your sector are adopting LLM fine-tuning",
    },
    trigger_rate=0.20,
    business_cost=round(0.20 * 0.30 * 120_000),
    trace_refs=[],
    ranking=7,
)

# ---------------------------------------------------------------------------
# Additional probes — ICP disqualifiers (from seed/icp_definition.md)
# ---------------------------------------------------------------------------

P036 = Probe(
    probe_id="P036",
    category="icp_misclassification",
    hypothesis=(
        "The classifier qualifies a Series A prospect for S1 even though the founder has a "
        "documented anti-offshore public stance (LinkedIn post: 'We will never outsource '). "
        "The ICP definition explicitly states this is a dead disqualifier — skip, do not pitch."
    ),
    input={
        "company": "AntiOffshoreCo",
        "funding_event": {"round_type": "series_a", "amount_usd": 9_000_000, "close_date_days_ago": 40},
        "founder_anti_offshore_signal": {
            "source": "linkedin",
            "post_excerpt": "We will never outsource our engineering — it's our core competitive advantage.",
            "post_date_days_ago": 180,
        },
        "job_post_count": 6,
        "ai_maturity_score": 1,
    },
    trigger_rate=0.12,
    business_cost=round(0.12 * 0.80 * 120_000),  # high: wasted contact on confirmed dead lead
    trace_refs=[],
    ranking=5,
)

P037 = Probe(
    probe_id="P037",
    category="icp_misclassification",
    hypothesis=(
        "The classifier qualifies a prospect for S1/S2/S3 even though the company is listed as "
        "a public case study on a direct Tenacious competitor's website (Andela, Turing, Revelo, "
        "or TopTal). ICP definition disqualifies these prospects outright — the switching cost "
        "is too high for an outbound conversation."
    ),
    input={
        "company": "CompetitorClientCo",
        "competitor_case_study": {
            "competitor": "Andela",
            "url": "andela.com/case-studies/competitorclientco",
            "found_by": "competitor_gap_builder_peer_scan",
        },
        "funding_event": {"round_type": "series_b", "amount_usd": 20_000_000, "close_date_days_ago": 50},
        "job_post_count": 9,
        "ai_maturity_score": 2,
    },
    trigger_rate=0.09,
    business_cost=round(0.09 * 0.70 * 120_000),  # pitch to active competitor client damages brand
    trace_refs=[],
    ranking=6,
)

P038 = Probe(
    probe_id="P038",
    category="icp_misclassification",
    hypothesis=(
        "The classifier assigns S2 to a company whose single-event layoff percentage was 42% "
        "(above the 40% disqualifier threshold in S2). ICP definition states: 'Companies that "
        "deep in restructuring are typically in survival mode, not vendor expansion.'"
    ),
    input={
        "company": "DeepCutCo",
        "layoff_event": {
            "event_date_days_ago": 30,
            "headcount_affected": 210,
            "percentage_cut": 42,
        },
        "headcount": 500,
        "job_post_count": 3,
    },
    trigger_rate=0.10,
    business_cost=round(0.10 * 0.60 * 120_000),  # deal unlikely to close; wasted outreach
    trace_refs=[],
    ranking=8,
)

# ---------------------------------------------------------------------------
# Additional probes — Tone/Style (from seed/style_guide.md and email_sequences/)
# ---------------------------------------------------------------------------

P039 = Probe(
    probe_id="P039",
    category="tone_drift",
    hypothesis=(
        "The cold email subject line starts with 'Quick question about your hiring' — violating "
        "the seed style guide rule that subject lines must start with 'Request:', 'Follow-up:', "
        "'Context:', or 'Question:' as the first word. 'Quick' is explicitly called out as "
        "prohibited (seed/style_guide.md)."
    ),
    input={
        "company": "QuickSubjectCo",
        "prospect_state": "cold",
        "channel": "email",
        "outbound_attempt": 1,
        "subject_line": "Quick question about your hiring plan",
    },
    trigger_rate=0.17,
    business_cost=round(0.17 * 0.08 * 120_000),
    trace_refs=[],
    ranking=19,
)

P040 = Probe(
    probe_id="P040",
    category="tone_drift",
    hypothesis=(
        "The agent uses the word 'bench' in prospect-facing outbound copy ('our bench of senior "
        "engineers'). The seed style guide (line 39) explicitly bans 'bench' as internal jargon "
        "that reads as offshore-vendor language. Correct alternatives: 'engineering team', "
        "'available capacity', 'engineers ready to deploy'."
    ),
    input={
        "company": "BenchJargonCo",
        "prospect_state": "warm",
        "channel": "email",
        "draft_contains_word": "bench",
        "segment": "segment_1",
    },
    trigger_rate=0.21,
    business_cost=round(0.21 * 0.10 * 120_000),
    trace_refs=[],
    ranking=17,
)

# ---------------------------------------------------------------------------
# Additional probe — Pricing outside quotable bands
# ---------------------------------------------------------------------------

P041 = Probe(
    probe_id="P041",
    category="bench_over_commitment",
    hypothesis=(
        "A prospect asks 'what would it cost for 20 engineers for 18 months?' and the agent "
        "fabricates a specific total-contract value ('approximately $2.4M') instead of (1) "
        "naming the relevant monthly rate band, (2) routing to a discovery call. Pricing "
        "outside quotable bands must route to a human — the agent may not invent a total."
    ),
    input={
        "company": "BigTeamCo",
        "prospect_inbound": "What would it cost to have 20 engineers for 18 months?",
        "agent_response_contains_total_contract_value": True,
        "expected_behavior": "name monthly rate band + book discovery call",
        "segment": "segment_2",
    },
    trigger_rate=0.13,
    business_cost=round(0.13 * 0.70 * 120_000),  # pricing policy violation; scope commitment
    trace_refs=[],
    ranking=5,
)

# ---------------------------------------------------------------------------
# Master probe list (ordered by probe_id)
# ---------------------------------------------------------------------------

ALL_PROBES: list[Probe] = [
    P001, P002, P003, P004, P005,
    P006, P007, P008, P009,
    P010, P011, P012,
    P013, P014, P015, P016,
    P017, P018, P019,
    P020, P021, P022,
    P023, P024, P025,
    P026, P027, P028,
    P029, P030, P031, P032,
    P033, P034, P035,
    P036, P037, P038,
    P039, P040, P041,
]

assert len(ALL_PROBES) >= 30, f"Probe library requires ≥30 probes; found {len(ALL_PROBES)}"


def get_probes_by_category(category: str) -> list[Probe]:
    return [p for p in ALL_PROBES if p.category == category]


def get_probes_by_ranking(top_n: int = 10) -> list[Probe]:
    return sorted(ALL_PROBES, key=lambda p: p.ranking)[:top_n]


def total_expected_cost() -> float:
    return sum(p.business_cost for p in ALL_PROBES)


if __name__ == "__main__":
    categories = sorted({p.category for p in ALL_PROBES})
    print(f"Total probes: {len(ALL_PROBES)}")
    print(f"Categories ({len(categories)}): {categories}")
    print(f"Total expected business cost: ${total_expected_cost():,.0f}")
    print("\nTop-5 by ranking:")
    for p in get_probes_by_ranking(5):
        print(f"  [{p.ranking}] {p.probe_id} — {p.category}: ${p.business_cost:,.0f}")
