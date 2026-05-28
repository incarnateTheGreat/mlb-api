"""
Standings router — endpoints for MLB standings data.
"""

from typing import Annotated, Any, Optional
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.services.mlb_client import get_mlb_client, MLBStatsClient, StandingsView


router = APIRouter()


def process_division_standings(response: dict) -> dict[str, Any]:
    """
    Process divisional standings into structured league/division data.
    
    Used for: view=division (default)
    """
    structure = response.get("structure", {})
    records = response.get("records", [])
    
    sports = structure.get("sports", [])
    if not sports:
        return {}
    
    leagues_and_divisions: dict[str, dict[str, Any]] = {}
    
    for league in sports[0].get("leagues", []):
        league_name = league.get("name", "")
        divisions_map: dict[str, Any] = {}
        
        for division in league.get("divisions", []):
            sort_order = str(division.get("sortOrder", 0))
            divisions_map[sort_order] = {
                "name": division.get("name", ""),
                "id": division.get("id", 0),
                "nameShort": division.get("nameShort", ""),
                "sortOrder": division.get("sortOrder", 0),
            }
        
        leagues_and_divisions[league_name] = divisions_map
    
    # Add team records to divisions
    for record in records:
        team_records = record.get("teamRecords", [])
        if not team_records:
            continue
        
        league_name = team_records[0].get("team", {}).get("league", {}).get("name", "")
        division_id = record.get("division")
        
        if league_name not in leagues_and_divisions:
            continue
        
        divisions = leagues_and_divisions[league_name]
        
        for sort_key, division_data in divisions.items():
            if division_data.get("id") == division_id:
                division_data["division"] = record
                break
    
    return leagues_and_divisions


def process_simple_standings(response: dict) -> list[dict]:
    """
    Process simple standings (flat team list).
    
    Used for: view=mlb, view=preseason
    """
    records = response.get("records", [])
    if not records:
        return []
    return records[0].get("teamRecords", [])


def process_wildcard_standings(response: dict) -> dict[str, Any]:
    """
    Process wildcard standings grouped by league.
    
    Used for: view=wildcard
    Returns: { "AL": { "divisionLeaders": [...], "wildCard": [...] }, "NL": { ... } }
    """
    structure = response.get("structure", {})
    records = response.get("records", [])
    
    sports = structure.get("sports", [])
    if not sports:
        return {}
    
    leagues = sports[0].get("leagues", [])
    
    # Initialize structure with league abbreviations
    result: dict[str, dict[str, list]] = {}
    for league in leagues:
        abbrev = league.get("abbreviation", "")
        result[abbrev] = {
            "divisionLeaders": [],
            "wildCard": [],
        }
    
    # Populate with team records
    for record in records:
        league_id = record.get("league")
        standings_type = record.get("standingsType", "")
        team_records = record.get("teamRecords", [])
        
        # Find the league abbreviation
        league = next((l for l in leagues if l.get("id") == league_id), None)
        if not league:
            continue
        
        abbrev = league.get("abbreviation", "")
        if abbrev in result:
            result[abbrev][standings_type] = team_records
    
    return result


@router.get("")
async def get_standings(
    mlb_client: Annotated[MLBStatsClient, Depends(get_mlb_client)],
    year: Annotated[Optional[int], Query(ge=1900, le=2100)] = None,
    view: Annotated[StandingsView, Query()] = StandingsView.DIVISION,
) -> dict:
    """
    Get MLB standings for a season.
    
    Args:
        year: Season year (defaults to current year)
        view: Standings view preset:
            - division: Divisional standings (default)
            - mlb: Full league ranking  
            - preseason: Spring training
            - wildcard: Wild card race
    
    Returns different data structures depending on view:
        - division: { "American League": { "1": { division data... } } }
        - mlb/preseason: { teamRecords: [...] }
        - wildcard: { "AL": { divisionLeaders: [...], wildCard: [...] }, "NL": {...} }
    """
    if year is None:
        year = date.today().year
    
    try:
        response = await mlb_client.get_standings(year, view)
        
        # Process based on view type
        if view == StandingsView.DIVISION:
            standings_data = process_division_standings(response)
        elif view in (StandingsView.MLB, StandingsView.PRESEASON):
            standings_data = process_simple_standings(response)
        elif view == StandingsView.WILDCARD:
            standings_data = process_wildcard_standings(response)
        else:
            standings_data = {}
        
        return {
            "standingsData": standings_data,
            "year": year,
            "lastUpdated": response.get("lastUpdated", ""),
        }
    except Exception:
        return {}
