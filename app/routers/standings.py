"""
Standings router — endpoints for MLB standings data.
"""

from typing import Annotated, Any, Optional
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.services.mlb_client import get_mlb_client, MLBStatsClient


router = APIRouter()


def process_standings_response(response: dict) -> dict[str, Any]:
    """
    Process raw standings API response into structured league/division data.
    
    Mirrors the frontend transformation logic:
    1. Build leagues and divisions structure from response.structure
    2. Add team records to each division
    """
    structure = response.get("structure", {})
    records = response.get("records", [])
    
    # Build leagues and divisions from structure
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
        
        # Find the division with matching ID
        for sort_key, division_data in divisions.items():
            if division_data.get("id") == division_id:
                division_data["division"] = record
                break
    
    return leagues_and_divisions


@router.get("")
async def get_standings(
    mlb_client: Annotated[MLBStatsClient, Depends(get_mlb_client)],
    year: Annotated[Optional[int], Query(ge=1900, le=2100)] = None,
) -> dict:
    """
    Get MLB standings for a season.
    
    Returns divisional standings organized by league with team records.
    If year is not provided, defaults to current year.
    """
    if year is None:
        year = date.today().year
    
    try:
        response = await mlb_client.get_standings(year)
        standings_data = process_standings_response(response)
        
        return {
            "standingsData": standings_data,
            "year": year,
            "lastUpdated": response.get("lastUpdated", ""),
        }
    except Exception:
        return {}
