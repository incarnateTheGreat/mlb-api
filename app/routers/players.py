"""
Players router — endpoints for player data and stats.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.models.player import PlayerBio, PlayerProfile, AdvancedBattingStats, BattingStats, GameLogsResponse
from app.services.mlb_client import get_mlb_client, MLBStatsClient
from app.services.ai_service import get_ai_service, AIService
from app.services.sabermetrics import enhance_batting_stats


router = APIRouter()


@router.get("/{player_id}", response_model=PlayerBio)
async def get_player(
    player_id: int,
    mlb_client: MLBStatsClient = Depends(get_mlb_client),
) -> PlayerBio:
    """Get player biographical information."""
    try:
        return await mlb_client.get_player(player_id)
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Player {player_id} not found: {str(e)}",
        )


@router.get("/{player_id}/stats")
async def get_player_stats(
    player_id: int,
    season: int = Query(default=2024, ge=1900, le=2100),
    include_advanced: bool = Query(
        default=True,
        description="Include calculated sabermetric stats (wOBA, wRC+, etc.)",
    ),
    mlb_client: MLBStatsClient = Depends(get_mlb_client),
) -> dict:
    """
    Get player statistics for a season.
    
    When `include_advanced=true` (default), adds sabermetric
    calculations like wOBA, wRC+, OPS+, ISO, and BABIP.
    """
    try:
        raw_stats = await mlb_client.get_player_stats(
            player_id=player_id,
            season=season,
            group="hitting",
        )
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Stats not found for player {player_id}: {str(e)}",
        )
    
    if not raw_stats:
        return {"message": "No stats found for this player/season"}
    
    if not include_advanced:
        return raw_stats
    
    # Convert raw stats to BattingStats model
    basic_stats = BattingStats(
        games=raw_stats.get("gamesPlayed", 0),
        at_bats=raw_stats.get("atBats", 0),
        runs=raw_stats.get("runs", 0),
        hits=raw_stats.get("hits", 0),
        doubles=raw_stats.get("doubles", 0),
        triples=raw_stats.get("triples", 0),
        home_runs=raw_stats.get("homeRuns", 0),
        rbi=raw_stats.get("rbi", 0),
        stolen_bases=raw_stats.get("stolenBases", 0),
        caught_stealing=raw_stats.get("caughtStealing", 0),
        walks=raw_stats.get("baseOnBalls", 0),
        strikeouts=raw_stats.get("strikeOuts", 0),
        batting_average=float(raw_stats.get("avg", "0") or 0),
        on_base_percentage=float(raw_stats.get("obp", "0") or 0),
        slugging_percentage=float(raw_stats.get("slg", "0") or 0),
        ops=float(raw_stats.get("ops", "0") or 0),
    )
    
    # Enhance with sabermetric calculations
    advanced = enhance_batting_stats(basic_stats)
    
    return advanced.model_dump()


@router.get("/{player_id}/scouting-report")
async def get_scouting_report(
    player_id: int,
    season: int = Query(default=2024, ge=1900, le=2100),
    regenerate: bool = Query(default=False),
    mlb_client: MLBStatsClient = Depends(get_mlb_client),
    ai_service: AIService = Depends(get_ai_service),
) -> dict:
    """
    Get an AI-generated scouting report for a player.
    
    Analyzes the player's stats and generates a professional
    scouting report highlighting strengths, weaknesses, and projections.
    """
    # Fetch player info and stats
    try:
        player = await mlb_client.get_player(player_id)
        raw_stats = await mlb_client.get_player_stats(player_id, season, "hitting")
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Player {player_id} not found: {str(e)}",
        )
    
    # Generate scouting report
    try:
        report, metadata = await ai_service.generate_scouting_report(
            player_name=player.full_name,
            player_id=player_id,
            batting_stats=raw_stats if raw_stats else None,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate scouting report: {str(e)}",
        )
    
    return {
        "player_id": player_id,
        "player_name": player.full_name,
        "season": season,
        "scouting_report": report,
        "metadata": metadata.model_dump(),
    }


@router.get("/{player_id}/profile")
async def get_player_profile(
    player_id: int,
    mlb_client: MLBStatsClient = Depends(get_mlb_client),
) -> dict:
    """
    Get full player profile with career statistics.
    
    Returns comprehensive player data including:
    - Biographical info (name, position, team, etc.)
    - Year-by-year hitting and pitching stats
    - Career regular season totals
    
    Returns an empty dict if player not found.
    """
    try:
        profile = await mlb_client.get_player_profile(player_id)
        return profile if profile else {}
    except Exception:
        return {}


@router.get("/{player_id}/gamelogs/{season}", response_model=GameLogsResponse)
async def get_player_gamelogs(
    player_id: int,
    season: int,
    month: Optional[int] = Query(default=None, ge=1, le=12, description="Month filter (1-12)"),
    game_type: str = Query(default="R", description="Game type: R (regular), S (spring), etc."),
    mlb_client: MLBStatsClient = Depends(get_mlb_client),
) -> GameLogsResponse:
    """
    Get player game logs for a season.
    
    Returns hitting and pitching game-by-game stats with isGameOver
    computed by checking the schedule API for recent games.
    
    - **month**: Optional filter for a specific month (regular season only)
    - **game_type**: R (regular), S (spring training), etc.
    """
    try:
        result = await mlb_client.get_player_gamelogs(
            player_id=player_id,
            season=season,
            month=month,
            game_type=game_type,
        )
        return GameLogsResponse(**result)
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Game logs not found for player {player_id}: {str(e)}",
        )
