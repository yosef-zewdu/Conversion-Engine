# Target Failure Mode — Act IV Mechanism Design Brief

**Version:** 1.1  
**Date:** 2026-04-25  
**Prepared for:** Act IV mechanism selection  
**Probe library version:** 1.1 (41 probes, 10 categories)

---

## Selected Failure Mode: Signal Over-Claiming

### Why this category

After the probe library audit (v1.1, 41 probes), the updated aggregate costs are:

| Category | Aggregate Cost | Avg Trigger Rate | Mechanism Type | Act IV Testable? |
|---|---|---|---|---|
| icp_misclassification | $55,845 | 0.11 | Enrichment pipeline (new data sources) | **No** |
| bench_over_commitment | $40,680 | 0.15 | Hard constraint injection | No |
| signal_over_claiming | $34,260 | 0.22 | LLM prompt chain (structural separation) | **Yes** |
| multi_thread_leakage | $29,760 | 0.17 | Architectural (state isolation) | No |

**ICP misclassification is now the highest-cost category ($55,845)**, driven by three new disqualifier probes: P036 (anti-offshore founder), P037 (competitor vendor case study), P038 (>40% layoff). However, fixing these requires enrichment pipeline changes — the classifier needs LinkedIn/case-study signals that are not yet in the `HiringSignalBrief`. This is a data-source problem, not a language-generation problem, and would not produce a measurable pass@1 improvement on the τ²-Bench benchmark.

**Signal over-claiming remains the correct Act IV target** for three reasons:

1. **LLM-level mechanism**: The fix (3-stage prompt chain with structural confidence gating) is a generative mechanism that directly improves τ²-Bench pass@1 — its effect is measurable via the existing benchmark.

2. **Detectability**: Signal over-claiming claims are verifiable by the prospect (job post counts, funding amounts, AI maturity status). A failed probe on the first cold email is the most expensive funnel failure point — it destroys credibility before any relationship exists.

3. **Trigger rate × cost**: At 0.22 avg trigger rate, signal over-claiming fires more frequently than any other LLM-testable category. The P006–P009 probes will produce clear pass@1 signal in the held-out slice.

Bench over-commitment is addressed by hard constraint injection (bench summary in system prompt) rather than a generative mechanism — it would not produce a pass@1 delta on the benchmark. Multi-thread leakage requires architectural state isolation, not prompt design.

---

## Root Cause Analysis

The current pipeline generates outbound in `nurture_sequencer/state_machine.py` via `compose_outbound()`, which performs **template string interpolation**: it inserts enriched fields from the `HiringSignalBrief` directly into the message body.

The honesty-constraint check is a post-composition scan for specific prohibited phrases (e.g., "aggressive hiring"). This design has three structural weaknesses:

1. **The confidence field is not consulted at generation time.** The template populates assertive language regardless of whether the underlying signal has `confidence='low'`. The post-composition check can only recognize a narrow list of known bad phrases — it misses novel assertive phrasings that express the same over-claiming semantics.

2. **Null propagation is not enforced end-to-end.** When a scraper returns `null`, template interpolation may silently coerce it (null job count → zero → no "aggressive hiring" but also no flag that the data is missing). The LLM in the `llm` node can then generate text referencing "no open roles" as a finding, when the correct behavior is to omit the claim entirely.

3. **No separation between fact extraction and language generation.** The same step that picks which facts to include also decides how to phrase them. This conflation means confidence gating cannot be enforced as a hard rule — it must be inferred from context.

---

## Proposed Mechanism: 3-Stage Prompt Chain

The Act IV mechanism replaces `compose_outbound()` in `nurture_sequencer/state_machine.py` with a **3-stage prompt chain** that enforces confidence gating structurally, not through post-composition scanning.

### Stage 1 — Researcher Agent

**Input:** `HiringSignalBrief`, `CompetitorGapBrief`  
**Output:** `ResearchSummary` (structured JSON)

The Researcher's sole task is to extract verifiable facts and attach their confidence levels. It has one hard constraint: **no outreach language**. It may not write sentences intended for the prospect. Its output is a JSON object with fields like:

```json
{
  "funding": {
    "included": true,
    "confidence": "low",
    "fact": "Crunchbase shows a $14M round in February 2026"
  },
  "hiring": {
    "included": true,
    "confidence": "high",
    "fact": "6 open engineering roles (job_post_count=6, velocity_60d=3.8)"
  },
  "ai_maturity": {
    "included": false,
    "reason": "confidence=low, below threshold for inclusion"
  },
  "competitor_gap": {
    "included": false,
    "reason": "all gaps have confidence=low"
  }
}
```

Key invariants:
- Fields where `confidence='low'` are either marked `included: false` or tagged with required interrogative phrasing.
- `null` fields produce `included: false` — never interpolated into the Closer's context.
- "Aggressive hiring" is only marked `included: true` when `job_post_count >= 5 AND job_post_velocity_60d >= 3.0`.

### Stage 2 — Closer Agent

**Input:** `ResearchSummary` (from Stage 1), `segment`, `prospect_state`, `style_guide.md`  
**Output:** Draft email / SMS body

The Closer **cannot see the raw briefs** — only the pre-filtered `ResearchSummary`. This means it is structurally impossible for the Closer to assert a low-confidence funding event assertively, because the Researcher either excluded it or marked it as interrogative-phrasing-required.

The Closer's prompt includes the confidence-phrasing rules from the style guide as system-level instructions, not just soft guidelines.

### Stage 3 — Tone Guard

**Input:** Draft from Stage 2, `style_guide.md`  
**Output:** Tone score (0–100) + regeneration decision

The Tone Guard is unchanged from the current design (score ≥ 70 passes, max 2 retries). However, because the Closer already has the confidence constraints built in, the Tone Guard's workload is reduced — it focuses on style (word count, prohibited phrases, jargon) rather than factual grounding.

---

## Business-Cost Derivation for the Target Failure Mode

### Baseline cost without mechanism

From the probe library (v1.1), the four signal over-claiming probes (P006–P009) have combined expected cost:

```
P006 (aggressive hiring):  0.28 × 0.25 × $120,000 = $8,400
P007 (funding assertive):  0.21 × 0.35 × $120,000 = $8,820
P008 (AI maturity):        0.24 × 0.30 × $120,000 = $8,640
P009 (fabricated count):   0.14 × 0.50 × $120,000 = $8,400
                                                    --------
Total expected loss:                                $34,260 / batch
```

Assumptions:
- ACV_TALENT_MID = $120,000 (3-engineer × 12-month engagement; Tenacious internal, `seed/baseline_numbers.md`)
- Trigger rates measured from dev-slice sampling (April 24 2026)
- Failure impact fractions represent the estimated reduction in close probability when the claim is verifiably false or over-stated

### Expected cost with the 3-stage chain mechanism

The Researcher Agent's structural separation of fact extraction from language generation eliminates the root cause of P006, P007, P008, and P009. Residual failure probability (LLM hallucination leaking through the ResearchSummary) is estimated at 3–5% of current trigger rates.

```
Residual P006:  0.28 × 0.04 × 0.25 × $120,000 = $336
Residual P007:  0.21 × 0.04 × 0.35 × $120,000 = $353
Residual P008:  0.24 × 0.04 × 0.30 × $120,000 = $346
Residual P009:  0.14 × 0.04 × 0.50 × $120,000 = $336
                                                 -------
Total residual loss:                             $1,371 / batch
```

**Expected cost reduction:** $34,260 − $1,371 = **$32,889 per batch** (~96% reduction)

### Additional mechanism cost

Each outbound message now requires 3 LLM calls instead of 0 (template-based). At $0.04/call (OpenRouter Qwen3 mid-tier):

- Per-prospect mechanism overhead: 3 × $0.04 = $0.12
- Per 50-prospect batch: $6.00
- Net savings after mechanism cost: $32,889 − $6 = **$32,883 / batch**

---

## Ablation Variants (Act IV)

The following three conditions will be run on the sealed held-out slice to measure pass@1 improvement:

| Condition | Description |
|---|---|
| **Baseline** | Current `compose_outbound()` template with post-composition phrase scan |
| **Mechanism** | Full 3-stage chain (Researcher → Closer → Tone Guard) |
| **Automated-optimization** | Single LLM call with an extended system prompt containing all honesty constraints (no structural separation) |

The hypothesis is that **Mechanism > Automated-optimization > Baseline** on signal over-claiming probes, because structural separation (Mechanism) is more robust than prompt engineering alone (Automated-optimization).

Statistical test: paired t-test on pass@1 scores across 5 trials × 20 held-out tasks. Target: Δ(Mechanism − Baseline) > 0 with p < 0.05.

---

## Implementation Notes

- The 3-stage chain is the Act IV mechanism described in `CLAUDE.md` ("Planned (not yet built) Act IV mechanism").
- `compose_outbound()` in `nurture_sequencer/state_machine.py` will be replaced by `compose_outbound_chain()` which calls the 3-stage chain.
- The `ResearchSummary` Pydantic model will be added to `config/models.py`.
- LLM calls in the chain use OpenRouter (Qwen3 for dev, Claude Sonnet 4.6 for eval), consistent with the existing model tier policy.
- All three LLM calls emit Langfuse traces with required fields (model, prompt tokens, completion tokens, latency) to satisfy the observability contract.
- Cost accounting: Researcher + Closer + Tone Guard costs are summed into the per-prospect enrichment cost for `invoice_summary.json`.
