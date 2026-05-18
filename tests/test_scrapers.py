"""tests/test_scrapers.py — Unit tests for async scrapers with mocked HTTP."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from models import DataSource, ScraperRun, ScraperStatus


def _make_run(source=DataSource.OPPORTUNITY_DESK) -> ScraperRun:
    return ScraperRun(source=source)


MOCK_RSS_CONTENT = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Opportunity Desk</title>
    <item>
      <title>2025 Global Health Innovation Fellowship</title>
      <link>https://opportunitydesk.org/2025/health-fellowship</link>
      <description>Apply by December 31, 2025. Deadline: December 31, 2025.</description>
      <author>WHO Foundation</author>
      <category>Health</category>
      <category>Fellowship</category>
    </item>
    <item>
      <title>Climate Tech Accelerator Program</title>
      <link>https://opportunitydesk.org/2025/climate-accelerator</link>
      <description>Funding for climate startups. Deadline: November 15, 2025.</description>
    </item>
  </channel>
</rss>"""

MOCK_F6S_HTML = """<!DOCTYPE html>
<html>
<body>
  <div class="program-card">
    <h3 class="program-title">MedTech Seed Fund 2025</h3>
    <a href="/programs/medtech-seed-2025">Apply</a>
    <p class="description">Seed funding for medical technology startups.</p>
    <span class="deadline">October 30, 2025</span>
    <span class="organization">HealthVC Partners</span>
    <span class="location">London, UK</span>
    <span class="tag">medtech</span>
    <span class="tag">seed</span>
  </div>
  <div class="program-card">
    <h3 class="program-title">AI Healthcare Grant</h3>
    <a href="/programs/ai-health-grant">Apply</a>
    <p class="description">Grants for AI applications in healthcare.</p>
    <span class="organization">Digital Health Fund</span>
    <span class="tag">ai</span>
    <span class="tag">healthtech</span>
  </div>
  <a rel="next" href="/opportunities?page=2">Next</a>
</body>
</html>"""


class TestOpportunityDeskScraper:
    @pytest.mark.asyncio
    async def test_returns_valid_opportunities(self):
        """Scraper should return OpportunityIn objects from valid RSS."""
        mock_response = MagicMock()
        mock_response.content = MOCK_RSS_CONTENT
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.get = AsyncMock(return_value=mock_response)

            from scrapers import scrape_opportunity_desk
            run = _make_run()
            opps = await scrape_opportunity_desk(run)

        assert len(opps) == 2
        assert all(o.source == DataSource.OPPORTUNITY_DESK for o in opps)
        assert opps[0].title == "2025 Global Health Innovation Fellowship"
        assert "opportunitydesk.org" in opps[0].url

    @pytest.mark.asyncio
    async def test_run_counts_updated(self):
        """ScraperRun counts should be updated after scraping."""
        mock_response = MagicMock()
        mock_response.content = MOCK_RSS_CONTENT
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.get = AsyncMock(return_value=mock_response)

            from scrapers import scrape_opportunity_desk
            run = _make_run()
            opps = await scrape_opportunity_desk(run)

        assert run.fetched_count == 2
        assert run.validated_count == 2

    @pytest.mark.asyncio
    async def test_invalid_url_skipped(self):
        """Items with invalid URLs should be skipped (Pydantic validation)."""
        bad_rss = b"""<?xml version="1.0"?>
        <rss version="2.0"><channel>
          <item><title>Bad Item</title><link>not-a-url</link><description>test</description></item>
          <item><title>Good Item</title><link>https://example.com/good</link><description>test</description></item>
        </channel></rss>"""

        mock_response = MagicMock()
        mock_response.content = bad_rss
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.get = AsyncMock(return_value=mock_response)

            from scrapers import scrape_opportunity_desk
            run = _make_run()
            opps = await scrape_opportunity_desk(run)

        # Only the valid item should be returned
        assert len(opps) == 1
        assert len(run.error_log) >= 1  # Bad item should be logged


class TestF6SScraper:
    @pytest.mark.asyncio
    async def test_parses_cards_correctly(self):
        """F6S scraper should extract program cards from HTML."""
        mock_response = MagicMock()
        mock_response.text = MOCK_F6S_HTML
        mock_response.raise_for_status = MagicMock()

        # Return HTML for page 1, then no next page for page 2
        mock_response_p2 = MagicMock()
        mock_response_p2.text = "<html><body><p>No programs</p></body></html>"
        mock_response_p2.raise_for_status = MagicMock()

        responses = [mock_response, mock_response_p2]

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.get = AsyncMock(side_effect=responses)

            with patch("asyncio.sleep", new_callable=AsyncMock):
                from scrapers import scrape_f6s
                run = _make_run(DataSource.F6S)
                opps = await scrape_f6s(run)

        assert len(opps) >= 1
        assert all(o.source == DataSource.F6S for o in opps)
        titles = [o.title for o in opps]
        assert any("MedTech" in t for t in titles)

    @pytest.mark.asyncio
    async def test_respects_max_pages(self):
        """F6S scraper must not exceed MAX_PAGES setting."""
        mock_response = MagicMock()
        # Every page has a "next" link to simulate infinite pagination
        mock_response.text = MOCK_F6S_HTML
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.get = AsyncMock(return_value=mock_response)

            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch("scrapers.settings") as mock_settings:
                    mock_settings.f6s_max_pages = 2
                    mock_settings.fuzzy_threshold = 85

                    from scrapers import scrape_f6s
                    run = _make_run(DataSource.F6S)
                    await scrape_f6s(run)

        assert run.pages_scraped <= 2
