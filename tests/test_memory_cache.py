"""
Tests for in-memory caching decorators and utilities.
"""

import pytest

from app.services.memory_cache import (
    _game_feed_cache,
    _game_content_cache,
    _schedule_cache,
    _standings_cache,
    _teams_cache,
    _matchup_cache,
    _gamelogs_cache,
    cached_game_feed,
    cached_game_content,
    cached_schedule,
    cached_standings,
    cached_team_info,
    cached_team_schedule,
    cached_matchup_analysis,
    cached_player_gamelogs,
    clear_game_cache,
    clear_schedule_cache,
    clear_teams_cache,
    clear_matchup_cache,
    clear_gamelogs_cache,
    get_cache_stats,
)


@pytest.fixture(autouse=True)
def clear_caches():
    """Clear all caches before and after each test."""
    _game_feed_cache.clear()
    _game_content_cache.clear()
    _schedule_cache.clear()
    _standings_cache.clear()
    _teams_cache.clear()
    _matchup_cache.clear()
    _gamelogs_cache.clear()
    yield
    _game_feed_cache.clear()
    _game_content_cache.clear()
    _schedule_cache.clear()
    _standings_cache.clear()
    _teams_cache.clear()
    _matchup_cache.clear()
    _gamelogs_cache.clear()


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


class TestCachedStandings:
    """Tests for the cached_standings decorator."""

    @pytest.mark.asyncio
    async def test_caches_result(self):
        """First call should cache; second should return cached value."""
        call_count = 0

        class MockClient:
            @cached_standings
            async def get_standings(self, year: int, view=None):
                nonlocal call_count
                call_count += 1
                return {"year": year, "standings": "data"}

        client = MockClient()

        result1 = await client.get_standings(2026)
        assert result1 == {"year": 2026, "standings": "data"}
        assert call_count == 1

        result2 = await client.get_standings(2026)
        assert result2 == {"year": 2026, "standings": "data"}
        assert call_count == 1  # Still 1, no new call

    @pytest.mark.asyncio
    async def test_different_years_cached_separately(self):
        """Different years should have separate cache entries."""
        call_count = 0

        class MockClient:
            @cached_standings
            async def get_standings(self, year: int, view=None):
                nonlocal call_count
                call_count += 1
                return {"year": year}

        client = MockClient()

        await client.get_standings(2025)
        await client.get_standings(2026)
        assert call_count == 2

        # Both should now be cached
        await client.get_standings(2025)
        await client.get_standings(2026)
        assert call_count == 2


class TestCachedTeamInfo:
    """Tests for the cached_team_info decorator."""

    @pytest.mark.asyncio
    async def test_caches_result(self):
        """First call should cache; second should return cached value."""
        call_count = 0

        class MockClient:
            @cached_team_info
            async def get_team_info(self, team_id: int, season=None):
                nonlocal call_count
                call_count += 1
                return {"team_id": team_id, "season": season}

        client = MockClient()

        result1 = await client.get_team_info(141, 2026)
        assert result1 == {"team_id": 141, "season": 2026}
        assert call_count == 1

        result2 = await client.get_team_info(141, 2026)
        assert result2 == {"team_id": 141, "season": 2026}
        assert call_count == 1  # Still 1, no new call

    @pytest.mark.asyncio
    async def test_different_teams_cached_separately(self):
        """Different teams should have separate cache entries."""
        call_count = 0

        class MockClient:
            @cached_team_info
            async def get_team_info(self, team_id: int, season=None):
                nonlocal call_count
                call_count += 1
                return {"team_id": team_id}

        client = MockClient()

        await client.get_team_info(141, 2026)
        await client.get_team_info(147, 2026)
        assert call_count == 2

        # Both should now be cached
        await client.get_team_info(141, 2026)
        await client.get_team_info(147, 2026)
        assert call_count == 2


class TestCachedTeamSchedule:
    """Tests for the cached_team_schedule decorator."""

    @pytest.mark.asyncio
    async def test_caches_result(self):
        """First call should cache; second should return cached value."""
        call_count = 0

        class MockClient:
            @cached_team_schedule
            async def get_team_schedule(self, team_id: int, start_date=None, end_date=None):
                nonlocal call_count
                call_count += 1
                return {"team_id": team_id, "games": []}

        client = MockClient()

        result1 = await client.get_team_schedule(141, "2026-05-01", "2026-05-31")
        assert result1 == {"team_id": 141, "games": []}
        assert call_count == 1

        result2 = await client.get_team_schedule(141, "2026-05-01", "2026-05-31")
        assert result2 == {"team_id": 141, "games": []}
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_different_date_ranges_cached_separately(self):
        """Different date ranges should have separate cache entries."""
        call_count = 0

        class MockClient:
            @cached_team_schedule
            async def get_team_schedule(self, team_id: int, start_date=None, end_date=None):
                nonlocal call_count
                call_count += 1
                return {"team_id": team_id, "start": start_date}

        client = MockClient()

        await client.get_team_schedule(141, "2026-05-01", "2026-05-31")
        await client.get_team_schedule(141, "2026-06-01", "2026-06-30")
        assert call_count == 2


class TestCachedMatchupAnalysis:
    """Tests for the cached_matchup_analysis decorator."""

    @pytest.mark.asyncio
    async def test_caches_result(self):
        """First call should cache; second should return cached value."""
        call_count = 0

        class MockService:
            @cached_matchup_analysis
            async def get_matchup(self, batter_id: int, pitcher_id: int, season: int):
                nonlocal call_count
                call_count += 1
                return {"batter": batter_id, "pitcher": pitcher_id, "season": season}

        service = MockService()

        result1 = await service.get_matchup(12345, 67890, 2026)
        assert result1 == {"batter": 12345, "pitcher": 67890, "season": 2026}
        assert call_count == 1

        result2 = await service.get_matchup(12345, 67890, 2026)
        assert result2 == {"batter": 12345, "pitcher": 67890, "season": 2026}
        assert call_count == 1  # Still 1, no new call

    @pytest.mark.asyncio
    async def test_different_matchups_cached_separately(self):
        """Different batter/pitcher combos should have separate cache entries."""
        call_count = 0

        class MockService:
            @cached_matchup_analysis
            async def get_matchup(self, batter_id: int, pitcher_id: int, season: int):
                nonlocal call_count
                call_count += 1
                return {"batter": batter_id, "pitcher": pitcher_id}

        service = MockService()

        await service.get_matchup(111, 222, 2026)
        await service.get_matchup(111, 333, 2026)
        await service.get_matchup(444, 222, 2026)
        assert call_count == 3

        # All should now be cached
        await service.get_matchup(111, 222, 2026)
        await service.get_matchup(111, 333, 2026)
        await service.get_matchup(444, 222, 2026)
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_different_seasons_cached_separately(self):
        """Same matchup in different seasons should have separate cache entries."""
        call_count = 0

        class MockService:
            @cached_matchup_analysis
            async def get_matchup(self, batter_id: int, pitcher_id: int, season: int):
                nonlocal call_count
                call_count += 1
                return {"season": season}

        service = MockService()

        await service.get_matchup(111, 222, 2025)
        await service.get_matchup(111, 222, 2026)
        assert call_count == 2


class TestCachedPlayerGamelogs:
    """Tests for the cached_player_gamelogs decorator."""

    @pytest.mark.asyncio
    async def test_caches_result(self):
        """First call should cache; second should return cached value."""
        call_count = 0

        class MockClient:
            @cached_player_gamelogs
            async def get_player_gamelogs(self, player_id: int, season: int, month=None, game_type="R"):
                nonlocal call_count
                call_count += 1
                return {"player_id": player_id, "season": season, "month": month}

        client = MockClient()

        result1 = await client.get_player_gamelogs(592450, 2026)
        assert result1 == {"player_id": 592450, "season": 2026, "month": None}
        assert call_count == 1

        result2 = await client.get_player_gamelogs(592450, 2026)
        assert result2 == {"player_id": 592450, "season": 2026, "month": None}
        assert call_count == 1  # Still 1, no new call

    @pytest.mark.asyncio
    async def test_different_players_cached_separately(self):
        """Different players should have separate cache entries."""
        call_count = 0

        class MockClient:
            @cached_player_gamelogs
            async def get_player_gamelogs(self, player_id: int, season: int, month=None, game_type="R"):
                nonlocal call_count
                call_count += 1
                return {"player_id": player_id}

        client = MockClient()

        await client.get_player_gamelogs(592450, 2026)
        await client.get_player_gamelogs(660271, 2026)
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_different_months_cached_separately(self):
        """Different month filters should have separate cache entries."""
        call_count = 0

        class MockClient:
            @cached_player_gamelogs
            async def get_player_gamelogs(self, player_id: int, season: int, month=None, game_type="R"):
                nonlocal call_count
                call_count += 1
                return {"month": month}

        client = MockClient()

        await client.get_player_gamelogs(592450, 2026, month=5)
        await client.get_player_gamelogs(592450, 2026, month=6)
        await client.get_player_gamelogs(592450, 2026, month=None)
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_different_game_types_cached_separately(self):
        """Different game types should have separate cache entries."""
        call_count = 0

        class MockClient:
            @cached_player_gamelogs
            async def get_player_gamelogs(self, player_id: int, season: int, month=None, game_type="R"):
                nonlocal call_count
                call_count += 1
                return {"game_type": game_type}

        client = MockClient()

        await client.get_player_gamelogs(592450, 2026, game_type="R")
        await client.get_player_gamelogs(592450, 2026, game_type="S")
        assert call_count == 2


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


class TestClearTeamsCache:
    """Tests for clear_teams_cache function."""

    def test_clear_all_teams(self):
        """Clearing without team_id should remove all team cache entries."""
        _teams_cache["team_info:141:2026"] = {"data": 1}
        _teams_cache["team_info:147:2026"] = {"data": 2}
        _teams_cache["team_schedule:141:abc"] = {"games": []}

        cleared = clear_teams_cache()

        assert cleared == 3
        assert len(_teams_cache) == 0

    def test_clear_specific_team(self):
        """Clearing a specific team should only remove that team's entries."""
        _teams_cache["team_info:141:2026"] = {"data": 1}
        _teams_cache["team_info:147:2026"] = {"data": 2}
        _teams_cache["team_schedule:141:abc"] = {"games": []}
        _teams_cache["team_schedule:147:def"] = {"games": []}

        cleared = clear_teams_cache(141)

        assert cleared == 2  # team_info + team_schedule for team 141
        assert "team_info:141:2026" not in _teams_cache
        assert "team_schedule:141:abc" not in _teams_cache
        assert "team_info:147:2026" in _teams_cache
        assert "team_schedule:147:def" in _teams_cache

    def test_clear_nonexistent_team(self):
        """Clearing a team not in cache should return 0."""
        _teams_cache["team_info:141:2026"] = {"data": 1}

        cleared = clear_teams_cache(999)

        assert cleared == 0
        assert "team_info:141:2026" in _teams_cache


class TestClearMatchupCache:
    """Tests for clear_matchup_cache function."""

    def test_clear_all_matchups(self):
        """Clearing without args should remove all matchup cache entries."""
        _matchup_cache["matchup:111:222:2026"] = {"data": 1}
        _matchup_cache["matchup:333:444:2026"] = {"data": 2}
        _matchup_cache["matchup:111:555:2025"] = {"data": 3}

        cleared = clear_matchup_cache()

        assert cleared == 3
        assert len(_matchup_cache) == 0

    def test_clear_by_batter(self):
        """Clearing by batter_id should only remove that batter's matchups."""
        _matchup_cache["matchup:111:222:2026"] = {"data": 1}
        _matchup_cache["matchup:111:333:2026"] = {"data": 2}
        _matchup_cache["matchup:444:222:2026"] = {"data": 3}

        cleared = clear_matchup_cache(batter_id=111)

        assert cleared == 2
        assert "matchup:111:222:2026" not in _matchup_cache
        assert "matchup:111:333:2026" not in _matchup_cache
        assert "matchup:444:222:2026" in _matchup_cache

    def test_clear_by_pitcher(self):
        """Clearing by pitcher_id should only remove that pitcher's matchups."""
        _matchup_cache["matchup:111:222:2026"] = {"data": 1}
        _matchup_cache["matchup:333:222:2026"] = {"data": 2}
        _matchup_cache["matchup:444:555:2026"] = {"data": 3}

        cleared = clear_matchup_cache(pitcher_id=222)

        assert cleared == 2
        assert "matchup:111:222:2026" not in _matchup_cache
        assert "matchup:333:222:2026" not in _matchup_cache
        assert "matchup:444:555:2026" in _matchup_cache

    def test_clear_nonexistent_players(self):
        """Clearing players not in cache should return 0."""
        _matchup_cache["matchup:111:222:2026"] = {"data": 1}

        cleared = clear_matchup_cache(batter_id=999)

        assert cleared == 0
        assert "matchup:111:222:2026" in _matchup_cache


class TestClearGamelogsCache:
    """Tests for clear_gamelogs_cache function."""

    def test_clear_all_gamelogs(self):
        """Clearing without player_id should remove all gamelogs cache entries."""
        _gamelogs_cache["gamelogs:592450:2026:None:R"] = {"data": 1}
        _gamelogs_cache["gamelogs:660271:2026:5:R"] = {"data": 2}
        _gamelogs_cache["gamelogs:592450:2025:None:S"] = {"data": 3}

        cleared = clear_gamelogs_cache()

        assert cleared == 3
        assert len(_gamelogs_cache) == 0

    def test_clear_specific_player(self):
        """Clearing a specific player should only remove that player's gamelogs."""
        _gamelogs_cache["gamelogs:592450:2026:None:R"] = {"data": 1}
        _gamelogs_cache["gamelogs:592450:2026:5:R"] = {"data": 2}
        _gamelogs_cache["gamelogs:660271:2026:None:R"] = {"data": 3}

        cleared = clear_gamelogs_cache(592450)

        assert cleared == 2
        assert "gamelogs:592450:2026:None:R" not in _gamelogs_cache
        assert "gamelogs:592450:2026:5:R" not in _gamelogs_cache
        assert "gamelogs:660271:2026:None:R" in _gamelogs_cache

    def test_clear_nonexistent_player(self):
        """Clearing a player not in cache should return 0."""
        _gamelogs_cache["gamelogs:592450:2026:None:R"] = {"data": 1}

        cleared = clear_gamelogs_cache(999999)

        assert cleared == 0
        assert "gamelogs:592450:2026:None:R" in _gamelogs_cache


class TestGetCacheStats:
    """Tests for get_cache_stats function."""

    def test_returns_correct_structure(self):
        """Stats should include size, maxsize, and ttl for each cache."""
        stats = get_cache_stats()

        assert "game_feed" in stats
        assert "game_content" in stats
        assert "schedule" in stats
        assert "teams" in stats
        assert "matchup" in stats
        assert "gamelogs" in stats

        for cache_name in ["game_feed", "game_content", "schedule", "teams", "matchup", "gamelogs"]:
            assert "size" in stats[cache_name]
            assert "maxsize" in stats[cache_name]
            assert "ttl" in stats[cache_name]

    def test_reflects_actual_cache_state(self):
        """Stats should reflect current cache contents."""
        _game_feed_cache["feed:1"] = {}
        _game_feed_cache["feed:2"] = {}
        _game_content_cache["content:1"] = {}
        _teams_cache["team_info:141:2026"] = {}
        _matchup_cache["matchup:111:222:2026"] = {}
        _gamelogs_cache["gamelogs:592450:2026:None:R"] = {}

        stats = get_cache_stats()

        assert stats["game_feed"]["size"] == 2
        assert stats["game_content"]["size"] == 1
        assert stats["schedule"]["size"] == 0
        assert stats["teams"]["size"] == 1
        assert stats["matchup"]["size"] == 1
        assert stats["gamelogs"]["size"] == 1

    def test_ttl_values(self):
        """TTL values should match the configured values."""
        stats = get_cache_stats()

        assert stats["game_feed"]["ttl"] == 10
        assert stats["game_content"]["ttl"] == 90
        assert stats["schedule"]["ttl"] == 120
        assert stats["teams"]["ttl"] == 300
        assert stats["matchup"]["ttl"] == 1800
        assert stats["gamelogs"]["ttl"] == 120
