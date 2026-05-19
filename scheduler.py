"""
scheduler.py — Alfaleus APScheduler Orchestrator  [Agent 2]

Wires the full pipeline: scrape → dedup → persist → AI enrich.
Cron job fires every SCRAPE_INTERVAL_HOURS hours (default: 6).
ScraperRun audit documents are written to MongoDB for every execution.
"""
from __future__ import annotations

import logging
from typing import Optional

import pymongo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import settings
from database import (
    COLLECTION_OPPORTUNITIES,
    COLLECTION_SCRAPER_RUNS,
    get_db,
)
from models import DataSource, OpportunityDB, ScraperRun, ScraperStatus

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None


# ── Pipeline Core ─────────────────────────────────────────────────────────────

async def _run_source_pipeline(source: DataSource) -> ScraperRun:
    """
    Execute the full pipeline for a single source:
    1. Scrape → list[OpportunityIn]
    2. Dedup each item (3-tier)
    3. Insert new items to MongoDB
    4. AI enrich the newly inserted batch
    5. Persist updated ScraperRun audit doc
    """
    from scrapers import scrape_opportunity_desk, scrape_techcrunch
    from dedup import DeduplicationEngine, handle_duplicate_key_error
    from ai_enrichment import enrich_batch, persist_ai_tags

    db = get_db()
    run = ScraperRun(source=source)

    # Persist run start immediately so dashboard shows "running" status
    await db[COLLECTION_SCRAPER_RUNS].insert_one(run.to_mongo_doc())
    logger.info("[Scheduler] Pipeline started: source=%s run_id=%s", source.value, run.run_id)

    try:
        # ── Step 1: Scrape ────────────────────────────────────────
        if source == DataSource.OPPORTUNITY_DESK:
            raw_items = await scrape_opportunity_desk(run)
        elif source == DataSource.TECHCRUNCH:
            raw_items = await scrape_techcrunch(run)
        else:
            run.log_error(f"Unknown source: {source.value}")
            return await _finalize_run(run, ScraperStatus.FAILED, db)

        # ── Step 2 & 3: Dedup + Persist ──────────────────────────
        engine = DeduplicationEngine(db)
        await engine.warm_cache()

        new_docs: list[OpportunityDB] = []
        duplicate_count = 0

        for opp_in in raw_items:
            dedup_result = await engine.check(opp_in)

            if dedup_result.is_duplicate:
                duplicate_count += 1
                logger.debug(
                    "DEDUP Tier %s: '%s' → %s",
                    dedup_result.tier_caught, opp_in.title, dedup_result.reason
                )
                continue

            # Build DB document
            opp_db = OpportunityDB.from_ingest(opp_in)

            try:
                await db[COLLECTION_OPPORTUNITIES].insert_one(opp_db.to_mongo_doc())
                new_docs.append(opp_db)
                await engine.add_to_cache(opp_db)
            except pymongo.errors.DuplicateKeyError:
                duplicate_count += 1
                await handle_duplicate_key_error(opp_in.source_url, db)

        logger.info(
            "[Scheduler] %s: inserted=%d duplicates=%d",
            source.value, len(new_docs), duplicate_count
        )

        # ── Step 4: AI Enrichment (non-blocking on failure) ───────
        if new_docs and settings.any_llm_available:
            try:
                tag_results = await enrich_batch(new_docs)
                for opp_id, tags in tag_results.items():
                    if tags is not None:
                        await persist_ai_tags(opp_id, tags)
            except Exception as exc:
                run.log_error(f"AI enrichment batch failed: {exc}")
                logger.error("[Scheduler] AI enrichment error: %s", exc)

        # ── Step 5: Finalize run ──────────────────────────────────
        return await _finalize_run(
            run, ScraperStatus.SUCCESS, db,
            items_scraped=run.items_scraped,
            validated=run.validated_count,
            items_added=len(new_docs),
            items_duplicate=duplicate_count,
            pages=getattr(run, "pages_scraped", 0),
        )

    except Exception as exc:
        run.log_error(f"Pipeline exception: {exc}")
        logger.exception("[Scheduler] Pipeline failed for %s: %s", source.value, exc)
        return await _finalize_run(run, ScraperStatus.FAILED, db)


async def _finalize_run(
    run: ScraperRun,
    final_status: ScraperStatus,
    db,
    **kwargs,
) -> ScraperRun:
    """Update the ScraperRun document in MongoDB with final stats."""
    run = run.finalize(status=final_status, **kwargs)
    await db[COLLECTION_SCRAPER_RUNS].update_one(
        {"_id": run.run_id},
        {"$set": run.to_mongo_doc()},
        upsert=True,
    )
    logger.info(
        "[Scheduler] Run finalized: id=%s status=%s inserted=%d",
        run.run_id, run.status.value, run.items_added
    )
    return run


# ── Scheduled Job ─────────────────────────────────────────────────────────────

async def run_pipeline() -> None:
    """APScheduler job: run pipeline for all sources sequentially."""
    logger.info("[Scheduler] Cron triggered — running all sources")
    for source in [DataSource.OPPORTUNITY_DESK, DataSource.TECHCRUNCH]:
        try:
            await _run_source_pipeline(source)
        except Exception as exc:
            logger.error("[Scheduler] Source %s failed: %s", source.value, exc)
    logger.info("[Scheduler] All sources complete")


async def run_pipeline_now(source: Optional[DataSource] = None) -> None:
    """
    Manually trigger the pipeline (called from POST /run-scraper).
    Runs in background without blocking the API response.
    """
    if source is None:
        await run_pipeline()
    else:
        await _run_source_pipeline(source)


# ── Scheduler Lifecycle ────────────────────────────────────────────────────────

async def start_scheduler() -> None:
    """Start APScheduler with the configured interval cron job."""
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        run_pipeline,
        trigger=CronTrigger(hour="*/6"),
        id="pipeline_cron",
        name="Full pipeline every 6h",
        max_instances=1,           # Prevent overlapping runs
        coalesce=True,             # Skip missed runs (e.g., after downtime)
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "[Scheduler] APScheduler started — cron every %dh",
        settings.scrape_interval_hours
    )


async def shutdown_scheduler() -> None:
    """Gracefully shut down the scheduler on API shutdown."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[Scheduler] APScheduler shut down")
    _scheduler = None
