"""
Team-related MLB API endpoints.

Handles team info and team schedule data.
"""

from datetime import date
from typing import Any, Optional

from app.services.memory_cache import (
    cached_league_rates,
    cached_team_info,
    cached_team_detail,
    cached_team_leaders,
    cached_team_schedule,
    cached_team_roster,
    cached_team_coaches,
)


# Team slug to ID mapping
TEAM_INDEX: dict[str, int] = {
    "angels": 108,
    "astros": 117,
    "athletics": 133,
    "bluejays": 141,
    "braves": 144,
    "brewers": 158,
    "cardinals": 138,
    "cubs": 112,
    "dbacks": 109,
    "dodgers": 119,
    "giants": 137,
    "guardians": 114,
    "mariners": 136,
    "marlins": 146,
    "mets": 121,
    "nationals": 120,
    "orioles": 110,
    "padres": 135,
    "phillies": 143,
    "pirates": 134,
    "rangers": 140,
    "rays": 139,
    "reds": 113,
    "redsox": 111,
    "rockies": 115,
    "royals": 118,
    "tigers": 116,
    "twins": 142,
    "whitesox": 145,
    "yankees": 147,
}

# Team display names
TEAM_NAMES: dict[str, str] = {
    "angels": "Angels",
    "astros": "Astros",
    "athletics": "Athletics",
    "bluejays": "Blue Jays",
    "braves": "Braves",
    "brewers": "Brewers",
    "cardinals": "Cardinals",
    "cubs": "Cubs",
    "dbacks": "Dbacks",
    "dodgers": "Dodgers",
    "giants": "Giants",
    "guardians": "Guardians",
    "mariners": "Mariners",
    "marlins": "Marlins",
    "mets": "Mets",
    "nationals": "Nationals",
    "orioles": "Orioles",
    "padres": "Padres",
    "phillies": "Phillies",
    "pirates": "Pirates",
    "rangers": "Rangers",
    "rays": "Rays",
    "reds": "Reds",
    "redsox": "Red Sox",
    "rockies": "Rockies",
    "royals": "Royals",
    "tigers": "Tigers",
    "twins": "Twins",
    "whitesox": "White Sox",
    "yankees": "Yankees",
}

# Team abbreviation to ID mapping
TEAM_ABBREV_INDEX: dict[str, int] = {
    "LAA": 108,
    "HOU": 117,
    "OAK": 133,
    "TOR": 141,
    "ATL": 144,
    "MIL": 158,
    "STL": 138,
    "CHC": 112,
    "ARI": 109,
    "LAD": 119,
    "SF": 137,
    "CLE": 114,
    "SEA": 136,
    "MIA": 146,
    "NYM": 121,
    "WSH": 120,
    "BAL": 110,
    "SD": 135,
    "PHI": 143,
    "PIT": 134,
    "TEX": 140,
    "TB": 139,
    "CIN": 113,
    "BOS": 111,
    "COL": 115,
    "KC": 118,
    "DET": 116,
    "MIN": 142,
    "CWS": 145,
    "NYY": 147,
}


def get_team_id(team_slug: str) -> Optional[int]:
    """Get team ID from slug (e.g., 'bluejays' -> 141)."""
    return TEAM_INDEX.get(team_slug)


def get_team_name(team_slug: str) -> Optional[str]:
    """Get team display name from slug (e.g., 'bluejays' -> 'Blue Jays')."""
    return TEAM_NAMES.get(team_slug)


def get_team_slug_by_id(team_id: int) -> Optional[str]:
    """Get team slug from ID (e.g., 141 -> 'bluejays')."""
    for slug, id_ in TEAM_INDEX.items():
        if id_ == team_id:
            return slug
    return None


class TeamsMixin:
    """Mixin providing team-related API methods."""

    # The categories a pre-game note has room for. Kept here rather than
    # taken from the caller so the cache key stays one per club per
    # season — varying the categories per request would fragment it.
    _DEFAULT_LEADER_CATEGORIES = (
        "homeRuns,battingAverage,runsBattedIn,strikeouts,earnedRunAverage,saves"
    )

    @cached_team_info
    async def get_team_info(
        self,
        team_id: int,
        season: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Fetch team information with standings.
        
        Args:
            team_id: MLB team ID
            season: Season year (defaults to current year)
        
        Returns:
            Team info including standings data
        """
        params = {
            "hydrate": "standings",
        }
        
        if season:
            params["season"] = season
        
        return await self._get(f"/teams/{team_id}", params=params)

    @cached_team_schedule
    async def get_team_schedule(
        self,
        team_id: int,
        start_date: date,
        end_date: date,
        season: int,
        time_zone: str = "America/New_York",
    ) -> dict[str, Any]:
        """
        Fetch team schedule for a date range.
        
        This is the full schedule with probable pitchers, decisions,
        linescores, series info, etc.
        
        Args:
            team_id: MLB team ID
            start_date: Start of date range
            end_date: End of date range
            season: Season year
            time_zone: Timezone for game times
        
        Returns:
            Full schedule response with hydrated data
        """
        params = {
            "lang": "en",
            "hydrate": (
                "team(venue(timezone)),"
                "venue(timezone),"
                "game(seriesStatus,seriesSummary),"
                "seriesStatus,"
                "seriesSummary,"
                "linescore,"
                "probablePitcher(note,stats(group=[pitching],type=[season],sportId=1)),"
                "decisions(stats(group=[pitching],type=[season],sportId=1))"
            ),
            "season": season,
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "teamId": team_id,
            "timeZone": time_zone,
            "eventTypes": "primary",
            "scheduleTypes": "games,events",
            "sportId": 1,
        }
        
        return await self._get("/schedule", params=params)

    @cached_league_rates
    async def get_league_batting_rates(self, season: int) -> dict[str, float]:
        """
        League-wide batting rates, aggregated from all 30 clubs.

        log5 needs a real league baseline; hardcoding one would silently bias
        every projection as run environments drift year to year. There is no
        single "league totals" endpoint, so sum the per-team totals instead —
        that is the definition of the league rate, not an approximation.

        Returns an empty dict when the upstream shape is unusable, so callers
        can fall back rather than divide by zero.
        """
        response = await self._get(
            "/teams/stats",
            params={
                "stats": "season",
                "group": "hitting",
                "sportId": 1,
                "season": season,
            },
        )

        hits = 0
        at_bats = 0
        for entry in response.get("stats", []):
            for split in entry.get("splits") or []:
                stat = split.get("stat") or {}
                try:
                    hits += int(stat.get("hits") or 0)
                    at_bats += int(stat.get("atBats") or 0)
                except (TypeError, ValueError):
                    continue

        if at_bats <= 0:
            return {}

        return {"avg": hits / at_bats, "at_bats": at_bats, "hits": hits}

    async def get_pitcher_season_stats(
        self,
        pitcher_ids: list[int],
        season: int,
        game_type: str = "R",
    ) -> dict[str, Any]:
        """
        Batch fetch season stats for multiple pitchers.
        
        Args:
            pitcher_ids: List of pitcher IDs
            season: Season year
            game_type: "S" for Spring Training, "R" for Regular/Post
        
        Returns:
            Raw API response with pitcher stats
        """
        if not pitcher_ids:
            return {"people": []}
        
        game_type_param = ",gameType=[S]" if game_type == "S" else ""
        
        params = {
            "personIds": ",".join(str(pid) for pid in pitcher_ids),
            "hydrate": f"stats(group=[pitching],type=[season],season={season},sportId=1{game_type_param})",
        }
        
        return await self._get("/people", params=params)

    async def get_pitcher_game_logs(
        self,
        pitcher_ids: list[int],
        season: int,
        game_type: str = "R",
    ) -> dict[str, Any]:
        """
        Fetch game-by-game pitching logs for multiple pitchers.
        
        Args:
            pitcher_ids: List of pitcher IDs
            season: Season year
            game_type: "S" for Spring Training, "R" for Regular/Post
        
        Returns:
            Raw API response with game logs
        """
        if not pitcher_ids:
            return {"people": []}
        
        game_type_param = ",gameType=[S]" if game_type == "S" else ""
        
        params = {
            "personIds": ",".join(str(pid) for pid in pitcher_ids),
            "hydrate": f"stats(group=[pitching],type=[gameLog],season={season}{game_type_param})",
        }
        
        return await self._get("/people", params=params)

    async def get_head_to_head_schedule(
        self,
        team_id: int,
        opponent_id: int,
        season: int,
        time_zone: str = "America/New_York",
    ) -> dict[str, Any]:
        """
        Fetch schedule for games between two teams.
        
        Args:
            team_id: MLB team ID
            opponent_id: Opponent team ID
            season: Season year
            time_zone: Timezone for game times
        
        Returns:
            Schedule response for head-to-head matchups
        """
        params = {
            "lang": "en",
            "hydrate": (
                "team(venue(timezone)),"
                "venue(timezone),"
                "game(seriesStatus,seriesSummary),"
                "seriesStatus,"
                "seriesSummary,"
                "linescore,"
                "probablePitcher,"
                "decisions"
            ),
            "season": season,
            "teamId": team_id,
            "opponentId": opponent_id,
            "timeZone": time_zone,
            "eventTypes": "primary",
            "scheduleTypes": "games,events",
            "sportId": 1,
        }
        
        return await self._get("/schedule", params=params)

    @cached_team_detail
    async def get_team_detail(
        self,
        team_id: int,
    ) -> dict[str, Any]:
        """
        Fetch detailed team information with venue, league, division.
        
        This endpoint is specifically for the team info page.
        
        Args:
            team_id: MLB team ID
        
        Returns:
            Team info including venue, league, division, sport
        """
        params = {
            "hydrate": "venue,league,division,sport",
        }
        
        data = await self._get(f"/teams/{team_id}", params=params)
        teams = data.get("teams", [])
        return teams[0] if teams else {}

    @cached_team_roster
    async def get_team_roster(
        self,
        team_id: int,
        roster_type: str = "fullSeason",
    ) -> list[dict[str, Any]]:
        """
        Fetch team roster.
        
        Args:
            team_id: MLB team ID
            roster_type: Type of roster ("active", "fullSeason", "40Man")
        
        Returns:
            List of roster entries with player and position info
        """
        params = {
            "rosterType": roster_type,
        }
        
        data = await self._get(f"/teams/{team_id}/roster", params=params)
        return data.get("roster", [])

    @cached_team_coaches
    async def get_team_coaches(
        self,
        team_id: int,
    ) -> list[dict[str, Any]]:
        """
        Fetch team coaching staff.
        
        Args:
            team_id: MLB team ID
        
        Returns:
            List of coach entries with person and title info
        """
        data = await self._get(f"/teams/{team_id}/coaches")
        return data.get("roster", [])

    @cached_team_leaders
    async def get_team_leaders(
        self,
        team_id: int,
        season: int,
        categories: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch a club's statistical leaders.

        Every category comes back for every stat group, whatever is asked
        for — passing `statGroup` does not narrow it. So a request for home
        runs returns the club leader in home runs *hit*, the leader in home
        runs *allowed*, and a catching figure as well, all under the same
        category name. Callers must read `statGroup` off each group to tell
        them apart; see `summarize_leaders`.

        Regular season only. Postseason totals are a handful of games and
        would crown whoever happened to homer in a division series.

        Args:
            team_id: MLB team ID
            season: Season year
            categories: Comma-separated leaderCategories values, defaulting
                to the set a pre-game note has room for

        Returns:
            The raw `teamLeaders` groups, each with its own statGroup.
        """
        data = await self._get(
            f"/teams/{team_id}/leaders",
            params={
                "leaderCategories": categories or self._DEFAULT_LEADER_CATEGORIES,
                "season": season,
                "leaderGameTypes": "R",
            },
        )
        return data.get("teamLeaders", [])
