"""
Game-related MLB API endpoints.

Handles live game feeds, boxscores, and game data parsing.
"""

from datetime import datetime
from typing import Any, Optional

from app.models.game import (
    GameBoxscore,
    GameScore,
    GameStatus,
    Pitcher,
    TeamInfo,
    TopPerformer,
)
from app.services.memory_cache import cached_game_feed

class GamesMixin:
    """Mixin providing game-related API methods."""
    
    @cached_game_feed
    async def get_game_feed(self, game_id: int) -> dict[str, Any]:
        """
        Fetch raw live feed data for a game.
        
        Returns the full unprocessed JSON from the MLB v1.1 API.
        Cached for 10 seconds to reduce API load during live games.
        """
        return await self._get_live(f"/game/{game_id}/feed/live", params={"language": "en"})
    
    async def get_game_boxscore(self, game_id: int) -> GameBoxscore:
        """
        Fetch boxscore data for a specific game.
        
        The MLB API returns deeply nested data — we flatten and normalize
        it into our clean Pydantic models here.
        
        Uses v1.1 API (ws.statsapi.mlb.com) for live game data.
        """
        feed_data = await self._get_live(f"/game/{game_id}/feed/live")
        
        game_data = feed_data.get("gameData", {})
        live_data = feed_data.get("liveData", {})
        
        linescore_data = live_data.get("linescore", {})
        boxscore_data = live_data.get("boxscore", {})
        
        teams = game_data.get("teams", {})
        home_team = teams.get("home", {})
        away_team = teams.get("away", {})
        
        linescore_teams = linescore_data.get("teams", {})
        home_line = linescore_teams.get("home", {})
        away_line = linescore_teams.get("away", {})
        
        innings = linescore_data.get("innings", [])
        inning_scores = {
            "home": [i.get("home", {}).get("runs", 0) for i in innings],
            "away": [i.get("away", {}).get("runs", 0) for i in innings],
        }
        
        status_data = game_data.get("status", {})
        status = GameStatus(
            abstract_state=status_data.get("abstractGameState", "Unknown"),
            detailed_state=status_data.get("detailedState", "Unknown"),
            status_code=status_data.get("statusCode", "U"),
        )
        
        decisions = live_data.get("decisions", {})
        winning_pitcher = self._parse_decision_pitcher(decisions.get("winner"))
        losing_pitcher = self._parse_decision_pitcher(decisions.get("loser"))
        save_pitcher = self._parse_decision_pitcher(decisions.get("save"))
        
        top_performers = self._extract_top_performers(boxscore_data)
        
        game_datetime_str = game_data.get("datetime", {}).get("dateTime")
        game_date = (
            datetime.fromisoformat(game_datetime_str.replace("Z", "+00:00"))
            if game_datetime_str
            else datetime.now()
        )
        
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
            innings_pitched=0.0,
            hits=0,
            runs=0,
            earned_runs=0,
            walks=0,
            strikeouts=0,
            home_runs=0,
        )
    
    def _extract_top_performers(self, boxscore_data: dict) -> list[TopPerformer]:
        """Extract top performers from boxscore data."""
        performers = []
        
        for side in ["home", "away"]:
            team_data = boxscore_data.get("teams", {}).get(side, {})
            players = team_data.get("players", {})
            
            for player_id, player_data in players.items():
                performer = self._check_top_performer(player_data)
                if performer:
                    performers.append(performer)
        
        return performers[:5]
    
    def _check_top_performer(self, player_data: dict) -> Optional[TopPerformer]:
        """Check if a player qualifies as a top performer."""
        stats = player_data.get("stats", {})
        batting = stats.get("batting", {})
        
        home_runs = batting.get("homeRuns", 0)
        rbi = batting.get("rbi", 0)
        hits = batting.get("hits", 0)
        at_bats = batting.get("atBats", 0)
        
        if home_runs >= 2 or rbi >= 4 or (hits >= 3 and at_bats >= 3):
            person = player_data.get("person", {})
            position = player_data.get("position", {})
            
            stat_parts = []
            if at_bats > 0:
                stat_parts.append(f"{hits}-{at_bats}")
            if home_runs > 0:
                stat_parts.append(f"{home_runs} HR")
            if rbi > 0:
                stat_parts.append(f"{rbi} RBI")
            
            return TopPerformer(
                player_id=person.get("id", 0),
                player_name=person.get("fullName", "Unknown"),
                position=position.get("abbreviation", ""),
                stat_line=", ".join(stat_parts),
            )
        
        return None
