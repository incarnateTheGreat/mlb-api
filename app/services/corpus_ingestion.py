"""
RAG Corpus Ingestion Pipeline

Loads, validates, and processes markdown sources into the vector store.
Handles metadata extraction, chunking, embedding, and citation building.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
import json


@dataclass
class SourceMetadata:
    """Validated source metadata from frontmatter."""
    source_id: str
    title: str
    url: str
    section: str
    license_type: str  # public_domain, cc_by, public_data
    attribution: str
    ingested_date: str
    version: Optional[str] = None


# Approved sources (whitelist)
APPROVED_SOURCES = {
    # === Official MLB Rules & Regulations ===
    "rules_infield_fly": {
        "title": "Infield Fly Rule",
        "url": "https://www.mlb.com/official-information/rules/infield-fly-rule",
        "license_type": "public_domain",
        "attribution": "MLB Official Rules",
        "category": "Official MLB Rules & Regulations",
    },
    "rules_designated_hitter": {
        "title": "Designated Hitter Rule",
        "url": "https://www.mlb.com/official-information/rules/designated-hitter",
        "license_type": "public_domain",
        "attribution": "MLB Official Rules",
        "category": "Official MLB Rules & Regulations",
    },
    "rules_strikes_balls": {
        "title": "Strike Zone and Strikes Rule",
        "url": "https://www.mlb.com/official-information/rules/strikes-balls-count",
        "license_type": "public_domain",
        "attribution": "MLB Official Rules",
        "category": "Official MLB Rules & Regulations",
    },
    "rules_double_play": {
        "title": "Double Play Rule",
        "url": "https://www.mlb.com/official-information/rules/double-play",
        "license_type": "public_domain",
        "attribution": "MLB Official Rules",
        "category": "Official MLB Rules & Regulations",
    },
    # === Historical Baseball Knowledge ===
    "history_world_series": {
        "title": "World Series History",
        "url": "https://www.mlb.com/official-information/world-series",
        "license_type": "public_domain",
        "attribution": "MLB Official Records",
        "category": "Historical Baseball Knowledge",
    },
    "history_records_achievements": {
        "title": "Famous Baseball Records and Achievements",
        "url": "https://www.mlb.com/official-information/records",
        "license_type": "public_domain",
        "attribution": "MLB Official Records",
        "category": "Historical Baseball Knowledge",
    },
    # === Baseball Statistics & Analytics ===
    "stats_sabermetrics": {
        "title": "Sabermetrics and Advanced Baseball Statistics",
        "url": "https://www.mlb.com/official-information/sabermetrics",
        "license_type": "public_domain",
        "attribution": "SABR / MLB Official Information",
        "category": "Baseball Statistics & Analytics",
    },
    # === League Structure & Organization ===
    "structure_league_teams": {
        "title": "MLB League Structure and Teams",
        "url": "https://www.mlb.com/official-information/league-structure",
        "license_type": "public_domain",
        "attribution": "MLB Official Organization",
        "category": "League Structure & Organization",
    },
}


class CorpusValidator:
    """Validates ingested sources against policy."""

    def validate_source(self, source_id: str, metadata: dict) -> tuple[bool, list[str]]:
        """
        Validate source metadata against corpus policy.
        
        Returns:
            (is_valid, error_messages)
        """
        errors = []

        # Check whitelist
        if source_id not in APPROVED_SOURCES:
            errors.append(f"Source '{source_id}' not in approved whitelist")
            return False, errors

        approved = APPROVED_SOURCES[source_id]

        # Check required fields
        required_fields = ["title", "url", "section", "license_type", "attribution"]
        for field in required_fields:
            if field not in metadata or not metadata[field]:
                errors.append(f"Missing required field: {field}")

        # Check URL is HTTPS
        if metadata.get("url") and not metadata["url"].startswith("https://"):
            errors.append("URL must use HTTPS")

        # Check license type is valid
        valid_licenses = ["public_domain", "cc_by", "public_data"]
        if metadata.get("license_type") not in valid_licenses:
            errors.append(f"Invalid license_type (must be one of {valid_licenses})")

        return len(errors) == 0, errors

    def build_metadata(self, source_id: str) -> SourceMetadata:
        """Build validated metadata from whitelist."""
        approved = APPROVED_SOURCES.get(source_id)
        if not approved:
            raise ValueError(f"Source {source_id} not in whitelist")

        return SourceMetadata(
            source_id=source_id,
            title=approved["title"],
            url=approved["url"],
            section=approved["title"],
            license_type=approved["license_type"],
            attribution=approved["attribution"],
            ingested_date=datetime.utcnow().strftime("%Y-%m-%d"),
            version="2024" if "rules" in source_id else None,
        )


class CorpusIngestionStats:
    """Statistics from corpus ingestion run."""

    def __init__(self):
        self.sources_processed: int = 0
        self.sources_ingested: int = 0
        self.sources_failed: list[tuple[str, str]] = []
        self.total_chunks: int = 0
        self.total_tokens: int = 0
        self.ingestion_start: datetime = None
        self.ingestion_end: datetime = None

    @property
    def ingestion_duration_ms(self) -> int:
        if self.ingestion_start and self.ingestion_end:
            return int((self.ingestion_end - self.ingestion_start).total_seconds() * 1000)
        return 0

    def to_dict(self) -> dict:
        """Export stats as dictionary."""
        return {
            "sources_processed": self.sources_processed,
            "sources_ingested": self.sources_ingested,
            "sources_failed": len(self.sources_failed),
            "failed_sources": self.sources_failed,
            "total_chunks": self.total_chunks,
            "total_tokens_approx": self.total_tokens,
            "ingestion_duration_ms": self.ingestion_duration_ms,
            "timestamp": self.ingestion_end.isoformat() if self.ingestion_end else None,
        }

    def to_json(self) -> str:
        """Export stats as JSON."""
        return json.dumps(self.to_dict(), indent=2)

    def print_summary(self):
        """Print human-readable summary."""
        print(f"\n{'='*70}")
        print(f"CORPUS INGESTION SUMMARY")
        print(f"{'='*70}")
        print(f"Sources processed:    {self.sources_processed}")
        print(f"Sources ingested:     {self.sources_ingested}")
        print(f"Sources failed:       {len(self.sources_failed)}")
        if self.sources_failed:
            print(f"\nFailed sources:")
            for source_id, error in self.sources_failed:
                print(f"  • {source_id}: {error}")
        print(f"\nChunking stats:")
        print(f"  Total chunks:       {self.total_chunks}")
        print(f"  Approx tokens:      {self.total_tokens}")
        print(f"  Ingestion time:     {self.ingestion_duration_ms}ms")
        print(f"{'='*70}\n")


def estimate_token_count(text: str) -> int:
    """Rough estimate: 1 token ≈ 4 chars (Claude tokenization)."""
    return len(text) // 4
