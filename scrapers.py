"""
scrapers.py — Alfaleus Async Scrapers  [Agent 1]

Two production-grade scrapers with:
- curl_cffi async HTTP client
- tenacity exponential backoff (3 attempts, 2–10s)
- Pydantic schema validation on every item
- ScraperRun audit logging throughout

Sources:
  1. Opportunity Desk — RSS/XML feed via BeautifulSoup
  2. TechCrunch Startups — RSS/XML feed via BeautifulSoup
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlparse

import feedparser
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.errors import RequestsError
from bs4 import BeautifulSoup
from pydantic import ValidationError
from tenacity import (
    AsyncRetrying,
    before_sleep_log,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from config import settings
from models import DataSource, OpportunityIn, ScraperRun, ScraperStatus

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
OPPORTUNITY_DESK_RSS = "https://opportunitydesk.org/feed/"
TECHCRUNCH_RSS = "https://techcrunch.com/category/startups/feed/"

REQUEST_TIMEOUT = 30.0
def is_retryable_exception(exc: BaseException) -> bool:
    if isinstance(exc, RequestsError):
        if hasattr(exc, "response") and exc.response:
            return exc.response.status_code == 429 or exc.response.status_code >= 500
        return True
    return False

RETRY_CONFIG = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception(is_retryable_exception),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)




def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """Safely parse various date string formats to timezone-aware datetime."""
    if not date_str:
        return None
    from dateutil import parser as dateutil_parser
    try:
        dt = dateutil_parser.parse(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


# ── Opportunity Desk RSS Scraper ─────────────────────────────────────────────

async def scrape_opportunity_desk(run: ScraperRun) -> list[OpportunityIn]:
    """
    Scrape Opportunity Desk RSS feed.
    Uses feedparser for RSS parsing, httpx for the raw fetch with retry.
    Returns validated OpportunityIn list; invalid items are logged and skipped.
    """
    logger.info("[OpportunityDesk] Starting RSS scrape: %s", OPPORTUNITY_DESK_RSS)
    raw_content: bytes = b""

    async with AsyncSession(impersonate="chrome110", timeout=REQUEST_TIMEOUT) as client:
        async for attempt in AsyncRetrying(**RETRY_CONFIG):
            with attempt:
                response = await client.get(OPPORTUNITY_DESK_RSS)
                print(f"[DEBUG] OpportunityDesk GET {response.url} - Status: {response.status_code}")
                if "Just a moment" in response.text:
                    print("!!! WARNING [OpportunityDesk] Possible Cloudflare bot protection detected! !!!")
                response.raise_for_status()
                raw_content = response.content

    soup = BeautifulSoup(raw_content, "xml")
    entries = soup.find_all("item")
    print(f"[DEBUG] OpportunityDesk RSS: Found {len(entries)} <item> elements")
    logger.info("[OpportunityDesk] Feed parsed: %d entries", len(entries))
    run.items_scraped = len(entries)

    opportunities: list[OpportunityIn] = []
    for entry in entries:
        try:
            title_tag = entry.find("title")
            title = title_tag.get_text(strip=True) if title_tag else ""
            
            link_tag = entry.find("link")
            link = link_tag.get_text(strip=True) if link_tag else ""
            
            desc_tag = entry.find("description")
            description = desc_tag.get_text(strip=True) if desc_tag else ""
            
            content_encoded = entry.find("content:encoded")
            content = content_encoded.get_text(strip=True) if content_encoded else description

            # Extract deadline from content if present
            deadline = None
            deadline_match = re.search(
                r"[Dd]eadline[:\s]+([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})", content
            )
            if deadline_match:
                deadline = _parse_date(deadline_match.group(1))

            # Extract tags from categories
            tags = [tag.get_text(strip=True) for tag in entry.find_all("category") if tag.get_text(strip=True)]

            from utils import calculate_text_metrics
            clean_desc = _strip_html(content)[:2000]
            analytics_obj = calculate_text_metrics(content, clean_desc)

            opp = OpportunityIn(
                title=title,
                source_url=link,
                source=DataSource.OPPORTUNITY_DESK,
                description=clean_desc,
                deadline=deadline,
                organization="",
                raw_tags=tags,
                analytics=analytics_obj,
            )
            opportunities.append(opp)
        except ValidationError as e:
            msg = f"Validation failed for entry '{entry.get('title', 'unknown')}': {e.error_count()} errors"
            logger.warning("[OpportunityDesk] %s", msg)
            run.log_error(msg)

    run.validated_count = len(opportunities)
    logger.info("[OpportunityDesk] %d/%d entries passed validation", len(opportunities), len(entries))
    return opportunities


# ── TechCrunch Startups RSS Scraper ─────────────────────────────────────────

async def scrape_techcrunch(run: ScraperRun) -> list[OpportunityIn]:
    """
    Scrape TechCrunch Startups RSS feed.
    Uses BeautifulSoup XML parser (identical pattern to Opportunity Desk).
    Returns validated OpportunityIn list; invalid items are logged and skipped.
    """
    logger.info("[TechCrunch] Starting RSS scrape: %s", TECHCRUNCH_RSS)
    raw_content: bytes = b""

    async with AsyncSession(impersonate="chrome110", timeout=REQUEST_TIMEOUT) as client:
        async for attempt in AsyncRetrying(**RETRY_CONFIG):
            with attempt:
                response = await client.get(TECHCRUNCH_RSS)
                print(f"[DEBUG] TechCrunch GET {response.url} - Status: {response.status_code}")
                response.raise_for_status()
                raw_content = response.content

    soup = BeautifulSoup(raw_content, "xml")
    entries = soup.find_all("item")
    print(f"[DEBUG] TechCrunch RSS: Found {len(entries)} <item> elements")
    logger.info("[TechCrunch] Feed parsed: %d entries", len(entries))
    run.items_scraped = len(entries)

    opportunities: list[OpportunityIn] = []
    for entry in entries:
        try:
            title_tag = entry.find("title")
            title = title_tag.get_text(strip=True) if title_tag else ""

            link_tag = entry.find("link")
            link = link_tag.get_text(strip=True) if link_tag else ""

            desc_tag = entry.find("description")
            description = desc_tag.get_text(strip=True) if desc_tag else ""

            # TechCrunch articles rarely carry deadlines; keep field None
            deadline = None

            # Author / creator maps to organization
            creator_tag = entry.find("dc:creator") or entry.find("creator")
            organization = creator_tag.get_text(strip=True) if creator_tag else "TechCrunch"

            # Extract tags from <category> elements
            tags = [
                tag.get_text(strip=True)
                for tag in entry.find_all("category")
                if tag.get_text(strip=True)
            ]

            from utils import calculate_text_metrics
            clean_desc = _strip_html(description)[:2000]
            analytics_obj = calculate_text_metrics(description, clean_desc)

            opp = OpportunityIn(
                title=title,
                source_url=link,
                source=DataSource.TECHCRUNCH,
                description=clean_desc,
                deadline=deadline,
                organization=organization,
                raw_tags=tags,
                analytics=analytics_obj,
            )
            opportunities.append(opp)
        except ValidationError as e:
            msg = f"Validation failed for TechCrunch entry '{title}': {e.error_count()} errors"
            logger.warning("[TechCrunch] %s", msg)
            run.log_error(msg)

    run.validated_count = len(opportunities)
    logger.info(
        "[TechCrunch] %d/%d entries passed validation",
        len(opportunities), len(entries)
    )
    return opportunities


def _strip_html(html: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r"<[^>]+>", " ", html).strip()
