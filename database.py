"""
database.py — Alfaleus Async Motor Client  [Agent 1]
Motor async MongoDB client with connection pooling, index management,
and a FastAPI-compatible dependency injection function.
"""
from __future__ import annotations

import logging
from typing import AsyncGenerator, Optional

import pymongo
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from config import settings

logger = logging.getLogger(__name__)

# ── Collection Names ──────────────────────────────────────────────────────────
COLLECTION_OPPORTUNITIES = "opportunities"
COLLECTION_SCRAPER_RUNS = "scraper_runs"

# ── Client Singleton ──────────────────────────────────────────────────────────
_client: Optional[AsyncIOMotorClient] = None


def get_client() -> AsyncIOMotorClient:
    """
    Return the global Motor client, creating it on first call.
    Uses connection pooling defaults (maxPoolSize=100) suitable for
    concurrent async scraper + API workloads.
    """
    global _client
    if _client is None:
        logger.info("Initializing Motor client → %s", settings.mongo_db_name)
        _client = AsyncIOMotorClient(
            settings.mongo_uri,
            serverSelectionTimeoutMS=5_000,
            connectTimeoutMS=5_000,
            socketTimeoutMS=30_000,
            maxPoolSize=50,
            minPoolSize=2,
        )
    return _client


def get_db() -> AsyncIOMotorDatabase:
    """Return the target database handle."""
    return get_client()[settings.mongo_db_name]


async def close_client() -> None:
    """Cleanly close the Motor client on application shutdown."""
    global _client
    if _client is not None:
        logger.info("Closing Motor client connection pool")
        _client.close()
        _client = None


# ── FastAPI Dependency ────────────────────────────────────────────────────────
async def get_db_dep() -> AsyncGenerator[AsyncIOMotorDatabase, None]:
    """
    FastAPI dependency: yields the database and ensures the client is
    available. Does NOT close the client per-request — the pool is shared.
    """
    yield get_db()


# ── Index Management ──────────────────────────────────────────────────────────
async def ensure_indexes() -> None:
    """
    Idempotently create all required MongoDB indexes.
    Called once at application startup via FastAPI lifespan.

    Index strategy:
    - opportunities.url      → unique (Tier 1 dedup: DuplicateKeyError = fast reject)
    - opportunities.hash     → unique (Tier 2 dedup: content hash lookup)
    - opportunities.source   → filter queries from API + dashboard
    - opportunities.deadline → sort/filter by upcoming deadlines
    - opportunities.created_at → time-series dashboard queries
    - scraper_runs.started_at → audit log pagination
    """
    db = get_db()
    opps = db[COLLECTION_OPPORTUNITIES]
    runs = db[COLLECTION_SCRAPER_RUNS]

    logger.info("Ensuring MongoDB indexes...")

    # ── Opportunities collection ──────────────────────────────────
    await opps.create_index(
        [("source_url", pymongo.ASCENDING)],
        unique=True,
        name="source_url_unique",
        background=True,
    )
    await opps.create_index(
        [("content_hash", pymongo.ASCENDING)],
        unique=True,
        name="content_hash_unique",
        background=True,
    )
    await opps.create_index(
        [("source", pymongo.ASCENDING)],
        name="source_filter",
        background=True,
    )
    await opps.create_index(
        [("deadline", pymongo.ASCENDING)],
        name="deadline_sort",
        background=True,
        sparse=True,  # documents without deadline are not indexed
    )
    await opps.create_index(
        [("created_at", pymongo.DESCENDING)],
        name="created_at_desc",
        background=True,
    )
    await opps.create_index(
        [
            ("ai_tags.funding_stage", pymongo.ASCENDING),
            ("ai_tags.is_remote", pymongo.ASCENDING),
        ],
        name="ai_tags_compound",
        background=True,
        sparse=True,
    )
    # Text index for keyword search
    await opps.create_index(
        [("title", pymongo.TEXT), ("description", pymongo.TEXT)],
        name="text_search",
        background=True,
    )

    # ── Scraper runs collection ───────────────────────────────────
    await runs.create_index(
        [("started_at", pymongo.DESCENDING)],
        name="started_at_desc",
        background=True,
    )
    await runs.create_index(
        [("source", pymongo.ASCENDING), ("status", pymongo.ASCENDING)],
        name="source_status",
        background=True,
    )

    logger.info("All MongoDB indexes verified ✓")


async def ping_database() -> bool:
    """Health check: return True if MongoDB is reachable."""
    try:
        await get_client().admin.command("ping")
        return True
    except Exception as exc:
        logger.error("MongoDB ping failed: %s", exc)
        return False
