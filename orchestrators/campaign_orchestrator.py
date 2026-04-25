"""
Campaign Orchestrator — turns Tenacious business intent into ranked qualified accounts.

Architecture spec §2:
  Trigger: POST /campaigns/run  or  python scripts/run_campaign.py --campaign <yaml>

  Nodes (in order):
    load_campaign_config
    load_tenacious_context
    discover_candidate_accounts
    enrich_accounts
    score_and_rank_accounts
    qualify_accounts
    generate_briefs
    create_synthetic_contacts
    queue_outreach

  Invariants:
    - No outreach happens before qualification
    - Every account carries source_refs
    - Contact person is synthetic for challenge safety
    - Accounts come from Crunchbase/job/layoff snapshot data
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_RUNS_DIR = Path("runs")


@dataclass
class CampaignRun:
    campaign_run_id: str
    campaign_id: str
    started_at: str
    candidate_count: int = 0
    qualified_count: int = 0
    queued_outreach_count: int = 0
    qualified_accounts_path: str = ""
    status: str = "running"
    error: str | None = None


class CampaignOrchestrator:
    """
    Orchestrates the full campaign flow from intent to queued outreach.

    Usage::

        orchestrator = CampaignOrchestrator()
        result = orchestrator.run(campaign_config)
    """

    async def run(self, campaign_config: dict) -> dict:
        """
        Execute a full campaign run.

        Args:
            campaign_config: Dict parsed from campaign YAML or POST body.

        Returns:
            CampaignRun as dict with counts and qualified_accounts_path.
        """
        run_id = f"camp_{uuid.uuid4().hex[:8]}"
        campaign_id = campaign_config.get("campaign_id", "unnamed")
        now = datetime.now(timezone.utc).isoformat()

        campaign_run = CampaignRun(
            campaign_run_id=run_id,
            campaign_id=campaign_id,
            started_at=now,
        )

        try:
            # Step 1: Load campaign config and Tenacious context
            logger.info("Campaign %s: loading config", run_id)
            tenacious_ctx = self._load_tenacious_context()

            # Step 2: Discover candidate accounts
            logger.info("Campaign %s: discovering candidate accounts", run_id)
            discovery_result = self._discover(campaign_config)
            all_candidates = discovery_result.get("candidate_accounts", [])
            campaign_run.candidate_count = len(all_candidates)
            
            # Apply budgeting limit to control enrichment costs
            limit = campaign_config.get("limit", 10)
            candidates = all_candidates[:limit]
            campaign_run.candidate_count = len(candidates)
            
            logger.info("Campaign %s: found %d total potential matches, limiting to %d for processing", run_id, len(all_candidates), len(candidates))

            if not candidates:
                campaign_run.status = "no_candidates"
                return asdict(campaign_run)

            # Step 3: Enrich accounts
            logger.info("Campaign %s: enriching accounts", run_id)
            enriched = await self._enrich_accounts(candidates)

            # Step 4: Score and rank
            logger.info("Campaign %s: scoring accounts", run_id)
            scored = self._score_and_rank(enriched)

            # Step 5: Qualify accounts
            logger.info("Campaign %s: qualifying accounts", run_id)
            qualified = self._qualify(scored, campaign_config)
            campaign_run.qualified_count = len(qualified)
            logger.info("Campaign %s: %d qualified", run_id, len(qualified))

            # Step 6: Generate briefs + synthetic contacts
            logger.info("Campaign %s: generating briefs", run_id)
            accounts_with_briefs = self._generate_briefs(qualified)
            accounts_with_contacts = self._create_synthetic_contacts(accounts_with_briefs)

            # Step 7: Save qualified accounts
            output_path = self._save_qualified_accounts(run_id, accounts_with_contacts)
            campaign_run.qualified_accounts_path = str(output_path)

            # Step 8: Queue outreach
            outreach_count = self._queue_outreach(accounts_with_contacts, campaign_config)
            campaign_run.queued_outreach_count = outreach_count
            campaign_run.status = "complete"
            
            return asdict(campaign_run)

        except Exception as exc:
            logger.error("Campaign %s failed: %s", run_id, exc, exc_info=True)
            campaign_run.status = "failed"
            return asdict(campaign_run)

    def _to_dict(self, obj: Any) -> dict:
        """Utility to ensure we are working with a dictionary."""
        if not obj:
            return {}
        if is_dataclass(obj):
            return asdict(obj)
        if isinstance(obj, dict):
            return obj
        return {}

    # ── Nodes ─────────────────────────────────────────────────────────────────

    def _load_tenacious_context(self) -> dict:
        """Load ICP definition, bench summary, and style guide."""
        ctx: dict[str, Any] = {}
        seed_dir = Path("data/tenacious_sales_data/seed")

        for fname in ("icp_definition.md", "bench_summary.json", "pricing.md"):
            fpath = seed_dir / fname
            if fpath.exists():
                ctx[fname.replace(".", "_").replace("-", "_")] = fpath.read_text()

        return ctx

    def _discover(self, campaign_config: dict) -> dict:
        """Run MarketDiscoveryAgent to find candidate accounts."""
        from agent.market_discovery import MarketDiscoveryAgent

        # If UI passes target_segments, inject filters to increase qualification rates
        segments = campaign_config.get("target_segments", [])
        if "S1" in segments or "segment_1" in segments:
            if "funding" not in campaign_config:
                campaign_config["funding"] = {"rounds": ["series a", "series b"]}
            if "company_size" not in campaign_config:
                campaign_config["company_size"] = {"min_employees": 15, "max_employees": 80}
        
        if "S2" in segments or "segment_2" in segments:
            if "signals_required" not in campaign_config:
                campaign_config["signals_required"] = {}
            if "min_layoff_percentage" not in campaign_config["signals_required"]:
                # Spec says > 10%
                campaign_config["signals_required"]["min_layoff_percentage"] = 0.10

        agent = MarketDiscoveryAgent()
        return agent.discover(campaign_config)

    async def _enrich_accounts(self, candidates: list[dict]) -> list[dict]:
        """Run full PipelineRunner on each candidate in parallel."""
        from signal_pipeline.pipeline_runner import PipelineRunner
        from config.models import Prospect, ProspectState
        import uuid
        import asyncio

        runner = PipelineRunner()
        semaphore = asyncio.Semaphore(5)  # Limit concurrency to 5 browsers

        async def _enrich_one(account: dict) -> dict:
            async with semaphore:
                try:
                    # Convert candidate to a Prospect for full enrichment
                    prospect_id = str(uuid.uuid4())
                    company_id = (
                        account.get("crunchbase_id") or f"cb_{uuid.uuid4().hex[:8]}"
                    )
                    prospect = Prospect(
                        prospect_id=prospect_id,
                        company_id=company_id,
                        contact_name="Engineering Leader",
                        email="test@tenacious.com",
                        phone=None,
                        timezone="UTC",
                        preferred_channel="email",
                        current_state=ProspectState.COLD,
                        outbound_attempt_count=0,
                    )
                    # Attach display name so PipelineRunner._enrich can use it
                    # for firmographic lookup and JobPostScraper
                    object.__setattr__(
                        prospect, "company_name", account.get("company_name", "")
                    )

                    brief, gap_brief = await runner._enrich(prospect)

                    account["hiring_signal_brief_obj"] = brief
                    account["competitor_gap_brief_obj"] = gap_brief
                    account["firmographic"] = None
                except Exception as exc:
                    logger.warning(
                        "Enrichment failed for %s: %s",
                        account.get("company_name"),
                        exc,
                    )
                    account["firmographic"] = None
                    account["hiring_signal_brief_obj"] = None
                    account["competitor_gap_brief_obj"] = None
                return account

        tasks = [_enrich_one(a) for a in candidates]
        return await asyncio.gather(*tasks)

    def _score_and_rank_accounts(self, enriched: list[dict]) -> list[dict]:
        """Score accounts by signal strength and rank descending."""
        return self._score_and_rank(enriched)

    def _score_and_rank(self, enriched: list[dict]) -> list[dict]:
        """Assign a numeric score based on available signals."""
        scored: list[dict] = []
        for account in enriched:
            score = 0
            firm = self._to_dict(account.get("firmographic"))

            # 1. Funding Signal (using discovery or enrichment data)
            funding_total = account.get("funding_total_usd") or firm.get("funding_total_usd") or 0
            if funding_total >= 10_000_000:
                score += 40
            elif funding_total >= 1_000_000:
                score += 20

            # 2. Company Size Signal
            emp_count = account.get("employee_count") or firm.get("employee_count_min") or 0
            if 50 <= emp_count <= 500:
                score += 20

            # 3. Last round signal
            last_round = str(account.get("last_funding_round") or firm.get("funding_stage") or "").lower()
            if any(r in last_round for r in ["series a", "series b"]):
                score += 30

            account["campaign_score"] = score
            scored.append(account)

        scored.sort(key=lambda a: a.get("campaign_score", 0), reverse=True)
        return scored

    def _qualify(self, scored: list[dict], campaign_config: dict) -> list[dict]:
        """Run ICP classifier on each fully enriched account and keep qualified ones."""
        from icp_classifier.classifier import ClassifierConfig, classify

        config = ClassifierConfig()
        qualified: list[dict] = []

        for account in scored:
            brief = account.get("hiring_signal_brief_obj")
            if not brief:
                logger.debug("Account %s lacks a valid brief; disqualifying.", account.get("company_name"))
                continue

            try:
                result = classify(brief, config)

                if not result.abstained:
                    account["icp_result"] = {
                        "decision": "qualified",
                        "segment": result.segment.value,
                        "confidence": result.confidence,
                        "signals_used": result.signals_used,
                    }
                    account["hiring_signal_brief"] = brief.model_dump(mode="json")
                    gap_obj = account.get("competitor_gap_brief_obj")
                    if gap_obj:
                        account["competitor_gap_brief"] = gap_obj.model_dump(mode="json")
                    qualified.append(account)
                else:
                    logger.debug(
                        "Account %s abstained: confidence=%.2f",
                        account.get("company_name"),
                        result.confidence,
                    )

            except Exception as exc:
                logger.warning("Qualification failed for %s: %s", account.get("company_name"), exc)

        return qualified

    def _generate_briefs(self, qualified: list[dict]) -> list[dict]:
        """Briefs are now generated concurrently during PipelineRunner._enrich()."""
        return qualified

    def _create_synthetic_contacts(self, accounts: list[dict]) -> list[dict]:
        """Add a synthetic contact person to each account (challenge safety requirement)."""
        for account in accounts:
            company_name = account.get("company_name", "Company")
            slug = company_name.lower().replace(" ", "").replace(",", "")[:12]
            account["synthetic_contact"] = {
                "contact_name": f"Engineering Leader",
                "email": f"eng-leader@{slug}-sandbox.tenacious-demo.dev",
                "phone": None,
                "timezone": "America/New_York",
                "preferred_channel": "email",
                "is_synthetic": True,
            }

        return accounts

    def _save_qualified_accounts(self, run_id: str, accounts: list[dict]) -> Path:
        """Write qualified accounts to JSONL file."""
        run_dir = _RUNS_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        output = run_dir / "qualified_accounts.jsonl"

        with open(output, "w") as f:
            for account in accounts:
                # Remove non-serializable Pydantic objects before writing
                clean_acct = account.copy()
                clean_acct.pop("hiring_signal_brief_obj", None)
                clean_acct.pop("competitor_gap_brief_obj", None)
                f.write(json.dumps(clean_acct) + "\n")

        logger.info("Saved %d qualified accounts to %s", len(accounts), output)
        return output

    def _queue_outreach(self, accounts: list[dict], campaign_config: dict) -> int:
        """
        Queue outreach for each qualified account.

        In the current implementation this writes a queue manifest to disk.
        The webhook handler / ConversationAgent picks it up on next startup,
        or it can be consumed by a background task.
        """
        outreach_channel = (campaign_config.get("outreach") or {}).get(
            "first_channel", "email"
        )

        queued: list[dict] = []
        for account in accounts:
            contact = account.get("synthetic_contact") or {}
            icp = account.get("icp_result") or {}
            queued.append({
                "prospect_id": str(uuid.uuid4()),
                "company_id": account.get("crunchbase_id", ""),
                "company_name": account.get("company_name", ""),
                "contact_name": contact.get("contact_name", ""),
                "email": contact.get("email", ""),
                "phone": contact.get("phone"),
                "timezone": contact.get("timezone", "UTC"),
                "preferred_channel": outreach_channel,
                "current_state": "cold",
                "outbound_attempt_count": 0,
                "segment": icp.get("segment"),
                "icp_confidence": icp.get("confidence"),
                "source_refs": account.get("source_refs", {}),
                "queued_at": datetime.now(timezone.utc).isoformat(),
            })

        if queued:
            queue_path = Path("runs") / "outreach_queue.jsonl"
            queue_path.parent.mkdir(parents=True, exist_ok=True)
            with open(queue_path, "a") as f:
                for item in queued:
                    f.write(json.dumps(item) + "\n")

        return len(queued)
