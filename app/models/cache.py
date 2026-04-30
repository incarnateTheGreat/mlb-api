"""
SQLAlchemy ORM models for the database cache.

SQLAlchemy is Python's most popular ORM — similar to Prisma but with
more explicit control. Key differences from Prisma:

1. Models are classes that inherit from Base (vs. schema file)
2. Column types are explicit (Integer, String, etc.)
3. Relationships are defined with relationship() function
4. Migrations are separate (Alembic) rather than built-in
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, DateTime, Text, Index
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base


class CachedResponse(Base):
    """
    Generic cache table for API responses and AI-generated content.
    
    Uses JSONB for flexible storage — similar to storing a JSON
    column in Postgres via Prisma.
    """
    __tablename__ = "cached_responses"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Cache key components
    cache_type = Column(String(50), nullable=False)  # "game_summary", "player_stats", etc.
    cache_key = Column(String(255), nullable=False)  # Unique identifier within type
    
    # The cached data (JSONB for efficient querying)
    data = Column(JSONB, nullable=False)
    
    # Timestamps for TTL management
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    
    # Composite unique constraint on type + key
    __table_args__ = (
        Index("ix_cache_type_key", "cache_type", "cache_key", unique=True),
        Index("ix_cache_expires", "expires_at"),
    )


class AIGeneration(Base):
    """
    Track AI content generations for analytics and debugging.
    
    This is useful for:
    - Monitoring token usage and costs
    - Debugging generation issues
    - A/B testing prompts
    """
    __tablename__ = "ai_generations"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # What was generated
    generation_type = Column(String(50), nullable=False)  # "game_summary", "scouting_report", etc.
    entity_id = Column(String(100), nullable=False)  # game_id, player_id, etc.
    
    # Generation details
    model = Column(String(100), nullable=False)
    prompt_hash = Column(String(64), nullable=True)  # SHA-256 of prompt for dedup
    
    # Metrics
    tokens_input = Column(Integer, nullable=False)
    tokens_output = Column(Integer, nullable=False)
    generation_time_ms = Column(Integer, nullable=False)
    
    # The actual output (for debugging)
    output = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index("ix_ai_gen_type_entity", "generation_type", "entity_id"),
        Index("ix_ai_gen_created", "created_at"),
    )
