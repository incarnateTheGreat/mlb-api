"""
Pydantic models for AI-generated analysis responses.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

from app.constants import DEFAULT_ANTHROPIC_MODEL


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
    model: str = DEFAULT_ANTHROPIC_MODEL
    tokens_used: int
    generation_time_ms: int
    cached: bool
    cache_key: Optional[str] = None


# ============================================================================
# Copilot Contract Models (v1) — Public Data Grounded AI
# ============================================================================


class Citation(BaseModel):
    """
    Source citation for grounded claims in a copilot response.
    
    Maps claims back to evidence: either retrieved documents or tool outputs.
    """
    source_id: str = Field(
        ..., 
        description="Unique ID for the source (URL, tool_name:result_id, etc.)"
    )
    source_type: str = Field(
        ..., 
        description="'document' (RAG), 'tool' (deterministic), or 'knowledge'"
    )
    title: str = Field(..., description="Source title or tool name")
    url: Optional[str] = Field(None, description="URL if document source")
    snippet: str = Field(
        ..., 
        description="Relevant excerpt supporting the claim (50-200 chars)"
    )
    section: Optional[str] = Field(None, description="Section/heading within source")


class ToolCall(BaseModel):
    """
    Record of a tool invocation in the orchestration flow.
    
    Tracks what was called, with what inputs, and the result state.
    """
    tool_name: str = Field(..., description="e.g., 'get_game_summary', 'get_player_stats'")
    input_summary: str = Field(
        ..., 
        description="Human-readable summary of tool inputs (e.g., 'playerId=545361, season=2024')"
    )
    success: bool = Field(..., description="Whether the tool call succeeded")
    error_message: Optional[str] = Field(None, description="Error details if failed")
    latency_ms: int = Field(..., description="Wall-clock execution time")
    cached: bool = Field(default=False, description="Whether result came from cache")


class ModelInfo(BaseModel):
    """Information about the LLM used for generation."""
    provider: str = Field(default="anthropic", description="e.g., 'anthropic', 'openai'")
    model_name: str = Field(default=DEFAULT_ANTHROPIC_MODEL)
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)


class TimingInfo(BaseModel):
    """Latency breakdown by orchestration stage."""
    total_ms: int = Field(..., description="Total end-to-end latency")
    orchestration_ms: int = Field(
        default=0, 
        description="Time to decide routing (tool vs. RAG vs. hybrid)"
    )
    tools_ms: int = Field(default=0, description="Total tool execution time")
    retrieval_ms: int = Field(default=0, description="RAG retrieval time")
    model_ms: int = Field(default=0, description="LLM inference time")
    synthesis_ms: int = Field(
        default=0, 
        description="Time to format final response"
    )


class CopilotRequest(BaseModel):
    """
    Request contract for the /copilot/query endpoint.
    
    Supports all three query classes: tool-only, rag-only, hybrid.
    """
    query: str = Field(..., description="User question or statement")
    context: Optional[dict] = Field(
        None,
        description="Optional context: game_id, team_id, player_id, season, etc."
    )
    mode: str = Field(
        default="auto",
        description="'auto' (infer), 'tool', 'rag', or 'hybrid'"
    )
    session_id: Optional[str] = Field(
        None,
        description="Optional conversation session ID for multi-turn context"
    )
    max_tokens: int = Field(
        default=1024,
        ge=100,
        le=4096,
        description="Output token budget"
    )
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Sampling temperature"
    )


class CopilotResponse(BaseModel):
    """
    Response contract from the /copilot/query endpoint.
    
    Includes answer, evidence sources, tool invocations, and observability data.
    """
    request_id: str = Field(
        ..., 
        description="Unique ID for trace correlation"
    )
    answer: str = Field(
        ..., 
        description="Final natural-language response to user query"
    )
    citations: list[Citation] = Field(
        default_factory=list,
        description="Sources and evidence backing claims"
    )
    tools_used: list[ToolCall] = Field(
        default_factory=list,
        description="All tool invocations during orchestration"
    )
    model_info: ModelInfo = Field(
        ..., 
        description="LLM metadata (tokens, model, provider)"
    )
    timing: TimingInfo = Field(
        ..., 
        description="Latency breakdown by stage"
    )
    confidence: str = Field(
        default="medium",
        description="'high', 'medium', 'low' — sufficiency of evidence"
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Fallback notes, uncertainty flags, partial-data signals"
    )
    query_mode: str = Field(
        ..., 
        description="Route taken: 'tool', 'rag', or 'hybrid'"
    )
    generated_at: datetime = Field(default_factory=datetime.utcnow)
