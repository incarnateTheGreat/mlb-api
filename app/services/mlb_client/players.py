"""
Player-related MLB API endpoints.

Handles player bios and statistics.
"""

from datetime import date
from typing import Any

from app.models.player import PlayerBio 
from app.services.memory_cache import (
    cached_player,
    cached_player_stats,
    cached_player_profile,
)

class PlayersMixin:
    """Mixin providing player-related API methods."""
    
    @cached_player
    async def get_player(self, player_id: int) -> PlayerBio:
        """Fetch player biographical information."""
        data = await self._get(f"/people/{player_id}")
        
        person = data.get("people", [{}])[0]
        position = person.get("primaryPosition", {})
        current_team = person.get("currentTeam", {})
        
        birth_date_str = person.get("birthDate")
        birth_date = date.fromisoformat(birth_date_str) if birth_date_str else None
        
        return PlayerBio(
            id=player_id,
            full_name=person.get("fullName", "Unknown"),
            first_name=person.get("firstName", ""),
            last_name=person.get("lastName", ""),
            primary_number=person.get("primaryNumber"),
            birth_date=birth_date,
            birth_city=person.get("birthCity"),
            birth_country=person.get("birthCountry"),
            height=person.get("height"),
            weight=person.get("weight"),
            primary_position=position.get("abbreviation", "DH"),
            bat_side=person.get("batSide", {}).get("code", "R"),
            pitch_hand=person.get("pitchHand", {}).get("code", "R"),
            current_team_id=current_team.get("id"),
            current_team_name=current_team.get("name"),
        )
    
    @cached_player_stats
    async def get_player_stats(
        self,
        player_id: int,
        season: int,
        group: str = "hitting",
    ) -> dict[str, Any]:
        """
        Fetch player statistics for a season.
        
        Args:
            player_id: MLB player ID
            season: Year (e.g., 2024)
            group: "hitting" or "pitching"
        
        Returns raw stats dict — the sabermetrics service will
        enhance these with advanced metrics.
        """
        params = {
            "stats": "season",
            "season": season,
            "group": group,
        }
        
        data = await self._get(f"/people/{player_id}/stats", params=params)
        
        stats = data.get("stats", [])
        if not stats:
            return {}
        
        splits = stats[0].get("splits", [])
        if not splits:
            return {}
        
        return splits[0].get("stat", {})

    @cached_player_profile
    async def get_player_profile(self, player_id: int) -> dict[str, Any]:
        """
        Fetch full player profile with career stats.
        
        Returns the raw MLB API response including:
        - currentTeam info
        - team info  
        - year-by-year hitting and pitching stats
        - career regular season totals
        """
        params = {
            "hydrate": "currentTeam,team,stats(group=[hitting,pitching],type=[yearByYear,careerRegularSeason],team(league),leagueListId=mlb_hist)",
            "site": "en",
        }
        
        data = await self._get(f"/people/{player_id}", params=params)
        people = data.get("people", [])
        
        return people[0] if people else {}
