"""tests/test_api.py — FastAPI route tests using TestClient."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a TestClient with all DB and scheduler calls mocked."""
    with (
        patch("database.ensure_indexes", new_callable=AsyncMock),
        patch("database.ping_database", new_callable=AsyncMock, return_value=True),
        patch("scheduler.start_scheduler", new_callable=AsyncMock),
        patch("scheduler.shutdown_scheduler", new_callable=AsyncMock),
    ):
        from api import app
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


class TestHealthEndpoint:
    def test_health_ok(self, client):
        with patch("api.ping_database", new_callable=AsyncMock, return_value=True):
            response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["database"] == "connected"
        assert "timestamp" in data
        assert "llm" in data

    def test_health_degraded_when_db_down(self, client):
        with patch("api.ping_database", new_callable=AsyncMock, return_value=False):
            response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "degraded"


class TestOpportunitiesEndpoint:
    def _mock_db_opportunities(self, docs=None, total=0):
        """Helper to mock the Motor collection for opportunity queries."""
        docs = docs or []
        mock_col = MagicMock()
        mock_col.count_documents = AsyncMock(return_value=total)

        mock_cursor = MagicMock()
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.skip = MagicMock(return_value=mock_cursor)
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        mock_cursor.__aiter__ = MagicMock(return_value=iter(docs))

        mock_col.find = MagicMock(return_value=mock_cursor)
        return mock_col

    def test_returns_paginated_response(self, client):
        sample_doc = {
            "_id": "abc123",
            "title": "Test Opportunity",
            "url": "https://example.com/test",
            "source": "opportunity_desk",
            "description": "Test",
            "is_duplicate": False,
        }

        mock_col = self._mock_db_opportunities([sample_doc], total=1)

        with patch("api.get_db_dep") as mock_dep:
            mock_db = MagicMock()
            mock_db.__getitem__ = MagicMock(return_value=mock_col)

            async def mock_gen():
                yield mock_db

            mock_dep.return_value = mock_gen()
            response = client.get("/opportunities?page=1&page_size=10")

        assert response.status_code == 200
        data = response.json()
        assert "total" in data
        assert "data" in data
        assert "page" in data
        assert "pages" in data

    def test_returns_200_on_empty_results(self, client):
        mock_col = self._mock_db_opportunities([], total=0)

        with patch("api.get_db_dep") as mock_dep:
            mock_db = MagicMock()
            mock_db.__getitem__ = MagicMock(return_value=mock_col)

            async def mock_gen():
                yield mock_db

            mock_dep.return_value = mock_gen()
            response = client.get("/opportunities")

        assert response.status_code == 200
        assert response.json()["total"] == 0


class TestRunScraperEndpoint:
    def test_requires_api_key(self, client):
        """POST /run-scraper without API key should return 401."""
        response = client.post("/run-scraper")
        assert response.status_code == 401

    def test_accepts_valid_api_key(self, client):
        """POST /run-scraper with valid key should return 200."""
        with (
            patch("api.settings") as mock_settings,
            patch("scheduler.run_pipeline_now", new_callable=AsyncMock),
        ):
            mock_settings.api_secret_key = "test-secret"
            mock_settings.gemini_available = False
            mock_settings.claude_available = False

            response = client.post(
                "/run-scraper",
                headers={"X-Api-Key": "test-secret"},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "triggered"

    def test_rejects_wrong_api_key(self, client):
        with patch("api.settings") as mock_settings:
            mock_settings.api_secret_key = "real-secret"
            response = client.post(
                "/run-scraper",
                headers={"X-Api-Key": "wrong-key"},
            )
        assert response.status_code == 401


class TestStatsEndpoint:
    def test_stats_returns_expected_keys(self, client):
        mock_col = MagicMock()
        mock_col.count_documents = AsyncMock(return_value=42)
        mock_col.distinct = AsyncMock(return_value=["opportunity_desk", "f6s"])

        with patch("api.get_db_dep") as mock_dep:
            mock_db = MagicMock()
            mock_db.__getitem__ = MagicMock(return_value=mock_col)

            async def mock_gen():
                yield mock_db

            mock_dep.return_value = mock_gen()
            response = client.get("/stats")

        assert response.status_code == 200
        data = response.json()
        assert "total_opportunities" in data
        assert "new_last_24h" in data
        assert "ai_tagged_count" in data
        assert "ai_tagged_pct" in data
        assert "active_sources" in data
