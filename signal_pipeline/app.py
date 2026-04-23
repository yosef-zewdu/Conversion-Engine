"""
FastAPI application for the Signal Pipeline.

Exposes:
  - GET  /health  — liveness probe
  - POST /enrich  — full enrichment run wiring all sub-components; emits a
                    Langfuse trace per run (Req 11.2).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from signal_pipeline.ai_maturity_scorer import AIMaturityInput, AIMaturityScorer
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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Signal Pipeline")

# ---------------------------------------------------------------------------
# Shared sub-component instances (module-level singletons)
# ---------------------------------------------------------------------------

_firmographic_enricher = FirmographicEnricher()
_layoff_scanner = LayoffScanner()
_job_post_scraper = JobPostScraper()
_leadership_detector = LeadershipChangeDetector()
_funding_fetcher = FundingEventFetcher()
_ai_scorer = AIMaturityScorer()
_competitor_gap_builder = CompetitorGapBuilder(
    enricher=_firmographic_enricher,
    scorer=_ai_scorer,
)
_assembler = HiringSignalBriefAssembler()

# ML stack keywords used to detect modern_data_ml_stack signal
_ML_STACK_KEYWORDS: frozenset[str] = frozenset(
    ["pytorch", "tensorflow", "spark", "kafka", "dbt", "airflow"]
)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class EnrichRequest(BaseModel):
    """Request body for POST /enrich."""

    company_id: str
    company_name: str
    website_url: Optional[str] = None  # passed to JobPostScraper


class EnrichResponse(BaseModel):
    """Successful response from POST /enrich."""

    hiring_signal_brief: dict           # HiringSignalBrief as JSON dict
    competitor_gap_brief: Optional[dict] = None  # CompetitorGapBrief as JSON dict, or None
    enrichment_latency_ms: float


# ---------------------------------------------------------------------------
# Langfuse helper
# ---------------------------------------------------------------------------


def _emit_langfuse_trace(
    *,
    company_id: str,
    company_name: str,
    firmographic,
    layoff,
    job_posts,
    leadership,
    funding,
    ai_maturity,
    competitor_gap,
    latency_ms: float,
) -> None:
    """
    Emit a single Langfuse trace for one enrichment run (Req 11.2).

    Records data source, result count, and latency.  Reads
    LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, and LANGFUSE_BASE_URL from
    the environment.  Logs a warning and returns without crashing when keys
    are missing or the SDK call fails.

    Args:
        company_id: Internal company identifier from the request.
        company_name: Human-readable company name from the request.
        firmographic: FirmographicResult from FirmographicEnricher.
        layoff: LayoffScanResult or None.
        job_posts: JobPostResult or None.
        leadership: LeadershipChangeResult or None.
        funding: FundingEventResult or None.
        ai_maturity: AIMaturityResult from AIMaturityScorer.
        competitor_gap: CompetitorGapBrief or None.
        latency_ms: Wall-clock enrichment latency in milliseconds.
    """
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    base_url = os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

    if not public_key or not secret_key:
        logger.warning(
            "Langfuse keys not configured — skipping trace for company_id=%r", company_id
        )
        return

    # Count non-null data sources for result_count (Req 11.2)
    result_count = sum([
        firmographic.crunchbase_id is not None,
        layoff is not None,
        job_posts is not None and job_posts.job_post_count is not None,
        leadership is not None and leadership.detected,
        funding is not None and funding.detected,
    ])

    data_sources = []
    if firmographic.crunchbase_id is not None:
        data_sources.append("crunchbase_odm")
    if layoff is not None:
        data_sources.append("layoffs_fyi")
    if job_posts is not None:
        data_sources.append("job_posts_playwright")
    if leadership is not None and leadership.detected:
        data_sources.append("leadership_crunchbase")
    if funding is not None and funding.detected:
        data_sources.append("funding_crunchbase")

    try:
        from langfuse import Langfuse  # local import to avoid hard startup dependency

        lf = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=base_url,
        )
        lf.trace(
            name="signal_pipeline.enrich",
            input={"company_id": company_id, "company_name": company_name},
            metadata={
                # Req 11.2: data source, result count, latency
                "data_sources": data_sources,
                "result_count": result_count,
                "latency_ms": latency_ms,
                "latency_seconds": latency_ms / 1000.0,
                # Additional context
                "crunchbase_found": firmographic.crunchbase_id is not None,
                "layoff_found": layoff is not None,
                "job_posts_count": job_posts.job_post_count if job_posts else None,
                "leadership_detected": leadership.detected if leadership else False,
                "funding_detected": funding.detected if funding else False,
                "ai_maturity_score": ai_maturity.score,
                "peer_count": competitor_gap.peer_count if competitor_gap else 0,
            },
            output={"status": "ok"},
        )
        lf.flush()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Langfuse trace emission failed for company_id=%r: %s", company_id, exc)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    """
    Liveness probe endpoint.

    Returns:
        JSON object with status "ok".
    """
    return {"status": "ok"}


@app.post("/enrich", response_model=EnrichResponse)
async def enrich(request: EnrichRequest) -> EnrichResponse | JSONResponse:
    """
    Run a full Signal Pipeline enrichment for the given company.

    Orchestrates all sub-components in sequence, assembles a HiringSignalBrief
    and optional CompetitorGapBrief, and emits a Langfuse trace (Req 11.2).

    Args:
        request: EnrichRequest containing company_id, company_name, and optional
                 website_url.

    Returns:
        EnrichResponse with hiring_signal_brief, competitor_gap_brief, and
        enrichment_latency_ms on success.
        HTTP 422 with {"error": "validation_failed", "detail": "..."} on
        BriefValidationError.
        HTTP 500 with {"error": "enrichment_failed", "detail": "..."} on any
        unexpected exception.
    """
    company_id = request.company_id
    company_name = request.company_name
    website_url = request.website_url

    start_time = time.monotonic()

    try:
        # 1. Firmographic enrichment
        firmographic = _firmographic_enricher.enrich_by_name(company_name)

        # 2. Layoff scan
        layoff = _layoff_scanner.most_recent(company_name)

        # 3. Job post scraping (async)
        job_posts = await _job_post_scraper.scrape(company_name, website_url)

        # 4. Leadership change detection
        leadership = _leadership_detector.detect(firmographic.raw_leadership_hire)

        # 5. Funding event fetch
        funding = _funding_fetcher.fetch(firmographic.raw_funding_rounds)

        # 6. Build AIMaturityInput from available signals
        ai_adjacent_open_roles: Optional[int] = None
        if job_posts is not None and job_posts.job_post_count is not None:
            ai_adjacent_open_roles = min(job_posts.job_post_count, 5)

        named_ai_ml_leadership: Optional[bool] = (
            leadership.detected if leadership is not None else None
        )

        modern_data_ml_stack: Optional[bool] = None
        if job_posts is not None and job_posts.tech_signals:
            tech_lower = {t.lower() for t in job_posts.tech_signals}
            if tech_lower & _ML_STACK_KEYWORDS:
                modern_data_ml_stack = True

        ai_input = AIMaturityInput(
            ai_adjacent_open_roles=ai_adjacent_open_roles,
            named_ai_ml_leadership=named_ai_ml_leadership,
            github_ai_activity=None,
            executive_ai_commentary=None,
            modern_data_ml_stack=modern_data_ml_stack,
            strategic_communications=None,
        )

        # 7. AI maturity scoring
        ai_maturity = _ai_scorer.score(ai_input)

        # 8. Competitor gap analysis
        competitor_gap = _competitor_gap_builder.build(
            prospect_company_id=firmographic.crunchbase_id or company_id,
            prospect_company_name=company_name,
            prospect_sectors=firmographic.sectors,
            prospect_funding_stage=firmographic.funding_stage,
            prospect_ai_maturity_score=ai_maturity.score,
            prospect_tech_stack=firmographic.builtwith_tech,
        )

        # 9. Assemble briefs
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
        brief, gap_brief = _assembler.assemble(assembler_input)

        # 10. Compute latency
        end_time = time.monotonic()
        latency_ms = (end_time - start_time) * 1000.0

        # 11. Emit Langfuse trace
        _emit_langfuse_trace(
            company_id=company_id,
            company_name=company_name,
            firmographic=firmographic,
            layoff=layoff,
            job_posts=job_posts,
            leadership=leadership,
            funding=funding,
            ai_maturity=ai_maturity,
            competitor_gap=competitor_gap,
            latency_ms=latency_ms,
        )

        # 12. Return response
        return EnrichResponse(
            hiring_signal_brief=brief.model_dump(mode="json"),
            competitor_gap_brief=gap_brief.model_dump(mode="json") if gap_brief else None,
            enrichment_latency_ms=latency_ms,
        )

    except BriefValidationError as exc:
        logger.error(
            "BriefValidationError during enrichment for company_id=%r: %s", company_id, exc
        )
        return JSONResponse(
            status_code=422,
            content={"error": "validation_failed", "detail": str(exc)},
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Unexpected error during enrichment for company_id=%r: %s", company_id, exc
        )
        return JSONResponse(
            status_code=500,
            content={"error": "enrichment_failed", "detail": str(exc)},
        )
