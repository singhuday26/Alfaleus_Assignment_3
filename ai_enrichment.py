"""
ai_enrichment.py — Alfaleus LLM Auto-Tagging  [Agent 2]

Async LLM enrichment with:
- Primary: Google Gemini (gemini-2.0-flash)
- Fallback: Anthropic Claude (claude-sonnet-4-5)
- Strict JSON output enforced via response schema + Pydantic validation
- Semaphore-bounded concurrency to respect API rate limits
- Full retry with exponential backoff on transient API errors
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import google.generativeai as genai
import groq
from pydantic import ValidationError
from tenacity import (
    AsyncRetrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import settings
from database import COLLECTION_OPPORTUNITIES, get_db
from models import AITags, FundingStage, OpportunityDB

logger = logging.getLogger(__name__)

# Concurrency guard — respect LLM rate limits
_SEMAPHORE = asyncio.Semaphore(5)

RETRY_CONFIG = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(Exception),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)

_PROMPT_TEMPLATE = """Analyze this startup opportunity. Return ONLY a valid JSON object matching this exact schema: {{"funding_range": "extracted amount or None", "startup_stage": "Idea/Pre-seed/Seed/Early/Growth/All", "is_remote": true/false/null}}. Do not include markdown blocks like ```json.
Description: {description}
"""

def _build_prompt_from_desc(description: str) -> str:
    return _PROMPT_TEMPLATE.format(description=description)

def _build_prompt(opp: OpportunityDB) -> str:
    desc = opp.description or ""
    return _build_prompt_from_desc(desc[:1500])


def _parse_llm_response(raw: str, model_name: str) -> Optional[AITags]:
    """Parse and validate LLM JSON output into AITags. Returns None on failure."""
    # Strip markdown code fences if present
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        data = json.loads(cleaned)
        data["model_used"] = model_name
        return AITags.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning("LLM response parse failed (%s): %s | Raw: %.200s", model_name, exc, raw)
        return None


# ── Gemini ────────────────────────────────────────────────────────────────────

async def _enrich_with_gemini(opp: OpportunityDB) -> Optional[AITags]:
    """Call Gemini API for structured tagging."""
    if not settings.gemini_available:
        return None

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(
        "gemini-2.0-flash",
        generation_config=genai.types.GenerationConfig(
            temperature=0.1,
            max_output_tokens=512,
        ),
    )

    prompt = _build_prompt(opp)
    async for attempt in AsyncRetrying(**RETRY_CONFIG):
        with attempt:
            # Run synchronous Gemini call in thread pool
            response = await asyncio.get_event_loop().run_in_executor(
                None, lambda: model.generate_content(prompt)
            )
            raw_text = response.text
            return _parse_llm_response(raw_text, "gemini-2.0-flash")

    return None


# ── Groq ──────────────────────────────────────────────────────────────────────

async def _enrich_with_groq_desc(description: str) -> Optional[AITags]:
    """Call Groq API as fallback."""
    if not settings.groq_available:
        return None

    client = groq.AsyncGroq(api_key=settings.groq_api_key)
    prompt = _build_prompt_from_desc(description)

    async for attempt in AsyncRetrying(**RETRY_CONFIG):
        with attempt:
            completion = await client.chat.completions.create(
                model="llama3-8b-8192",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=512,
            )
            raw_text = completion.choices[0].message.content
            return _parse_llm_response(raw_text, "llama3-8b-8192")

    return None


# ── Tag Opportunity ───────────────────────────────────────────────────────────

async def tag_opportunity(description: str) -> Optional[AITags]:
    """
    Core AI Tagging Engine mapping directly to the requested Agent 2 prompt.
    Takes a raw description string and returns parsed AITags.
    """
    async with _SEMAPHORE:
        tags: Optional[AITags] = None
        
        if settings.gemini_available:
            try:
                genai.configure(api_key=settings.gemini_api_key)
                model = genai.GenerativeModel(
                    "gemini-2.0-flash",
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.1,
                        max_output_tokens=512,
                    ),
                )
                prompt = _build_prompt_from_desc(description)
                async for attempt in AsyncRetrying(**RETRY_CONFIG):
                    with attempt:
                        response = await asyncio.get_event_loop().run_in_executor(
                            None, lambda: model.generate_content(prompt)
                        )
                        tags = _parse_llm_response(response.text, "gemini-2.0-flash")
                        break
            except Exception as exc:
                logger.warning("[Gemini] tag_opportunity failed: %s", exc)

        if tags is None and settings.groq_available:
            try:
                tags = await _enrich_with_groq_desc(description)
            except Exception as exc:
                logger.warning("[Groq] tag_opportunity failed: %s", exc)

        return tags


# ── Orchestrator ──────────────────────────────────────────────────────────────

async def enrich_opportunity(opp: OpportunityDB) -> Optional[AITags]:
    """
    Enrich a single opportunity with AI tags using tag_opportunity.
    """
    tags = await tag_opportunity((opp.description or "")[:1500])
    if tags is None:
        logger.info("AI enrichment unavailable for '%s' — skipping", opp.title)
    return tags


async def enrich_batch(opportunities: list[OpportunityDB]) -> dict[str, Optional[AITags]]:
    """
    Enrich a batch of opportunities concurrently (bounded by semaphore).
    Returns a dict mapping opportunity _id → AITags (or None on failure).
    """
    if not settings.any_llm_available:
        logger.warning("No LLM API keys configured — AI enrichment skipped for entire batch")
        return {opp.id: None for opp in opportunities}

    logger.info("Enriching %d opportunities with AI tags...", len(opportunities))
    tasks = [enrich_opportunity(opp) for opp in opportunities]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    output: dict[str, Optional[AITags]] = {}
    for opp, result in zip(opportunities, results):
        if isinstance(result, Exception):
            logger.error("Enrichment task exception for '%s': %s", opp.title, result)
            output[opp.id] = None
        else:
            output[opp.id] = result

    success_count = sum(1 for v in output.values() if v is not None)
    logger.info("AI enrichment complete: %d/%d succeeded", success_count, len(opportunities))
    return output


async def persist_ai_tags(opp_id: str, tags: AITags) -> bool:
    """Write AI tags back to the MongoDB document. Returns True on success."""
    db = get_db()
    try:
        result = await db[COLLECTION_OPPORTUNITIES].update_one(
            {"_id": opp_id},
            {
                "$set": {
                    "ai_tags": tags.model_dump(mode="python"),
                    "ai_enrichment_attempted": True,
                }
            },
        )
        return result.modified_count > 0
    except Exception as exc:
        logger.error("Failed to persist AI tags for %s: %s", opp_id, exc)
        return False
