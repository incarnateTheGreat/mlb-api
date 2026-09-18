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
    
    async def get_game_feed_raw(self, game_id: int) -> dict[str, Any]:
        """
        Fetch raw live feed data without decorator caching.
        
        Used by the /feed endpoint which manages its own bytes cache
        for faster JSON serialization.
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
    
    async def get_game_plays(self, game_id: int) -> list[dict[str, Any]]:
        """
        Fetch play-by-play data for a game.
        
        Returns the allPlays array from the live feed, chronologically ordered.
        Each play contains about (inning, time), result (event type, description),
        matchup (batter, pitcher), and runners (on base).
        
        Args:
            game_id: MLB game ID (game_pk)
        
        Returns:
            List of play objects with timestamp, players, result, etc.
        """
        feed_data = await self.get_game_feed(game_id)
        plays_obj = feed_data.get("liveData", {}).get("plays", {})
        # plays is an object with keys: allPlays, currentPlay, scoringPlays, playsByInning
        all_plays = plays_obj.get("allPlays", [])
        return all_plays if isinstance(all_plays, list) else []
    
    def _summarize_plays(self, plays: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Create a condensed summary of key moments from plays.
        
        Extracts:
        - Scoring plays with score progression
        - Home runs with player names
        - Key events (walks, strikeouts in crucial moments)
        - Inning-by-inning progression
        
        Returns structured summary for Claude to synthesize into narrative.
        """
        key_moments = []
        inning_progression = {}
        
        for play in plays:
            if not isinstance(play, dict):
                continue
            
            about = play.get("about", {})
            result = play.get("result", {})
            matchup = play.get("matchup", {})
            
            inning = about.get("inning", 0)
            is_scoring = about.get("isScoringPlay", False)
            
            event_type = result.get("eventType", "")
            description = result.get("description", "")
            away_score = result.get("awayScore")
            home_score = result.get("homeScore")
            
            batter_name = matchup.get("batter", {}).get("fullName", "Unknown")
            pitcher_name = matchup.get("pitcher", {}).get("fullName", "Unknown")
            
            # Track scoring plays
            if is_scoring:
                key_moments.append({
                    "inning": inning,
                    "type": "scoring_play",
                    "description": description,
                    "away_score": away_score,
                    "home_score": home_score,
                    "batter": batter_name,
                })
            
            # Track home runs specifically
            if event_type == "home_run":
                key_moments.append({
                    "inning": inning,
                    "type": "home_run",
                    "player": batter_name,
                    "description": description,
                })
            
            # Track important pitcher moments (strikeouts in tight games)
            if event_type == "strikeout" and inning >= 7 and abs((home_score or 0) - (away_score or 0)) <= 1:
                key_moments.append({
                    "inning": inning,
                    "type": "strikeout",
                    "pitcher": pitcher_name,
                    "batter": batter_name,
                })
            
            # Track inning progression
            if away_score is not None and home_score is not None:
                if inning not in inning_progression:
                    inning_progression[inning] = {"away": away_score, "home": home_score}
        
        return {
            "key_moments_count": len(key_moments),
            "key_moments": key_moments[:15],  # Limit to top 15 moments
            "total_plays": len(plays),
            "inning_progression": inning_progression,
        }
