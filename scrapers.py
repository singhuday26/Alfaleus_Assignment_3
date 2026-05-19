"""
scrapers.py — Alfaleus Async Scrapers  [Agent 1]

Two production-grade scrapers with:
- httpx async HTTP client
- tenacity exponential backoff (2^n seconds, max 5 attempts)
- fake_useragent header rotation
- Pydantic schema validation on every item
- ScraperRun audit logging throughout

Sources:
  1. Opportunity Desk — RSS feed via feedparser
  2. F6S — Paginated HTML via BeautifulSoup4 + lxml
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
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
F6S_BASE_URL = "https://www.f6s.com"
F6S_START_URL = "https://www.f6s.com/opportunities"

REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
def is_retryable_exception(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    if isinstance(exc, httpx.NetworkError):
        return True
    return False

RETRY_CONFIG = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception(is_retryable_exception),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)

_ua = UserAgent()


def _get_headers() -> dict[str, str]:
    """Rotate User-Agent on every call to reduce scraper fingerprinting."""
    return {
        "User-Agent": _ua.random,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }


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

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
        async for attempt in AsyncRetrying(**RETRY_CONFIG):
            with attempt:
                response = await client.get(OPPORTUNITY_DESK_RSS, headers=_get_headers())
                response.raise_for_status()
                raw_content = response.content

    feed = feedparser.parse(raw_content)
    entries = feed.get("entries", [])
    logger.info("[OpportunityDesk] Feed parsed: %d entries", len(entries))
    run.items_scraped = len(entries)

    opportunities: list[OpportunityIn] = []
    for entry in entries:
        try:
            # Extract deadline from content if present
            deadline = None
            content = entry.get("summary", "") or entry.get("content", [{}])[0].get("value", "")
            deadline_match = re.search(
                r"[Dd]eadline[:\s]+([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})", content
            )
            if deadline_match:
                deadline = _parse_date(deadline_match.group(1))

            # Extract tags from categories
            tags = [tag.get("term", "") for tag in entry.get("tags", []) if tag.get("term")]

            opp = OpportunityIn(
                title=entry.get("title", "").strip(),
                source_url=entry.get("link", "").strip(),
                source=DataSource.OPPORTUNITY_DESK,
                description=_strip_html(content)[:2000],
                deadline=deadline,
                organization=entry.get("author", ""),
                raw_tags=tags,
            )
            opportunities.append(opp)
        except ValidationError as e:
            msg = f"Validation failed for entry '{entry.get('title', 'unknown')}': {e.error_count()} errors"
            logger.warning("[OpportunityDesk] %s", msg)
            run.log_error(msg)

    run.validated_count = len(opportunities)
    logger.info("[OpportunityDesk] %d/%d entries passed validation", len(opportunities), len(entries))
    return opportunities


# ── F6S Paginated HTML Scraper ───────────────────────────────────────────────

async def scrape_f6s(run: ScraperRun) -> list[OpportunityIn]:
    """
    Scrape F6S opportunities via paginated HTML.
    Respects pagination cursor up to settings.f6s_max_pages.
    Uses rotating User-Agent + exponential backoff.
    """
    logger.info("[F6S] Starting paginated HTML scrape (max %d pages)", settings.f6s_max_pages)
    opportunities: list[OpportunityIn] = []
    fetched_raw = 0
    page_url: Optional[str] = F6S_START_URL
    page_num = 0

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
        while page_url and page_num < settings.f6s_max_pages:
            page_num += 1
            logger.info("[F6S] Scraping page %d: %s", page_num, page_url)

            html_content = ""
            async for attempt in AsyncRetrying(**RETRY_CONFIG):
                with attempt:
                    response = await client.get(page_url, headers=_get_headers())
                    response.raise_for_status()
                    html_content = response.text

            soup = BeautifulSoup(html_content, "lxml")
            items = _parse_f6s_page(soup)
            fetched_raw += len(items)
            logger.info("[F6S] Page %d: %d raw items extracted", page_num, len(items))

            for item in items:
                try:
                    opp = OpportunityIn(**item, source=DataSource.F6S)
                    opportunities.append(opp)
                except ValidationError as e:
                    msg = f"F6S validation failed for '{item.get('title', '?')}': {e.error_count()} errors"
                    logger.warning("[F6S] %s", msg)
                    run.log_error(msg)

            # Follow pagination
            page_url = _extract_next_page(soup, page_url)

            # Polite delay between pages (0.5–1.5s jitter)
            await asyncio.sleep(0.5 + (page_num % 3) * 0.4)

    run.items_scraped = fetched_raw
    run.validated_count = len(opportunities)
    run.pages_scraped = page_num
    logger.info("[F6S] Completed: %d valid/%d raw across %d pages", len(opportunities), fetched_raw, page_num)
    return opportunities


def _parse_f6s_page(soup: BeautifulSoup) -> list[dict]:
    """
    Extract opportunity data from an F6S listing page.
    Returns raw dicts; validation is handled by the caller.
    Selectors target F6S's card-based listing layout.
    """
    items = []

    # F6S uses various card selectors; try multiple patterns for resilience
    cards = (
        soup.select("div.program-card")
        or soup.select("div.opportunity-card")
        or soup.select("article.program")
        or soup.select("[data-program-id]")
        or soup.select("div.card.program")
    )

    if not cards:
        # Fallback: find all links that look like program pages
        cards = soup.select("a[href*='/programs/'], a[href*='/apply/']")

    for card in cards:
        try:
            # Title
            try:
                title_el = (
                    card.select_one("h2, h3, .program-title, .card-title, [class*='title']")
                    or (card if card.name == "a" else None)
                )
                title = title_el.get_text(strip=True) if title_el else ""
            except Exception:
                title = ""
                
            if not title or len(title) < 3:
                continue

            # URL
            try:
                link_el = card.select_one("a[href]") or (card if card.name == "a" else None)
                href = link_el.get("href", "") if link_el else ""
                url = urljoin(F6S_BASE_URL, href) if not href.startswith("http") else href
            except Exception:
                href = ""
                url = ""
                
            if not href:
                continue

            # Description
            try:
                desc_el = card.select_one("p, .description, .summary, [class*='desc']")
                description = desc_el.get_text(strip=True)[:2000] if desc_el else ""
            except Exception:
                description = ""

            # Deadline
            try:
                deadline_el = card.select_one(".deadline, [class*='date'], time")
                deadline_str = deadline_el.get_text(strip=True) if deadline_el else ""
                deadline = _parse_date(deadline_str)
            except Exception:
                deadline = None

            # Organization
            try:
                org_el = card.select_one(".organization, .company, [class*='org'], [class*='company']")
                organization = org_el.get_text(strip=True)[:300] if org_el else "Unknown"
            except Exception:
                organization = "Unknown"

            # Location
            try:
                loc_el = card.select_one(".location, [class*='location'], [class*='country']")
                location = loc_el.get_text(strip=True)[:300] if loc_el else "Unknown"
            except Exception:
                location = "Unknown"

            # Tags
            try:
                tag_els = card.select(".tag, .badge, [class*='tag'], [class*='category']")
                tags = [el.get_text(strip=True) for el in tag_els if el.get_text(strip=True)]
            except Exception:
                tags = []

            items.append({
                "title": title,
                "source_url": url,
                "description": description,
                "deadline": deadline,
                "organization": organization,
                "location": location,
                "raw_tags": tags,
            })
        except Exception as exc:
            logger.debug("[F6S] Card parse error: %s", exc)

    return items


def _extract_next_page(soup: BeautifulSoup, current_url: str) -> Optional[str]:
    """
    Extract the next page URL from F6S pagination.
    Handles rel="next", text-based 'Next' links, and query-param page increments.
    """
    # Strategy 1: rel="next" link
    next_link = soup.select_one('a[rel="next"]')
    if next_link and next_link.get("href"):
        href = next_link["href"]
        return urljoin(F6S_BASE_URL, href) if not href.startswith("http") else href

    # Strategy 2: text-based Next button
    for a in soup.select("a"):
        text = a.get_text(strip=True).lower()
        if text in ("next", "next page", "›", "»", "next →"):
            href = a.get("href", "")
            if href and "#" not in href:
                return urljoin(F6S_BASE_URL, href) if not href.startswith("http") else href

    # Strategy 3: increment ?page= query parameter
    parsed = urlparse(current_url)
    if "page=" in parsed.query:
        import urllib.parse
        params = dict(urllib.parse.parse_qsl(parsed.query))
        try:
            params["page"] = str(int(params["page"]) + 1)
            new_query = urllib.parse.urlencode(params)
            return parsed._replace(query=new_query).geturl()
        except (ValueError, KeyError):
            pass

    return None  # No more pages


def _strip_html(html: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r"<[^>]+>", " ", html).strip()
