"""Trace models for copilot query execution visibility."""

from typing import Any
from pydantic import BaseModel, Field


class TraceStep(BaseModel):
    """Single step in query execution trace."""
    step_number: int = Field(..., description="Sequential step number (1-8)")
    stage_name: str = Field(..., description="Stage name (query analysis, mode selection, etc.)")
    description: str = Field(..., description="What happened in this stage")
    details: dict[str, Any] = Field(default_factory=dict, description="Detailed data from this stage")
    timing_ms: int = Field(default=0, description="Time spent in this stage")


class CopilotTrace(BaseModel):
    """Complete execution trace for a copilot query."""
    request_id: str = Field(..., description="Request ID for correlation")
    query: str = Field(..., description="Original query")
    mode: str = Field(..., description="Inferred or requested query mode")
    steps: list[TraceStep] = Field(..., description="Ordered trace steps (1-8)")
    total_ms: int = Field(..., description="Total execution time")
    final_response: dict[str, Any] = Field(..., description="Final answer + metadata")
