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
    GameContent,
    GameVideo,
    GameArticle,
    VideoPlayback,
)
from app.models.player import PlayerBio, BattingStats, PitchingStats


class MLBStatsClient:
    """
    Async client for the MLB Stats API.
    
    This wraps the raw API calls and transforms responses into
    our Pydantic models — similar to how you might wrap fetch
    with a typed API client in TypeScript.
    
    Uses two base URLs:
    - v1 (statsapi.mlb.com) for schedule, players, etc.
    - v1.1 (ws.statsapi.mlb.com) for live game data and boxscores
    """
    
    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.mlb_stats_api_base_url  # v1 API
        self.live_url = settings.mlb_stats_api_live_url  # v1.1 API
        self.graphql_url = "https://data-graph.mlb.com/graphql/"  # GraphQL API
        self._client_v1: Optional[httpx.AsyncClient] = None
        self._client_live: Optional[httpx.AsyncClient] = None
        self._client_graphql: Optional[httpx.AsyncClient] = None
    
    async def _get_client_v1(self) -> httpx.AsyncClient:
        """Get client for v1 API (schedule, players, etc.)."""
        if self._client_v1 is None or self._client_v1.is_closed:
            self._client_v1 = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=30.0,
                headers={"User-Agent": "mlb-api/1.0"},
                verify=False,
            )
        return self._client_v1
    
    async def _get_client_live(self) -> httpx.AsyncClient:
        """Get client for v1.1 API (live game data, boxscores)."""
        if self._client_live is None or self._client_live.is_closed:
            self._client_live = httpx.AsyncClient(
                base_url=self.live_url,
                timeout=30.0,
                headers={"User-Agent": "mlb-api/1.0"},
                verify=False,
            )
        return self._client_live
    
    async def _get_client_graphql(self) -> httpx.AsyncClient:
        """Get client for MLB GraphQL API (content, videos, articles)."""
        if self._client_graphql is None or self._client_graphql.is_closed:
            self._client_graphql = httpx.AsyncClient(
                base_url=self.graphql_url,
                timeout=30.0,
                headers={
                    "User-Agent": "mlb-api/1.0",
                    "Apollo-Require-Preflight": "true",
                },
                verify=False,
            )
        return self._client_graphql
    
    async def close(self) -> None:
        """Close all HTTP client connections."""
        if self._client_v1 is not None:
            await self._client_v1.aclose()
            self._client_v1 = None
        if self._client_live is not None:
            await self._client_live.aclose()
            self._client_live = None
        if self._client_graphql is not None:
            await self._client_graphql.aclose()
            self._client_graphql = None
    
    async def _get(self, endpoint: str, params: Optional[dict] = None) -> dict[str, Any]:
        """Make a GET request to the v1 MLB Stats API."""
        client = await self._get_client_v1()
        response = await client.get(endpoint, params=params)
        response.raise_for_status()
        return response.json()
    
    async def _get_live(self, endpoint: str, params: Optional[dict] = None) -> dict[str, Any]:
        """Make a GET request to the v1.1 MLB Stats API (live data)."""
        client = await self._get_client_live()
        response = await client.get(endpoint, params=params)
        response.raise_for_status()
        return response.json()
    
    
    
    # =========================================================================
    # Game endpoints
    # =========================================================================
    
    async def get_game_boxscore(self, game_id: int) -> GameBoxscore:
        """
        Fetch boxscore data for a specific game.
        
        The MLB API returns deeply nested data — we flatten and normalize
        it into our clean Pydantic models here.
        
        Uses v1.1 API (ws.statsapi.mlb.com) for live game data.
        """
        # Fetch the live feed which contains all game data
        feed_data = await self._get_live(f"/game/{game_id}/feed/live")
        
        # Parse game info from feed
        game_data = feed_data.get("gameData", {})
        live_data = feed_data.get("liveData", {})
        
        # Extract linescore and boxscore from live data
        linescore_data = live_data.get("linescore", {})
        boxscore_data = live_data.get("boxscore", {})
        
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
        }

        params["timeZone"] = time_zone
        
        if date:
            date_str = date.isoformat()
            params["startDate"] = date_str
            params["endDate"] = date_str
        if team_id:
            params["teamId"] = team_id
            
        data = await self._get("/schedule", params=params)
        
        return data
    
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

    async def get_player_profile(self, player_id: int) -> dict[str, Any]:
        """
        Fetch full player profile with career stats.
        
        This returns the raw MLB API response including:
        - currentTeam info
        - team info  
        - year-by-year hitting and pitching stats
        - career regular season totals
        
        Used by the player profile page to display comprehensive stats.
        """
        params = {
            "hydrate": "currentTeam,team,stats(group=[hitting,pitching],type=[yearByYear,careerRegularSeason],team(league),leagueListId=mlb_hist)",
            "site": "en",
        }
        
        data = await self._get(f"/people/{player_id}", params=params)
        people = data.get("people", [])
        
        return people[0] if people else {}

    # =========================================================================
    # Content endpoints (GraphQL)
    # =========================================================================

    async def get_game_content(self, game_id: int) -> GameContent:
        """
        Fetch rich content (videos, articles) for a game via GraphQL.
        
        This calls MLB's data-graph.mlb.com GraphQL endpoint to get:
        - Video highlights
        - Recap article
        - Related articles
        """
        query = """
        query getGamesByGamePks(
            $gamePks: [Int],
            $locale: Language,
            $gameRecapTags: [String]!,
            $relatedArticleTags: [String]!,
            $contentSource: ContentSource
        ) {
            getGamesByGamePks(gamePks: $gamePks) {
                gamePk
                gameDate
                content {
                    videoContent(locale: $locale) {
                        headline
                        duration
                        title
                        description
                        slug
                        blurb
                        contentDate
                        preferredPlaybackScenarioURL(preferredPlaybacks: ["hlsCloud", "mp4Avc"])
                        playbacks: playbackScenarios {
                            name: playback
                            url: location
                        }
                        thumbnail {
                            templateUrl
                        }
                    }
                    ... on GameContent {
                        recapArticle: articleContent(
                            locale: $locale,
                            tags: $gameRecapTags,
                            limit: 1,
                            contentSource: $contentSource
                        ) {
                            contentDate
                            description
                            headline
                            slug
                            blurb: summary
                            templateUrl: thumbnail
                            type
                        }
                        relatedArticles: articleContent(
                            locale: $locale,
                            tags: $relatedArticleTags,
                            excludeTags: $gameRecapTags,
                            limit: 5
                        ) {
                            contentDate
                            description
                            headline
                            slug
                            blurb: summary
                            templateUrl: thumbnail
                            type
                        }
                    }
                }
            }
        }
        """
        
        variables = {
            "gamePks": [game_id],
            "locale": "EN_US",
            "gameRecapTags": ["game-recap"],
            "relatedArticleTags": ["storytype-article"],
            "contentSource": "MLB",
        }
        
        client = await self._get_client_graphql()
        response = await client.post(
            "",
            json={
                "operationName": "getGamesByGamePks",
                "query": query,
                "variables": variables,
            },
        )
        response.raise_for_status()
        data = response.json()
        
        # Parse the response
        games = data.get("data", {}).get("getGamesByGamePks", [])
        if not games:
            return GameContent(game_pk=game_id)
        
        game = games[0]
        content = game.get("content", {}) or {}
        
        # Parse videos
        videos = []
        for v in content.get("videoContent", []) or []:
            videos.append(GameVideo(
                headline=v.get("headline"),
                title=v.get("title"),
                description=v.get("description"),
                duration=v.get("duration"),
                slug=v.get("slug", ""),
                blurb=v.get("blurb"),
                content_date=v.get("contentDate"),
                thumbnail_url=v.get("thumbnail", {}).get("templateUrl") if v.get("thumbnail") else None,
                preferred_playback_url=v.get("preferredPlaybackScenarioURL"),
                playbacks=[
                    VideoPlayback(name=p.get("name", ""), url=p.get("url", ""))
                    for p in (v.get("playbacks") or [])
                ],
            ))
        
        # Parse recap article
        recap_list = content.get("recapArticle") or []
        recap_article = None
        if recap_list:
            r = recap_list[0]
            recap_article = GameArticle(
                headline=r.get("headline"),
                description=r.get("description"),
                slug=r.get("slug", ""),
                blurb=r.get("blurb"),
                thumbnail_url=r.get("templateUrl"),
                content_date=r.get("contentDate"),
                type=r.get("type"),
            )
        
        # Parse related articles
        related_articles = []
        for a in content.get("relatedArticles", []) or []:
            related_articles.append(GameArticle(
                headline=a.get("headline"),
                description=a.get("description"),
                slug=a.get("slug", ""),
                blurb=a.get("blurb"),
                thumbnail_url=a.get("templateUrl"),
                content_date=a.get("contentDate"),
                type=a.get("type"),
            ))
        
        return GameContent(
            videos=videos,
            recap_article=recap_article,
            related_articles=related_articles,
        )


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
    return _mlb_client
