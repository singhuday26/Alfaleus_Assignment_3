"""tests/test_analytics.py — Unit tests for Ingestion Analytics engine."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from models import DataSource, OpportunityIn, OpportunityDB, ScraperRun, ScraperStatus
from utils import calculate_text_metrics

def test_calculate_text_metrics():
    """Verify that calculate_text_metrics computes character, word, element, and density ratios correctly."""
    raw_html = "<div><p>This is a <b>test</b> description for analytics.</p><ul><li>First Item</li><li>Second Item</li></ul></div>"
    clean_text = "This is a test description for analytics. First Item Second Item"

    metrics = calculate_text_metrics(raw_html, clean_text)

    # Word count and character count of clean text
    assert metrics.character_count == len(clean_text)
    assert metrics.word_count == len(clean_text.split())

    # Count tags: <div>, <p>, <b>, </p>, </b>, <ul>, <li>, </li>, <li>, </li>, </ul>, </div> = 12 HTML elements
    # Using regex: <([a-zA-Z1-6]+)...> maps opening tags.
    # Opening tags in raw_html: <div>, <p>, <b>, <ul>, <li>, <li> = 6 opening elements.
    assert metrics.html_element_count == 6
    assert metrics.list_item_count == 2
    assert metrics.text_ratio == round(6 / len(clean_text), 4)
    assert metrics.scraped_at is not None

def test_opportunity_db_propagates_analytics():
    """Check that OpportunityDB inherits analytics from OpportunityIn or defaults to fallback."""
    raw_html = "<p>Fellowship program</p>"
    clean_text = "Fellowship program"
    analytics_obj = calculate_text_metrics(raw_html, clean_text)

    opp_in = OpportunityIn(
        title="Medtech Fellowship",
        source_url="https://example.com/fellowship",
        source=DataSource.OPPORTUNITY_DESK,
        description=clean_text,
        analytics=analytics_obj
    )

    opp_db = OpportunityDB.from_ingest(opp_in)
    assert opp_db.analytics is not None
    assert opp_db.analytics.character_count == len(clean_text)
    assert opp_db.analytics.word_count == len(clean_text.split())
    assert opp_db.analytics.html_element_count == 1
    assert opp_db.analytics.list_item_count == 0

def test_scraper_run_telemetry():
    """Verify ScraperRun.finalize calculates duration, velocity throughput, and source distribution."""
    run = ScraperRun(source=DataSource.OPPORTUNITY_DESK)
    
    # Finalize immediately to calculate analytics
    finalized = run.finalize(
        status=ScraperStatus.SUCCESS,
        items_scraped=10,
        validated=10,
        items_added=8,
        items_duplicate=2
    )

    assert finalized.analytics is not None
    assert "execution_duration_seconds" in finalized.analytics
    assert finalized.analytics["throughput_velocity"] >= 0
    assert finalized.analytics["source_distribution"] == {"opportunity_desk": 1.0}
