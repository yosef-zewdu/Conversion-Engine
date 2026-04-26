"""
Generates memo/memo.pdf — 2-page decision memo for TRP1 Week 10.

All dollar figures are sourced from:
  seed/baseline_numbers.md   (Tenacious internal)
  seed/bench_summary.json    (Tenacious internal, as of 2026-04-21)
  eval/score_log.json        (measured)
  mechanism/ablation_results.json  (measured)
  probes/probe_library.md    (measured trigger rates, estimated costs)

ACV placeholders remain as $[ACV_*] because seed/baseline_numbers.md
explicitly prohibits citing the placeholder values until resolved.
The probe business-cost formula (trigger_rate × impact_fraction × ACV_reference)
uses ACV_TALENT_MID=$120,000 and ACV_PROJECT_MID=$45,000 as structural
estimates; those are used to reproduce the probe_library numbers only —
they are not cited as Tenacious internal figures in the memo text.
"""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

OUTPUT = Path(__file__).parent / "memo.pdf"

# ── palette ──────────────────────────────────────────────────────────────────
DARK   = colors.HexColor("#1a1a2e")
ACCENT = colors.HexColor("#0f3460")
MUTED  = colors.HexColor("#4a4a6a")
LIGHT  = colors.HexColor("#eef2ff")
GREEN  = colors.HexColor("#dcfce7")
AMBER  = colors.HexColor("#fef9c3")
RED_BG = colors.HexColor("#fee2e2")


def _make_styles():
    base = getSampleStyleSheet()["Normal"]

    def S(name, **kw):
        return ParagraphStyle(name, parent=base, **kw)

    return dict(
        TI=S("title",   fontName="Helvetica-Bold",  fontSize=12, textColor=DARK,
             spaceAfter=1, leading=14),
        SB=S("sub",     fontName="Helvetica",        fontSize=7, textColor=MUTED,
             spaceAfter=2),
        H1=S("h1",      fontName="Helvetica-Bold",  fontSize=8.8, textColor=ACCENT,
             spaceBefore=4, spaceAfter=1, leading=11),
        H2=S("h2",      fontName="Helvetica-Bold",  fontSize=8, textColor=DARK,
             spaceBefore=2, spaceAfter=1, leading=10),
        BD=S("body",    fontName="Helvetica",        fontSize=7.4, textColor=DARK,
             spaceAfter=2, leading=9.6, alignment=TA_JUSTIFY),
        BU=S("bullet",  fontName="Helvetica",        fontSize=7.4, textColor=DARK,
             spaceAfter=1, leading=9.4, leftIndent=10, firstLineIndent=-8),
        CA=S("caption", fontName="Helvetica-Oblique",fontSize=6.5, textColor=MUTED,
             spaceAfter=2, alignment=TA_CENTER),
        EM=S("em",      fontName="Helvetica-Bold",  fontSize=7.4, textColor=DARK,
             spaceAfter=2, leading=9.6, alignment=TA_JUSTIFY),
        BOX=S("box",    fontName="Helvetica",        fontSize=7.4, textColor=DARK,
              spaceAfter=1, leading=9.6, leftIndent=6, rightIndent=6,
              alignment=TA_JUSTIFY),
    )


_BASE_TS = [
    ("BACKGROUND",    (0, 0), (-1, 0), ACCENT),
    ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
    ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTSIZE",      (0, 0), (-1, -1), 6.8),
    ("ROWBACKGROUNDS",(0, 1), (-1, -1), [LIGHT, colors.white]),
    ("GRID",          (0, 0), (-1, -1), 0.35, colors.HexColor("#d0d0e0")),
    ("LEFTPADDING",   (0, 0), (-1, -1), 3),
    ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
    ("TOPPADDING",    (0, 0), (-1, -1), 1),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
]


def tbl(data, col_widths, extra=None):
    t = Table(data, colWidths=col_widths)
    style = list(_BASE_TS)
    if extra:
        style.extend(extra)
    t.setStyle(TableStyle(style))
    return t


def build():
    doc = SimpleDocTemplate(
        str(OUTPUT), pagesize=LETTER,
        leftMargin=0.65*inch, rightMargin=0.65*inch,
        topMargin=0.5*inch,   bottomMargin=0.5*inch,
    )
    s = _make_styles()
    story = []

    # ── HEADER ───────────────────────────────────────────────────────────────
    story.append(Paragraph("Conversion Engine — Decision Memo", s["TI"]))
    story.append(Paragraph(
        "TRP1 Week 10 · Tenacious Consulting and Outsourcing · 2026-04-25 · "
        "yosefz@10academy.org · Evidence: evidence_graph/evidence_graph.json",
        s["SB"]))
    story.append(HRFlowable(width="100%", thickness=1.5, color=ACCENT, spaceAfter=5))

    # ── EXECUTIVE SUMMARY (boxed) ─────────────────────────────────────────────
    story.append(Paragraph("Executive Summary", s["H1"]))

    # Box via a single-cell table
    summary_text = (
        "We built a fully automated B2B outreach system for Tenacious that qualifies "
        "leads against a four-segment ICP, composes signal-grounded email and SMS without "
        "human intervention, and books Cal.com discovery calls — achieving <b>72.67 % "
        "pass@1</b> on τ²-Bench (vs. the leaderboard ceiling of ~42 % for voice agents; "
        "source: seed/baseline_numbers.md) and reducing honesty violations to zero on the "
        "20-case held-out probe set (mechanism pass@1: <b>95 %</b>, baseline: 80 %, "
        "p = 0.033). "
        "<b>Recommendation: run a 30-day Segment 2 pilot targeting 100 leads/week "
        "sourced from layoffs.fyi + Crunchbase ODM, with a $800/week LLM budget "
        "(Qwen3 dev tier), success criterion ≥ 12 % reply rate on signal-grounded "
        "cold email within 30 days.</b>"
    )
    box_table = Table([[Paragraph(summary_text, s["BOX"])]], colWidths=[6.56*inch])
    box_table.setStyle(TableStyle([
        ("BOX",           (0, 0), (-1, -1), 1.0, ACCENT),
        ("BACKGROUND",    (0, 0), (-1, -1), LIGHT),
        ("LEFTPADDING",   (0, 0), (-1, -1), 7),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 7),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(box_table)
    story.append(Spacer(1, 4))

    # ── SECTION 1: BASELINE ───────────────────────────────────────────────────
    story.append(Paragraph("1. Act I — τ²-Bench Baseline  (eval/score_log.json · commit d11a970)", s["H1"]))
    t1 = tbl(
        [["Metric", "Value", "95 % CI / Notes"],
         ["pass@1",          "0.7267", "[0.6504, 0.7917]"],
         ["Simulations",     "150",    "30 tasks × 5 trials, retail domain"],
         ["Infra errors",    "0",      "Harness production-stable"],
         ["p50 / p95 latency", "105.95 s / 551.65 s", "Dominated by τ²-Bench simulator I/O"],
         ["Avg cost / run",  "$0.0199","Well under $5 / lead challenge envelope"]],
        [1.45*inch, 1.7*inch, 3.41*inch])
    story.append(t1)
    story.append(Paragraph(
        "Table 1. Source: eval/score_log.json, eval/latency_report.json. "
        "Voice-agent leaderboard ceiling ~42 % (seed/baseline_numbers.md); our task-automation "
        "pass@1 of 72.67 % substantially exceeds that reference ceiling.", s["CA"]))

    # ── SECTION 2: COST PER QUALIFIED LEAD ───────────────────────────────────
    story.append(Paragraph("2. Cost per Qualified Lead", s["H1"]))
    story.append(Paragraph(
        "<b>Definition of 'qualified lead':</b> a prospect that (a) passes ICP classification "
        "with confidence ≥ 0.6, and (b) receives at least one signal-grounded outbound message "
        "without triggering a policy-gate block (bench mismatch, kill-switch, or abstention). "
        "This mirrors the challenge cost-envelope definition (seed/baseline_numbers.md: "
        "'target cost per qualified lead: under $[TARGET_CPL]').", s["BD"]))

    _bd = s["BD"]
    t2 = tbl(
        [["Cost component", "Per-run estimate", "Basis"],
         ["LLM (enrichment + graph)",   "$0.0199",    "Measured: eval/score_log.json avg_agent_cost"],
         ["Signal enrichment APIs",     "$0.00",       "Crunchbase ODM local; layoffs.fyi CSV local"],
         ["Outbound APIs (Resend/AT)",  "$0.00 (dev)", "Free-tier / AT sandbox; prod costs < $0.002/msg"],
         ["Total per contacted lead",   "~$0.02",      "Sum above"],
         ["ICP qualification rate",     "~40 %",       "Estimated from 50-run dev-slice sampling sweep"],
         [Paragraph("<b>Cost per qualified lead</b>", _bd),
          Paragraph("<b>~$0.05</b>", _bd),
          Paragraph("<b>$0.02 ÷ 0.40; challenge envelope: $[TARGET_CPL]</b>", _bd)]],
        [1.9*inch, 1.4*inch, 3.26*inch],
        extra=[
            ("BACKGROUND", (0, 6), (-1, 6), GREEN),
        ])
    story.append(t2)
    story.append(Paragraph(
        "Table 2. Manual SDR equivalent: ~$180–$400 per qualified meeting "
        "(B2B Services Industry Benchmarks, seed/baseline_numbers.md). "
        "The automated system is approximately 3,600× cheaper per qualified lead "
        "than a manual SDR process before reply-rate differences are applied.", s["CA"]))

    # ── SECTION 3: STALLED-THREAD RATE ───────────────────────────────────────
    story.append(Paragraph("3. Stalled-Thread Rate", s["H1"]))
    story.append(Paragraph(
        "<b>Definition:</b> no outbound action within 48 h of an inbound reply that has not "
        "triggered STOP/UNSUB. The FSM is event-driven — a reply event calls "
        "<tt>send_next_outbound()</tt> synchronously within the same webhook cycle, so "
        "the theoretical stalled rate is <b>0 %</b> under normal operation. "
        "Manual SDR baseline: 30–40 % of inbound replies go unactioned within 24 h "
        "(seed/baseline_numbers.md). "
        "<b>Caveat — P019 (trigger rate 35 %):</b> every Render deploy wipes the "
        "module-level <tt>_fsm_registry</tt>. Warm prospects re-enter cold and receive "
        "a duplicate first-contact email — effectively inflating the real stalled rate "
        "in continuous-deployment. Must be resolved (durable HubSpot-backed FSM) before "
        "production.", s["BD"]))

    # ── SECTION 4: REPLY-RATE DELTA ──────────────────────────────────────────
    story.append(Paragraph("4. Competitive-Gap Outbound Reply-Rate Delta", s["H1"]))
    story.append(Paragraph(
        "<b>Variant A — signal-grounded outbound:</b> CompetitorGapBrief referenced "
        "(peer_count ≥ 5, gap confidence ≥ medium); specific peer practice cited in body; "
        "interrogative framing on low-confidence claims (Researcher Stage 1 gate). "
        "<b>Variant B — generic outbound:</b> no gap reference; template pitch only.", s["BD"]))

    t3 = tbl(
        [["Variant", "Expected reply rate", "Source / basis", "N"],
         ["Signal-grounded (top quartile)", "7–12 %",
          "Clay 2025 + Smartlead 2025 case studies (seed/baseline_numbers.md)", "industry"],
         ["Generic cold outbound (industry)", "1–3 %",
          "LeadIQ 2026 + Apollo 2026 benchmarks (seed/baseline_numbers.md)", "industry"],
         ["Delta", "+4 to +9 pp", "Signal-grounded vs. generic", "—"]],
        [1.8*inch, 1.25*inch, 2.9*inch, 0.61*inch])
    story.append(t3)
    story.append(Paragraph(
        "Table 3. No live A/B data — all outreach used synthetic prospects routed to STAFF_SINK. "
        "These figures are the published industry benchmarks from seed/baseline_numbers.md; "
        "actual delta will be measured in the 30-day Segment 2 pilot. "
        "Target: ≥ 12 % reply rate on signal-grounded outreach within the pilot window.", s["CA"]))

    # ── SECTION 5: ACT III PROBES ─────────────────────────────────────────────
    story.append(Paragraph("5. Act III — Adversarial Probe Library  (probes/probe_library.md)", s["H1"]))

    t4 = tbl(
        [["Category",                  "Probes", "Agg. Cost", "Avg Trigger"],
         ["ICP misclassification",     "8",  "$55,845", "0.11"],
         ["Bench over-commitment",     "6",  "$40,680", "0.15"],
         ["Signal over-claiming",      "8",  "$34,260", "0.22"],
         ["Multi-thread state leakage","5",  "$29,760", "0.17"],
         ["Tone drift",                "5",  "$11,400", "0.19"],
         ["Channel violation",         "3",  " $9,000", "0.12"],
         ["Scheduling edge cases",     "3",  "$26,460", "0.14"],
         ["Cost pathology",            "3",  "    $70", "0.16"],
         ["Kill-switch bypass",        "2",  "∞ safety","0.04"],
         ["Data handling violation",   "1",  "∞ policy","0.02"]],
        [2.2*inch, 0.65*inch, 1.05*inch, 1.08*inch])
    story.append(t4)
    story.append(Paragraph(
        "Table 4: 41 probes across 10 categories; trigger rates from 50-run dev-slice sweep. "
        "ICP misclassification is the highest-cost category ($55,845) but requires new "
        "data-source enrichment — not addressable as a generative mechanism (see §7).",
        s["CA"]))

    # ── SECTION 6: ACT IV MECHANISM ──────────────────────────────────────────
    story.append(Paragraph("6. Act IV — 3-Stage Prompt Chain  (mechanism/three_stage_chain.py)", s["H1"]))

    for bullet in [
        ("<b>Stage 1 — Researcher (deterministic):</b> consumes HiringSignalBrief + "
         "CompetitorGapBrief; applies all honesty invariants as code (confidence gates, "
         "null-propagation, aggressive-hiring dual-threshold count ≥ 5 AND velocity ≥ 3.0, "
         "AI-maturity gate for S4). Outputs ResearchSummary — no outreach language."),
        ("<b>Stage 2 — Closer (LLM, Qwen3 dev / Sonnet 4.6 eval):</b> sees ResearchSummary "
         "only — structurally blocked from raw brief. Cannot assert low-confidence facts."),
        ("<b>Stage 3 — ToneGuard (rule-based):</b> scores against seed/style_guide.md; "
         "pass threshold 70/100; enforces 120-word cold-email limit; max 2 retries."),
    ]:
        story.append(Paragraph(f"• {bullet}", s["BU"]))

    t5 = tbl(
        [["Condition",             "pass@1",  "95 % CI",     "Constraint pass","Coverage","Cost/task"],
         ["Baseline (template)",   "80.00 %", "[72 %, 87 %]","100 %",          "80 %",   "$0.00"],
         ["Auto-opt (single LLM)", "89.00 %", "[83 %, 95 %]","94 %",           "95 %",   "$0.04"],
         ["Mechanism (3-stage)",   "95.00 %", "[90 %, 99 %]","100 %",          "95 %",   "$0.00"]],
        [1.5*inch, 0.68*inch, 1.02*inch, 1.08*inch, 0.78*inch, 0.74*inch],
        extra=[
            ("BACKGROUND", (0, 3), (-1, 3), GREEN),
            ("FONTNAME",   (0, 3), (-1, 3), "Helvetica-Bold"),
        ])
    story.append(t5)
    story.append(Paragraph(
        "Table 5: 20-case probe-based held-out set, 5 trials, seed=42. "
        "Source: mechanism/ablation_results.json. Raw traces: mechanism/held_out_traces.jsonl. "
        "Paired one-tailed t-test: mechanism vs. baseline Δ = +15 pp, t = 1.831, "
        "<b>p = 0.0335</b>; mechanism vs. auto-opt Δ = +6 pp, p = 0.047. "
        "Both significant at α = 0.05.", s["CA"]))

    # ── SECTION 7: AI MATURITY SCORING LOSSINESS ─────────────────────────────
    story.append(Paragraph("7. AI Maturity Scoring Lossiness", s["H1"]))
    story.append(Paragraph(
        "The scorer (<tt>signal_pipeline/ai_maturity_scorer.py</tt>) combines six public "
        "signals: AI/ML open roles (wt 3), named AI/ML leadership (wt 3), GitHub AI activity "
        "(wt 2), executive AI commentary (wt 2), modern ML stack (wt 1), strategic comms (wt 1). "
        "Both failure directions create material business risk:", s["BD"]))

    fp_text = (
        "<b>False positive — 'conference-talk scorer':</b> CTO gave one LLM keynote "
        "(executive_ai_commentary=True, wt 2) + 2 AI-adjacent roles "
        "(ai_adjacent_open_roles=2, contributing 1.8/3). Ratio ≈ 0.48 → score 2, "
        "S4 eligible. Agent pitches capability-gap engagement. CTO replies "
        "\"we have no MLOps at all.\" Impact: credibility loss on the highest-ACV slot "
        "before any relationship; S4 pitch implies insider knowledge the scorer fabricated. "
        "(P008, trigger rate 0.24.)"
    )
    story.append(Paragraph(f"• {fp_text}", s["BU"]))

    fn_text = (
        "<b>False negative — 'stealth AI company':</b> private GitHub monorepo, no public "
        "AI commentary, no press releases — but ships a production recommender at 50 M "
        "req/day. All six signals null or False → score 0. Agent sends a generic "
        "'stand up your first AI function' pitch to a company with mature ML infrastructure. "
        "Impact: wrong-segment pitch, no brand damage, but S4 project-consulting ACV "
        "missed with no recovery mechanism."
    )
    story.append(Paragraph(f"• {fn_text}", s["BU"]))

    story.append(Paragraph(
        "Both modes are structural: scorer accesses only public, scraped signals. "
        "Fix requires additional enrichment sources (LinkedIn private activity, "
        "direct founder interview) — not addressable by prompt tuning.", s["BD"]))

    # ── SECTION 8: UNRESOLVED FAILURE ────────────────────────────────────────
    story.append(Paragraph("8. Honest Unresolved Failure — Anti-Offshore Signal Not Checked", s["H1"]))

    unresolved = (
        "<b>Probe P036 — anti-offshore founder stance not checked "
        "(icp_misclassification, trigger rate 12 %).</b> "
        "The ICP classifier has no access to founder LinkedIn posts or podcast transcripts. "
        "A prospect whose founder publicly wrote 'we will never outsource our engineering' "
        "is classified S1 (fresh Series A, ≥ 5 open roles) and receives cold outreach. "
        "<b>The 3-stage mechanism does not resolve this</b> — it operates on "
        "HiringSignalBrief fields; the anti-offshore signal is absent from the brief. "
        "Triggering condition: any S1-qualifying prospect with a public founder "
        "anti-offshore statement in the past 24 months (LinkedIn, podcast, blog). "
        "ICP definition §1 disqualifies these prospects; the classifier cannot enforce "
        "the rule without the signal. "
        "<b>Business impact if deployed:</b> at 12 % trigger rate on S1 leads, "
        "~$11,500 per ACV-reference per 1,000 qualified S1 leads processed "
        "(P036 derivation: 0.12 × 0.80 impact fraction × ACV_TALENT_MID; "
        "probes/probe_library.md). "
        "<b>Fix:</b> LinkedIn-post enricher added to HiringSignalBrief — "
        "post-challenge roadmap item."
    )
    un_table = Table([[Paragraph(unresolved, s["BOX"])]], colWidths=[6.56*inch])
    un_table.setStyle(TableStyle([
        ("BOX",           (0, 0), (-1, -1), 1.0, colors.HexColor("#dc2626")),
        ("BACKGROUND",    (0, 0), (-1, -1), RED_BG),
        ("LEFTPADDING",   (0, 0), (-1, -1), 7),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 7),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(un_table)
    story.append(Spacer(1, 3))

    # ── SECTION 9: PILOT RECOMMENDATION ──────────────────────────────────────
    story.append(Paragraph("9. Pilot Recommendation — Segment 2, 30 Days", s["H1"]))

    pilot_text = (
        "<b>Segment:</b> S2 (mid-market 200–2,000 employees, layoff in last 120 days, "
        "≥ 3 open eng roles post-layoff). Chosen because: mechanism's 100 % constraint-pass "
        "rate directly protects the cost-discipline pitch S2 buyers respond to; S2 requires "
        "no AI-maturity scoring (no §7 exposure); $40,680 aggregate bench + signal probe cost "
        "is addressable without new enrichment. "
        "<b>Lead volume:</b> 100 leads/week from layoffs.fyi CSV + Crunchbase ODM (local, $0 marginal). "
        "<b>Weekly budget:</b> $800 (actual ~$8/week at $0.02/run × 100 × 4 interactions; "
        "$800 ceiling = 100× safety margin for enrichment API costs). "
        "<b>Success criterion:</b> ≥ 12 % reply rate on signal-grounded cold email within "
        "30 days, across all S2 prospects that received ≥ 1 outbound and did not STOP/UNSUB. "
        "12 % = top-quartile benchmark (Clay 2025 / Smartlead 2025, seed/baseline_numbers.md). "
        "<b>Abort:</b> any real-prospect contact outside STAFF_SINK, or honesty violation "
        "found in 10 % manual spot-check of drafts."
    )
    pilot_table = Table([[Paragraph(pilot_text, s["BOX"])]], colWidths=[6.56*inch])
    pilot_table.setStyle(TableStyle([
        ("BOX",           (0, 0), (-1, -1), 1.0, colors.HexColor("#16a34a")),
        ("BACKGROUND",    (0, 0), (-1, -1), GREEN),
        ("LEFTPADDING",   (0, 0), (-1, -1), 7),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 7),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(pilot_table)
    story.append(Spacer(1, 3))

    # ── FOOTER ───────────────────────────────────────────────────────────────
    story.append(Spacer(1, 3))
    story.append(HRFlowable(width="100%", thickness=0.8, color=MUTED, spaceAfter=2))
    story.append(Paragraph(
        "Evidence: evidence_graph/evidence_graph.json (v1.1 · 16 claims · 7 decisions · "
        "6 resolved failures) · Key sources: eval/score_log.json (C001–C004), "
        "mechanism/ablation_results.json (C005–C011), probes/probe_library.md (C012–C015) · "
        "Conversion Engine · TRP1 Week 10 · yosefz@10academy.org",
        s["CA"]))

    doc.build(story)
    size = OUTPUT.stat().st_size
    print(f"Written: {OUTPUT}  ({size:,} bytes, {size/1024:.1f} KB)")


if __name__ == "__main__":
    build()
