"""
Standings router — endpoints for MLB standings data.
"""

from typing import Annotated, Any, Optional
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.services.mlb_client import get_mlb_client, MLBStatsClient, StandingsView
from app.services.sabermetrics import calculate_pythagorean_record


router = APIRouter()


def _as_int(value: Any) -> Optional[int]:
    """Standings mixes ints and numeric strings depending on the field."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def enrich_with_pythagorean(response: dict) -> None:
    """
    Attach an expected record to every club in the payload.

    Done on the raw records rather than per view: all four views reshape
    the same team-record dicts, so enriching here once reaches every one
    of them without the processors having to know about it.

    A record missing either run total leaves the field null rather than
    raising. Nothing in the live feed does that today — spring training
    carries run totals too — but this runs over a payload we do not own.

    Mutates the response in place. The upstream payload is cached, so this
    can run against the same dict repeatedly — it is idempotent, and a
    deep copy of a league-wide standings response to avoid that would cost
    more than the calculation does.
    """
    for record in response.get("records", []):
        for team_record in record.get("teamRecords", []):
            fields = [
                _as_int(team_record.get(key))
                for key in ("runsScored", "runsAllowed", "wins", "losses")
            ]
            team_record["pythagorean"] = (
                calculate_pythagorean_record(*fields)
                if all(field is not None for field in fields)
                else None
            )


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

        enrich_with_pythagorean(response)

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
