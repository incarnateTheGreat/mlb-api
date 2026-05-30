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

# Player cache: 10 min TTL
_player_cache: TTLCache = TTLCache(maxsize=200, ttl=600)

# Standings cache: 5 min TTL, max 20 entries (one per year)
# Standings only update once per day's games complete
_standings_cache: TTLCache = TTLCache(maxsize=20, ttl=300)

# Teams cache: 5 min TTL, max 50 entries
# Team info with standings rarely changes mid-game
_teams_cache: TTLCache = TTLCache(maxsize=50, ttl=300)

# Matchup cache: 30 min TTL, max 200 entries
# AI-generated matchup analyses are expensive and don't change quickly
_matchup_cache: TTLCache = TTLCache(maxsize=200, ttl=1800)


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


def cached_player(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator to cache player bio API calls.
    
    10-minute TTL since player bios rarely change.
    """
    @wraps(func)
    async def wrapper(self, player_id: int, *args, **kwargs) -> T:
        cache_key = f"player:{player_id}"
        
        if cache_key in _player_cache:
            return _player_cache[cache_key]
        
        result = await func(self, player_id, *args, **kwargs)
        _player_cache[cache_key] = result
        return result
    
    return wrapper


def cached_player_stats(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator to cache player stats API calls.
    
    Includes season in cache key since stats differ by year.
    """
    @wraps(func)
    async def wrapper(self, player_id: int, season: int, *args, **kwargs) -> T:
        cache_key = f"player_stats:{player_id}:{season}:{_make_cache_key(*args, **kwargs)}"
        
        if cache_key in _player_cache:
            return _player_cache[cache_key]
        
        result = await func(self, player_id, season, *args, **kwargs)
        _player_cache[cache_key] = result
        return result
    
    return wrapper


def cached_player_profile(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator to cache player profile API calls.
    
    10-minute TTL for full profile with career stats.
    """
    @wraps(func)
    async def wrapper(self, player_id: int, *args, **kwargs) -> T:
        cache_key = f"player_profile:{player_id}"
        
        if cache_key in _player_cache:
            return _player_cache[cache_key]
        
        result = await func(self, player_id, *args, **kwargs)
        _player_cache[cache_key] = result
        return result
    
    return wrapper


def cached_standings(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator to cache standings API calls.
    
    5-minute TTL since standings only change after games complete.
    Cache key includes both year and view to handle different standings types.
    """
    @wraps(func)
    async def wrapper(self, year: int, view: Any = None, *args, **kwargs) -> T:
        # Get view value - could be positional or keyword arg
        view_key = str(view) if view else "division"
        cache_key = f"standings:{year}:{view_key}"
        
        if cache_key in _standings_cache:
            return _standings_cache[cache_key]
        
        result = await func(self, year, view, *args, **kwargs)
        _standings_cache[cache_key] = result
        return result
    
    return wrapper


def cached_team_info(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator to cache team info API calls.
    
    5-minute TTL since team info with standings rarely changes mid-game.
    """
    @wraps(func)
    async def wrapper(self, team_id: int, season: Optional[int] = None, *args, **kwargs) -> T:
        cache_key = f"team_info:{team_id}:{season}"
        
        if cache_key in _teams_cache:
            return _teams_cache[cache_key]
        
        result = await func(self, team_id, season, *args, **kwargs)
        _teams_cache[cache_key] = result
        return result
    
    return wrapper


def cached_team_schedule(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator to cache team schedule API calls.
    
    2-minute TTL since schedules can update for postponements/changes.
    """
    @wraps(func)
    async def wrapper(self, team_id: int, *args, **kwargs) -> T:
        cache_key = f"team_schedule:{team_id}:{_make_cache_key(*args, **kwargs)}"
        
        if cache_key in _teams_cache:
            return _teams_cache[cache_key]
        
        result = await func(self, team_id, *args, **kwargs)
        _teams_cache[cache_key] = result
        return result
    
    return wrapper


def cached_matchup_analysis(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator to cache AI-generated matchup analyses.
    
    30-minute TTL since AI analyses are expensive and player stats
    don't change within a game.
    """
    @wraps(func)
    async def wrapper(self, batter_id: int, pitcher_id: int, season: int, *args, **kwargs) -> T:
        cache_key = f"matchup:{batter_id}:{pitcher_id}:{season}"
        
        if cache_key in _matchup_cache:
            return _matchup_cache[cache_key]
        
        result = await func(self, batter_id, pitcher_id, season, *args, **kwargs)
        _matchup_cache[cache_key] = result
        return result
    
    return wrapper


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


def clear_player_cache(player_id: Optional[int] = None) -> int:
    """
    Clear cached player data.
    
    Args:
        player_id: Specific player to clear, or None to clear all
    
    Returns:
        Number of cache entries cleared
    """
    if player_id is None:
        cleared = len(_player_cache)
        _player_cache.clear()
        return cleared
    
    # Clear entries matching this player_id
    cleared = 0
    keys_to_delete = [
        k for k in _player_cache.keys()
        if k.startswith(f"player:{player_id}") or
           k.startswith(f"player_stats:{player_id}:") or
           k.startswith(f"player_profile:{player_id}")
    ]
    for key in keys_to_delete:
        del _player_cache[key]
        cleared += 1
    
    return cleared


def clear_teams_cache(team_id: Optional[int] = None) -> int:
    """
    Clear cached team data.
    
    Args:
        team_id: Specific team to clear, or None to clear all
    
    Returns:
        Number of cache entries cleared
    """
    if team_id is None:
        cleared = len(_teams_cache)
        _teams_cache.clear()
        return cleared
    
    # Clear entries matching this team_id
    cleared = 0
    keys_to_delete = [
        k for k in _teams_cache.keys()
        if k.startswith(f"team_info:{team_id}:") or
           k.startswith(f"team_schedule:{team_id}:")
    ]
    for key in keys_to_delete:
        del _teams_cache[key]
        cleared += 1
    
    return cleared


def clear_matchup_cache(
    batter_id: Optional[int] = None,
    pitcher_id: Optional[int] = None,
) -> int:
    """
    Clear cached matchup analyses.
    
    Args:
        batter_id: Clear matchups involving this batter, or None
        pitcher_id: Clear matchups involving this pitcher, or None
        If both are None, clears all matchup cache.
    
    Returns:
        Number of cache entries cleared
    """
    if batter_id is None and pitcher_id is None:
        cleared = len(_matchup_cache)
        _matchup_cache.clear()
        return cleared
    
    # Clear entries matching the specified player(s)
    cleared = 0
    keys_to_delete = []
    
    for key in _matchup_cache.keys():
        # Key format: matchup:{batter_id}:{pitcher_id}:{season}
        parts = key.split(":")
        if len(parts) >= 3:
            key_batter = int(parts[1])
            key_pitcher = int(parts[2])
            
            if batter_id and key_batter == batter_id:
                keys_to_delete.append(key)
            elif pitcher_id and key_pitcher == pitcher_id:
                keys_to_delete.append(key)
    
    for key in keys_to_delete:
        del _matchup_cache[key]
        cleared += 1
    
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
        "player": {
            "size": len(_player_cache),
            "maxsize": _player_cache.maxsize,
            "ttl": _player_cache.ttl,
        },
        "teams": {
            "size": len(_teams_cache),
            "maxsize": _teams_cache.maxsize,
            "ttl": _teams_cache.ttl,
        },
        "matchup": {
            "size": len(_matchup_cache),
            "maxsize": _matchup_cache.maxsize,
            "ttl": _matchup_cache.ttl,
        },
    }
