"""
dedup.py — Alfaleus 3-Tier Deduplication Engine  [Agent 1]

Tier 1 — MongoDB Unique Index (O(1)): DuplicateKeyError on url.
Tier 2 — Content Hash (O(1)): SHA-256(title+url+deadline) lookup.
Tier 3 — rapidfuzz Fuzzy Match (O(N)): token_sort_ratio on recent titles.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from rapidfuzz import fuzz, process

from config import settings
from database import COLLECTION_OPPORTUNITIES
from models import OpportunityDB, OpportunityIn

logger = logging.getLogger(__name__)

_FUZZY_WINDOW_DAYS = 30
_FUZZY_WINDOW_SIZE = 2000


@dataclass
class DedupResult:
    is_duplicate: bool
    tier_caught: Optional[int] = None
    duplicate_of_url: Optional[str] = None
    fuzzy_score: Optional[float] = None
    reason: str = ""


class DeduplicationEngine:
    """Stateful async dedup engine with bounded in-memory fuzzy title cache."""

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._db = db
        self._title_cache: list[tuple[str, str, Optional[datetime]]] = []
        self._cache_loaded_at: Optional[datetime] = None

    async def check(self, opp: OpportunityIn) -> DedupResult:
        """Run all 3 tiers. Returns first duplicate match found."""
        t1 = await self._check_url(opp.source_url)
        if t1.is_duplicate:
            return t1
        t2 = await self._check_content_hash(opp)
        if t2.is_duplicate:
            return t2
        return await self._check_fuzzy(opp)

    async def add_to_cache(self, opp_db: OpportunityDB) -> None:
        """Add newly inserted title to in-memory cache."""
        self._title_cache.append((opp_db.title, opp_db.source_url, opp_db.deadline))
        if len(self._title_cache) > _FUZZY_WINDOW_SIZE:
            self._title_cache = self._title_cache[-_FUZZY_WINDOW_SIZE:]

    async def warm_cache(self) -> None:
        """Pre-load recent titles from MongoDB into fuzzy cache."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=_FUZZY_WINDOW_DAYS)
        cursor = (
            self._db[COLLECTION_OPPORTUNITIES]
            .find({"created_at": {"$gte": cutoff}}, projection={"title": 1, "source_url": 1, "deadline": 1, "_id": 0})
            .sort("created_at", -1)
            .limit(_FUZZY_WINDOW_SIZE)
        )
        self._title_cache = [(doc.get("title", ""), doc.get("source_url", ""), doc.get("deadline")) async for doc in cursor]
        self._cache_loaded_at = datetime.now(timezone.utc)
        logger.info("Fuzzy cache warmed: %d titles loaded", len(self._title_cache))

    async def _check_url(self, url: str) -> DedupResult:
        existing = await self._db[COLLECTION_OPPORTUNITIES].find_one(
            {"source_url": url}, projection={"source_url": 1, "_id": 0}
        )
        if existing:
            logger.debug("Tier 1 CATCH: source_url=%s", url)
            return DedupResult(is_duplicate=True, tier_caught=1, duplicate_of_url=url, reason="source_url_exists")
        return DedupResult(is_duplicate=False)

    async def _check_content_hash(self, opp: OpportunityIn) -> DedupResult:
        content_hash = opp.compute_content_hash()
        existing = await self._db[COLLECTION_OPPORTUNITIES].find_one(
            {"content_hash": content_hash}, projection={"source_url": 1, "_id": 0}
        )
        if existing:
            logger.debug("Tier 2 CATCH: hash match for source_url=%s", existing.get("source_url"))
            return DedupResult(
                is_duplicate=True, tier_caught=2,
                duplicate_of_url=existing.get("source_url"), reason="content_hash_match"
            )
        return DedupResult(is_duplicate=False)

    async def _check_fuzzy(self, opp: OpportunityIn) -> DedupResult:
        if not self._title_cache:
            try:
                await self.warm_cache()
            except Exception as exc:
                logger.warning("Fuzzy cache warm failed, skipping Tier 3: %s", exc)
                return DedupResult(is_duplicate=False, reason="fuzzy_skipped")

        if not self._title_cache:
            return DedupResult(is_duplicate=False, reason="fuzzy_empty_cache")

        titles_only = [t for t, _, _ in self._title_cache]
        results = process.extract(
            opp.title, titles_only,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=settings.fuzzy_threshold,
            limit=5
        )
        for matched_title, score, idx in results:
            _, matched_url, matched_deadline = self._title_cache[idx]
            
            # Check deadline constraint: a >85% title match should ONLY trigger a duplicate flag if the deadlines are within 3 days of each other.
            if opp.deadline and matched_deadline:
                if abs((opp.deadline - matched_deadline).total_seconds()) > 3 * 86400:
                    continue  # Deadlines are more than 3 days apart
            elif (opp.deadline is not None) != (matched_deadline is not None):
                continue  # One has a deadline, the other doesn't
                
            logger.debug("Tier 3 CATCH: score=%.1f '%s' ~ '%s'", score, opp.title, matched_title)
            return DedupResult(
                is_duplicate=True, tier_caught=3,
                duplicate_of_url=matched_url, fuzzy_score=float(score),
                reason=f"fuzzy_score_{score:.0f}"
            )
        return DedupResult(is_duplicate=False, reason="passed_all_tiers")


async def handle_duplicate_key_error(url: str, db: AsyncIOMotorDatabase) -> None:
    """Log and record race-condition duplicate inserts."""
    logger.info("DuplicateKeyError race condition: source_url=%s", url)
    try:
        await db[COLLECTION_OPPORTUNITIES].update_one(
            {"source_url": url}, {"$inc": {"_collision_count": 1}}
        )
    except Exception:
        pass
