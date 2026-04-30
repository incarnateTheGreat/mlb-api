"""
Pydantic models for player data and statistics.
"""

from datetime import date
from typing import Optional
from pydantic import BaseModel, Field


class PlayerBio(BaseModel):
    """Basic player biographical information."""
    id: int
    full_name: str
    first_name: str
    last_name: str
    primary_number: Optional[str] = None
    birth_date: Optional[date] = None
    birth_city: Optional[str] = None
    birth_country: Optional[str] = None
    height: Optional[str] = None
    weight: Optional[int] = None
    primary_position: str
    bat_side: str  # "R", "L", "S"
    pitch_hand: str  # "R", "L"
    current_team_id: Optional[int] = None
    current_team_name: Optional[str] = None


class BattingStats(BaseModel):
    """Standard batting statistics."""
    games: int = 0
    at_bats: int = 0
    runs: int = 0
    hits: int = 0
    doubles: int = 0
    triples: int = 0
    home_runs: int = 0
    rbi: int = 0
    stolen_bases: int = 0
    caught_stealing: int = 0
    walks: int = 0
    strikeouts: int = 0
    batting_average: float = 0.0
    on_base_percentage: float = 0.0
    slugging_percentage: float = 0.0
    ops: float = 0.0
    

class AdvancedBattingStats(BattingStats):
    """
    Extended batting stats with sabermetric calculations.
    These are computed by our sabermetrics service, not from the API directly.
    """
    # Sabermetric stats
    woba: Optional[float] = Field(None, description="Weighted On-Base Average")
    wrc_plus: Optional[int] = Field(None, description="Weighted Runs Created Plus (100 is league average)")
    ops_plus: Optional[int] = Field(None, description="OPS+ adjusted for park and league (100 is average)")
    iso: Optional[float] = Field(None, description="Isolated Power (SLG - AVG)")
    babip: Optional[float] = Field(None, description="Batting Average on Balls In Play")
    
    # Plate discipline
    bb_rate: Optional[float] = Field(None, description="Walk rate (BB/PA)")
    k_rate: Optional[float] = Field(None, description="Strikeout rate (K/PA)")
    

class PitchingStats(BaseModel):
    """Standard pitching statistics."""
    games: int = 0
    games_started: int = 0
    wins: int = 0
    losses: int = 0
    saves: int = 0
    holds: int = 0
    innings_pitched: float = 0.0
    hits: int = 0
    runs: int = 0
    earned_runs: int = 0
    walks: int = 0
    strikeouts: int = 0
    home_runs: int = 0
    era: float = 0.0
    whip: float = 0.0
    

class AdvancedPitchingStats(PitchingStats):
    """Extended pitching stats with sabermetric calculations."""
    # Sabermetric stats
    fip: Optional[float] = Field(None, description="Fielding Independent Pitching")
    xfip: Optional[float] = Field(None, description="Expected FIP (normalizes HR/FB rate)")
    era_plus: Optional[int] = Field(None, description="ERA+ adjusted for park/league (100 is average)")
    k_9: Optional[float] = Field(None, description="Strikeouts per 9 innings")
    bb_9: Optional[float] = Field(None, description="Walks per 9 innings")
    hr_9: Optional[float] = Field(None, description="Home runs per 9 innings")
    k_bb_ratio: Optional[float] = Field(None, description="Strikeout to walk ratio")


class PlatoonSplits(BaseModel):
    """Stats broken down by opponent handedness."""
    vs_right: BattingStats
    vs_left: BattingStats


class RollingForm(BaseModel):
    """Recent performance over a rolling window."""
    window_games: int = Field(..., description="Number of games in window (e.g., 7, 15, 30)")
    stats: BattingStats
    trend: str = Field(..., description="'hot', 'cold', or 'neutral'")
    trend_description: str = Field(..., description="Human-readable trend analysis")


class PlayerProfile(BaseModel):
    """Complete player profile with stats and analysis."""
    bio: PlayerBio
    season_batting: Optional[AdvancedBattingStats] = None
    season_pitching: Optional[AdvancedPitchingStats] = None
    platoon_splits: Optional[PlatoonSplits] = None
    rolling_form: Optional[RollingForm] = None
    
    # AI-generated content
    scouting_report: Optional[str] = Field(
        None, 
        description="AI-generated scouting report highlighting strengths and weaknesses"
    )
