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

MOCK_TECHCRUNCH_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>Startups | TechCrunch</title>
    <item>
      <title>Anduril raises $5B, doubles valuation to $61B</title>
      <link>https://techcrunch.com/2026/05/13/anduril-raises-5b-doubles-valuation-to-61b/</link>
      <dc:creator>Julie Bort</dc:creator>
      <description>After achieving $2.2 billion in revenue in 2025, the defense tech startup raises another round.</description>
      <category>Fundraising</category>
      <category>Startups</category>
      <category>defense tech</category>
    </item>
    <item>
      <title>Nectar Social raises $30M Series A led by Menlo</title>
      <link>https://techcrunch.com/2026/05/16/nectar-social-raises-30m-series-a/</link>
      <dc:creator>Dominic-Madori Davis</dc:creator>
      <description>AI-powered marketing platform Nectar Social announced a $30 million Series A round.</description>
      <category>Startups</category>
      <category>Venture</category>
    </item>
  </channel>
</rss>"""


class TestOpportunityDeskScraper:
    @pytest.mark.asyncio
    async def test_returns_valid_opportunities(self):
        """Scraper should return OpportunityIn objects from valid RSS."""
        mock_response = MagicMock()
        mock_response.content = MOCK_RSS_CONTENT
        mock_response.text = MOCK_RSS_CONTENT.decode()
        mock_response.status_code = 200
        mock_response.url = "https://opportunitydesk.org/feed/"
        mock_response.raise_for_status = MagicMock()

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("scrapers.AsyncSession", return_value=mock_session):
            from scrapers import scrape_opportunity_desk
            run = _make_run()
            opps = await scrape_opportunity_desk(run)

        assert len(opps) == 2
        assert all(o.source == DataSource.OPPORTUNITY_DESK for o in opps)
        assert opps[0].title == "2025 Global Health Innovation Fellowship"
        assert "opportunitydesk.org" in opps[0].source_url

    @pytest.mark.asyncio
    async def test_run_counts_updated(self):
        """ScraperRun counts should be updated after scraping."""
        mock_response = MagicMock()
        mock_response.content = MOCK_RSS_CONTENT
        mock_response.text = MOCK_RSS_CONTENT.decode()
        mock_response.status_code = 200
        mock_response.url = "https://opportunitydesk.org/feed/"
        mock_response.raise_for_status = MagicMock()

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("scrapers.AsyncSession", return_value=mock_session):
            from scrapers import scrape_opportunity_desk
            run = _make_run()
            opps = await scrape_opportunity_desk(run)

        assert run.items_scraped == 2
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
        mock_response.text = bad_rss.decode()
        mock_response.status_code = 200
        mock_response.url = "https://opportunitydesk.org/feed/"
        mock_response.raise_for_status = MagicMock()

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("scrapers.AsyncSession", return_value=mock_session):
            from scrapers import scrape_opportunity_desk
            run = _make_run()
            opps = await scrape_opportunity_desk(run)

        # Only the valid item should be returned
        assert len(opps) == 1
        assert len(run.errors_encountered) >= 1  # Bad item should be logged


class TestTechCrunchScraper:
    @pytest.mark.asyncio
    async def test_returns_valid_opportunities(self):
        """TechCrunch scraper should return OpportunityIn objects from RSS."""
        mock_response = MagicMock()
        mock_response.content = MOCK_TECHCRUNCH_RSS
        mock_response.text = MOCK_TECHCRUNCH_RSS.decode()
        mock_response.status_code = 200
        mock_response.url = "https://techcrunch.com/category/startups/feed/"
        mock_response.raise_for_status = MagicMock()

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("scrapers.AsyncSession", return_value=mock_session):
            from scrapers import scrape_techcrunch
            run = _make_run(DataSource.TECHCRUNCH)
            opps = await scrape_techcrunch(run)

        assert len(opps) == 2
        assert all(o.source == DataSource.TECHCRUNCH for o in opps)
        assert "techcrunch.com" in opps[0].source_url

    @pytest.mark.asyncio
    async def test_run_counts_updated(self):
        """ScraperRun counts should be updated after TechCrunch scrape."""
        mock_response = MagicMock()
        mock_response.content = MOCK_TECHCRUNCH_RSS
        mock_response.text = MOCK_TECHCRUNCH_RSS.decode()
        mock_response.status_code = 200
        mock_response.url = "https://techcrunch.com/category/startups/feed/"
        mock_response.raise_for_status = MagicMock()

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("scrapers.AsyncSession", return_value=mock_session):
            from scrapers import scrape_techcrunch
            run = _make_run(DataSource.TECHCRUNCH)
            opps = await scrape_techcrunch(run)

        assert run.items_scraped == 2
        assert run.validated_count == 2

    @pytest.mark.asyncio
    async def test_extracts_categories_as_tags(self):
        """TechCrunch scraper should populate raw_tags from <category> elements."""
        mock_response = MagicMock()
        mock_response.content = MOCK_TECHCRUNCH_RSS
        mock_response.text = MOCK_TECHCRUNCH_RSS.decode()
        mock_response.status_code = 200
        mock_response.url = "https://techcrunch.com/category/startups/feed/"
        mock_response.raise_for_status = MagicMock()

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("scrapers.AsyncSession", return_value=mock_session):
            from scrapers import scrape_techcrunch
            run = _make_run(DataSource.TECHCRUNCH)
            opps = await scrape_techcrunch(run)

        # First item (Anduril) has 3 categories
        assert "Fundraising" in opps[0].raw_tags
        assert "Startups" in opps[0].raw_tags
        assert "defense tech" in opps[0].raw_tags

    @pytest.mark.asyncio
    async def test_maps_creator_to_organization(self):
        """dc:creator field should be mapped to the organization field."""
        mock_response = MagicMock()
        mock_response.content = MOCK_TECHCRUNCH_RSS
        mock_response.text = MOCK_TECHCRUNCH_RSS.decode()
        mock_response.status_code = 200
        mock_response.url = "https://techcrunch.com/category/startups/feed/"
        mock_response.raise_for_status = MagicMock()

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("scrapers.AsyncSession", return_value=mock_session):
            from scrapers import scrape_techcrunch
            run = _make_run(DataSource.TECHCRUNCH)
            opps = await scrape_techcrunch(run)

        assert opps[0].organization == "Julie Bort"
        assert opps[1].organization == "Dominic-Madori Davis"

    @pytest.mark.asyncio
    async def test_invalid_url_skipped(self):
        """Items with missing/invalid URLs should be skipped."""
        bad_rss = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <item><title>No URL item</title><link>not-a-url</link><description>test</description></item>
            <item><title>Valid TC Item</title><link>https://techcrunch.com/valid</link><description>ok</description></item>
          </channel>
        </rss>"""

        mock_response = MagicMock()
        mock_response.content = bad_rss
        mock_response.text = bad_rss.decode()
        mock_response.status_code = 200
        mock_response.url = "https://techcrunch.com/category/startups/feed/"
        mock_response.raise_for_status = MagicMock()

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("scrapers.AsyncSession", return_value=mock_session):
            from scrapers import scrape_techcrunch
            run = _make_run(DataSource.TECHCRUNCH)
            opps = await scrape_techcrunch(run)

        assert len(opps) == 1
        assert len(run.errors_encountered) >= 1
