"""tests/test_dedup.py — Unit tests for the 3-Tier deduplication engine."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import DataSource, OpportunityIn


def _make_opp(**kwargs) -> OpportunityIn:
    defaults = dict(
        title="MedTech Startup Fellowship 2025",
        source_url="https://example.com/fellowship-2025",
        source=DataSource.OPPORTUNITY_DESK,
        description="Apply now for funding",
    )
    defaults.update(kwargs)
    return OpportunityIn(**defaults)


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=MagicMock())
    return db


@pytest.fixture
def engine(mock_db):
    from dedup import DeduplicationEngine
    return DeduplicationEngine(mock_db)


# ── Tier 1 Tests ──────────────────────────────────────────────────────────────

class TestTier1URL:
    @pytest.mark.asyncio
    async def test_catches_existing_url(self, engine, mock_db):
        """Tier 1: URL already in DB → is_duplicate=True, tier_caught=1."""
        mock_db["opportunities"].find_one = AsyncMock(return_value={"source_url": "https://example.com/fellowship-2025"})
        opp = _make_opp()
        result = await engine._check_url(opp.source_url)
        assert result.is_duplicate is True
        assert result.tier_caught == 1

    @pytest.mark.asyncio
    async def test_passes_new_url(self, engine, mock_db):
        """Tier 1: URL not in DB → not a duplicate."""
        mock_db["opportunities"].find_one = AsyncMock(return_value=None)
        opp = _make_opp()
        result = await engine._check_url(opp.source_url)
        assert result.is_duplicate is False


# ── Tier 2 Tests ──────────────────────────────────────────────────────────────

class TestTier2ContentHash:
    @pytest.mark.asyncio
    async def test_catches_hash_collision(self, engine, mock_db):
        """Tier 2: Same content, different URL (UTM params) → caught by hash."""
        opp = _make_opp()
        content_hash = opp.compute_content_hash()

        call_count = 0
        async def mock_find_one(query, projection=None):
            nonlocal call_count
            call_count += 1
            if "source_url" in query:
                return None  # Tier 1: URL not found
            if "content_hash" in query:
                return {"source_url": "https://example.com/original"}  # Tier 2 hit
            return None

        mock_db["opportunities"].find_one = AsyncMock(side_effect=mock_find_one)
        result = await engine._check_content_hash(opp)
        assert result.is_duplicate is True
        assert result.tier_caught == 2

    @pytest.mark.asyncio
    async def test_different_content_passes(self, engine, mock_db):
        """Tier 2: Different content → hash lookup misses."""
        mock_db["opportunities"].find_one = AsyncMock(return_value=None)
        opp = _make_opp(title="Completely Different Grant", source_url="https://example.com/grant")
        result = await engine._check_content_hash(opp)
        assert result.is_duplicate is False

    def test_content_hash_deterministic(self):
        """Same input must always produce the same hash."""
        opp = _make_opp()
        h1 = opp.compute_content_hash()
        h2 = opp.compute_content_hash()
        assert h1 == h2

    def test_content_hash_sensitivity(self):
        """Different titles must produce different hashes."""
        opp1 = _make_opp(title="Fellowship A")
        opp2 = _make_opp(title="Fellowship B")
        assert opp1.compute_content_hash() != opp2.compute_content_hash()


# ── Tier 3 Tests ──────────────────────────────────────────────────────────────

class TestTier3Fuzzy:
    @pytest.mark.asyncio
    async def test_catches_rephrased_title(self, engine, mock_db):
        """Tier 3: Word-reordered title above threshold → caught."""
        engine._title_cache = [
            ("MedTech Startup Fellowship 2025", "https://example.com/orig", None),
            ("Climate Innovation Grant Program", "https://example.com/climate", None),
        ]
        # Slightly rephrased version
        opp = _make_opp(title="2025 Startup Fellowship MedTech", source_url="https://example.com/new")
        result = await engine._check_fuzzy(opp)
        assert result.is_duplicate is True
        assert result.tier_caught == 3
        assert result.fuzzy_score is not None and result.fuzzy_score >= 85

    @pytest.mark.asyncio
    async def test_passes_genuinely_different_title(self, engine, mock_db):
        """Tier 3: Genuinely different title → passes."""
        engine._title_cache = [
            ("MedTech Startup Fellowship 2025", "https://example.com/orig", None),
        ]
        opp = _make_opp(title="Quantum Computing Research Award", source_url="https://example.com/quantum")
        result = await engine._check_fuzzy(opp)
        assert result.is_duplicate is False

    @pytest.mark.asyncio
    async def test_skips_gracefully_with_empty_cache(self, engine, mock_db):
        """Tier 3: Empty cache with failed warm → gracefully skipped."""
        engine._title_cache = []
        mock_db["opportunities"].find = MagicMock()
        # Simulate warm_cache failure
        engine.warm_cache = AsyncMock(side_effect=Exception("DB unreachable"))
        opp = _make_opp()
        result = await engine._check_fuzzy(opp)
        assert result.is_duplicate is False
        assert "skipped" in result.reason


# ── Full Pipeline Tests ───────────────────────────────────────────────────────

class TestFullPipeline:
    @pytest.mark.asyncio
    async def test_new_opportunity_passes_all_tiers(self, engine, mock_db):
        """End-to-end: Brand new opportunity → not a duplicate."""
        mock_db["opportunities"].find_one = AsyncMock(return_value=None)
        engine._title_cache = [("Something Totally Different", "https://other.com", None)]
        opp = _make_opp()
        result = await engine.check(opp)
        assert result.is_duplicate is False
        assert result.reason == "passed_all_tiers"
