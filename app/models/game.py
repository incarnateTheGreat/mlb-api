"""
Pydantic models for game data.

Pydantic models are like TypeScript interfaces + Zod schemas combined.
They define the shape of data AND validate it at runtime.

Key differences from TypeScript/Zod:
- `Field()` is like `z.string().min(1).describe("...")` — adds metadata and validation
- `Optional[str]` is like `z.string().optional()` — allows None/null
- Models are classes, not objects, so you instantiate them: `Game(id=123, ...)`
- Use `.model_dump()` to convert to dict (like spreading in JS)
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class TeamInfo(BaseModel):
    """Basic team information."""
    id: int
    name: str
    abbreviation: str = Field(..., min_length=2, max_length=3)
    
    
class GameScore(BaseModel):
    """Score information for a team in a game."""
    team: TeamInfo
    runs: int = Field(..., ge=0)  # ge = greater than or equal (like z.number().min(0))
    hits: int = Field(..., ge=0)
    errors: int = Field(..., ge=0)
    

class GameStatus(BaseModel):
    """Game status information."""
    abstract_state: str  # "Live", "Final", "Preview"
    detailed_state: str  # "In Progress", "Final", "Scheduled"
    status_code: str     # "I", "F", "S"
    

class Pitcher(BaseModel):
    """Pitcher summary for game lines."""
    id: int
    name: str
    innings_pitched: float
    hits: int
    runs: int
    earned_runs: int
    walks: int
    strikeouts: int
    home_runs: int
    era: Optional[float] = None


class TopPerformer(BaseModel):
    """A standout player performance."""
    player_id: int
    player_name: str
    position: str
    stat_line: str  # e.g., "3-4, 2 HR, 5 RBI"
    

class GameBoxscore(BaseModel):
    """
    Full boxscore data from the MLB Stats API.
    This is the raw data we'll use to generate AI summaries.
    """
    game_id: int
    game_date: datetime
    status: GameStatus
    home: GameScore
    away: GameScore
    winning_pitcher: Optional[Pitcher] = None
    losing_pitcher: Optional[Pitcher] = None
    save_pitcher: Optional[Pitcher] = None
    top_performers: list[TopPerformer] = Field(default_factory=list)
    inning_scores: dict[str, list[int]] = Field(default_factory=dict)


class GameSummary(BaseModel):
    """
    Enriched game summary with AI-generated content.
    This is what we return to the Remix frontend.
    """
    game_id: int
    game_date: datetime
    status: GameStatus
    home: GameScore
    away: GameScore
    
    # AI-generated content
    headline: str = Field(..., description="Short, punchy headline for the game")
    summary: str = Field(..., description="2-3 paragraph game recap")
    key_moments: list[str] = Field(
        default_factory=list,
        description="List of pivotal moments in the game"
    )
    player_of_the_game: Optional[TopPerformer] = None
    
    # Metadata
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    cached: bool = False


class GameSummaryRequest(BaseModel):
    """Request parameters for game summary generation."""
    include_key_moments: bool = True
    include_player_analysis: bool = True
    regenerate: bool = False  # Force regeneration even if cached
