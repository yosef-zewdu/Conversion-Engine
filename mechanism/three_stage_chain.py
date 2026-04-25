"""
3-Stage Prompt Chain — Act IV mechanism for the Conversion Engine.

Replaces compose_outbound() in nurture_sequencer/state_machine.py with a
pipeline that enforces confidence gating *structurally*, not by post-composition
phrase scanning.

    Stage 1  ResearcherAgent   deterministic fact extraction + confidence gating
    Stage 2  CloserAgent       LLM draft constructed from filtered ResearchSummary only
    Stage 3  ToneGuard         rule-based tone scoring; max 2 Closer retries

Entry point: compose_outbound_chain()

Requirements: 14.1, 14.2  (Act IV mechanism)
              1.4, 3.4, 4.5, 6.1–6.3, 8.7  (existing honesty invariants)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from config.models import Prospect, ResearchField, ResearchSummary, Segment
from nurture_sequencer.outbound_message import OutboundMessage
from signal_pipeline.models import CompetitorGapBrief, HiringSignalBrief

logger = logging.getLogger(__name__)

# ── Stage 1: ResearcherAgent ──────────────────────────────────────────────────


class ResearcherAgent:
    """
    Deterministic fact extractor — converts briefs to ResearchSummary.

    Design rationale: Stage 1 is deterministic (not LLM-based) because
    confidence gating is a hard rule, not a soft preference.  Putting an LLM
    here re-introduces the exact hallucination risk the mechanism is designed
    to eliminate.  All invariants from CLAUDE.md (§Honesty Constraints) are
    enforced as code-level assertions in the individual _extract_* methods.
    """

    def run(
        self,
        brief: HiringSignalBrief,
        gap_brief: Optional[CompetitorGapBrief],
    ) -> ResearchSummary:
        return ResearchSummary(
            company_name=brief.company_name,
            funding=self._extract_funding(brief),
            hiring=self._extract_hiring(brief),
            ai_maturity=self._extract_ai_maturity(brief),
            competitor_gap=self._extract_gap(gap_brief),
            layoff=self._extract_layoff(brief),
            bench_mismatch=bool(brief.bench_mismatch),
        )

    # ── individual extractors ─────────────────────────────────────

    def _extract_funding(self, brief: HiringSignalBrief) -> ResearchField:
        evt = brief.funding_event
        if evt is None:
            return ResearchField(included=False, reason="no funding event")

        confidence = evt.confidence
        amount_str = (
            f"${evt.amount_usd / 1_000_000:.0f}M" if evt.amount_usd else "undisclosed amount"
        )
        date_str = f" in {evt.close_date}" if evt.close_date else ""

        if confidence is None or confidence == "low":
            # Req 6.1: low/null confidence → interrogative phrasing only
            return ResearchField(
                included=True,
                confidence=confidence,
                fact=f"signals suggest a {evt.round_type} round{date_str}",
                phrasing="interrogative",
            )
        if confidence == "medium":
            return ResearchField(
                included=True,
                confidence=confidence,
                fact=f"Crunchbase shows a {amount_str} {evt.round_type}{date_str}",
                phrasing="assertive",
            )
        # high
        return ResearchField(
            included=True,
            confidence=confidence,
            fact=f"closed a {amount_str} {evt.round_type}{date_str}",
            phrasing="assertive",
        )

    def _extract_hiring(self, brief: HiringSignalBrief) -> ResearchField:
        count = brief.job_post_count
        velocity = brief.job_post_velocity_60d
        confidence = brief.job_post_confidence

        # Null propagation invariant — never coerce null to zero (Req 2.7)
        if count is None:
            if confidence in ("medium", "high"):
                return ResearchField(
                    included=True,
                    confidence=confidence,
                    fact="active engineering hiring (exact count unavailable)",
                    phrasing="assertive",
                )
            return ResearchField(
                included=False,
                reason="null job_post_count and confidence below threshold",
            )

        if confidence in ("medium", "high"):
            # Aggressive hiring gate — BOTH thresholds required (Req 6.3 / P006)
            if count >= 5 and velocity is not None and velocity >= 3.0:
                fact = (
                    f"{count} open engineering roles with strong hiring velocity "
                    f"({velocity:.1f}/60d)"
                )
            else:
                fact = f"{count} open engineering role{'s' if count != 1 else ''}"
            return ResearchField(
                included=True, confidence=confidence, fact=fact, phrasing="assertive"
            )
        # low/null → interrogative (Req 6.2)
        return ResearchField(
            included=True,
            confidence=confidence,
            fact=f"signals suggest around {count} open engineering roles",
            phrasing="interrogative",
        )

    def _extract_ai_maturity(self, brief: HiringSignalBrief) -> ResearchField:
        score = brief.ai_maturity_score
        confidence = brief.ai_maturity_confidence

        if score is None:
            return ResearchField(included=False, reason="null ai_maturity_score")
        if confidence is None:
            return ResearchField(included=False, reason="null ai_maturity_confidence")

        if confidence == "low":
            # Req 3.4: low confidence → interrogative phrasing (structural, not instructed)
            return ResearchField(
                included=True,
                confidence=confidence,
                fact=f"signals suggest AI investment (score={score}/3)",
                phrasing="interrogative",
            )
        # medium or high
        framing = "scale your AI team" if score >= 2 else "stand up your first AI function"
        return ResearchField(
            included=True,
            confidence=confidence,
            fact=f"AI maturity {score}/3 — framing: '{framing}'",
            phrasing="assertive",
        )

    def _extract_gap(
        self, gap_brief: Optional[CompetitorGapBrief]
    ) -> ResearchField:
        if gap_brief is None:
            return ResearchField(included=False, reason="no gap brief")
        eligible = [g for g in gap_brief.gaps if g.confidence in ("medium", "high")]
        if not eligible:
            return ResearchField(
                included=False, reason="no gaps with medium/high confidence (Req 4.5)"
            )
        eligible.sort(key=lambda g: 0 if g.confidence == "high" else 1)
        top = eligible[0]
        return ResearchField(
            included=True,
            confidence=top.confidence,
            fact=f"sector peers show strong signal for {top.practice}",
            phrasing="assertive",
        )

    def _extract_layoff(self, brief: HiringSignalBrief) -> ResearchField:
        evt = brief.layoff_event
        if evt is None:
            return ResearchField(included=False, reason="no layoff event")
        confidence = evt.confidence
        # No fabrication: use headcount only when actually available (Req P009)
        if evt.headcount_affected is not None:
            fact = f"{evt.headcount_affected} headcount reduction"
        elif evt.percentage_cut is not None:
            fact = f"{evt.percentage_cut:.0f}% headcount reduction"
        else:
            fact = "recent headcount reduction (scale unknown)"
        phrasing = "assertive" if confidence in ("medium", "high") else "interrogative"
        return ResearchField(
            included=True, confidence=confidence, fact=fact, phrasing=phrasing
        )


# ── Stage 2: CloserAgent ──────────────────────────────────────────────────────

_CLOSER_SYSTEM = """\
You are a B2B outreach writer for Tenacious Consulting and Outsourcing.

CRITICAL RULE: Use ONLY the signals listed in the ResearchSummary below.
Do not reference any fact from the raw brief that does not appear in the ResearchSummary.
Fields marked OMIT must not appear in your output.

Style rules (non-negotiable):
- Cold email ≤ 120 words. One call-to-action only.
- Subject line must start with one of: Request:, Follow-up:, Context:, Question:
- No prohibited phrases: world-class, best-in-class, synergy, leverage (verb), ecosystem,
  bench, aggressive hiring (unless fact explicitly states velocity ≥ 3.0/60d), just following up,
  i hope this finds you well, touching base, circle back
- phrasing=interrogative → use "we noticed signals suggesting … — is that right?" form
- phrasing=assertive → state the fact directly with attribution matching confidence level
- bench_mismatch=True → do not promise specific headcount or timelines
- End with exactly one question proposing a 20-minute discovery call
"""


class CloserAgent:
    """
    Stage 2: LLM draft generated from filtered ResearchSummary only.

    The Closer never receives the raw HiringSignalBrief — only the ResearchSummary
    produced by Stage 1.  This structural boundary is the mechanism's core guarantee:
    the Closer cannot assert a low-confidence funding event assertively because
    the Researcher either excluded it or marked it phrasing=interrogative.

    Falls back to a deterministic template when OPENROUTER_API_KEY is absent
    (keeps tests and CI fast without burning API credits).
    """

    def __init__(self) -> None:
        self._api_key = os.environ.get("OPENROUTER_API_KEY", "")
        self._model = os.environ.get("OPENROUTER_MODEL", "qwen/qwen3-235b-a22b")

    def run(
        self,
        summary: ResearchSummary,
        prospect: Prospect,
        channel: str,
    ) -> str:
        if self._api_key:
            try:
                return self._llm_draft(summary, prospect, channel)
            except Exception as exc:
                logger.warning("CloserAgent LLM call failed (%s); using fallback", exc)
        return self._fallback_draft(summary, prospect, channel)

    def _llm_draft(
        self, summary: ResearchSummary, prospect: Prospect, channel: str
    ) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model=self._model,
            openai_api_key=self._api_key,
            openai_api_base="https://openrouter.ai/api/v1",
            temperature=0.4,
        )
        user_msg = (
            f"Write a {channel} outreach message for {prospect.contact_name} "
            f"at {summary.company_name}.\n\n"
            f"ResearchSummary:\n{summary.to_prompt_block()}"
        )
        response = llm.invoke(
            [SystemMessage(content=_CLOSER_SYSTEM), HumanMessage(content=user_msg)]
        )
        return str(response.content).strip()

    def _fallback_draft(
        self, summary: ResearchSummary, prospect: Prospect, channel: str
    ) -> str:
        """Deterministic template that respects the ResearchSummary gating."""
        parts: list[str] = [f"Hi {prospect.contact_name},", ""]

        # All included signals — emit each one (not elif)
        any_signal = False
        if summary.hiring.included and summary.hiring.fact:
            any_signal = True
            if summary.hiring.phrasing == "assertive":
                parts.append(f"We see {summary.hiring.fact}.")
            else:
                parts.append(f"We noticed {summary.hiring.fact} — is that right?")

        if summary.funding.included and summary.funding.fact:
            any_signal = True
            if summary.funding.phrasing == "assertive":
                parts.append(f"{summary.funding.fact}.")
            else:
                parts.append(
                    f"We noticed {summary.funding.fact} — if accurate, "
                    "this is often a strong time to scale engineering capacity."
                )

        if summary.layoff.included and summary.layoff.fact:
            any_signal = True
            if summary.layoff.phrasing == "assertive":
                parts.append(
                    f"Given the recent {summary.layoff.fact}, many teams look to "
                    "optimise engineering costs without losing momentum."
                )
            else:
                parts.append(
                    f"We noticed signals suggesting {summary.layoff.fact} — "
                    "is that right?"
                )

        if not any_signal:
            parts.append("We've been following your company's growth.")

        parts.append("")

        # AI maturity framing
        if summary.ai_maturity.included and summary.ai_maturity.fact:
            if summary.ai_maturity.phrasing == "assertive":
                framing = (
                    "scale your AI team"
                    if "scale" in summary.ai_maturity.fact
                    else "stand up your first AI function"
                )
                parts.append(f"Tenacious can help you {framing}.")
            else:
                parts.append(
                    "We noticed signals suggesting your team may be investing in AI — "
                    "if so, Tenacious can help you build that out."
                )
        else:
            parts.append("Tenacious can help you build out your engineering team.")

        # Competitor gap (optional)
        if summary.competitor_gap.included and summary.competitor_gap.fact:
            parts += ["", f"{summary.competitor_gap.fact}."]

        # Capacity caveat
        if summary.bench_mismatch:
            parts += ["", "We'd want to confirm resource availability on a call before any commitment."]

        parts += [
            "",
            "Would you be open to a 20-minute call to explore fit?",
            "",
            "Best,",
            "Tenacious Team",
        ]
        return "\n".join(parts)


# ── Stage 3: ToneGuard ────────────────────────────────────────────────────────

_PROHIBITED = [
    "i hope this finds you well",
    "just following up",
    "touching base",
    "circle back",
    "circling back",
    "move the needle",
    "world-class",
    "best-in-class",
    "cutting-edge",
    "synergy",
    "synergies",
    "leverage",
    "ecosystem",
    "bench",
]

_SUBJECT_PREFIXES = ("request:", "follow-up:", "context:", "question:")
_COLD_WORD_LIMIT = 120
_WARM_WORD_LIMIT = 200


@dataclass
class ToneScore:
    total: int
    violations: list[str] = field(default_factory=list)

    @property
    def passes(self) -> bool:
        return self.total >= 70


class ToneGuard:
    """
    Stage 3: Rule-based tone scorer against style_guide.md markers.

    Hyperparameters (documented for ablation):
    - score_floor: 0
    - points_per_violation: 15
    - pass_threshold: 70
    - cold_word_limit: 120  (seed/style_guide.md — stricter than agent/style_guide.md 150)
    - max_retries: 2  (set at compose_outbound_chain call site)
    """

    POINTS_PER_VIOLATION = 15
    PASS_THRESHOLD = 70

    def check(
        self,
        draft: str,
        subject: Optional[str],
        channel: str,
    ) -> ToneScore:
        violations: list[str] = []
        lower = draft.lower()

        for phrase in _PROHIBITED:
            if phrase in lower:
                violations.append(f"prohibited phrase: '{phrase}'")

        if subject:
            subj_lower = subject.lower()
            if not subj_lower.startswith(_SUBJECT_PREFIXES):
                violations.append(
                    "subject must start with Request/Follow-up/Context/Question"
                )
            for bad in ("quick", "just", "hey", "dear"):
                if subj_lower.startswith(bad):
                    violations.append(f"subject starts with prohibited opener '{bad}'")

        if channel == "email":
            wc = len(draft.split())
            if wc > _COLD_WORD_LIMIT:
                violations.append(f"word count {wc} exceeds {_COLD_WORD_LIMIT}")

        total = max(0, 100 - len(violations) * self.POINTS_PER_VIOLATION)
        return ToneScore(total=total, violations=violations)


# ── Entry point ───────────────────────────────────────────────────────────────


def compose_outbound_chain(
    prospect: Prospect,
    brief: HiringSignalBrief,
    gap_brief: Optional[CompetitorGapBrief],
    channel: str,
    max_retries: int = 2,
) -> OutboundMessage:
    """
    3-stage entry point replacing compose_outbound() in state_machine.py.

    Stage 1 (Researcher) → Stage 2 (Closer) → Stage 3 (ToneGuard + retries).

    The Closer only receives the filtered ResearchSummary, structurally preventing
    it from asserting low-confidence facts that the Researcher excluded or tagged
    as interrogative.

    Args:
        prospect: Prospect dataclass.
        brief: Enriched HiringSignalBrief from signal pipeline.
        gap_brief: CompetitorGapBrief or None.
        channel: "email" | "sms".
        max_retries: ToneGuard regeneration ceiling (default 2).

    Returns:
        OutboundMessage with draft=True and mechanism metadata.
    """
    # ── Stage 1 ───────────────────────────────────────────────────
    researcher = ResearcherAgent()
    summary = researcher.run(brief, gap_brief)

    # ── Stage 2 + 3 loop ──────────────────────────────────────────
    closer = CloserAgent()
    tone_guard = ToneGuard()

    draft_content = ""
    subject: Optional[str] = None
    tone_result = ToneScore(total=0)
    attempts = 0

    while attempts <= max_retries:
        draft_content = closer.run(summary, prospect, channel)
        if channel == "email":
            subject = f"Context: engineering hiring signal at {brief.company_name}"
        tone_result = tone_guard.check(draft_content, subject, channel)
        if tone_result.passes:
            break
        attempts += 1
        logger.info(
            "ToneGuard retry %d/%d for %s — score=%d violations=%s",
            attempts,
            max_retries,
            prospect.prospect_id,
            tone_result.total,
            tone_result.violations,
        )

    return OutboundMessage(
        prospect_id=prospect.prospect_id,
        channel=channel,
        content=draft_content,
        subject=subject,
        draft=True,  # ALWAYS True — Req 1.4
        sent_at=datetime.now(timezone.utc).isoformat(),
        metadata={
            "draft": True,
            "mechanism": "3-stage-chain",
            "tone_score": tone_result.total,
            "tone_violations": tone_result.violations,
            "stage1_included": {
                k: getattr(summary, k).included
                for k in ("funding", "hiring", "ai_maturity", "competitor_gap", "layoff")
            },
        },
    )
