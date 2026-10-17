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
    
    async def get_game_start_times(
        self, game_ids: list[int]
    ) -> dict[int, str]:
        """
        First pitch for several games at once, as ISO timestamps.
        
        Built for the scoring watcher, which has to know whether a subscribed
        game is worth polling yet. That makes it a hot-ish path on a cold
        schedule, so it asks for as little as possible: the `gamePks` filter
        answers every pending game in ONE request, and `fields` trims the
        response from ~1.6KB per game to ~140 bytes.
        
        A postponed game keeps its gamePk and is listed TWICE - once on the
        original date and once on the make-up date - so the response cannot be
        collapsed by gamePk alone without the result depending on array order.
        Observed live: 849817 appeared as both a rained-out Oct 17 entry and a
        rescheduled Oct 18 one. Picking the wrong one tells the watcher a game
        started in the past, which makes it poll continuously until the real
        first pitch. Abandoned dates are therefore discarded explicitly.
        
        Games the schedule does not return are simply absent from the result;
        the caller decides what an unknown start time means.
        """
        if not game_ids:
            return {}
        
        data = await self._get(
            "/schedule",
            params={
                "gamePks": ",".join(str(game_id) for game_id in game_ids),
                "fields": "dates,games,gamePk,gameDate,status,codedGameState",
            },
        )
        
        # gamePk -> candidate start times, split by whether that date still
        # stands. Postponed ("D") and cancelled ("C") entries are only used
        # when nothing else is on offer.
        playable: dict[int, list[str]] = {}
        abandoned: dict[int, list[str]] = {}
        
        for date_entry in data.get("dates", []) or []:
            for game in date_entry.get("games", []) or []:
                game_pk = game.get("gamePk")
                game_date = game.get("gameDate")
                
                if game_pk is None or not game_date:
                    continue
                
                coded = (game.get("status") or {}).get("codedGameState")
                bucket = abandoned if coded in ("D", "C") else playable
                
                bucket.setdefault(game_pk, []).append(game_date)
        
        starts: dict[int, str] = {}
        
        for game_pk in set(playable) | set(abandoned):
            # Earliest of the dates still standing, so a game is polled too
            # early rather than slept through. Falls back to the abandoned
            # date when a postponement has no make-up date yet, which at least
            # keeps the game known rather than unknown.
            candidates = playable.get(game_pk) or abandoned.get(game_pk) or []
            
            if candidates:
                starts[game_pk] = min(candidates)
        
        return starts
    
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
