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

from typing import Optional
from datetime import date as date_type

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.game import GameBoxscore, GameSummary, GameSummaryRequest
from app.models.analysis import AIGenerationMetadata
from app.services.mlb_client import get_mlb_client, MLBStatsClient
from app.services.ai_service import get_ai_service, AIService
from app.services.cache_service import (
    get_cached_game_summary,
    cache_game_summary,
    CacheService,
)
from app.models.game import GameBoxscore, GameSummary, GameSummaryRequest, GameContent
from app.utils import process_schedule_response

router = APIRouter()


@router.get("/{game_id}/feed")
async def get_game_feed(
    game_id: int,
    mlb_client: MLBStatsClient = Depends(get_mlb_client),
) -> dict:
    """
    Fetch raw live feed data for a game.
    
    Returns the full unprocessed JSON from the MLB v1.1 API.
    This is the same data as /boxscore but without any transformation.
    """
    try:
        return await mlb_client.get_game_feed(game_id)
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Game {game_id} not found or error fetching data: {str(e)}",
        )


@router.get("/{game_id}/boxscore", response_model=GameBoxscore)
async def get_game_boxscore(
    game_id: int,
    mlb_client: MLBStatsClient = Depends(get_mlb_client),
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
    regenerate: bool = Query(
        default=False,
        description="Force regeneration even if cached",
    ),
    db: AsyncSession = Depends(get_db),
    mlb_client: MLBStatsClient = Depends(get_mlb_client),
    ai_service: AIService = Depends(get_ai_service),
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
    date: Optional[date_type] = Query(
        default=None,
        description="Date in YYYY-MM-DD format (defaults to today)",
    ),
    fav_team: Optional[int] = Query(
        default=None,
        description="Favorite team ID to prioritize in sorting",
    ),
    mlb_client: MLBStatsClient = Depends(get_mlb_client),
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
    date: Optional[date_type] = Query(
        default=None,
        description="Date in YYYY-MM-DD format (defaults to today)",
    ),
    mlb_client: MLBStatsClient = Depends(get_mlb_client),
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
    start_date: date_type = Query(
        description="Start date in YYYY-MM-DD format",
    ),
    end_date: date_type = Query(
        description="End date in YYYY-MM-DD format",
    ),
    time_zone: str = Query(
        default="America/Toronto",
        description="Timezone for game times",
    ),
    fields: Optional[str] = Query(
        default="dates,date,games,gamePk",
        description="Comma-separated fields to return (minimal by default)",
    ),
    mlb_client: MLBStatsClient = Depends(get_mlb_client),
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
    mlb_client: MLBStatsClient = Depends(get_mlb_client),
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
    mlb_client: MLBStatsClient = Depends(get_mlb_client),
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