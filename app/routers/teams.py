"""
Teams router — endpoints for team data and schedules.
"""

from typing import Annotated, Any, Optional
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Path

from app.services.mlb_client import (
    get_mlb_client,
    MLBStatsClient,
    TEAM_INDEX,
    TEAM_NAMES,
)
from app.utils import (
    get_date_range_for_month,
    extract_pitcher_ids_from_schedule,
    build_pitcher_stats_map,
    build_game_log_record_map,
    process_team_schedule,
)


router = APIRouter()


# Timezone mapping for cookie values
TZ_OPTIONS = {
    "EST": "America/Toronto",
    "CST": "America/Chicago",
    "MST": "America/Denver",
    "PST": "America/Los_Angeles",
}


@router.get("/")
async def list_teams() -> list[dict[str, Any]]:
    """
    List all MLB teams with their IDs and slugs.
    
    Useful for building team pickers in the UI.
    """
    return [
        {
            "slug": slug,
            "id": team_id,
            "name": TEAM_NAMES.get(slug, slug.title()),
        }
        for slug, team_id in TEAM_INDEX.items()
    ]


@router.get("/{team_slug}/info")
async def get_team_info(
    team_slug: Annotated[str, Path(description="Team slug (e.g., 'bluejays')")],
    season: Annotated[Optional[int], Query(description="Season year")] = None,
    mlb_client: Annotated[MLBStatsClient, Depends(get_mlb_client)] = None,
) -> dict[str, Any]:
    """
    Fetch team information with standings.
    
    Returns team details including current standings data.
    """
    team_id = TEAM_INDEX.get(team_slug)
    if not team_id:
        raise HTTPException(status_code=404, detail=f"Unknown team: {team_slug}")
    
    if season is None:
        season = date.today().year
    
    try:
        response = await mlb_client.get_team_info(team_id, season)
        teams = response.get("teams", [])
        return teams[0] if teams else {}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching team info: {str(e)}",
        )


@router.get("/{team_slug}/schedule")
async def get_team_schedule(
    team_slug: Annotated[str, Path(description="Team slug (e.g., 'bluejays')")],
    year: Annotated[Optional[int], Query(description="Season year")] = None,
    month: Annotated[Optional[str], Query(description="Month name or 'All'")] = None,
    tz: Annotated[str, Query(description="Timezone code")] = "EST",
    mlb_client: Annotated[MLBStatsClient, Depends(get_mlb_client)] = None,
) -> dict[str, Any]:
    """
    Fetch team schedule with enriched pitcher data.
    
    This endpoint:
    1. Fetches the team schedule for the specified date range
    2. Extracts all pitcher IDs (probable + decision pitchers)
    3. Batch fetches pitcher season stats and game logs
    4. Enriches the schedule with stats and cumulative records
    
    Query params:
    - year: Season year (default: current year)
    - month: Month name (e.g., "May") or "All" for full season
    - tz: Timezone code (EST, CST, MST, PST)
    
    Returns:
    - data: List of processed game objects
    - teamName: Team slug
    - teamId: MLB team ID
    - monthRecord: Win-loss record for the date range
    - teamInfoResponse: Team info with standings
    """
    team_id = TEAM_INDEX.get(team_slug)
    if not team_id:
        raise HTTPException(status_code=404, detail=f"Unknown team: {team_slug}")
    
    # Default year and month
    today = date.today()
    if year is None:
        year = today.year
    
    if month is None:
        # Default to current month if within a month, else "All"
        month = today.strftime("%B")  # Full month name
    
    # Get timezone
    timezone = TZ_OPTIONS.get(tz, "America/Toronto")
    
    # Calculate date range
    date_range = get_date_range_for_month(year, month)
    start_date = date.fromisoformat(date_range["startDate"])
    end_date = date.fromisoformat(date_range["endDate"])
    
    try:
        # Fetch team info and schedule in parallel
        import asyncio
        
        team_info_task = mlb_client.get_team_info(team_id, year)
        schedule_task = mlb_client.get_team_schedule(
            team_id, start_date, end_date, year, "America/New_York"
        )
        
        team_info_response, schedule_response = await asyncio.gather(
            team_info_task, schedule_task
        )
        
        # Extract pitcher IDs for batch stats fetching
        pitcher_ids = extract_pitcher_ids_from_schedule(schedule_response)
        
        # Fetch stats for spring and regular season pitchers in parallel
        spring_stats_task = mlb_client.get_pitcher_season_stats(
            list(pitcher_ids["springPitcherIds"]), year, "S"
        )
        regular_stats_task = mlb_client.get_pitcher_season_stats(
            list(pitcher_ids["regularPitcherIds"]), year, "R"
        )
        spring_logs_task = mlb_client.get_pitcher_game_logs(
            list(pitcher_ids["springDecisionIds"]), year, "S"
        )
        regular_logs_task = mlb_client.get_pitcher_game_logs(
            list(pitcher_ids["regularDecisionIds"]), year, "R"
        )
        
        (
            spring_stats_response,
            regular_stats_response,
            spring_logs_response,
            regular_logs_response,
        ) = await asyncio.gather(
            spring_stats_task,
            regular_stats_task,
            spring_logs_task,
            regular_logs_task,
        )
        
        # Build combined stats and game log maps
        spring_stats_map = build_pitcher_stats_map(spring_stats_response, "S")
        regular_stats_map = build_pitcher_stats_map(regular_stats_response, "R")
        stats_map = {**spring_stats_map, **regular_stats_map}
        
        spring_log_map = build_game_log_record_map(spring_logs_response)
        regular_log_map = build_game_log_record_map(regular_logs_response)
        game_log_map = {**spring_log_map, **regular_log_map}
        
        # Process the schedule into the final format
        processed = process_team_schedule(
            schedule_response,
            team_id,
            stats_map,
            game_log_map,
            timezone,
            month,
        )
        
        # Extract team info
        teams = team_info_response.get("teams", [])
        team_info = teams[0] if teams else {}
        
        return {
            "data": processed["games"],
            "teamName": team_slug,
            "teamId": team_id,
            "selectedTeam": processed["selectedTeam"],
            "monthRecord": processed["monthRecord"],
            "teamInfoResponse": team_info,
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching team schedule: {str(e)}",
        )


@router.get("/{team_slug}/head-to-head/{opponent_slug}")
async def get_head_to_head(
    team_slug: Annotated[str, Path(description="Team slug (e.g., 'bluejays')")],
    opponent_slug: Annotated[str, Path(description="Opponent team slug (e.g., 'yankees')")],
    year: Annotated[Optional[int], Query(description="Season year")] = None,
    tz: Annotated[str, Query(description="Timezone code")] = "EST",
    mlb_client: Annotated[MLBStatsClient, Depends(get_mlb_client)] = None,
) -> dict[str, Any]:
    """
    Fetch head-to-head schedule between two teams.
    
    This endpoint:
    1. Fetches all games between the two teams for the season
    2. Extracts pitcher IDs and batch fetches stats
    3. Enriches with stats and cumulative records
    4. Calculates the head-to-head record
    
    Query params:
    - year: Season year (default: current year)
    - tz: Timezone code (EST, CST, MST, PST)
    
    Returns:
    - data: List of processed game objects
    - teamName: Team slug
    - teamId: MLB team ID
    - opponentName: Opponent display name
    - headToHeadRecord: Win-loss record against opponent
    - teamInfoResponse: Team info with standings
    """
    team_id = TEAM_INDEX.get(team_slug)
    opponent_id = TEAM_INDEX.get(opponent_slug)
    opponent_name = TEAM_NAMES.get(opponent_slug)
    
    if not team_id:
        raise HTTPException(status_code=404, detail=f"Unknown team: {team_slug}")
    if not opponent_id:
        raise HTTPException(status_code=404, detail=f"Unknown opponent: {opponent_slug}")
    
    today = date.today()
    if year is None:
        year = today.year
    
    timezone = TZ_OPTIONS.get(tz, "America/Toronto")
    
    try:
        import asyncio
        
        # Fetch team info and head-to-head schedule in parallel
        team_info_task = mlb_client.get_team_info(team_id, year)
        schedule_task = mlb_client.get_head_to_head_schedule(
            team_id, opponent_id, year, "America/New_York"
        )
        
        team_info_response, schedule_response = await asyncio.gather(
            team_info_task, schedule_task
        )
        
        # Extract pitcher IDs for batch stats fetching
        pitcher_ids = extract_pitcher_ids_from_schedule(schedule_response)
        
        # Fetch stats for spring and regular season pitchers in parallel
        spring_stats_task = mlb_client.get_pitcher_season_stats(
            list(pitcher_ids["springPitcherIds"]), year, "S"
        )
        regular_stats_task = mlb_client.get_pitcher_season_stats(
            list(pitcher_ids["regularPitcherIds"]), year, "R"
        )
        spring_logs_task = mlb_client.get_pitcher_game_logs(
            list(pitcher_ids["springDecisionIds"]), year, "S"
        )
        regular_logs_task = mlb_client.get_pitcher_game_logs(
            list(pitcher_ids["regularDecisionIds"]), year, "R"
        )
        
        (
            spring_stats_response,
            regular_stats_response,
            spring_logs_response,
            regular_logs_response,
        ) = await asyncio.gather(
            spring_stats_task,
            regular_stats_task,
            spring_logs_task,
            regular_logs_task,
        )
        
        # Build combined stats and game log maps
        spring_stats_map = build_pitcher_stats_map(spring_stats_response, "S")
        regular_stats_map = build_pitcher_stats_map(regular_stats_response, "R")
        stats_map = {**spring_stats_map, **regular_stats_map}
        
        spring_log_map = build_game_log_record_map(spring_logs_response)
        regular_log_map = build_game_log_record_map(regular_logs_response)
        game_log_map = {**spring_log_map, **regular_log_map}
        
        # Process the schedule into the final format
        # Note: we pass None for month since head-to-head doesn't filter by month
        processed = process_team_schedule(
            schedule_response,
            team_id,
            stats_map,
            game_log_map,
            timezone,
            None,  # No month filter
        )
        
        # Extract team info
        teams = team_info_response.get("teams", [])
        team_info = teams[0] if teams else {}
        
        return {
            "data": processed["games"],
            "teamName": team_slug,
            "teamId": team_id,
            "selectedTeam": processed["selectedTeam"],
            "opponentName": opponent_name,
            "headToHeadRecord": processed["monthRecord"],  # Reuse monthRecord as headToHeadRecord
            "teamInfoResponse": team_info,
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching head-to-head schedule: {str(e)}",
        )
