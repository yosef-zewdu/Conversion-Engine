"""
End-to-end pipeline runner for the Conversion Engine.

Wires Signal Pipeline → ICP Classifier → Bench-to-Brief Match →
Nurture Sequencer → Kill Switch → Staff Sink / Prospect → CRM Writer
for a single prospect.

Usage::

    runner = PipelineRunner()
    result = await runner.run(prospect)

Requirements: 2.1–2.8, 5.1–5.7, 7.1–7.3, 8.8, 10.1–10.3, 11.1–11.2
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from config.kill_switch import get_outbound_destination
from config.models import Destination, Prospect, Segment
from crm_writer.writer import CRMWriter
from icp_classifier.bench_match import BenchToBriefMatch
from icp_classifier.classifier import ClassifierConfig, classify
from nurture_sequencer.outbound_message import OutboundMessage
from nurture_sequencer.state_machine import ProspectFSM
from signal_pipeline.ai_maturity_scorer import AIMaturityInput, AIMaturityScorer
from signal_pipeline.assembler import validate_competitor_gap_brief, validate_hiring_signal_brief
from signal_pipeline.competitor_gap_builder import CompetitorGapBuilder
from signal_pipeline.firmographic_enricher import FirmographicEnricher
from signal_pipeline.funding_event_fetcher import FundingEventFetcher
from signal_pipeline.hiring_signal_brief_assembler import (
    AssemblerInput,
    BriefValidationError,
    HiringSignalBriefAssembler,
)
from signal_pipeline.job_post_scraper import JobPostScraper
from signal_pipeline.layoff_scanner import LayoffScanner
from signal_pipeline.leadership_change_detector import LeadershipChangeDetector
from signal_pipeline.models import CompetitorGapBrief, HiringSignalBrief

logger = logging.getLogger(__name__)

_ML_STACK_KEYWORDS: frozenset[str] = frozenset(
    ["pytorch", "tensorflow", "spark", "kafka", "dbt", "airflow"]
)


@dataclass
class PipelineResult:
    """Result of a full pipeline run for one prospect.

    Args:
        prospect_id: UUID of the processed prospect.
        hiring_signal_brief: Validated HiringSignalBrief, or None on failure.
        competitor_gap_brief: Validated CompetitorGapBrief, or None.
        segment: Assigned ICP segment.
        bench_mismatch: True when no bench engineers match the prospect stack.
        outbound_message: The first outbound message, or None if kill-switched.
        destination: Where the outbound was routed (STAFF_SINK or PROSPECT).
        crm_contact_id: HubSpot contact ID, or None on CRM failure.
        error: Error message if the pipeline failed, else None.
    """

    prospect_id: str
    hiring_signal_brief: Optional[HiringSignalBrief] = None
    competitor_gap_brief: Optional[CompetitorGapBrief] = None
    segment: Optional[Segment] = None
    bench_mismatch: bool = False
    outbound_message: Optional[OutboundMessage] = None
    destination: Optional[Destination] = None
    crm_contact_id: Optional[str] = None
    error: Optional[str] = None


class PipelineRunner:
    """Orchestrates the full Conversion Engine pipeline for a single prospect.

    Instantiates all sub-components once and reuses them across calls.

    Args:
        crm_writer: CRMWriter instance. Creates a new one when None.
    """

    def __init__(
        self,
        crm_writer: Optional[CRMWriter] = None,
    ) -> None:
        self._crm = crm_writer or CRMWriter()

        # Signal pipeline sub-components
        self._firmographic = FirmographicEnricher()
        self._layoff = LayoffScanner()
        self._job_posts = JobPostScraper()
        self._leadership = LeadershipChangeDetector()
        self._funding = FundingEventFetcher()
        self._ai_scorer = AIMaturityScorer()
        self._gap_builder = CompetitorGapBuilder(
            enricher=self._firmographic,
            scorer=self._ai_scorer,
        )
        self._assembler = HiringSignalBriefAssembler()
        self._bench_match = BenchToBriefMatch()

    async def run(self, prospect: Prospect) -> PipelineResult:
        """Run the full pipeline for a single prospect.

        Steps:
        1. Signal Pipeline enrichment → HiringSignalBrief + CompetitorGapBrief
        2. Schema validation (blocks writes on failure)
        3. ICP classification → SegmentResult
        4. Bench-to-Brief match → bench_mismatch flag
        5. Update prospect with segment + bench data
        6. CRM upsert (bypasses Kill Switch — internal record-keeping)
        7. Write briefs to CRM
        8. Nurture Sequencer → first outbound message
        9. Kill Switch routing → STAFF_SINK or PROSPECT
        10. Log outbound activity to CRM

        Args:
            prospect: The Prospect to process.

        Returns:
            PipelineResult with all outputs and any error details.
        """
        result = PipelineResult(prospect_id=prospect.prospect_id)

        # ------------------------------------------------------------------
        # 1. Signal Pipeline enrichment
        # ------------------------------------------------------------------
        try:
            brief, gap_brief = await self._enrich(prospect)
        except BriefValidationError as exc:
            logger.error(
                "Brief validation failed for prospect_id=%s: %s",
                prospect.prospect_id,
                exc,
            )
            result.error = f"validation_failed: {exc}"
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Enrichment failed for prospect_id=%s: %s",
                prospect.prospect_id,
                exc,
            )
            result.error = f"enrichment_failed: {exc}"
            return result

        result.hiring_signal_brief = brief
        result.competitor_gap_brief = gap_brief

        # ------------------------------------------------------------------
        # 2. ICP classification
        # ------------------------------------------------------------------
        # Wire headcount and open-role count into ClassifierConfig so the
        # classifier can enforce headcount and role-count gates (S1/S2/S3).
        _open_roles: Optional[int] = None
        if brief.hiring_velocity is not None:
            _open_roles = brief.hiring_velocity.open_roles_today
        elif brief.job_post_count is not None:
            _open_roles = brief.job_post_count
        classifier_config = ClassifierConfig(
            employee_min=brief.employee_count_min,
            employee_max=brief.employee_count_max,
            open_roles=_open_roles,
        )
        segment_result = classify(brief, classifier_config)
        prospect.segment = segment_result.segment

        # Write ICP result back into the brief fields
        brief.icp_segment = segment_result.segment.value  # type: ignore[assignment]
        brief.icp_confidence = segment_result.confidence
        brief.icp_signals_used = segment_result.signals_used

        result.segment = segment_result.segment
        logger.info(
            "ICP classification: prospect_id=%s segment=%s confidence=%.2f abstained=%s",
            prospect.prospect_id,
            segment_result.segment.value,
            segment_result.confidence,
            segment_result.abstained,
        )

        # ------------------------------------------------------------------
        # 3. Bench-to-Brief match
        # ------------------------------------------------------------------
        bench_result = self._bench_match.match(brief.tech_stack)
        brief.bench_mismatch = bench_result.bench_mismatch
        brief.bench_summary_version = bench_result.bench_summary_version
        result.bench_mismatch = bench_result.bench_mismatch
        logger.info(
            "Bench match: prospect_id=%s bench_mismatch=%s matched=%s",
            prospect.prospect_id,
            bench_result.bench_mismatch,
            bench_result.matched_stacks,
        )

        # ------------------------------------------------------------------
        # 4. CRM upsert — internal record-keeping, bypasses Kill Switch
        # ------------------------------------------------------------------
        try:
            contact_id = await self._crm.upsert_contact(prospect)
            result.crm_contact_id = contact_id
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "CRM upsert failed for prospect_id=%s: %s",
                prospect.prospect_id,
                exc,
            )

        # ------------------------------------------------------------------
        # 5. Write briefs to CRM
        # ------------------------------------------------------------------
        try:
            await self._crm.write_brief(brief, hs_contact_id=result.crm_contact_id or "")
            if gap_brief is not None:
                await self._crm.write_brief(gap_brief, hs_contact_id=result.crm_contact_id or "")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "CRM brief write failed for prospect_id=%s: %s",
                prospect.prospect_id,
                exc,
            )

        # ------------------------------------------------------------------
        # 6. Nurture Sequencer — compose first outbound message
        # ------------------------------------------------------------------
        fsm = ProspectFSM(prospect)
        outbound = fsm.start_sequence(brief, gap_brief)
        result.outbound_message = outbound

        # ------------------------------------------------------------------
        # 7. Kill Switch routing
        # ------------------------------------------------------------------
        destination = get_outbound_destination()
        result.destination = destination

        if destination == Destination.STAFF_SINK:
            logger.info(
                "Kill switch: routing outbound to STAFF_SINK for prospect_id=%s",
                prospect.prospect_id,
            )
        else:
            logger.info(
                "Kill switch: routing outbound to PROSPECT for prospect_id=%s",
                prospect.prospect_id,
            )

        # ------------------------------------------------------------------
        # 8. Log outbound activity to CRM
        # ------------------------------------------------------------------
        try:
            await self._crm.log_activity({
                "type": f"outbound_{outbound.channel}",
                "prospect_id": prospect.prospect_id,
                "channel": outbound.channel,
                "timestamp": outbound.sent_at,
                "content": outbound.content,
                "direction": "outbound",
                "destination": destination.value,
                "draft": True,
            }, hs_contact_id=result.crm_contact_id or "")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "CRM outbound log failed for prospect_id=%s: %s",
                prospect.prospect_id,
                exc,
            )

        return result

    async def _enrich(
        self, prospect: Prospect
    ) -> tuple[HiringSignalBrief, Optional[CompetitorGapBrief]]:
        """Run all Signal Pipeline sub-components and assemble briefs.

        Args:
            prospect: The Prospect to enrich.

        Returns:
            Tuple of (HiringSignalBrief, CompetitorGapBrief | None).

        Raises:
            BriefValidationError: When assembled brief fails schema validation.
        """
        company_id = prospect.company_id
        # Prefer a stored display name; fall back to company_id only as last resort
        company_name = getattr(prospect, "company_name", None) or prospect.company_id

        # Try UUID-exact lookup first, then fuzzy-by-name
        firmographic = self._firmographic.enrich_by_id(company_id)
        if firmographic.company_name is None and company_name != company_id:
            firmographic = self._firmographic.enrich_by_name(company_name)
        # Use the enriched display name when available
        if firmographic.company_name:
            company_name = firmographic.company_name

        layoff = self._layoff.most_recent(company_name)
        # Pass the real company name and homepage URL — not the UUID
        job_posts = await self._job_posts.scrape(
            company_name, firmographic.website
        )
        leadership = self._leadership.detect(firmographic.raw_leadership_hire)
        funding = self._funding.fetch(firmographic.raw_funding_rounds)

        # Build AI maturity input
        ai_adjacent_open_roles: Optional[int] = None
        
        # API FALLBACK: If web scraping fails to get job counts, ask LLM to estimate based on world knowledge
        if job_posts is not None and job_posts.job_post_count is None:
            try:
                import json
                from openai import AsyncOpenAI

                client = AsyncOpenAI(
                    api_key=os.environ.get("OPENROUTER_API_KEY", ""),
                    base_url="https://openrouter.ai/api/v1",
                )

                prompt = (
                    f"Estimate the AI maturity for the company '{company_name}'. "
                    f"Return ONLY a JSON object with two keys: "
                    f"'job_post_count' (integer 0-20 estimating open AI/ML engineering roles) and "
                    f"'tech_signals' (list of strings representing their likely ML stack, e.g. ['python', 'pytorch', 'aws']). "
                    f"If the company is notoriously an AI or Data company, return high numbers. If traditional, return 0."
                )

                resp = await client.chat.completions.create(
                    model=os.environ.get("OPENROUTER_MODEL", "qwen/qwen3-235b-a22b"),
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                )

                if resp.choices and resp.choices[0].message.content:
                    data = json.loads(resp.choices[0].message.content)
                    est_count = data.get("job_post_count", 0)
                    if est_count > 0:
                        job_posts.job_post_count = est_count
                        job_posts.tech_signals = data.get("tech_signals", [])
                        job_posts.sources_checked.append("llm_inference_api")
            except Exception as exc:
                logger.warning("LLM API fallback failed for AI maturity on %s: %s", company_name, exc)

        if job_posts is not None and job_posts.job_post_count is not None:
            ai_adjacent_open_roles = min(job_posts.job_post_count, 5)

        named_ai_ml_leadership: Optional[bool] = (
            leadership.detected if leadership is not None else None
        )

        modern_data_ml_stack: Optional[bool] = None
        # Combine tech signals from both scraper and firmographic BuiltWith data
        all_tech = set()
        if job_posts is not None and job_posts.tech_signals:
            all_tech.update(t.lower() for t in job_posts.tech_signals)
        if firmographic.builtwith_tech:
            all_tech.update(t.lower() for t in firmographic.builtwith_tech)
            
        if all_tech & _ML_STACK_KEYWORDS:
            modern_data_ml_stack = True

        ai_input = AIMaturityInput(
            ai_adjacent_open_roles=ai_adjacent_open_roles,
            named_ai_ml_leadership=named_ai_ml_leadership,
            github_ai_activity=None,
            executive_ai_commentary=None,
            modern_data_ml_stack=modern_data_ml_stack,
            strategic_communications=None,
        )
        ai_maturity = self._ai_scorer.score(ai_input)

        competitor_gap = self._gap_builder.build(
            prospect_company_id=firmographic.crunchbase_id or company_id,
            prospect_company_name=company_name,
            prospect_sectors=firmographic.sectors,
            prospect_funding_stage=firmographic.funding_stage,
            prospect_ai_maturity_score=ai_maturity.score,
            prospect_tech_stack=firmographic.builtwith_tech,
        )

        assembler_input = AssemblerInput(
            company_id=company_id,
            company_name=company_name,
            firmographic=firmographic,
            layoff=layoff,
            job_posts=job_posts,
            leadership=leadership,
            funding=funding,
            ai_maturity=ai_maturity,
            competitor_gap=competitor_gap,
        )
        brief, gap_brief = self._assembler.assemble(assembler_input)

        # Validate via assembler (blocks writes on failure per standards)
        validate_hiring_signal_brief(brief.model_dump(mode="json"))
        if gap_brief is not None:
            validate_competitor_gap_brief(gap_brief.model_dump(mode="json"))

        return brief, gap_brief
