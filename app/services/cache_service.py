"""
Caching service using Neon Postgres.

This provides a simple cache layer for:
1. MLB API responses (to reduce API calls)
2. AI-generated content (expensive to regenerate)

The cache uses SQLAlchemy + Postgres JSONB for flexible storage.
TTL is handled via expiration timestamps and periodic cleanup.
"""

import json
from datetime import datetime, timedelta
from typing import Optional, TypeVar, Type

from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import get_settings
from app.models.cache import CachedResponse, AIGeneration


# Generic type for Pydantic models
T = TypeVar("T", bound=BaseModel)


class CacheService:
    """
    Postgres-backed cache service.
    
    Pattern note: Unlike Redis, Postgres doesn't have built-in TTL.
    We store an expires_at timestamp and check it on reads.
    Periodic cleanup removes expired entries.
    """
    
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.default_ttl = get_settings().cache_ttl_seconds
    
    async def get(
        self,
        cache_type: str,
        cache_key: str,
        model_class: Type[T],
    ) -> Optional[T]:
        """
        Retrieve a cached value and deserialize to a Pydantic model.
        
        Args:
            cache_type: Category of cached data (e.g., "game_summary")
            cache_key: Unique key within the category (e.g., "game_12345")
            model_class: Pydantic model class to deserialize into
        
        Returns:
            Deserialized model instance or None if not found/expired
        """
        stmt = select(CachedResponse).where(
            CachedResponse.cache_type == cache_type,
            CachedResponse.cache_key == cache_key,
            CachedResponse.expires_at > datetime.utcnow(),
        )
        
        result = await self.db.execute(stmt)
        cached = result.scalar_one_or_none()
        
        if cached is None:
            return None
        
        # Deserialize JSONB data to Pydantic model
        # model_validate is Pydantic v2's parse_obj equivalent
        return model_class.model_validate(cached.data)
    
    async def set(
        self,
        cache_type: str,
        cache_key: str,
        data: BaseModel,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """
        Cache a Pydantic model.
        
        Uses Postgres UPSERT (INSERT ... ON CONFLICT UPDATE) to handle
        both inserts and updates atomically — similar to Prisma's upsert.
        
        Args:
            cache_type: Category of cached data
            cache_key: Unique key within the category
            data: Pydantic model to cache
            ttl_seconds: Optional custom TTL (defaults to config value)
        """
        ttl = ttl_seconds or self.default_ttl
        expires_at = datetime.utcnow() + timedelta(seconds=ttl)
        
        # model_dump is Pydantic v2's dict() equivalent
        # mode="json" ensures datetime serialization
        json_data = data.model_dump(mode="json")
        
        # Postgres UPSERT: insert or update on conflict
        stmt = pg_insert(CachedResponse).values(
            cache_type=cache_type,
            cache_key=cache_key,
            data=json_data,
            created_at=datetime.utcnow(),
            expires_at=expires_at,
        ).on_conflict_do_update(
            index_elements=["cache_type", "cache_key"],
            set_={
                "data": json_data,
                "expires_at": expires_at,
            }
        )
        
        await self.db.execute(stmt)
        await self.db.commit()
    
    async def invalidate(self, cache_type: str, cache_key: str) -> bool:
        """
        Remove a specific cache entry.
        
        Returns True if an entry was deleted, False if not found.
        """
        stmt = delete(CachedResponse).where(
            CachedResponse.cache_type == cache_type,
            CachedResponse.cache_key == cache_key,
        )
        
        result = await self.db.execute(stmt)
        await self.db.commit()
        
        return result.rowcount > 0
    
    async def invalidate_type(self, cache_type: str) -> int:
        """
        Remove all cache entries of a specific type.
        
        Returns the number of entries deleted.
        """
        stmt = delete(CachedResponse).where(
            CachedResponse.cache_type == cache_type,
        )
        
        result = await self.db.execute(stmt)
        await self.db.commit()
        
        return result.rowcount
    
    async def cleanup_expired(self) -> int:
        """
        Remove all expired cache entries.
        
        Call this periodically (e.g., via a background task or cron)
        to prevent the cache table from growing unbounded.
        
        Returns the number of entries deleted.
        """
        stmt = delete(CachedResponse).where(
            CachedResponse.expires_at <= datetime.utcnow(),
        )
        
        result = await self.db.execute(stmt)
        await self.db.commit()
        
        return result.rowcount
    
    async def log_ai_generation(
        self,
        generation_type: str,
        entity_id: str,
        model: str,
        tokens_input: int,
        tokens_output: int,
        generation_time_ms: int,
        output: Optional[str] = None,
    ) -> None:
        """
        Log an AI generation for analytics and debugging.
        
        This helps track:
        - Token usage and costs
        - Generation latency
        - Cache hit rates (compare generations to cache hits)
        """
        generation = AIGeneration(
            generation_type=generation_type,
            entity_id=entity_id,
            model=model,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            generation_time_ms=generation_time_ms,
            output=output,
        )
        
        self.db.add(generation)
        await self.db.commit()


# ============================================================================
# Typed cache helpers for common operations
# ============================================================================

async def get_cached_game_summary(
    db: AsyncSession,
    game_id: int,
) -> Optional[dict]:
    """Get a cached game summary."""
    cache = CacheService(db)
    # Using dict here instead of GameSummary to avoid circular imports
    # The router will validate with Pydantic
    stmt = select(CachedResponse).where(
        CachedResponse.cache_type == "game_summary",
        CachedResponse.cache_key == str(game_id),
        CachedResponse.expires_at > datetime.utcnow(),
    )
    
    result = await db.execute(stmt)
    cached = result.scalar_one_or_none()
    
    if cached:
        return cached.data
    return None


async def cache_game_summary(
    db: AsyncSession,
    game_id: int,
    summary_data: dict,
    ttl_seconds: int = 3600,  # 1 hour default for game summaries
) -> None:
    """Cache a game summary."""
    expires_at = datetime.utcnow() + timedelta(seconds=ttl_seconds)
    
    stmt = pg_insert(CachedResponse).values(
        cache_type="game_summary",
        cache_key=str(game_id),
        data=summary_data,
        created_at=datetime.utcnow(),
        expires_at=expires_at,
    ).on_conflict_do_update(
        index_elements=["cache_type", "cache_key"],
        set_={
            "data": summary_data,
            "expires_at": expires_at,
        }
    )
    
    await db.execute(stmt)
    await db.commit()
