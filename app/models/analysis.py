"""
Pydantic models for AI-generated analysis responses.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class MatchupAnalysis(BaseModel):
    """AI-generated batter vs pitcher matchup analysis."""
    batter_id: int
    batter_name: str
    pitcher_id: int
    pitcher_name: str
    
    # Historical stats if available
    career_at_bats: int = 0
    career_hits: int = 0
    career_home_runs: int = 0
    career_strikeouts: int = 0
    career_walks: int = 0
    career_avg: Optional[float] = None
    
    # AI-generated content
    advantage: str = Field(..., description="'batter', 'pitcher', or 'neutral'")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence in prediction")
    analysis: str = Field(..., description="Detailed matchup breakdown")
    key_factors: list[str] = Field(default_factory=list)
    prediction: str = Field(..., description="Expected outcome prediction")
    
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class TeamMatchupAnalysis(BaseModel):
    """AI analysis for an upcoming or ongoing series/game."""
    home_team_id: int
    home_team_name: str
    away_team_id: int
    away_team_name: str
    
    # Context
    series_info: Optional[str] = None
    recent_history: Optional[str] = None
    
    # AI-generated content
    preview: str = Field(..., description="Game/series preview narrative")
    key_matchups: list[MatchupAnalysis] = Field(default_factory=list)
    x_factors: list[str] = Field(..., description="Potential game-changing elements")
    prediction: str
    prediction_confidence: float = Field(..., ge=0.0, le=1.0)
    
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class AIGenerationRequest(BaseModel):
    """
    Base request model for AI content generation.
    Allows the frontend to control generation parameters.
    """
    max_tokens: int = Field(default=1024, ge=100, le=4096)
    temperature: float = Field(default=0.7, ge=0.0, le=1.0)
    regenerate: bool = Field(
        default=False, 
        description="Force regeneration even if cached result exists"
    )


class AIGenerationMetadata(BaseModel):
    """Metadata about an AI generation response."""
    model: str = "claude-sonnet-4-20250514"
    tokens_used: int
    generation_time_ms: int
    cached: bool
    cache_key: Optional[str] = None
