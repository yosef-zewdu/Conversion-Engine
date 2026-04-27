"""
CompetitorGapBuilder — selects sector peers from Crunchbase ODM, scores their AI
maturity, computes the prospect's sector percentile, and extracts gap practices.

Req 4.1: Identify 5–10 companies in same sector+stage from Crunchbase ODM; apply
         AI maturity scoring to each → CompetitorGapBrief.
Req 4.2: Compute prospect's position in sector AI maturity score distribution.
Req 4.3: Extract 2–3 specific practices top-quartile peers show that prospect does
         not; record with evidence references.
Req 4.4: If fewer than 5 sector peers available, record actual peer_count; do not
         fabricate additional peers.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from signal_pipeline.ai_maturity_scorer import AIMaturityInput, AIMaturityScorer
from signal_pipeline.firmographic_enricher import FirmographicEnricher
from signal_pipeline.models import CompetitorGap, CompetitorGapBrief, CompetitorGapPeer

# ---------------------------------------------------------------------------
# Stage banding constants
# ---------------------------------------------------------------------------

_STAGE_BAND_MAP: dict[str, str] = {
    "seed": "seed",
    "pre-seed": "seed",
    "angel": "seed",
    "seed round": "seed",
    "series a": "series_ab",
    "series b": "series_ab",
    "series c": "series_c_plus",
    "series d": "series_c_plus",
    "series e": "series_c_plus",
    "series f": "series_c_plus",
    "series g": "series_c_plus",
    "series h": "series_c_plus",
    "series i": "series_c_plus",
    "series j": "series_c_plus",
    "post-ipo": "public",
    "public": "public",
    "ipo": "public",
}

# Keywords used to detect AI/ML signals in text fields
_AI_KEYWORDS: list[str] = ["ai", "ml", "machine learning", "deep learning", "llm"]

# ML stack keywords for modern_data_ml_stack signal
_ML_STACK_KEYWORDS: list[str] = [
    "pytorch", "tensorflow", "spark", "kafka", "dbt", "airflow",
]

# Strategic AI language for strategic_communications signal
_STRATEGIC_AI_PHRASES: list[str] = [
    "ai strategy", "ai-first", "machine learning platform",
]

# Tech practices to check for gap extraction (tech keyword → human-readable practice name)
_PRACTICE_MAP: list[tuple[str, str]] = [
    ("pytorch", "PyTorch deep learning framework"),
    ("tensorflow", "TensorFlow model training"),
    ("spark", "Apache Spark data pipeline"),
    ("kafka", "Apache Kafka event streaming"),
    ("dbt", "dbt data transformation"),
    ("airflow", "Apache Airflow workflow orchestration"),
    ("mlflow", "MLflow experiment tracking"),
    ("kubeflow", "Kubeflow ML pipeline"),
    ("ray", "Ray distributed computing"),
    ("feast", "Feast feature store"),
]


# ---------------------------------------------------------------------------
# Stage banding helper
# ---------------------------------------------------------------------------


def _band_stage(funding_stage: Optional[str]) -> str:
    """
    Map a raw funding stage string to a normalised stage band.

    Args:
        funding_stage: Raw stage string from Crunchbase ODM, or None.

    Returns:
        One of "seed", "series_ab", "series_c_plus", "public", or "unknown".
    """
    if not funding_stage:
        return "unknown"
    normalised = funding_stage.strip().lower()
    return _STAGE_BAND_MAP.get(normalised, "unknown")


# ---------------------------------------------------------------------------
# AI maturity signal extraction from raw Crunchbase record
# ---------------------------------------------------------------------------


def _count_ai_keywords_in_text(text: Optional[str]) -> int:
    """
    Count distinct AI/ML keyword occurrences in a text field (case-insensitive).

    Uses a 0–5 proxy scale: counts unique keyword matches, capped at 5.

    Args:
        text: Free-text field (about, full_description), or None.

    Returns:
        Integer in [0, 5].
    """
    if not text:
        return 0
    lower = text.lower()
    count = sum(1 for kw in _AI_KEYWORDS if kw in lower)
    return min(count, 5)


def _has_ai_ml_leadership(leadership_hire: list[dict]) -> bool:
    """
    Check whether any leadership hire label mentions AI or ML keywords.

    Args:
        leadership_hire: List of leadership hire event dicts from Crunchbase ODM.

    Returns:
        True if at least one hire label contains an AI/ML keyword.
    """
    for event in leadership_hire:
        label: str = event.get("label", "") or ""
        lower = label.lower()
        if any(kw in lower for kw in _AI_KEYWORDS):
            return True
    return False


def _has_executive_ai_commentary(about: Optional[str], full_description: Optional[str]) -> bool:
    """
    Detect executive AI commentary from about/full_description text.

    Looks for phrases that suggest executive-level AI statements.

    Args:
        about: Short company description, or None.
        full_description: Long company description, or None.

    Returns:
        True if AI commentary is detected in either field.
    """
    combined = " ".join(filter(None, [about, full_description])).lower()
    executive_patterns = [
        r"\bour\s+(?:ceo|cto|chief)\b.*\bai\b",
        r"\bai\b.*\bstrategy\b",
        r"\binvesting\s+in\s+ai\b",
        r"\bai.first\b",
        r"\bpowered\s+by\s+(?:ai|machine\s+learning)\b",
    ]
    return any(re.search(pat, combined) for pat in executive_patterns)


def _has_modern_ml_stack(builtwith_tech: list[str], siftery_products: list[str]) -> bool:
    """
    Check whether the company's tech stack includes modern ML/data tools.

    Args:
        builtwith_tech: List of detected technologies from BuiltWith.
        siftery_products: List of products from Siftery.

    Returns:
        True if at least one ML stack keyword is found.
    """
    combined = " ".join(builtwith_tech + siftery_products).lower()
    return any(kw in combined for kw in _ML_STACK_KEYWORDS)


def _has_strategic_communications(about: Optional[str]) -> bool:
    """
    Check whether the company's about text contains strategic AI language.

    Args:
        about: Short company description, or None.

    Returns:
        True if a strategic AI phrase is found.
    """
    if not about:
        return False
    lower = about.lower()
    return any(phrase in lower for phrase in _STRATEGIC_AI_PHRASES)


def _build_ai_maturity_input_from_record(record: dict) -> AIMaturityInput:
    """
    Build an AIMaturityInput from a raw Crunchbase ODM record dict.

    Extracts signals from available text and tech fields; github_ai_activity
    is always None (not available from Crunchbase ODM).

    Args:
        record: A single row from the Crunchbase ODM CSV as a dict.

    Returns:
        AIMaturityInput populated from the record's available fields.
    """
    from signal_pipeline.firmographic_enricher import _parse_json_list

    about: Optional[str] = record.get("about") or None
    full_description: Optional[str] = record.get("full_description") or None

    # Parse tech lists from raw record
    builtwith_raw = _parse_json_list(record.get("builtwith_tech", ""))
    siftery_raw = _parse_json_list(record.get("siftery_products", ""))

    builtwith_tech: list[str] = []
    for item in builtwith_raw:
        if isinstance(item, str) and item:
            builtwith_tech.append(item)
        elif isinstance(item, dict):
            val = item.get("name") or item.get("value") or item.get("tag")
            if isinstance(val, str) and val:
                builtwith_tech.append(val)

    siftery_products: list[str] = []
    for item in siftery_raw:
        if isinstance(item, str) and item:
            siftery_products.append(item)
        elif isinstance(item, dict):
            val = item.get("name") or item.get("value")
            if isinstance(val, str) and val:
                siftery_products.append(val)

    leadership_hire = _parse_json_list(record.get("leadership_hire", ""))

    combined_text = " ".join(filter(None, [about, full_description]))
    ai_role_count = _count_ai_keywords_in_text(combined_text)

    return AIMaturityInput(
        ai_adjacent_open_roles=ai_role_count,
        named_ai_ml_leadership=_has_ai_ml_leadership(leadership_hire) or None,
        github_ai_activity=None,
        executive_ai_commentary=_has_executive_ai_commentary(about, full_description) or None,
        modern_data_ml_stack=_has_modern_ml_stack(builtwith_tech, siftery_products) or None,
        strategic_communications=_has_strategic_communications(about) or None,
    )


# ---------------------------------------------------------------------------
# Sector and stage matching helpers
# ---------------------------------------------------------------------------


def _extract_sectors_from_record(record: dict) -> list[str]:
    """
    Extract sector strings from a raw Crunchbase ODM record.

    Args:
        record: A single row from the Crunchbase ODM CSV as a dict.

    Returns:
        List of sector value strings (lowercased).
    """
    from signal_pipeline.firmographic_enricher import _extract_names, _parse_json_list

    industries = _parse_json_list(record.get("industries", ""))
    return [v.lower() for v in _extract_names(industries, key="value")]


def _infer_stage_band_from_record(record: dict) -> str:
    """
    Infer the stage band for a raw Crunchbase ODM record.

    Args:
        record: A single row from the Crunchbase ODM CSV as a dict.

    Returns:
        Stage band string: "seed", "series_ab", "series_c_plus", "public", or "unknown".
    """
    from signal_pipeline.firmographic_enricher import _infer_funding_stage, _parse_json_list

    funding_rounds = _parse_json_list(record.get("funding_rounds_list", ""))
    raw_stage = _infer_funding_stage(record.get("investment_stage", ""), funding_rounds)
    return _band_stage(raw_stage)


def _shares_sector(record_sectors: list[str], prospect_sectors_lower: list[str]) -> bool:
    """
    Check whether a record shares at least one sector with the prospect.

    Args:
        record_sectors: Lowercased sector strings from the candidate record.
        prospect_sectors_lower: Lowercased sector strings from the prospect.

    Returns:
        True if there is at least one common sector.
    """
    return bool(set(record_sectors) & set(prospect_sectors_lower))


# ---------------------------------------------------------------------------
# Gap extraction helpers
# ---------------------------------------------------------------------------


def _collect_tech_from_record(record: dict) -> set[str]:
    """
    Collect all tech keywords (lowercased) from a record's tech stack fields.

    Args:
        record: A single row from the Crunchbase ODM CSV as a dict.

    Returns:
        Set of lowercased tech strings found in builtwith_tech and siftery_products.
    """
    from signal_pipeline.firmographic_enricher import _parse_json_list

    builtwith_raw = _parse_json_list(record.get("builtwith_tech", ""))
    siftery_raw = _parse_json_list(record.get("siftery_products", ""))

    tech: set[str] = set()
    for item in builtwith_raw + siftery_raw:
        if isinstance(item, str) and item:
            tech.add(item.lower())
        elif isinstance(item, dict):
            val = item.get("name") or item.get("value") or item.get("tag")
            if isinstance(val, str) and val:
                tech.add(val.lower())
    return tech


def _extract_gaps(
    top_quartile_records: list[dict],
    top_quartile_ids: list[str],
    prospect_tech_stack_lower: set[str],
) -> list[CompetitorGap]:
    """
    Extract up to 3 gap practices that top-quartile peers show but the prospect lacks.

    For each practice in _PRACTICE_MAP, checks how many top-quartile peers have it
    and whether the prospect lacks it. Assigns confidence based on peer count.

    Args:
        top_quartile_records: Raw Crunchbase records for top-quartile peers (score ≥ 2).
        top_quartile_ids: Corresponding company_id strings for each record.
        prospect_tech_stack_lower: Lowercased tech strings from the prospect.

    Returns:
        List of up to 3 CompetitorGap objects, sorted by peer count descending.
    """
    # Map practice keyword → list of peer company_ids that have it
    practice_peer_map: dict[str, list[str]] = {}

    for record, company_id in zip(top_quartile_records, top_quartile_ids):
        peer_tech = _collect_tech_from_record(record)
        for keyword, _practice_name in _PRACTICE_MAP:
            if keyword in peer_tech:
                practice_peer_map.setdefault(keyword, []).append(company_id)

    gaps: list[CompetitorGap] = []
    for keyword, practice_name in _PRACTICE_MAP:
        peer_ids = practice_peer_map.get(keyword, [])
        if not peer_ids:
            continue
        # Skip if prospect already has this practice
        if keyword in prospect_tech_stack_lower:
            continue

        peer_count = len(peer_ids)
        if peer_count >= 3:
            confidence = "high"
        elif peer_count == 2:
            confidence = "medium"
        else:
            confidence = "low"

        peer_names = [
            r.get("name", cid)
            for r, cid in zip(top_quartile_records, top_quartile_ids)
            if cid in peer_ids
        ]
        evidence = f"{', '.join(peer_names)} use {practice_name}"

        gaps.append(
            CompetitorGap(
                practice=practice_name,
                evidence=evidence,
                confidence=confidence,
                peer_refs=peer_ids,
            )
        )

        if len(gaps) == 3:
            break

    # Sort by peer count descending (highest confidence first)
    gaps.sort(key=lambda g: len(g.peer_refs), reverse=True)
    return gaps[:3]


# ---------------------------------------------------------------------------
# Sector percentile computation
# ---------------------------------------------------------------------------


def _compute_sector_percentile(
    peers: list[CompetitorGapPeer], prospect_score: Optional[int]
) -> Optional[float]:
    """
    Compute the prospect's percentile rank within the peer AI maturity distribution.

    Percentile = (number of peers with score strictly less than prospect) / total peers * 100.

    Args:
        peers: List of scored peer records.
        prospect_score: The prospect's AI maturity score.

    Returns:
        Float in [0.0, 100.0], or None if there are no peers or if score is None.
    """
    if not peers or prospect_score is None:
        return None
    below = sum(1 for p in peers if p.ai_maturity_score is not None and p.ai_maturity_score < prospect_score)
    return below / len(peers) * 100.0


# ---------------------------------------------------------------------------
# CompetitorGapBuilder
# ---------------------------------------------------------------------------


class CompetitorGapBuilder:
    """
    Builds a CompetitorGapBrief for a given prospect by:
    1. Selecting 5–10 sector+stage peers from Crunchbase ODM.
    2. Scoring each peer's AI maturity.
    3. Computing the prospect's sector percentile.
    4. Extracting 2–3 gap practices with evidence references.

    Never fabricates peer records (Req 4.4).
    """

    def __init__(self, enricher: FirmographicEnricher, scorer: AIMaturityScorer) -> None:
        """
        Initialise the builder with a FirmographicEnricher and AIMaturityScorer.

        Args:
            enricher: FirmographicEnricher instance for accessing Crunchbase ODM records.
            scorer: AIMaturityScorer instance for computing peer AI maturity scores.
        """
        self._enricher = enricher
        self._scorer = scorer

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        prospect_company_id: str,
        prospect_company_name: str,
        prospect_sectors: list[str],
        prospect_funding_stage: Optional[str],
        prospect_ai_maturity_score: int,
        prospect_tech_stack: list[str],
    ) -> CompetitorGapBrief:
        """
        Build a CompetitorGapBrief for the given prospect.

        Selects sector+stage peers, scores them, computes the prospect's percentile,
        and extracts gap practices. Never fabricates peer records (Req 4.4).

        Args:
            prospect_company_id: Crunchbase company ID (or internal ID) of the prospect.
            prospect_company_name: Display name of the prospect company.
            prospect_sectors: List of sector strings for the prospect (from industries.value).
            prospect_funding_stage: Raw funding stage string, or None.
            prospect_ai_maturity_score: Pre-computed AI maturity score for the prospect (0–3).
            prospect_tech_stack: List of tech strings from the prospect's tech stack.

        Returns:
            CompetitorGapBrief with peers, gaps, sector_percentile, and peer_count.
        """
        prospect_stage_band = _band_stage(prospect_funding_stage)
        prospect_sectors_lower = [s.lower() for s in prospect_sectors]
        prospect_tech_lower = {t.lower() for t in prospect_tech_stack}

        candidate_records = self._select_candidate_records(
            prospect_company_id=prospect_company_id,
            prospect_company_name=prospect_company_name,
            prospect_sectors_lower=prospect_sectors_lower,
            prospect_stage_band=prospect_stage_band,
        )

        peers, peer_records = self._score_peers(candidate_records)

        sector_percentile = _compute_sector_percentile(peers, prospect_ai_maturity_score)

        top_quartile_pairs = [
            (rec, peer)
            for rec, peer in zip(peer_records, peers)
            if peer.ai_maturity_score >= 2
        ]
        top_quartile_records = [p[0] for p in top_quartile_pairs]
        top_quartile_ids = [p[1].company_id for p in top_quartile_pairs]

        gaps = _extract_gaps(top_quartile_records, top_quartile_ids, prospect_tech_lower)

        return CompetitorGapBrief(
            schema_version="1.0",
            company_id=prospect_company_id,
            generated_at=datetime.now(timezone.utc).isoformat(),
            prospect_ai_maturity_score=prospect_ai_maturity_score,
            sector_percentile=sector_percentile,
            peer_count=len(peers),
            peers=peers,
            gaps=gaps,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _select_candidate_records(
        self,
        prospect_company_id: str,
        prospect_company_name: str,
        prospect_sectors_lower: list[str],
        prospect_stage_band: str,
    ) -> list[dict]:
        """
        Filter Crunchbase ODM records to sector+stage peers, excluding the prospect.

        If more than 10 qualify, keeps the 10 with the highest AI maturity score
        (top-quartile bias). Records actual count without fabrication (Req 4.4).

        Args:
            prospect_company_id: ID of the prospect to exclude.
            prospect_company_name: Name of the prospect to exclude (fallback).
            prospect_sectors_lower: Lowercased sector strings for matching.
            prospect_stage_band: Normalised stage band for the prospect.

        Returns:
            List of up to 10 raw Crunchbase record dicts.
        """
        all_records = self._enricher._records()
        candidates: list[dict] = []

        for record in all_records:
            record_id = record.get("id", "")
            record_name = record.get("name", "")

            # Exclude the prospect itself
            if record_id == prospect_company_id:
                continue
            if record_name.lower() == prospect_company_name.lower():
                continue

            # Sector match (at least one shared sector)
            record_sectors = _extract_sectors_from_record(record)
            if not _shares_sector(record_sectors, prospect_sectors_lower):
                continue

            # Stage band match
            record_band = _infer_stage_band_from_record(record)
            if record_band != prospect_stage_band:
                continue

            candidates.append(record)

        if len(candidates) <= 10:
            return candidates

        # More than 10: keep top 10 by AI maturity score
        return self._top_n_by_score(candidates, n=10)

    def _top_n_by_score(self, records: list[dict], n: int) -> list[dict]:
        """
        Return the top-n records ranked by AI maturity score (descending).

        Args:
            records: List of raw Crunchbase record dicts.
            n: Maximum number of records to return.

        Returns:
            Up to n records with the highest AI maturity scores.
        """
        def _score_record(record: dict) -> int:
            inputs = _build_ai_maturity_input_from_record(record)
            result = self._scorer.score(inputs)
            return result.score

        scored = sorted(records, key=_score_record, reverse=True)
        return scored[:n]

    def _score_peers(
        self, records: list[dict]
    ) -> tuple[list[CompetitorGapPeer], list[dict]]:
        """
        Score each candidate record and build CompetitorGapPeer objects.

        Args:
            records: List of raw Crunchbase record dicts (already filtered to ≤10).

        Returns:
            Tuple of (list of CompetitorGapPeer, list of corresponding raw records).
            Both lists are parallel (same order and length).
        """
        peers: list[CompetitorGapPeer] = []
        scored_records: list[dict] = []

        for record in records:
            company_id = record.get("id") or record.get("uuid") or record.get("name", "unknown")
            company_name = record.get("name", "")

            inputs = _build_ai_maturity_input_from_record(record)
            result = self._scorer.score(inputs)

            peers.append(
                CompetitorGapPeer(
                    company_id=company_id,
                    company_name=company_name,
                    ai_maturity_score=result.score,
                )
            )
            scored_records.append(record)

        return peers, scored_records
