"""
Tests for the player profile Hall of Fame derivation.

Run with: pytest tests/test_player_profile.py -v
"""

import pytest
from unittest.mock import AsyncMock, patch

from app.services.memory_cache import _player_cache
from app.services.mlb_client import MLBStatsClient


HOF_AWARD = {
    "id": "MLBHOF",
    "name": "Hall Of Fame",
    "date": "2020-01-21",
    "season": "2020",
    "votes": 396,
}

OTHER_AWARD = {
    "id": "ALMVP",
    "name": "American League Most Valuable Player",
    "season": "2009",
}


@pytest.fixture(autouse=True)
def clear_player_cache():
    """get_player_profile memoizes by player id — keep tests independent."""
    _player_cache.clear()
    yield
    _player_cache.clear()


async def fetch_profile(person: dict | None) -> dict:
    """Run get_player_profile against a stubbed MLB API response."""
    people = [person] if person is not None else []
    client = MLBStatsClient()

    with patch.object(
        MLBStatsClient, "_get", new=AsyncMock(return_value={"people": people})
    ):
        return await client.get_player_profile(116539)


class TestHallOfFameDerivation:
    """Tests for the derived hallOfFame field on /players/{id}/profile."""

    @pytest.mark.asyncio
    async def test_inductee_gets_hall_of_fame_object(self):
        profile = await fetch_profile(
            {"id": 116539, "fullName": "Derek Jeter", "awards": [OTHER_AWARD, HOF_AWARD]}
        )

        assert profile["hallOfFame"] == {"season": "2020", "date": "2020-01-21"}

    @pytest.mark.asyncio
    async def test_non_inductee_gets_none(self):
        profile = await fetch_profile(
            {"id": 592450, "fullName": "Aaron Judge", "awards": [OTHER_AWARD]}
        )

        assert profile["hallOfFame"] is None

    @pytest.mark.asyncio
    async def test_missing_awards_key_gets_none(self):
        profile = await fetch_profile({"id": 660271, "fullName": "Shohei Ohtani"})

        assert profile["hallOfFame"] is None

    @pytest.mark.asyncio
    async def test_raw_awards_list_is_stripped(self):
        """The full awards list is ~18KB — it must not reach the client."""
        profile = await fetch_profile(
            {"id": 116539, "fullName": "Derek Jeter", "awards": [OTHER_AWARD, HOF_AWARD]}
        )

        assert "awards" not in profile

    @pytest.mark.asyncio
    async def test_unknown_player_returns_empty_dict(self):
        assert await fetch_profile(None) == {}
