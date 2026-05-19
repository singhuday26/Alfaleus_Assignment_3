"""
models.py — Alfaleus Pydantic v2 Data Schemas  [Agent 1]
Strict schemas for all data flowing through the pipeline.
No field is optional that shouldn't be; validators enforce data integrity.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Optional
from uuid import uuid4

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


# ── Enumerations ──────────────────────────────────────────────────────────────

class DataSource(str, Enum):
    OPPORTUNITY_DESK = "opportunity_desk"
    TECHCRUNCH = "techcrunch"
    MANUAL = "manual"


class FundingStage(str, Enum):
    PRE_SEED = "pre_seed"
    SEED = "seed"
    SERIES_A = "series_a"
    SERIES_B = "series_b"
    SERIES_C_PLUS = "series_c_plus"
    GRANT = "grant"
    ACCELERATOR = "accelerator"
    INCUBATOR = "incubator"
    UNKNOWN = "unknown"


class ScraperStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"  # completed with some errors
    FAILED = "failed"


# ── AI Enrichment Tags ────────────────────────────────────────────────────────

class AITags(BaseModel):
    """Structured output from the LLM enrichment layer."""

    model_config = ConfigDict(extra="ignore")

    funding_range: Optional[str] = None
    startup_stage: str = Field(description="Idea/Pre-seed/Seed/Early/Growth/All")
    is_remote: Optional[bool] = None


# ── Ingest Model (Raw Scraper Output) ─────────────────────────────────────────

class OpportunityIn(BaseModel):
    """
    Validated ingest schema — what scrapers must produce before any DB write.
    Intentionally strict: invalid data is rejected here, not silently stored.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    title: str = Field(min_length=3, max_length=500)
    source_url: str = Field(description="Canonical URL — used as unique identifier")
    source: DataSource
    description: str = Field(default="", max_length=5000)
    deadline: Optional[datetime] = None
    organization: str = Field(default="", max_length=300)
    location: str = Field(default="", max_length=300)
    raw_tags: list[str] = Field(default_factory=list, description="Tags from source HTML/RSS")

    @field_validator("source_url")
    @classmethod
    def _url_must_be_http(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"URL must start with http(s)://: {v!r}")
        return v

    @field_validator("title")
    @classmethod
    def _title_no_html(cls, v: str) -> str:
        # Naive HTML tag removal for safety
        import re
        return re.sub(r"<[^>]+>", "", v).strip()

    def compute_content_hash(self) -> str:
        """SHA-256 of normalized (title + url + deadline) for Tier 2 dedup."""
        deadline_str = self.deadline.isoformat() if self.deadline else ""
        raw = f"{self.title.lower().strip()}|{self.source_url.lower().strip()}|{deadline_str}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── Database Model (Stored Document) ─────────────────────────────────────────

class OpportunityDB(BaseModel):
    """
    Full document schema as stored in MongoDB.
    Extends OpportunityIn with metadata fields added during pipeline processing.
    """

    model_config = ConfigDict(
        extra="allow",  # tolerate legacy fields from DB reads
        populate_by_name=True,
    )

    # MongoDB document ID — aliased for JSON serialization
    id: str = Field(default_factory=lambda: str(uuid4()), alias="_id")

    # Core fields (mirrored from OpportunityIn)
    title: str
    source_url: str
    source: DataSource
    description: str = ""
    deadline: Optional[datetime] = None
    organization: str = ""
    location: str = ""
    raw_tags: list[str] = Field(default_factory=list)

    # Pipeline metadata
    content_hash: str = Field(description="SHA-256 hash for Tier 2 dedup")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Deduplication state
    is_duplicate: bool = False
    duplicate_of_url: Optional[str] = None  # URL of canonical document

    # AI enrichment
    ai_tags: Optional[AITags] = None
    ai_enrichment_attempted: bool = False

    @classmethod
    def from_ingest(cls, opp: OpportunityIn) -> "OpportunityDB":
        """Construct a DB document from a validated ingest model."""
        data = opp.model_dump()
        data["content_hash"] = opp.compute_content_hash()
        data["_id"] = str(uuid4())
        return cls(**data)

    def to_mongo_doc(self) -> dict[str, Any]:
        """Serialize for MongoDB insertion (uses alias _id)."""
        return self.model_dump(by_alias=True, mode="python")


# ── Scraper Audit Log ─────────────────────────────────────────────────────────

class ScraperRun(BaseModel):
    """
    Audit record written to MongoDB after each scheduled or manual scrape run.
    Feeds the Streamlit audit log panel.
    """

    model_config = ConfigDict(populate_by_name=True)

    run_id: str = Field(default_factory=lambda: str(uuid4()), alias="_id")
    source: DataSource
    status: ScraperStatus = ScraperStatus.RUNNING

    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    # Counts
    items_scraped: int = 0       # Raw items returned by scraper
    validated_count: int = 0     # Passed Pydantic validation
    items_added: int = 0      # Actually written to DB (not deduped)
    items_duplicate: int = 0     # Caught by any dedup tier

    # Error tracking
    errors_encountered: list[str] = Field(default_factory=list, max_length=100)
    pages_scraped: int = 0       # Relevant for paginated scrapers

    def finalize(
        self,
        status: ScraperStatus,
        items_scraped: int = 0,
        validated: int = 0,
        items_added: int = 0,
        items_duplicate: int = 0,
        pages: int = 0,
    ) -> "ScraperRun":
        """Return updated model with final stats (immutable update pattern)."""
        return self.model_copy(
            update={
                "status": status,
                "completed_at": datetime.now(timezone.utc),
                "items_scraped": items_scraped,
                "validated_count": validated,
                "items_added": items_added,
                "items_duplicate": items_duplicate,
                "pages_scraped": pages,
            }
        )

    def log_error(self, msg: str) -> None:
        """Append an error message, capped to avoid document bloat."""
        if len(self.errors_encountered) < 100:
            self.errors_encountered.append(f"[{datetime.now(timezone.utc).isoformat()}] {msg}")

    def to_mongo_doc(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="python")


# ── Filter / Query Schema (API Layer) ────────────────────────────────────────

class OpportunityFilter(BaseModel):
    """Query parameters for the GET /opportunities endpoint."""

    model_config = ConfigDict(extra="forbid")

    source: Optional[DataSource] = None
    type: Optional[str] = None
    is_remote: Optional[bool] = None
    sector_tag: Optional[str] = None
    keyword: Optional[str] = Field(default=None, max_length=200)
    deadline_after: Optional[datetime] = None
    deadline_before: Optional[datetime] = None
    exclude_duplicates: bool = True
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)
