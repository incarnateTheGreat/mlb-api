"""
Player-related MLB API endpoints.

Handles player bios and statistics.
"""

from datetime import date, datetime, timedelta
from typing import Any, Optional

from app.models.player import PlayerBio, GameLogSplit, GameLogTeam, GameLogGame
from app.services.memory_cache import (
    cached_player,
    cached_player_stats,
    cached_player_profile,
    cached_player_gamelogs,
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

    @cached_player_gamelogs
    async def get_player_gamelogs(
        self,
        player_id: int,
        season: int,
        month: Optional[int] = None,
        game_type: str = "R",
    ) -> dict[str, list[GameLogSplit]]:
        """
        Fetch player game logs for a season.
        
        Args:
            player_id: MLB player ID
            season: Year (e.g., 2024)
            month: Optional month filter (1-12) - only applies to regular season
            game_type: "R" (regular), "S" (spring training), etc.
        
        Returns dict with hittingSplits and pitchingSplits lists.
        """
        # Build date range if month is specified (only for regular season)
        date_range = ""
        if month and game_type == "R":
            start = f"{season}-{str(month).zfill(2)}-01"
            # Get last day of month
            if month == 12:
                last_day = 31
            else:
                next_month = date(season, month + 1, 1)
                last_day = (next_month - timedelta(days=1)).day
            end = f"{season}-{str(month).zfill(2)}-{str(last_day).zfill(2)}"
            date_range = f"&startDate={start}&endDate={end}"
        
        # Fetch hitting and pitching logs in parallel
        hitting_params = {
            "stats": "gameLog",
            "group": "hitting",
            "season": season,
            "gameType": game_type,
            "language": "en",
        }
        pitching_params = {
            "stats": "gameLog",
            "group": "pitching",
            "season": season,
            "gameType": game_type,
            "language": "en",
        }
        
        # Add date range to URL if specified
        hitting_url = f"/people/{player_id}/stats"
        pitching_url = f"/people/{player_id}/stats"
        if date_range:
            hitting_url += f"?stats=gameLog&group=hitting&season={season}&gameType={game_type}&language=en{date_range}"
            pitching_url += f"?stats=gameLog&group=pitching&season={season}&gameType={game_type}&language=en{date_range}"
            hitting_data = await self._get(hitting_url)
            pitching_data = await self._get(pitching_url)
        else:
            hitting_data = await self._get(hitting_url, params=hitting_params)
            pitching_data = await self._get(pitching_url, params=pitching_params)
        
        hitting_splits_raw = hitting_data.get("stats", [{}])[0].get("splits", []) if hitting_data.get("stats") else []
        pitching_splits_raw = pitching_data.get("stats", [{}])[0].get("splits", []) if pitching_data.get("stats") else []
        
        # Get today's and yesterday's date for checking live games
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        recent_dates = {today, yesterday}
        
        # Collect game PKs from recent dates to check if they're final
        recent_game_pks = set()
        for split in hitting_splits_raw + pitching_splits_raw:
            if split.get("date") in recent_dates and split.get("game", {}).get("gamePk"):
                recent_game_pks.add(split["game"]["gamePk"])
        
        # Check which games are final
        final_game_pks = await self._fetch_final_game_pks(list(recent_game_pks))
        
        # Enrich splits with isGameOver
        hitting_splits = self._enrich_splits(hitting_splits_raw, recent_dates, final_game_pks)
        pitching_splits = self._enrich_splits(pitching_splits_raw, recent_dates, final_game_pks)
        
        return {
            "hittingSplits": hitting_splits,
            "pitchingSplits": pitching_splits,
        }
    
    async def _fetch_final_game_pks(self, game_pks: list[int]) -> set[int]:
        """Check schedule API to determine which games are Final."""
        final_pks: set[int] = set()
        if not game_pks:
            return final_pks
        
        try:
            params = {
                "gamePks": ",".join(str(pk) for pk in game_pks),
                "language": "en",
            }
            data = await self._get("/schedule", params=params)
            
            for date_entry in data.get("dates", []):
                for game in date_entry.get("games", []):
                    if game.get("status", {}).get("abstractGameState") == "Final":
                        final_pks.add(game["gamePk"])
        except Exception:
            # On error, return empty set - caller treats games as in-progress
            pass
        
        return final_pks
    
    def _enrich_splits(
        self,
        splits_raw: list[dict],
        recent_dates: set[str],
        final_game_pks: set[int],
    ) -> list[GameLogSplit]:
        """Convert raw splits to GameLogSplit with isGameOver computed."""
        result = []
        for s in splits_raw:
            game_pk = s.get("game", {}).get("gamePk")
            split_date = s.get("date", "")
            
            # Games outside recent window are always over
            # Recent games need schedule API confirmation
            is_game_over = split_date not in recent_dates or game_pk in final_game_pks
            
            result.append(GameLogSplit(
                season=s.get("season", ""),
                stat=s.get("stat", {}),
                team=GameLogTeam(
                    id=s.get("team", {}).get("id", 0),
                    name=s.get("team", {}).get("name", ""),
                    abbreviation=s.get("team", {}).get("abbreviation", ""),
                    link=s.get("team", {}).get("link", ""),
                ),
                opponent=GameLogTeam(
                    id=s.get("opponent", {}).get("id", 0),
                    name=s.get("opponent", {}).get("name", ""),
                    abbreviation=s.get("opponent", {}).get("abbreviation", ""),
                    link=s.get("opponent", {}).get("link", ""),
                ),
                date=split_date,
                isHome=s.get("isHome", False),
                isWin=s.get("isWin", False),
                isGameOver=is_game_over,
                game=GameLogGame(
                    gamePk=game_pk or 0,
                    link=s.get("game", {}).get("link", ""),
                ),
            ))
        
        return result
