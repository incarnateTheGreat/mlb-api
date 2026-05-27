"""
Tests for in-memory caching decorators and utilities.
"""

import pytest

from app.services.memory_cache import (
    _game_feed_cache,
    _game_content_cache,
    _schedule_cache,
    cached_game_feed,
    cached_game_content,
    cached_schedule,
    clear_game_cache,
    clear_schedule_cache,
    get_cache_stats,
)


@pytest.fixture(autouse=True)
def clear_caches():
    """Clear all caches before and after each test."""
    _game_feed_cache.clear()
    _game_content_cache.clear()
    _schedule_cache.clear()
    yield
    _game_feed_cache.clear()
    _game_content_cache.clear()
    _schedule_cache.clear()


# ============================================================================
# Decorator Tests
# ============================================================================


class TestCachedGameFeed:
    """Tests for the cached_game_feed decorator."""

    @pytest.mark.asyncio
    async def test_caches_result(self):
        """First call should cache; second should return cached value."""
        call_count = 0

        class MockClient:
            @cached_game_feed
            async def get_feed(self, game_id: int):
                nonlocal call_count
                call_count += 1
                return {"game_id": game_id, "data": "live"}

        client = MockClient()

        # First call - should hit the "API"
        result1 = await client.get_feed(12345)
        assert result1 == {"game_id": 12345, "data": "live"}
        assert call_count == 1

        # Second call - should use cache
        result2 = await client.get_feed(12345)
        assert result2 == {"game_id": 12345, "data": "live"}
        assert call_count == 1  # Still 1, no new call

    @pytest.mark.asyncio
    async def test_different_game_ids_cached_separately(self):
        """Different game IDs should have separate cache entries."""
        call_count = 0

        class MockClient:
            @cached_game_feed
            async def get_feed(self, game_id: int):
                nonlocal call_count
                call_count += 1
                return {"game_id": game_id}

        client = MockClient()

        await client.get_feed(111)
        await client.get_feed(222)
        assert call_count == 2

        # Both should now be cached
        await client.get_feed(111)
        await client.get_feed(222)
        assert call_count == 2


class TestCachedGameContent:
    """Tests for the cached_game_content decorator."""

    @pytest.mark.asyncio
    async def test_caches_result(self):
        """Content should be cached separately from feed."""
        call_count = 0

        class MockClient:
            @cached_game_content
            async def get_content(self, game_id: int):
                nonlocal call_count
                call_count += 1
                return {"highlights": ["video1", "video2"]}

        client = MockClient()

        result1 = await client.get_content(12345)
        result2 = await client.get_content(12345)

        assert result1 == result2
        assert call_count == 1


class TestCachedSchedule:
    """Tests for the cached_schedule decorator."""

    @pytest.mark.asyncio
    async def test_caches_by_arguments(self):
        """Cache key should incorporate function arguments."""
        call_count = 0

        class MockClient:
            @cached_schedule()
            async def get_schedule(self, date: str, team_id: int = None):
                nonlocal call_count
                call_count += 1
                return {"date": date, "team": team_id}

        client = MockClient()

        # Different args = different cache entries
        await client.get_schedule("2024-06-01")
        await client.get_schedule("2024-06-01", team_id=137)
        await client.get_schedule("2024-06-02")
        assert call_count == 3

        # Same args = cached
        await client.get_schedule("2024-06-01")
        await client.get_schedule("2024-06-01", team_id=137)
        assert call_count == 3


# ============================================================================
# Cache Management Tests
# ============================================================================


class TestClearGameCache:
    """Tests for clear_game_cache function."""

    def test_clear_specific_game(self):
        """Clearing a specific game should only remove that game's entries."""
        _game_feed_cache["feed:111"] = {"data": 1}
        _game_feed_cache["feed:222"] = {"data": 2}
        _game_content_cache["content:111"] = {"videos": []}
        _game_content_cache["content:222"] = {"videos": []}

        cleared = clear_game_cache(111)

        assert cleared == 2  # feed + content for game 111
        assert "feed:111" not in _game_feed_cache
        assert "content:111" not in _game_content_cache
        assert "feed:222" in _game_feed_cache
        assert "content:222" in _game_content_cache

    def test_clear_all_games(self):
        """Clearing without game_id should remove all game cache entries."""
        _game_feed_cache["feed:111"] = {"data": 1}
        _game_feed_cache["feed:222"] = {"data": 2}
        _game_content_cache["content:333"] = {"videos": []}

        cleared = clear_game_cache()

        assert cleared == 3
        assert len(_game_feed_cache) == 0
        assert len(_game_content_cache) == 0

    def test_clear_nonexistent_game(self):
        """Clearing a game not in cache should return 0."""
        _game_feed_cache["feed:111"] = {"data": 1}

        cleared = clear_game_cache(999)

        assert cleared == 0
        assert "feed:111" in _game_feed_cache


class TestClearScheduleCache:
    """Tests for clear_schedule_cache function."""

    def test_clears_all_schedule_entries(self):
        """Should clear all schedule cache entries."""
        _schedule_cache["schedule:abc"] = {"games": []}
        _schedule_cache["schedule:def"] = {"games": []}

        cleared = clear_schedule_cache()

        assert cleared == 2
        assert len(_schedule_cache) == 0


class TestGetCacheStats:
    """Tests for get_cache_stats function."""

    def test_returns_correct_structure(self):
        """Stats should include size, maxsize, and ttl for each cache."""
        stats = get_cache_stats()

        assert "game_feed" in stats
        assert "game_content" in stats
        assert "schedule" in stats

        for cache_name in ["game_feed", "game_content", "schedule"]:
            assert "size" in stats[cache_name]
            assert "maxsize" in stats[cache_name]
            assert "ttl" in stats[cache_name]

    def test_reflects_actual_cache_state(self):
        """Stats should reflect current cache contents."""
        _game_feed_cache["feed:1"] = {}
        _game_feed_cache["feed:2"] = {}
        _game_content_cache["content:1"] = {}

        stats = get_cache_stats()

        assert stats["game_feed"]["size"] == 2
        assert stats["game_content"]["size"] == 1
        assert stats["schedule"]["size"] == 0

    def test_ttl_values(self):
        """TTL values should match the configured values."""
        stats = get_cache_stats()

        assert stats["game_feed"]["ttl"] == 10
        assert stats["game_content"]["ttl"] == 90
        assert stats["schedule"]["ttl"] == 120
