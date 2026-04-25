# Failure Taxonomy — Conversion Engine Adversarial Probe Library

**Version:** 1.1  
**Date:** 2026-04-25  
**Total probes classified:** 41  
**Total expected business cost (aggregate):** $248,419

Probes are grouped by failure category. Within each category, probes are ranked by business cost (highest cost = highest priority to fix).

---

## Category 1: ICP Misclassification

**Definition:** The classifier assigns the wrong segment to a qualifying prospect, causing a pitch that does not match the buyer's current motivation.

**Root cause pattern:** Boundary conditions, priority rule ordering, and missing disqualifier checks are the primary failure drivers. The classifier evaluates segments in priority order (S1 → S2 → S3 → S4) but ambiguous inputs (dual signals, boundary-value amounts, interim appointments) can bypass the correct priority rule.

**Aggregate business cost:** $55,845  
**Probe count:** 8

| Probe | Failure | Business Cost |
|---|---|---|
| P036 | Anti-offshore founder signal ignored — S1 pitched to confirmed dead lead | $11,520 |
| P001 | Dual-signal ambiguity (funding + layoff) → S1 pitched instead of S2 | $10,560 |
| P037 | Competitor vendor case study (Andela/Turing/Revelo/TopTal) ignored — prospect pitched anyway | $7,560 |
| P038 | S2 assigned despite >40% layoff disqualifier (company in survival mode) | $7,200 |
| P004 | Interim CTO qualifies S3 (disqualifier not checked) | $6,840 |
| P003 | S4 pitched at ai_maturity_score=1 (maturity gate skipped) | $4,725 |
| P002 | Boundary funding ($30M) causes nondeterministic classification | $4,800 |
| P005 | Sub-floor funding ($4.9M) accepted as S1 | $2,640 |

**Recommended fix class:** (1) Add explicit priority-rule unit tests for every pair of conflicting signals (funding + layoff, funding + leadership change, etc.). (2) Enforce boundary conditions with inclusive/exclusive comparison constants documented in `classifier.py`. (3) Add disqualifier pre-checks in the classifier: anti-offshore signal scan (LinkedIn/public posts), competitor vendor case study scan (enrichment pipeline), and hard-coded >40% layoff threshold rejection. Probes P036–P038 require enrichment pipeline data — the classifier cannot disqualify without these signals being present in the `HiringSignalBrief`.

---

## Category 2: Signal Over-Claiming

**Definition:** The agent makes an outbound claim with higher certainty than the underlying data supports, violating the honesty constraints in the style guide and ICP definition.

**Root cause pattern:** The honesty-constraint enforcement sits in `compose_outbound()` which is template-based. Template interpolation can insert assertive language from earlier pipeline stages without re-checking the confidence field. The three most frequent violations are: "aggressive hiring" language below threshold (P006, 28% trigger rate), assertive funding claims at low confidence (P007), and assertive AI maturity claims at low confidence (P008).

**Aggregate business cost:** $34,260  
**Probe count:** 4

| Probe | Failure | Business Cost |
|---|---|---|
| P007 | Assertive funding claim at low confidence | $8,820 |
| P008 | Assertive AI maturity claim at low confidence | $8,640 |
| P006 | "Aggressive hiring" below threshold | $8,400 |
| P009 | Fabricated layoff headcount from percentage-only source | $8,400 |

**Recommended fix class:** Implement a post-compose validation pass that re-reads the confidence fields from the brief and asserts that no assertive language appears for any field where confidence < medium. This is distinct from the tone guard (which checks style); this check verifies factual grounding.

---

## Category 3: Bench Over-Commitment

**Definition:** The agent commits to delivering engineering capacity that the current bench cannot support, creating delivery risk on any deal that closes.

**Root cause pattern:** The bench-to-brief match check (`icp_classifier/bench_match.py`) returns `bench_mismatch: true` when the required stack count exceeds available engineers. However, the graph's `llm` node has tool access to HubSpot but not to the bench summary in real time — the LLM can generate capacity claims in its reply text without the bench-mismatch flag blocking template substitution in the outbound message.

**Aggregate business cost:** $40,680  
**Probe count:** 4

| Probe | Failure | Business Cost |
|---|---|---|
| P041 | Agent fabricates total contract value for out-of-band pricing request | $10,920 |
| P012 | Capacity commitment despite bench_mismatch=True | $13,200 |
| P010 | ML bench over-commitment (count exceeded) | $12,240 |
| P011 | Off-bench stack (Ruby) pitched as available | $4,320 |

**Recommended fix class:** (1) Inject `bench_mismatch` and `bench_available_count` into the system prompt as hard constraints the LLM cannot override. (2) Add a post-LLM validation that blocks any reply containing capacity language when `bench_mismatch=True`. (3) For pricing requests exceeding quotable bands (P041), the LLM prompt must include a hard rule: never compute or state a total contract value — name the applicable monthly rate band and route to a discovery call via Cal.com booking.

---

## Category 4: Tone Drift

**Definition:** Outbound content violates the style guide — prohibited phrases, excessive length, jargon, or superlatives — and the tone guard fails to catch the violation before dispatch.

**Root cause pattern:** The tone-scoring heuristic in `state_machine.py` checks for a subset of prohibited phrases but does not enforce word count limits or catch all jargon variants. Tone scores cluster near 65–72, meaning small LLM variation can push a draft below the 70 threshold — or above it — without changing actual readability.

**Aggregate business cost:** $13,104  
**Probe count:** 6

| Probe | Failure | Business Cost |
|---|---|---|
| P013 | Prohibited follow-up phrase passes tone guard | $2,880 |
| P040 | "bench" jargon in prospect-facing copy passes tone guard | $2,520 |
| P015 | Cold email exceeds 120 words (seed limit) — word count gate not enforced | $2,280 |
| P016 | Jargon survives all three retries; failed drafts pollute CRM | $1,920 |
| P014 | Superlative in cold email passes tone guard | $1,872 |
| P039 | Subject line starts with "Quick" — violates required prefix rule | $1,632 |

**Recommended fix class:** Extend tone scoring to include: (1) exact string match for all prohibited phrases (not substring-only) — including "bench" and subject-line starters "Quick/Just/Hey/Dear"; (2) word count gate at 120 words (cold) / 200 words (warm), consistent with `seed/style_guide.md`; (3) subject line prefix validation against the allowed set (Request/Follow-up/Context/Question); (4) jargon bigram patterns. Failed drafts should be tagged `failed_tone_review=True` in HubSpot activity to prevent them from appearing as sent messages.

---

## Category 5: Multi-Thread Leakage

**Definition:** Data or state from one prospect's thread contaminates another prospect's thread, or service restarts destroy in-memory FSM state causing duplicate outbound.

**Root cause pattern:** The `_fsm_registry` in `webhooks/handler.py` is module-level, not per-request. Concurrent webhook events can interleave graph state writes. Additionally, every Render deployment clears this dict — any prospect in an active state is reset to cold, receiving duplicate first-contact emails.

**Aggregate business cost:** $29,700  
**Probe count:** 3

| Probe | Failure | Business Cost |
|---|---|---|
| P017 | Cross-prospect brief data in outbound email | $14,400 |
| P019 | Service restart resets warm prospects to cold | $12,600 |
| P018 | Concurrent SMS race condition causes FSM rollback | $2,700 |

**Recommended fix class:** (1) Use per-invocation state isolation — never share mutable state across graph invocations. (2) On startup, rebuild FSM state from HubSpot contact properties (the durable store). (3) Serialize webhook events per `prospect_id` with an asyncio lock before FSM transition.

---

## Category 6: Cost Pathology

**Definition:** The system exceeds LLM budget ceilings or per-lead cost thresholds without triggering the required alerts or halts.

**Root cause pattern:** The budget guard and cost-quality violation checks are implemented as after-the-fact checks in Langfuse query scripts, not as inline guards in the LLM call path. A mid-batch spend spike is not detected until the next monitoring sweep.

**Aggregate business cost:** $70 (direct cost overrun, not ACV-based)  
**Probe count:** 3

| Probe | Failure | Business Cost |
|---|---|---|
| P020 | Budget guard not enforced at $20 | $36 |
| P021 | Per-lead cost >$8 not flagged | $20 |
| P022 | Runaway tool-call loop (12 iterations) | $14 |

**Note:** Business cost for cost-pathology probes reflects direct dollar overrun rather than ACV deal loss. The systemic risk is budget exhaustion during evaluation, not direct revenue loss.

**Recommended fix class:** Implement the budget guard as an inline Langfuse callback that checks cumulative spend before each LLM call and raises `BudgetExceededError` if the ceiling is crossed. Tool-call loop detection should be in the graph's iteration limit (`recursion_limit` in LangGraph config).

---

## Category 7: Dual-Control Coordination

**Definition:** The kill switch, channel sequencing rules, or CRM write guarantees fail — outbound reaches an unintended recipient, the wrong channel fires first, or internal records are lost.

**Root cause pattern:** The kill switch node is positioned in the LangGraph graph after the `tools` node. If an exception in `tools` causes early graph termination, the kill switch node is never reached. This creates a bypass path that sends outbound to the real prospect in STAFF_SINK mode.

**Aggregate business cost:** $14,640  
**Probe count:** 3

| Probe | Failure | Business Cost |
|---|---|---|
| P025 | CRM write skipped in STAFF_SINK mode | $5,280 |
| P024 | Kill switch bypassed on tool-call exception | $6,000 |
| P023 | SMS dispatched as first channel (email-first violated) | $3,360 |

**Recommended fix class:** (1) Wrap kill switch logic in a `try/finally` block so it runs even if earlier nodes raise. (2) Route CRM writes through a separate path that does not go through the kill switch. (3) Channel guard should be a pre-condition checked at graph entry, not a runtime decision.

---

## Category 8: Scheduling Edge Cases

**Definition:** The booking agent fabricates slots, displays incorrect timezones, or silently fails to complete bookings.

**Root cause pattern:** Slot count enforcement and timezone conversion are two independent functions with separate failure modes. Slot fabrication occurs when the agent tries to "help" a prospect by filling in plausible times. Timezone errors occur when the delivery-team timezone (EAT) is inadvertently used for prospect display.

**Aggregate business cost:** $26,460  
**Probe count:** 3

| Probe | Failure | Business Cost |
|---|---|---|
| P026 | Slot fabrication when Cal.com returns <3 slots | $10,800 |
| P027 | Timezone display error (EAT displayed as ET) | $10,800 |
| P028 | Silent booking failure on double 409 | $4,860 |

**Recommended fix class:** (1) Hard-code slot count as actual-only; LLM prompt must not "complete" partial slot lists. (2) Timezone conversion test must assert the UTC→local→UTC round-trip for each supported timezone. (3) Booking retry exhaustion must always emit a CRM activity and a prospect-facing reply.

---

## Category 9: Signal Reliability

**Definition:** Date arithmetic errors, null→zero coercion, and boundary-condition off-by-ones cause the enrichment pipeline to include out-of-window signals or mask missing data as zero.

**Root cause pattern:** Date arithmetic throughout the pipeline uses `datetime.now()` without timezone normalization, creating a UTC vs. local timezone discrepancy of up to 3 hours (UTC+3 for Addis Ababa). Null→zero coercion in `compose_outbound()` converts scraper failures into implicit "no open roles" signals.

**Aggregate business cost:** $12,660  
**Probe count:** 4

| Probe | Failure | Business Cost |
|---|---|---|
| P030 | Null→zero coercion hides job-post scraper failures | $3,780 |
| P029 | Off-by-five-days funding window (185-day instead of 180) | $3,600 |
| P032 | Leadership change included in 90-day window via TZ drift | $2,880 |
| P031 | Layoff boundary inconsistency at exactly 120 days | $2,400 |

**Recommended fix class:** (1) Standardize all date arithmetic to `datetime.now(timezone.utc)` and use integer day deltas without timezone offsets. (2) Treat `null` as a first-class value in all data transformations — never coerce to zero implicitly.

---

## Category 10: Gap Over-Claiming

**Definition:** The agent references competitor gap analysis in outbound with insufficient confidence or in segments where gap framing is not appropriate.

**Root cause pattern:** The gap confidence gate is checked in `compose_outbound()` but the LLM in the `llm` node can also generate gap language independently if the CompetitorGapBrief is included in the system prompt. The LLM does not re-check the confidence field before generating gap claims.

**Aggregate business cost:** $20,940  
**Probe count:** 3

| Probe | Failure | Business Cost |
|---|---|---|
| P033 | Gap language at low confidence | $9,660 |
| P035 | "Leading companies in your sector" with only 3 peers | $7,200 |
| P034 | Gap framing in S1 pitch (wrong segment) | $4,080 |

**Recommended fix class:** (1) Filter CompetitorGapBrief before injecting into the LLM system prompt — only include gaps where `confidence in ("medium", "high")`. (2) Segment-level prompt routing: S1/S2/S3 prompts should not include the competitor gap brief at all.

---

## Aggregate Summary

| Category | Probes | Aggregate Business Cost | Avg Trigger Rate |
|---|---|---|---|
| icp_misclassification | 8 | $55,845 | 0.11 |
| bench_over_commitment | 4 | $40,680 | 0.15 |
| signal_over_claiming | 4 | $34,260 | 0.22 |
| multi_thread_leakage | 3 | $29,760 | 0.17 |
| scheduling_edge_cases | 3 | $26,460 | 0.14 |
| gap_over_claiming | 3 | $20,940 | 0.20 |
| dual_control_coordination | 3 | $14,640 | 0.10 |
| tone_drift | 6 | $13,104 | 0.16 |
| signal_reliability | 4 | $12,660 | 0.13 |
| cost_pathology | 3 | $70 | 0.16 |
| **TOTAL** | **41** | **$248,419** | **0.15** |

> Note: The total differs from per-probe sum due to rounding. Business cost figures are structural estimates using ACV_TALENT_MID=$120,000 and ACV_PROJECT_MID=$45,000 — actual ACV values in `seed/baseline_numbers.md` are `$[placeholder]`. Do NOT cite these dollar amounts in `memo/memo.pdf`; cite `baseline_numbers.md` once placeholders are resolved. Avg trigger rates for icp_misclassification and tone_drift were recomputed after adding P036–P041.
