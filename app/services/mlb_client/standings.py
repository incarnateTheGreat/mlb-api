"""
Standings-related MLB API endpoints.

Handles division and league standings.
"""

from enum import Enum
from typing import Any

import httpx

from app.services.memory_cache import cached_standings


class StandingsView(str, Enum):
    """Available standings view presets."""
    DIVISION = "division"      # Divisional standings (default)
    MLB = "mlb"                # Full league ranking
    PRESEASON = "preseason"    # Spring training
    WILDCARD = "wildcard"      # Wild card race


# Preset configurations for each view
_VIEW_CONFIGS: dict[StandingsView, dict[str, str]] = {
    StandingsView.DIVISION: {
        "standingsView": "division",
        "standingsTypes": "regularSeason",
    },
    StandingsView.MLB: {
        "standingsView": "sport",
        "standingsTypes": "regularSeason",
    },
    StandingsView.PRESEASON: {
        "standingsView": "sport",
        "standingsTypes": "springTraining",
    },
    StandingsView.WILDCARD: {
        "standingsView": "league",
        "standingsTypes": "wildCard",
    },
}


class StandingsMixin:
    """Mixin providing standings-related API methods."""

    # BDF transform API for pre-processed standings data
    _BDF_STANDINGS_URL = "https://bdfed.stitch.mlbinfra.com/bdfed/transform-mlb-standings"

    @cached_standings
    async def get_standings(
        self, 
        year: int, 
        view: StandingsView = StandingsView.DIVISION,
    ) -> dict[str, Any]:
        """
        Fetch MLB standings for a season.

        Uses MLB's internal transform API which returns pre-processed
        standings data.

        Args:
            year: Season year (e.g., 2024)
            view: Standings view preset (division, mlb, preseason, wildcard)

        Returns:
            Standings data including structure and team records.
        """
        config = _VIEW_CONFIGS[view]
        
        params = {
            "splitPcts": "false",
            "numberPcts": "false",
            "standingsView": config["standingsView"],
            "sortTemplate": "3",
            "season": str(year),
            "leagueIds": ["103", "104"],
            "standingsTypes": config["standingsTypes"],
            "hydrateAlias": "noSchedule",
            "sortDivisions": "201,202,200,204,205,203",
            "sortLeagues": "103,104,115,114",
            "sortSports": "1",
        }

        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            response = await client.get(self._BDF_STANDINGS_URL, params=params)
            response.raise_for_status()
            return response.json()
