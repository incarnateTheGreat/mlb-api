"""
MLB Stats API client using httpx.

httpx is the Python equivalent of axios/fetch — it's an async HTTP client
with similar ergonomics. Key differences from JS:
- No automatic JSON parsing — use `response.json()` explicitly
- Context managers (`async with`) handle connection cleanup (like try/finally)
- Type hints help but don't enforce at runtime (Pydantic does enforcement)
"""

from datetime import datetime, date
from typing import Any, Optional

import httpx

from app.config import get_settings
from app.models.game import (
    GameBoxscore, 
    GameScore, 
    GameStatus, 
    Pitcher, 
    TeamInfo, 
    TopPerformer,
)
from app.models.player import PlayerBio, BattingStats, PitchingStats


class MLBStatsClient:
    """
    Async client for the MLB Stats API.
    
    This wraps the raw API calls and transforms responses into
    our Pydantic models — similar to how you might wrap fetch
    with a typed API client in TypeScript.
    """
    
    def __init__(self) -> None:
        self.base_url = get_settings().mlb_stats_api_base_url
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily create and return the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=30.0,
                headers={"User-Agent": "mlb-api/1.0"},
                verify=False,  # Bypass SSL verification (corporate proxy/VPN workaround)
            )

        return self._client
    
    async def close(self) -> None:
        """Close the HTTP client connection pool."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
    
    async def _get(self, endpoint: str, params: Optional[dict] = None) -> dict[str, Any]:
        """Make a GET request to the MLB Stats API."""
        client = await self._get_client()
        response = await client.get(endpoint, params=params)
        response.raise_for_status()  # Raises HTTPStatusError on 4xx/5xx
        return response.json()
    
    # =========================================================================
    # Game endpoints
    # =========================================================================
    
    async def get_game_boxscore(self, game_id: int) -> GameBoxscore:
        """
        Fetch boxscore data for a specific game.
        
        The MLB API returns deeply nested data — we flatten and normalize
        it into our clean Pydantic models here.
        """
        # Fetch boxscore and linescore in parallel
        # In Python, we use asyncio.gather() — similar to Promise.all()
        import asyncio
        
        boxscore_task = self._get(f"/game/{game_id}/boxscore")
        linescore_task = self._get(f"/game/{game_id}/linescore")
        feed_task = self._get(f"/game/{game_id}/feed/live")
        
        boxscore_data, linescore_data, feed_data = await asyncio.gather(
            boxscore_task, linescore_task, feed_task
        )
        
        # Parse game info from feed
        game_data = feed_data.get("gameData", {})
        live_data = feed_data.get("liveData", {})
        
        # Extract team info
        teams = game_data.get("teams", {})
        home_team = teams.get("home", {})
        away_team = teams.get("away", {})
        
        # Parse line score for runs/hits/errors
        linescore_teams = linescore_data.get("teams", {})
        home_line = linescore_teams.get("home", {})
        away_line = linescore_teams.get("away", {})
        
        # Parse inning-by-inning scores
        innings = linescore_data.get("innings", [])
        inning_scores = {
            "home": [i.get("home", {}).get("runs", 0) for i in innings],
            "away": [i.get("away", {}).get("runs", 0) for i in innings],
        }
        
        # Parse game status
        status_data = game_data.get("status", {})
        status = GameStatus(
            abstract_state=status_data.get("abstractGameState", "Unknown"),
            detailed_state=status_data.get("detailedState", "Unknown"),
            status_code=status_data.get("statusCode", "U"),
        )
        
        # Parse decisions (winning/losing/save pitchers)
        decisions = live_data.get("decisions", {})
        winning_pitcher = self._parse_decision_pitcher(decisions.get("winner"))
        losing_pitcher = self._parse_decision_pitcher(decisions.get("loser"))
        save_pitcher = self._parse_decision_pitcher(decisions.get("save"))
        
        # Parse top performers (simplified — real implementation would be more sophisticated)
        top_performers = self._extract_top_performers(boxscore_data)
        
        # Parse game date
        game_datetime_str = game_data.get("datetime", {}).get("dateTime")
        game_date = datetime.fromisoformat(game_datetime_str.replace("Z", "+00:00")) if game_datetime_str else datetime.utcnow()
        
        return GameBoxscore(
            game_id=game_id,
            game_date=game_date,
            status=status,
            home=GameScore(
                team=TeamInfo(
                    id=home_team.get("id", 0),
                    name=home_team.get("name", "Unknown"),
                    abbreviation=home_team.get("abbreviation", "UNK"),
                ),
                runs=home_line.get("runs", 0),
                hits=home_line.get("hits", 0),
                errors=home_line.get("errors", 0),
            ),
            away=GameScore(
                team=TeamInfo(
                    id=away_team.get("id", 0),
                    name=away_team.get("name", "Unknown"),
                    abbreviation=away_team.get("abbreviation", "UNK"),
                ),
                runs=away_line.get("runs", 0),
                hits=away_line.get("hits", 0),
                errors=away_line.get("errors", 0),
            ),
            winning_pitcher=winning_pitcher,
            losing_pitcher=losing_pitcher,
            save_pitcher=save_pitcher,
            top_performers=top_performers,
            inning_scores=inning_scores,
        )
    
    def _parse_decision_pitcher(self, data: Optional[dict]) -> Optional[Pitcher]:
        """Parse pitcher decision data into Pitcher model."""
        if not data:
            return None
        
        return Pitcher(
            id=data.get("id", 0),
            name=data.get("fullName", "Unknown"),
            innings_pitched=0.0,  # Not included in decisions
            hits=0,
            runs=0,
            earned_runs=0,
            walks=0,
            strikeouts=0,
            home_runs=0,
        )
    
    def _extract_top_performers(self, boxscore_data: dict) -> list[TopPerformer]:
        """
        Extract top performers from boxscore data.
        
        This is a simplified implementation — a real version would
        analyze all player stats to find standout performances.
        """
        performers = []
        
        for side in ["home", "away"]:
            team_data = boxscore_data.get("teams", {}).get(side, {})
            players = team_data.get("players", {})
            
            for player_id, player_data in players.items():
                stats = player_data.get("stats", {})
                batting = stats.get("batting", {})
                
                # Look for multi-HR games or high RBI games
                home_runs = batting.get("homeRuns", 0)
                rbi = batting.get("rbi", 0)
                hits = batting.get("hits", 0)
                at_bats = batting.get("atBats", 0)
                
                if home_runs >= 2 or rbi >= 4 or (hits >= 3 and at_bats >= 3):
                    person = player_data.get("person", {})
                    position = player_data.get("position", {})
                    
                    # Build stat line string
                    stat_parts = []
                    if at_bats > 0:
                        stat_parts.append(f"{hits}-{at_bats}")
                    if home_runs > 0:
                        stat_parts.append(f"{home_runs} HR")
                    if rbi > 0:
                        stat_parts.append(f"{rbi} RBI")
                    
                    performers.append(TopPerformer(
                        player_id=person.get("id", 0),
                        player_name=person.get("fullName", "Unknown"),
                        position=position.get("abbreviation", ""),
                        stat_line=", ".join(stat_parts),
                    ))
        
        return performers[:5]  # Limit to top 5
    
    # =========================================================================
    # Schedule endpoints
    # =========================================================================
    
    async def get_schedule(
        self, 
        date: Optional[date] = None,
        team_id: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Fetch game schedule for a date and/or team."""
        params = {"sportId": 1}  # MLB
        
        if date:
            params["date"] = date.isoformat()
        if team_id:
            params["teamId"] = team_id
            
        data = await self._get("/schedule", params=params)
        
        # Flatten the nested dates/games structure
        games = []
        for date_entry in data.get("dates", []):
            games.extend(date_entry.get("games", []))
        
        return games
    
    # =========================================================================
    # Player endpoints
    # =========================================================================
    
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
    
    async def get_player_stats(
        self, 
        player_id: int, 
        season: int,
        group: str = "hitting",  # "hitting" or "pitching"
    ) -> dict[str, Any]:
        """
        Fetch player statistics for a season.
        
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


# Singleton instance for dependency injection
_mlb_client: Optional[MLBStatsClient] = None


def get_mlb_client() -> MLBStatsClient:
    """
    Returns a singleton MLB client instance.
    
    This pattern is similar to creating a shared axios instance
    in JavaScript — we reuse connection pools across requests.
    """
    global _mlb_client
    if _mlb_client is None:
        _mlb_client = MLBStatsClient()

        _mlb_client.base_url
    return _mlb_client
