"""
Pydantic models for standings data.
"""

from typing import Any, Optional
from pydantic import BaseModel


class DivisionInfo(BaseModel):
    """Division metadata."""
    id: int
    name: str
    name_short: str
    sort_order: int


class TeamRecord(BaseModel):
    """Team standings record."""
    id: str
    name: str
    abbreviation: str
    short_name: str
    wins: int
    losses: int
    pct: str
    division_rank: int
    division_games_back: str
    wild_card_rank: Optional[int] = None
    wild_card_games_back: str
    streak: str
    runs_scored: int
    runs_allowed: int
    run_differential: int
    magic_number: int
    elimination_number: str
    clinch_indicator: Optional[str] = None
    clinched: bool
    division_leader: bool
    division_champ: bool
    wild_card_leader: bool
    record_last_ten: Optional[str] = None
    record_home: Optional[str] = None
    record_away: Optional[str] = None


class DivisionStandings(BaseModel):
    """Standings for a single division."""
    id: int
    name: str
    name_short: str
    sort_order: int
    team_records: list[TeamRecord]


class LeagueStandings(BaseModel):
    """Standings for a single league."""
    name: str
    divisions: dict[str, DivisionStandings]


class StandingsResponse(BaseModel):
    """Full standings response."""
    standings_data: dict[str, dict[str, Any]]
    year: int
    last_updated: str
