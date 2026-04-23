"""
JobPostScraper — Playwright-based scraper for public job listings.

Req 2.3: Scrape public job listings from BuiltIn, Wellfound, and company careers
         page using Playwright; respect robots.txt; no auth or captcha bypass;
         set job_post_count: null on failure.
Req 2.7: If scrape fails or returns no results, record job_post_count: null;
         do not infer velocity from absent data.
"""
from __future__ import annotations

import logging
import re
import urllib.robotparser
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse
from urllib.request import urlopen

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USER_AGENT = "ConversionEngine/1.0 (research bot; contact: admin@example.com)"
PAGE_TIMEOUT_MS = 8_000
VIEWPORT = {"width": 1280, "height": 800}

TECH_KEYWORDS: list[str] = [
    "python", "go", "golang", "rust", "java", "typescript", "react",
    "kubernetes", "terraform", "pytorch", "tensorflow", "spark", "kafka",
    "dbt", "airflow", "mlflow", "langchain", "openai", "aws", "gcp", "azure",
]

CAREERS_PATHS: list[str] = ["/careers", "/jobs", "/work-with-us"]

JOB_CONTENT_KEYWORDS: list[str] = [
    "engineer", "developer", "data", "ml", "ai", "product", "design",
]

LOGIN_INDICATORS: list[str] = ["login", "signin", "auth"]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class JobPostResult:
    """Aggregated result from all job-listing sources."""

    job_post_count: Optional[int]           # None on failure
    job_post_velocity_60d: Optional[float]  # Always None (requires historical data)
    job_post_confidence: Optional[str]      # "high" | "medium" | "low" | None
    sources_checked: list[str] = field(default_factory=list)
    tech_signals: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# robots.txt helpers
# ---------------------------------------------------------------------------

def _is_allowed(url: str) -> bool:
    """
    Check whether USER_AGENT is permitted to fetch *url* per the domain's robots.txt.

    Returns True when robots.txt is unreachable or unparseable (fail-open for
    availability, but logs a warning).  Returns False when the path is explicitly
    disallowed.
    """
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch robots.txt from %s: %s", robots_url, exc)
        return True  # fail-open

    allowed = rp.can_fetch(USER_AGENT, url)
    if not allowed:
        logger.warning("robots.txt disallows %s for user-agent %s", url, USER_AGENT)
    return allowed


# ---------------------------------------------------------------------------
# Login-wall detection
# ---------------------------------------------------------------------------

def _is_login_wall(url: str) -> bool:
    """Return True if the current URL looks like a login/auth wall."""
    lower = url.lower()
    return any(indicator in lower for indicator in LOGIN_INDICATORS)


# ---------------------------------------------------------------------------
# Tech signal extraction
# ---------------------------------------------------------------------------

def _extract_tech_signals(text: str) -> list[str]:
    """
    Scan *text* for known tech keywords (case-insensitive) and return a
    deduplicated list of matched keywords.
    """
    lower = text.lower()
    found: list[str] = []
    for kw in TECH_KEYWORDS:
        # Use word-boundary matching to avoid partial hits (e.g. "goland")
        if re.search(r"\b" + re.escape(kw) + r"\b", lower):
            found.append(kw)
    return found


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def _confidence(source_count: int) -> Optional[str]:
    """Map the number of successful sources to a confidence label."""
    return {1: "low", 2: "medium", 3: "high"}.get(source_count)


def _aggregate(counts: dict[str, Optional[int]]) -> tuple[Optional[int], Optional[str]]:
    """
    Sum non-None counts and derive confidence.

    Returns:
        (total_count_or_none, confidence_or_none)
    """
    valid = {src: c for src, c in counts.items() if c is not None}
    if not valid:
        return None, None
    total = sum(valid.values())
    return total, _confidence(len(valid))


# ---------------------------------------------------------------------------
# JobPostScraper
# ---------------------------------------------------------------------------

class JobPostScraper:
    """
    Playwright-based scraper that collects public job listing counts from
    BuiltIn, Wellfound, and a company's own careers page.

    Respects robots.txt, never authenticates, and never bypasses captchas.
    Sets job_post_count to None on any failure (Req 2.3, Req 2.7).
    """

    async def scrape(
        self,
        company_name: str,
        website_url: Optional[str] = None,
    ) -> JobPostResult:
        """
        Scrape job listings for *company_name* from all configured sources.

        Args:
            company_name: Human-readable company name used to build search URLs.
            website_url:  Optional base URL of the company's own website.

        Returns:
            A JobPostResult with aggregated counts, confidence, and tech signals.
        """
        counts: dict[str, Optional[int]] = {}
        all_tech: list[str] = []
        sources_checked: list[str] = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    user_agent=USER_AGENT,
                    viewport=VIEWPORT,
                )

                # --- BuiltIn ---
                builtin_count, builtin_tech = await self._scrape_builtin(
                    context, company_name
                )
                counts["builtin"] = builtin_count
                all_tech.extend(builtin_tech)
                sources_checked.append("builtin")

                # --- Wellfound ---
                wellfound_count, wellfound_tech = await self._scrape_wellfound(
                    context, company_name
                )
                counts["wellfound"] = wellfound_count
                all_tech.extend(wellfound_tech)
                sources_checked.append("wellfound")

                # --- Company careers page ---
                if website_url:
                    careers_count, careers_tech = await self._scrape_careers(
                        context, website_url
                    )
                    counts["careers"] = careers_count
                    all_tech.extend(careers_tech)
                    sources_checked.append("careers")

            finally:
                await browser.close()

        total, confidence = _aggregate(counts)
        tech_signals = sorted(set(all_tech))

        return JobPostResult(
            job_post_count=total,
            job_post_velocity_60d=None,  # Req 2.7: requires historical data
            job_post_confidence=confidence,
            sources_checked=sources_checked,
            tech_signals=tech_signals,
        )

    # ------------------------------------------------------------------
    # BuiltIn
    # ------------------------------------------------------------------

    async def _scrape_builtin(
        self, context, company_name: str
    ) -> tuple[Optional[int], list[str]]:
        """
        Scrape job listings from BuiltIn for *company_name*.

        URL pattern: https://builtin.com/jobs?search={company_name}

        Returns:
            (count_or_none, tech_signals)
        """
        url = f"https://builtin.com/jobs?search={company_name}"
        if not _is_allowed(url):
            return None, []

        page = await context.new_page()
        try:
            await page.goto(url, timeout=PAGE_TIMEOUT_MS)

            if _is_login_wall(page.url):
                logger.warning("BuiltIn redirected to login wall; aborting source.")
                return None, []

            # Count job cards: prefer [data-id] elements, fall back to <article>
            cards = await page.query_selector_all("[data-id]")
            if not cards:
                cards = await page.query_selector_all("article")

            count = len(cards) if cards else None

            text = await page.inner_text("body")
            tech = _extract_tech_signals(text)
            return count, tech

        except Exception as exc:  # noqa: BLE001
            logger.warning("BuiltIn scrape failed for %r: %s", company_name, exc)
            return None, []
        finally:
            await page.close()

    # ------------------------------------------------------------------
    # Wellfound
    # ------------------------------------------------------------------

    async def _scrape_wellfound(
        self, context, company_name: str
    ) -> tuple[Optional[int], list[str]]:
        """
        Scrape job listings from Wellfound for *company_name*.

        URL pattern: https://wellfound.com/company/{slug}/jobs
        Slug = company name lowercased with spaces replaced by hyphens.

        Returns:
            (count_or_none, tech_signals)
        """
        slug = company_name.lower().replace(" ", "-")
        url = f"https://wellfound.com/company/{slug}/jobs"
        if not _is_allowed(url):
            return None, []

        page = await context.new_page()
        try:
            await page.goto(url, timeout=PAGE_TIMEOUT_MS)

            if _is_login_wall(page.url):
                logger.warning("Wellfound redirected to login wall; aborting source.")
                return None, []

            cards = await page.query_selector_all('[data-test="StartupResult"]')
            if not cards:
                # Fallback: generic job card selectors
                cards = await page.query_selector_all("[data-test*='job']")
            if not cards:
                cards = await page.query_selector_all("article")

            count = len(cards) if cards else None

            text = await page.inner_text("body")
            tech = _extract_tech_signals(text)
            return count, tech

        except Exception as exc:  # noqa: BLE001
            logger.warning("Wellfound scrape failed for %r: %s", company_name, exc)
            return None, []
        finally:
            await page.close()

    # ------------------------------------------------------------------
    # Company careers page
    # ------------------------------------------------------------------

    async def _scrape_careers(
        self, context, website_url: str
    ) -> tuple[Optional[int], list[str]]:
        """
        Scrape job listings from the company's own careers page.

        Tries CAREERS_PATHS in order; stops at the first path that returns
        a 200 response and contains job-like content.

        Returns:
            (count_or_none, tech_signals)
        """
        base = website_url.rstrip("/")

        for path in CAREERS_PATHS:
            url = base + path
            if not _is_allowed(url):
                logger.warning("robots.txt disallows %s; skipping.", url)
                continue

            page = await context.new_page()
            try:
                response = await page.goto(url, timeout=PAGE_TIMEOUT_MS)

                if response is None or response.status != 200:
                    await page.close()
                    continue

                if _is_login_wall(page.url):
                    logger.warning(
                        "Careers page %s redirected to login wall; skipping.", url
                    )
                    await page.close()
                    continue

                count, tech = await self._count_careers_jobs(page)
                if count is not None:
                    return count, tech

            except Exception as exc:  # noqa: BLE001
                logger.warning("Careers scrape failed for %s: %s", url, exc)
            finally:
                try:
                    await page.close()
                except Exception:  # noqa: BLE001
                    pass

        return None, []

    async def _count_careers_jobs(
        self, page
    ) -> tuple[Optional[int], list[str]]:
        """
        Count job-like list items or articles on a careers page.

        An element is considered job-like if its text contains at least one
        keyword from JOB_CONTENT_KEYWORDS.

        Returns:
            (count_or_none, tech_signals)
        """
        text = await page.inner_text("body")
        tech = _extract_tech_signals(text)

        # Gather candidate elements: <li> and <article>
        candidates = await page.query_selector_all("li, article")
        count = 0
        for el in candidates:
            try:
                el_text = (await el.inner_text()).lower()
            except Exception:  # noqa: BLE001
                continue
            if any(kw in el_text for kw in JOB_CONTENT_KEYWORDS):
                count += 1

        return (count if count > 0 else None), tech
