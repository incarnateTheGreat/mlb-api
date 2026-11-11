"""
Games router — endpoints for game data and AI-generated summaries.

FastAPI routers are like Express Router or Remix route modules.
Each router groups related endpoints under a common prefix.

Key concepts for the TS developer:
- @router.get("/path") is like a Remix loader or Express GET handler
- Path params use {param} syntax (vs. :param in Express, $param in Remix)
- Query params are function arguments with defaults
- Depends() is dependency injection (like React context but for requests)
- Response types are validated by Pydantic before sending
"""

from typing import Annotated, Optional
from datetime import date as date_type
import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.mlb_client import get_mlb_client, MLBStatsClient
from app.services.ai_service import get_ai_service, AIService
from app.services.cache_service import (
    get_cached_game_summary,
    cache_game_summary,
    CacheService,
)
from app.services.memory_cache import (
    get_cached_game_feed_bytes,
    cache_game_feed_bytes,
)
from app.models.game import (
    GameBoxscore,
    GameSummary,
    GameContent,
    AllStarGameItem,
    AllStarGamesResponse,
)
from app.utils import process_schedule_response

router = APIRouter()

ALL_STAR_CACHE_TTL_SECONDS = 60 * 60 * 24 * 270  # 270 days
ALL_STAR_CACHE_CONTROL = (
    f"public, max-age={ALL_STAR_CACHE_TTL_SECONDS}, "
    f"s-maxage={ALL_STAR_CACHE_TTL_SECONDS}, stale-while-revalidate=86400"
)

_all_star_cache: AllStarGamesResponse | None = None
_all_star_cache_expires_at: float = 0.0
_all_star_cache_lock = asyncio.Lock()


def _format_all_star_result(game: dict) -> str:
    status = (game.get("status") or {}).get("detailedState")

    away = (game.get("teams") or {}).get("away") or {}
    home = (game.get("teams") or {}).get("home") or {}

    away_team_name = ((away.get("team") or {}).get("name") or "Away").strip()
    home_team_name = ((home.get("team") or {}).get("name") or "Home").strip()

    away_score = away.get("score")
    home_score = home.get("score")

    if isinstance(away_score, int) and isinstance(home_score, int):
        return f"{away_team_name} {away_score}-{home_score} {home_team_name}"

    if isinstance(status, str) and status.strip():
        return status.strip()

    return "Result unavailable"


def _format_all_star_pitchers(game: dict) -> str | None:
    decisions = game.get("decisions") or {}

    winner = decisions.get("winner") or {}
    loser = decisions.get("loser") or {}
    save = decisions.get("save") or {}

    parts: list[str] = []

    winner_name = winner.get("fullName")
    winner_record = winner.get("record")
    if isinstance(winner_name, str) and winner_name.strip():
        winner_text = f"W: {winner_name.strip()}"
        if isinstance(winner_record, str) and winner_record.strip():
            winner_text += f" ({winner_record.strip()})"
        parts.append(winner_text)

    loser_name = loser.get("fullName")
    loser_record = loser.get("record")
    if isinstance(loser_name, str) and loser_name.strip():
        loser_text = f"L: {loser_name.strip()}"
        if isinstance(loser_record, str) and loser_record.strip():
            loser_text += f" ({loser_record.strip()})"
        parts.append(loser_text)

    save_name = save.get("fullName")
    save_record = save.get("record")
    if isinstance(save_name, str) and save_name.strip():
        save_text = f"SV: {save_name.strip()}"
        if isinstance(save_record, str) and save_record.strip():
            save_text += f" ({save_record.strip()})"
        parts.append(save_text)

    if not parts:
        return None

    return " | ".join(parts)


def _format_all_star_location(game: dict) -> str | None:
    venue = game.get("venue") or {}
    venue_name = venue.get("name")
    location = venue.get("location") or {}
    city = location.get("city")

    if not isinstance(venue_name, str) or not venue_name.strip():
        return None

    cleaned_venue = venue_name.strip()

    if isinstance(city, str) and city.strip():
        cleaned_city = city.strip()
        return f"{cleaned_venue} ({cleaned_city})"

    return cleaned_venue


@router.get("/all-star", response_model=AllStarGamesResponse)
async def get_all_star_games(
    response: Response,
    mlb_client: Annotated[MLBStatsClient, Depends(get_mlb_client)],
) -> AllStarGamesResponse:
    """
    Get historical MLB All-Star games.

    Returns a minimal contract for frontend table views:
    gamePk, officialDate, result, pitchers, and location.
    """
    global _all_star_cache, _all_star_cache_expires_at

    now = time.monotonic()
    cached = _all_star_cache
    if cached is not None and now < _all_star_cache_expires_at:
        response.headers["Cache-Control"] = ALL_STAR_CACHE_CONTROL
        response.headers["X-Cache"] = "HIT"
        return cached

    async with _all_star_cache_lock:
        now = time.monotonic()
        cached = _all_star_cache
        if cached is not None and now < _all_star_cache_expires_at:
            response.headers["Cache-Control"] = ALL_STAR_CACHE_CONTROL
            response.headers["X-Cache"] = "HIT"
            return cached

        try:
            data = await mlb_client.get_all_star_games_schedule()
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to fetch All-Star games: {str(e)}",
            )

        games: list[AllStarGameItem] = []

        for date_entry in data.get("dates", []) or []:
            default_date = date_entry.get("date")

            for game in date_entry.get("games", []) or []:
                game_pk = game.get("gamePk")
                official_date = game.get("officialDate") or default_date

                if not isinstance(game_pk, int):
                    continue

                if not isinstance(official_date, str) or not official_date:
                    continue

                games.append(
                    AllStarGameItem(
                        gamePk=game_pk,
                        officialDate=official_date,
                        result=_format_all_star_result(game),
                        pitchers=_format_all_star_pitchers(game),
                        location=_format_all_star_location(game),
                    )
                )

        games.sort(key=lambda item: item.officialDate, reverse=True)

        payload = AllStarGamesResponse(games=games)
        _all_star_cache = payload
        _all_star_cache_expires_at = time.monotonic() + ALL_STAR_CACHE_TTL_SECONDS

    response.headers["Cache-Control"] = ALL_STAR_CACHE_CONTROL
    response.headers["X-Cache"] = "MISS"
    return payload


@router.get("/{game_id}/feed")
async def get_game_feed(
    game_id: int,
    mlb_client: Annotated[MLBStatsClient, Depends(get_mlb_client)],
) -> Response:
    """
    Fetch raw live feed data for a game.
    
    Returns the full unprocessed JSON from the MLB v1.1 API.
    This is the same data as /boxscore but without any transformation.
    
    Uses pre-serialized JSON bytes cache to avoid re-serializing
    multi-MB payloads on every request (10x faster response times).
    """
    try:
        # Check for pre-serialized cached bytes (fast path)
        cached_bytes = get_cached_game_feed_bytes(game_id)
        if cached_bytes is not None:
            return Response(
                content=cached_bytes,
                media_type="application/json",
                headers={"X-Cache": "HIT"},
            )
        
        # Fetch from API, serialize once, and cache
        data = await mlb_client.get_game_feed_raw(game_id)
        json_bytes = cache_game_feed_bytes(game_id, data)
        
        return Response(
            content=json_bytes,
            media_type="application/json",
            headers={"X-Cache": "MISS"},
        )
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Game {game_id} not found or error fetching data: {str(e)}",
        )


@router.get("/{game_id}/boxscore", response_model=GameBoxscore)
async def get_game_boxscore(
    game_id: int,
    mlb_client: Annotated[MLBStatsClient, Depends(get_mlb_client)],
) -> GameBoxscore:
    """
    Fetch raw boxscore data for a game.
    
    This is the unenriched data directly from the MLB Stats API,
    useful when you don't need AI-generated content.
    """
    try:
        return await mlb_client.get_game_boxscore(game_id)
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Game {game_id} not found or error fetching data: {str(e)}",
        )


@router.get(
    "/{game_id}/summary",
    response_model=GameSummary,
    responses={
        200: {
            "description": "Game summary with AI-generated content",
            "headers": {
                "X-Cache-Status": {
                    "description": "Whether the response was served from cache",
                    "schema": {"type": "string", "enum": ["HIT", "MISS"]},
                },
                "X-Generation-Time-Ms": {
                    "description": "Time to generate AI content in milliseconds",
                    "schema": {"type": "integer"},
                },
            },
        },
    },
)
async def get_game_summary(
    game_id: int,
    regenerate: Annotated[bool, Query(description="Force regeneration even if cached")] = False,
    db: Annotated[AsyncSession, Depends(get_db)] = None,
    mlb_client: Annotated[MLBStatsClient, Depends(get_mlb_client)] = None,
    ai_service: Annotated[AIService, Depends(get_ai_service)] = None,
) -> GameSummary:
    """
    Get an AI-generated game summary.
    
    This is the main endpoint for the Remix frontend — it returns:
    - Basic score and status information
    - AI-generated headline and 2-3 paragraph recap
    - Key moments from the game
    - Player of the game
    
    Results are cached to avoid redundant API calls and AI generations.
    Use `regenerate=true` to force a fresh generation.
    
    **Example workflow in your Remix loader:**
    ```typescript
    const response = await fetch(`${API_URL}/games/${gameId}/summary`);
    const summary = await response.json();
    return json({ summary });
    ```
    """
    # Check cache first (unless regenerate is requested)
    if not regenerate:
        cached = await get_cached_game_summary(db, game_id)
        if cached:
            # Parse cached data back into GameSummary
            summary = GameSummary.model_validate(cached)
            summary.cached = True
            return summary
    
    # Fetch fresh boxscore data
    try:
        boxscore = await mlb_client.get_game_boxscore(game_id)
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Game {game_id} not found: {str(e)}",
        )
    
    # Only generate AI summary for completed games
    if boxscore.status.abstract_state != "Final":
        # For live/preview games, return a simple summary without AI
        return GameSummary(
            game_id=boxscore.game_id,
            game_date=boxscore.game_date,
            status=boxscore.status,
            home=boxscore.home,
            away=boxscore.away,
            headline=f"{boxscore.away.team.name} vs {boxscore.home.team.name}",
            summary=f"Game is {boxscore.status.detailed_state.lower()}.",
            key_moments=[],
            player_of_the_game=None,
            cached=False,
        )
    
    # Generate AI summary
    try:
        summary, metadata = await ai_service.generate_game_summary(boxscore)
    except Exception as e:
        # If AI generation fails, return basic summary
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate summary: {str(e)}",
        )
    
    # Cache the result (1 hour TTL for completed games)
    try:
        summary_data = summary.model_dump(mode="json")
        await cache_game_summary(db, game_id, summary_data, ttl_seconds=3600)
    except Exception as e:
        # Log but don't fail the request if caching fails
        print(f"Failed to cache summary for game {game_id}: {e}")
    
    # Log the generation for analytics
    try:
        cache_service = CacheService(db)
        await cache_service.log_ai_generation(
            generation_type="game_summary",
            entity_id=str(game_id),
            model=metadata.model,
            tokens_input=metadata.tokens_used // 2,  # Approximate split
            tokens_output=metadata.tokens_used // 2,
            generation_time_ms=metadata.generation_time_ms,
            output=summary.summary[:500],  # Truncate for storage
        )
    except Exception:
        pass  # Don't fail on logging errors
    
    return summary


@router.get("/schedule")
async def get_schedule(
    time_zone: str,
    date: Annotated[Optional[date_type], Query(description="Date in YYYY-MM-DD format (defaults to today)")] = None,
    fav_team: Annotated[Optional[int], Query(description="Favorite team ID to prioritize in sorting")] = None,
    mlb_client: Annotated[MLBStatsClient, Depends(get_mlb_client)] = None,
) -> dict:
    """
    Get processed game schedule with AL/NL/WBC separation.
    
    Returns games grouped by league, sorted by favorite team,
    plus metadata like postseason flag and completed game count.
    """
    try:
        raw_data = await mlb_client.get_schedule(date=date, team_id=None, time_zone=time_zone)
        return process_schedule_response(raw_data, fav_team=fav_team)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch schedule: {str(e)}",
        )


@router.get("/schedule/raw")
async def get_schedule_raw(
    time_zone: str,
    date: Annotated[Optional[date_type], Query(description="Date in YYYY-MM-DD format (defaults to today)")] = None,
    mlb_client: Annotated[MLBStatsClient, Depends(get_mlb_client)] = None,
) -> dict:
    """
    Get raw MLB API schedule response without processing.
    
    Returns the full dates/games structure directly from MLB API.
    """
    try:
        return await mlb_client.get_schedule(date=date, team_id=None, time_zone=time_zone)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch schedule: {str(e)}",
        )


@router.get("/schedule/range")
async def get_schedule_range(
    start_date: Annotated[date_type, Query(description="Start date in YYYY-MM-DD format")],
    end_date: Annotated[date_type, Query(description="End date in YYYY-MM-DD format")],
    time_zone: Annotated[str, Query(description="Timezone for game times")] = "America/Toronto",
    fields: Annotated[Optional[str], Query(description="Comma-separated fields to return (minimal by default)")] = "dates,date,games,gamePk",
    mlb_client: Annotated[MLBStatsClient, Depends(get_mlb_client)] = None,
) -> dict:
    """
    Get schedule for a date range with minimal data.
    
    Returns raw MLB API response, useful for getting game IDs
    across multiple days without heavy hydration.
    """
    try:
        return await mlb_client.get_schedule_range(
            start_date=start_date,
            end_date=end_date,
            time_zone=time_zone,
            fields=fields,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch schedule range: {str(e)}",
        )


@router.get("/{game_id}/details")
async def get_game_details(
    game_id: int,
    mlb_client: Annotated[MLBStatsClient, Depends(get_mlb_client)],
) -> dict:
    """
    Fetch detailed schedule data for a specific game.
    
    Returns lineups, broadcasts, probable pitchers, tickets, and more.
    This is the schedule endpoint filtered to a single gamePk.
    """
    try:
        return await mlb_client.get_game_details(game_id)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch details for game {game_id}: {str(e)}",
        )


@router.get("/{game_id}/content", response_model=GameContent)
async def get_game_content(
    game_id: int,
    mlb_client: Annotated[MLBStatsClient, Depends(get_mlb_client)],
) -> GameContent:
    """
    Fetch rich content for a game (videos, articles).
    
    Returns video highlights, the official recap article,
    and related articles from MLB's content API.
    """
    try:
        return await mlb_client.get_game_content(game_id)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch content for game {game_id}: {str(e)}",
        )