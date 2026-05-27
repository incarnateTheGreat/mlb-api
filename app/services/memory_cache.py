"""
In-memory caching for MLB API responses.

Uses cachetools TTLCache for fast, automatic expiration.
This avoids the extra network hop penalty when proxying MLB API calls.

Key differences from the Postgres cache:
- In-memory: Much faster (nanoseconds vs milliseconds)
- Not persistent: Lost on restart
- Per-instance: Not shared across workers

Use this for:
- High-frequency read-only data (game feeds during live games)
- Data that changes frequently (live scores every 10s)
- Reducing MLB API rate limit pressure

Use Postgres cache (CacheService) for:
- Expensive-to-compute data (AI summaries)
- Data that should survive restarts
- Shared state across multiple workers
"""

import hashlib
import json
from functools import wraps
from typing import Any, Callable, Optional, TypeVar

from cachetools import TTLCache

# Type variable for generic function return types
T = TypeVar("T")


# ============================================================================
# Cache Instances
# ============================================================================

# Game feed cache: 10 second TTL, max 500 games
# Short TTL because live game data updates frequently
_game_feed_cache: TTLCache = TTLCache(maxsize=500, ttl=10)

# Game content cache: 90 second TTL, max 200 games
# Shorter TTL so new highlights appear within ~1.5 minutes
_game_content_cache: TTLCache = TTLCache(maxsize=200, ttl=90)

# Schedule cache: 2 minute TTL, max 50 entries
# Longer TTL acceptable since schedule changes are rare
_schedule_cache: TTLCache = TTLCache(maxsize=50, ttl=120)


# ============================================================================
# Cache Decorators
# ============================================================================

def _make_cache_key(*args, **kwargs) -> str:
    """Create a stable cache key from function arguments."""
    # Combine args and kwargs into a hashable string
    key_data = json.dumps({"args": args, "kwargs": kwargs}, sort_keys=True, default=str)
    return hashlib.md5(key_data.encode()).hexdigest()


def cached_game_feed(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator to cache game feed API calls.
    
    Short 10-second TTL for live game data that updates frequently.
    """
    @wraps(func)
    async def wrapper(self, game_id: int, *args, **kwargs) -> T:
        cache_key = f"feed:{game_id}"
        
        # Check cache
        if cache_key in _game_feed_cache:
            return _game_feed_cache[cache_key]
        
        # Fetch from API
        result = await func(self, game_id, *args, **kwargs)
        
        # Store in cache
        _game_feed_cache[cache_key] = result
        return result
    
    return wrapper


def cached_game_content(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator to cache game content (videos/articles) API calls.
    
    5-minute TTL since highlights don't change as frequently.
    """
    @wraps(func)
    async def wrapper(self, game_id: int, *args, **kwargs) -> T:
        cache_key = f"content:{game_id}"
        
        # Check cache
        if cache_key in _game_content_cache:
            return _game_content_cache[cache_key]
        
        # Fetch from API
        result = await func(self, game_id, *args, **kwargs)
        
        # Store in cache
        _game_content_cache[cache_key] = result
        return result
    
    return wrapper


def cached_schedule(ttl_override: Optional[int] = None):
    """
    Decorator to cache schedule API calls.
    
    Args:
        ttl_override: Optional TTL in seconds (uses default 30s if not provided)
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(self, *args, **kwargs) -> T:
            cache_key = f"schedule:{_make_cache_key(*args, **kwargs)}"
            
            # Check cache
            if cache_key in _schedule_cache:
                return _schedule_cache[cache_key]
            
            # Fetch from API
            result = await func(self, *args, **kwargs)
            
            # Store in cache
            _schedule_cache[cache_key] = result
            return result
        
        return wrapper
    return decorator


# ============================================================================
# Cache Management
# ============================================================================

def clear_game_cache(game_id: Optional[int] = None) -> int:
    """
    Clear cached data for a specific game or all games.
    
    Args:
        game_id: Specific game to clear, or None to clear all
    
    Returns:
        Number of cache entries cleared
    """
    cleared = 0
    
    if game_id is None:
        # Clear all game caches
        cleared += len(_game_feed_cache)
        cleared += len(_game_content_cache)
        _game_feed_cache.clear()
        _game_content_cache.clear()
    else:
        # Clear specific game
        feed_key = f"feed:{game_id}"
        content_key = f"content:{game_id}"
        
        if feed_key in _game_feed_cache:
            del _game_feed_cache[feed_key]
            cleared += 1
        if content_key in _game_content_cache:
            del _game_content_cache[content_key]
            cleared += 1
    
    return cleared


def clear_schedule_cache() -> int:
    """Clear all cached schedule data."""
    cleared = len(_schedule_cache)
    _schedule_cache.clear()
    return cleared


def get_cache_stats() -> dict[str, Any]:
    """
    Get cache statistics for monitoring.
    
    Returns dict with cache sizes and max sizes.
    """
    return {
        "game_feed": {
            "size": len(_game_feed_cache),
            "maxsize": _game_feed_cache.maxsize,
            "ttl": _game_feed_cache.ttl,
        },
        "game_content": {
            "size": len(_game_content_cache),
            "maxsize": _game_content_cache.maxsize,
            "ttl": _game_content_cache.ttl,
        },
        "schedule": {
            "size": len(_schedule_cache),
            "maxsize": _schedule_cache.maxsize,
            "ttl": _schedule_cache.ttl,
        },
    }
