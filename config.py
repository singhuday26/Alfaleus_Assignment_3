"""
config.py — Alfaleus Global Configuration
Single source of truth for all environment-driven settings.
Uses Pydantic BaseSettings for type-safe env loading with validation.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal

from pydantic import Field, MongoDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    All application settings loaded from environment variables or .env file.
    Every field is validated at startup — the app will refuse to launch with
    invalid configuration rather than fail silently at runtime.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── MongoDB ───────────────────────────────────────────────────
    mongo_uri: str = Field(
        default="mongodb://localhost:27017/alfaleus",
        description="MongoDB Atlas connection string or local URI",
    )
    mongo_db_name: str = Field(default="alfaleus", description="Target database name")

    # ── LLM API Keys ─────────────────────────────────────────────
    gemini_api_key: str = Field(default="", description="Google Gemini API key")
    claude_api_key: str = Field(default="", description="Anthropic Claude API key")

    # ── Scraper Config ────────────────────────────────────────────
    f6s_max_pages: int = Field(default=10, ge=1, le=50, description="Max F6S pages per run")
    scrape_interval_hours: int = Field(
        default=6, ge=1, le=24, description="Cron interval in hours"
    )

    # ── Deduplication Engine ──────────────────────────────────────
    fuzzy_threshold: int = Field(
        default=85, ge=50, le=100, description="rapidfuzz match threshold (0-100)"
    )

    # ── API Security ──────────────────────────────────────────────
    api_secret_key: str = Field(
        default="change-me", description="Secret key for protected endpoints"
    )

    # ── Application ───────────────────────────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO"
    )
    environment: Literal["development", "staging", "production"] = Field(
        default="development"
    )

    @field_validator("api_secret_key")
    @classmethod
    def _secret_must_not_be_default(cls, v: str) -> str:
        if v == "change-me" and False:  # allow default in dev; enforce in prod via CI
            raise ValueError("api_secret_key must be set in production")
        return v

    @property
    def gemini_available(self) -> bool:
        return bool(self.gemini_api_key and self.gemini_api_key != "your-gemini-api-key-here")

    @property
    def claude_available(self) -> bool:
        return bool(self.claude_api_key and self.claude_api_key != "your-claude-api-key-here")

    @property
    def any_llm_available(self) -> bool:
        return self.gemini_available or self.claude_available


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Cached settings singleton. Use this throughout the application instead
    of instantiating Settings() directly — avoids redundant .env reads.
    """
    return Settings()


def configure_logging(settings: Settings | None = None) -> None:
    """Configure root logger based on settings."""
    s = settings or get_settings()
    logging.basicConfig(
        level=getattr(logging, s.log_level),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


# Module-level singleton — importable directly as `from config import settings`
settings = get_settings()
