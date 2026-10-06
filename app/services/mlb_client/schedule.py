"""
Schedule-related MLB API endpoints.

Handles game schedules and game details.
"""

from datetime import date
from typing import Any, Optional


# Event type mappings for playoff/special games
EVENT_TYPE_MAP = {
    "world series": "WS",
    "alcs": "ALCS",
    "alds": "ALDS",
    "nlcs": "NLCS",
    "nlds": "NLDS",
    "spring training": "S",
    "regular season": "R",
    "all-star": "A",
    "playoffs": ["ALCS", "ALDS", "NLCS", "NLDS", "WS"],
}


class ScheduleMixin:
    """Mixin providing schedule-related API methods."""
    
    async def get_schedule(
        self,
        time_zone: str,
        date: Optional[date] = None,
        team_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """Fetch game schedule for a date and/or team. Returns the full API response."""
        params = {
            "sportId": [1, 51, 21],
            "gameType": ["E", "S", "R", "F", "D", "L", "W", "A", "C"],
            "leagueId": [104, 103, 160, 590],
            "language": "en",
            "hydrate": "team,linescore(matchup,runners),xrefId,story,flags,statusFlags,broadcasts(all),venue(location),decisions,person,probablePitcher,stats,game(content(media(epg),summary),tickets),seriesStatus(useOverride=true)",
            "sortBy": "gameDate,gameStatus,gameType",
            "timeZone": time_zone,
        }
        
        if date:
            date_str = date.isoformat()
            params["startDate"] = date_str
            params["endDate"] = date_str
        if team_id:
            params["teamId"] = team_id
        
        return await self._get("/schedule", params=params)
    
    async def get_game_details(self, game_id: int) -> dict[str, Any]:
        """
        Fetch detailed schedule data for a specific game.
        
        Returns lineups, broadcasts, probable pitchers, tickets, etc.
        This is the schedule endpoint filtered to a single game.
        """
        params = {
            "gamePk": game_id,
            "language": "en",
            "hydrate": "story,xrefId,lineups,broadcasts(all),probablePitcher(note),game(content(media(epg)),tickets)",
            "useLatestGames": "true",
            "fields": "dates,games,teams,probablePitcher,note,id,dates,games,broadcasts,type,name,homeAway,language,isNational,callSign,mediaState,mediaStateCode,availableForStreaming,freeGame,mediaId,dates,games,game,tickets,ticketType,ticketLinks,dates,games,content,media,epg,dates,games,lineups,homePlayers,awayPlayers,useName,lastName,primaryPosition,abbreviation,dates,games,xrefIds,xrefId,xrefType,story,seriesStatus(useOverride=true)",
        }
        
        return await self._get("/schedule", params=params)
    
    async def get_schedule_range(
        self,
        start_date: date,
        end_date: date,
        time_zone: str = "America/Toronto",
        fields: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Fetch schedule for a date range with minimal data.
        
        Useful for getting game IDs across multiple days without
        heavy hydration.
        
        Args:
            start_date: Start of date range
            end_date: End of date range  
            time_zone: Timezone for game times
            fields: Comma-separated field filter (e.g., "dates,date,games,gamePk")
        """
        params = {
            "sportId": [1, 51, 21],
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "timeZone": time_zone,
        }
        
        if fields:
            params["fields"] = fields
        
        return await self._get("/schedule", params=params)
    
    async def get_historical_games(
        self,
        event_type: str,
        season: int,
        game_number: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch historical games by event type and season.
        
        Args:
            event_type: "world series", "alcs", "alds", "nlcs", "nlds", "regular season", etc.
            season: Year (e.g., 1993, 2024)
            game_number: Specific game number in series (1-7 for playoffs, optional)
        
        Returns:
            List of games matching the criteria, sorted by date
            Each game has: gamePk, gameDate, teams, score, status, series info
        """
        event_type_lower = event_type.lower()
        event_codes = EVENT_TYPE_MAP.get(event_type_lower, event_type_lower.upper())
        
        # Handle "playoffs" -> multiple event codes
        if isinstance(event_codes, list):
            event_codes_str = ",".join(event_codes)
        else:
            event_codes_str = event_codes
        
        params = {
            "sportId": 1,  # MLB only
            "startDate": f"{season}-01-01",
            "endDate": f"{season}-12-31",
            "eventTypes": event_codes_str,
            "language": "en",
            "sortBy": "gameDate",
        }
        
        schedule_data = await self._get("/schedule", params=params)
        
        # Flatten games from all dates into a single list
        all_games = []
        for date_obj in schedule_data.get("dates", []):
            for game in date_obj.get("games", []):
                game_info = {
                    "game_pk": game.get("gamePk"),
                    "game_date": game.get("gameDate"),
                    "game_type": game.get("gameType"),
                    "status": game.get("status", {}).get("detailedState", ""),
                    "home_team": game.get("teams", {}).get("home", {}).get("team", {}).get("name", ""),
                    "away_team": game.get("teams", {}).get("away", {}).get("team", {}).get("name", ""),
                    "home_score": game.get("teams", {}).get("home", {}).get("score"),
                    "away_score": game.get("teams", {}).get("away", {}).get("score"),
                    "description": f"{game.get('teams', {}).get('away', {}).get('team', {}).get('name', '')} @ {game.get('teams', {}).get('home', {}).get('team', {}).get('name', '')}",
                }
                all_games.append(game_info)
        
        # Filter by game number if specified
        if game_number and 1 <= game_number <= len(all_games):
            return [all_games[game_number - 1]]
        
        return all_games
