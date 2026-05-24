"""
Schedule-related MLB API endpoints.

Handles game schedules and game details.
"""

from datetime import date
from typing import Any, Optional


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
