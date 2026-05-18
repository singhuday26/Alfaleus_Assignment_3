"""
api.py — Alfaleus FastAPI Application  [Agent 2]

Routes:
  GET  /health           — liveness probe (DB ping included)
  GET  /opportunities    — paginated, filtered opportunity list
  POST /run-scraper      — manual scraper trigger (API key protected)
  GET  /scraper-runs     — scraper audit log (last N runs)
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Any, Optional

import pymongo
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorDatabase

from config import settings
from database import (
    COLLECTION_OPPORTUNITIES,
    COLLECTION_SCRAPER_RUNS,
    close_client,
    ensure_indexes,
    get_db_dep,
    ping_database,
)
from models import DataSource, OpportunityFilter

logger = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: ensure DB indexes and start scheduler. Shutdown: close pool."""
    logger.info("Alfaleus API starting up...")
    await ensure_indexes()

    # Import here to avoid circular imports
    from scheduler import start_scheduler, shutdown_scheduler
    await start_scheduler()

    yield

    logger.info("Alfaleus API shutting down...")
    await shutdown_scheduler()
    await close_client()


# ── App Instance ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="Alfaleus Opportunity Aggregator API",
    description="Production-grade startup opportunity pipeline with 3-tier deduplication and AI enrichment",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB = Annotated[AsyncIOMotorDatabase, Depends(get_db_dep)]


# ── Auth Dependency ───────────────────────────────────────────────────────────

async def verify_api_key(x_api_key: Annotated[str, Header()] = "") -> None:
    """Simple header-based API key check for protected endpoints."""
    if x_api_key != settings.api_secret_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Api-Key header",
        )


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Operations"])
async def health_check() -> dict[str, Any]:
    """Liveness probe. Returns DB connectivity status."""
    db_ok = await ping_database()
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "connected" if db_ok else "unreachable",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": settings.environment,
        "llm": {
            "gemini": settings.gemini_available,
            "claude": settings.claude_available,
        },
    }


# ── Opportunities ─────────────────────────────────────────────────────────────

@app.get("/opportunities", tags=["Opportunities"])
async def list_opportunities(
    db: DB,
    source: Optional[DataSource] = Query(None),
    type: Optional[str] = Query(None, description="Startup stage type (Idea/Pre-seed/Seed/Early/Growth/All)"),
    is_remote: Optional[bool] = Query(None),
    sector_tag: Optional[str] = Query(None, max_length=100),
    keyword: Optional[str] = Query(None, max_length=200),
    deadline_after: Optional[datetime] = Query(None),
    deadline_before: Optional[datetime] = Query(None),
    exclude_duplicates: bool = Query(True),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    """
    Paginated, filtered opportunity list.
    All filters are optional and combinable.
    """
    query: dict[str, Any] = {}

    if exclude_duplicates:
        query["is_duplicate"] = {"$ne": True}
    if source:
        query["source"] = source.value
    if type:
        query["ai_tags.startup_stage"] = type
    if is_remote is not None:
        query["ai_tags.is_remote"] = is_remote
    if sector_tag:
        query["ai_tags.funding_range"] = {"$regex": sector_tag, "$options": "i"} # Re-purpose for funding range search for now
    if keyword:
        query["$text"] = {"$search": keyword}
    if deadline_after or deadline_before:
        deadline_filter: dict[str, Any] = {}
        if deadline_after:
            deadline_filter["$gte"] = deadline_after
        if deadline_before:
            deadline_filter["$lte"] = deadline_before
        query["deadline"] = deadline_filter

    skip = (page - 1) * page_size
    total = await db[COLLECTION_OPPORTUNITIES].count_documents(query)

    cursor = (
        db[COLLECTION_OPPORTUNITIES]
        .find(query)
        .sort("created_at", pymongo.DESCENDING)
        .skip(skip)
        .limit(page_size)
    )

    docs = [_serialize_doc(doc) async for doc in cursor]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),  # ceiling division
        "data": docs,
    }


@app.get("/opportunities/{opportunity_id}", tags=["Opportunities"])
async def get_opportunity(opportunity_id: str, db: DB) -> dict[str, Any]:
    """Fetch a single opportunity by its MongoDB _id."""
    doc = await db[COLLECTION_OPPORTUNITIES].find_one({"_id": opportunity_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return _serialize_doc(doc)


# ── Manual Scraper Trigger ────────────────────────────────────────────────────

@app.post("/run-scraper", tags=["Operations"], dependencies=[Depends(verify_api_key)], status_code=status.HTTP_202_ACCEPTED)
async def trigger_scraper(
    source: Optional[DataSource] = Query(None, description="Scrape specific source, or all if omitted"),
) -> dict[str, str]:
    """
    Manually trigger a scraper run outside the cron schedule.
    Requires X-Api-Key header.
    """
    from scheduler import run_pipeline_now
    asyncio.create_task(run_pipeline_now(source=source))
    return {
        "status": "triggered",
        "source": source.value if source else "all",
        "message": "Scraper pipeline started in background",
    }


# ── Scraper Audit Log ─────────────────────────────────────────────────────────

@app.get("/scraper-runs", tags=["Operations"])
async def list_scraper_runs(
    db: DB,
    limit: int = Query(10, ge=1, le=500),
    source: Optional[DataSource] = Query(None),
) -> dict[str, Any]:
    """Return the scraper audit log, newest first."""
    query: dict[str, Any] = {}
    if source:
        query["source"] = source.value

    cursor = (
        db[COLLECTION_SCRAPER_RUNS]
        .find(query)
        .sort("started_at", pymongo.DESCENDING)
        .limit(limit)
    )
    runs = [_serialize_doc(doc) async for doc in cursor]
    return {"count": len(runs), "data": runs}


# ── Stats Endpoint ────────────────────────────────────────────────────────────

@app.get("/stats", tags=["Operations"])
async def get_stats(db: DB) -> dict[str, Any]:
    """Aggregate stats for the Streamlit dashboard KPI cards."""
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(hours=24)

    total = await db[COLLECTION_OPPORTUNITIES].count_documents({"is_duplicate": {"$ne": True}})
    new_24h = await db[COLLECTION_OPPORTUNITIES].count_documents({
        "is_duplicate": {"$ne": True},
        "created_at": {"$gte": yesterday},
    })
    ai_tagged = await db[COLLECTION_OPPORTUNITIES].count_documents({
        "ai_enrichment_attempted": True,
        "ai_tags": {"$ne": None},
    })
    sources = await db[COLLECTION_OPPORTUNITIES].distinct("source")

    return {
        "total_opportunities": total,
        "new_last_24h": new_24h,
        "ai_tagged_count": ai_tagged,
        "ai_tagged_pct": round(ai_tagged / total * 100, 1) if total else 0.0,
        "active_sources": len(sources),
        "sources": sources,
        "as_of": now.isoformat(),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _serialize_doc(doc: dict) -> dict:
    """Convert MongoDB document to JSON-serializable dict."""
    doc = dict(doc)
    # Rename _id → id for cleaner API response
    if "_id" in doc:
        doc["id"] = str(doc.pop("_id"))
    # Convert datetime objects to ISO strings
    for k, v in doc.items():
        if isinstance(v, datetime):
            doc[k] = v.isoformat()
    return doc


import asyncio  # noqa: E402 — imported here to avoid top-level async issues
