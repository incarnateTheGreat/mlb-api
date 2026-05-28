"""
Standings-related MLB API endpoints.

Handles division and league standings.
"""

from typing import Any

import httpx

from app.services.memory_cache import cached_standings


class StandingsMixin:
    """Mixin providing standings-related API methods."""

    # BDF transform API for pre-processed standings data
    _BDF_STANDINGS_URL = "https://bdfed.stitch.mlbinfra.com/bdfed/transform-mlb-standings"

    @cached_standings
    async def get_standings(self, year: int) -> dict[str, Any]:
        """
        Fetch MLB standings for a season.

        Uses MLB's internal transform API which returns pre-processed
        standings data with division structure.

        Args:
            year: Season year (e.g., 2024)

        Returns:
            Standings data including structure and team records by division.
        """
        params = {
            "splitPcts": "false",
            "numberPcts": "false",
            "standingsView": "division",
            "sortTemplate": "3",
            "season": str(year),
            "leagueIds": ["103", "104"],
            "standingsTypes": "regularSeason",
            "hydrateAlias": "noSchedule",
            "sortDivisions": "201,202,200,204,205,203",
            "sortLeagues": "103,104,115,114",
            "sortSports": "1",
        }

        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            response = await client.get(self._BDF_STANDINGS_URL, params=params)
            response.raise_for_status()
            return response.json()
