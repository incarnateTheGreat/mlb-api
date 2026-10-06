"""
Eval dataset: test cases for Week 4 evaluation with simulated expected results.

Each case defines:
- Query and optional context
- Expected orchestration mode
- Expected citations (source types to validate)
- Answer quality criteria
- Scoring thresholds
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class EvalCase:
    """A single evaluation test case."""
    
    case_id: str
    """Unique identifier for the test case."""
    
    query: str
    """User query to test."""
    
    mode: str
    """Expected mode: 'tool', 'rag', or 'hybrid'."""
    
    context: Optional[dict] = None
    """Optional context for tool selection (game_id, player_id, team_id, season, etc.)."""
    
    expected_citations_types: list[str] = None
    """Expected citation source types. E.g., ['tool'] or ['document'] or ['tool', 'document']."""
    
    expected_answer_contains: list[str] = None
    """Keywords/phrases the answer should contain (case-insensitive)."""
    
    min_confidence: str = "low"
    """Minimum acceptable confidence: 'low', 'medium', 'high'."""
    
    max_latency_ms: int = 10000
    """Maximum acceptable latency for the overall query."""
    
    should_warn: bool = False
    """Whether warnings are expected (e.g., no tool match, weak retrieval)."""
    
    description: str = ""
    """Human-readable test description."""

    def __post_init__(self):
        if self.expected_citations_types is None:
            self.expected_citations_types = []
        if self.expected_answer_contains is None:
            self.expected_answer_contains = []


# Tool-only test cases
TOOL_CASES = [
    EvalCase(
        case_id="tool_001",
        query="What are the stats for player 545361 in 2024?",
        mode="tool",
        context={"player_id": 545361, "season": 2024},
        expected_citations_types=["tool"],
        expected_answer_contains=["545361", "2024"],
        min_confidence="high",
        max_latency_ms=2000,
        should_warn=True,
        description="Tool: Player stats with full context",
    ),
    EvalCase(
        case_id="tool_002",
        query="What's the status of game 777428?",
        mode="tool",
        context={"game_pk": 777428},
        expected_citations_types=["tool"],
        expected_answer_contains=["Final"],
        min_confidence="high",
        max_latency_ms=2000,
        should_warn=True,
        description="Tool: Game summary",
    ),
    EvalCase(
        case_id="tool_003",
        query="Player 545361 hitting stats in 2024",
        mode="tool",
        context={"player_id": 545361, "season": 2024, "split_type": "hitting"},
        expected_citations_types=["tool"],
        expected_answer_contains=["hitting"],
        min_confidence="high",
        should_warn=True,
        description="Tool: Explicit split type",
    ),
    EvalCase(
        case_id="tool_004",
        query="Tell me something without context",
        mode="tool",
        context=None,
        expected_citations_types=[],
        expected_answer_contains=["could not"],
        min_confidence="low",
        should_warn=True,
        description="Tool: No context should warn",
    ),
    EvalCase(
        case_id="tool_005",
        query="Get player stats",
        mode="tool",
        context={"player_id": 545361, "season": 2024},
        expected_citations_types=["tool"],
        expected_answer_contains=["stats"],
        min_confidence="high",
        should_warn=True,
        description="Tool: Generic stats request",
    ),
]

# RAG-only test cases
RAG_CASES = [
    EvalCase(
        case_id="rag_001",
        query="What is the infield fly rule?",
        mode="rag",
        expected_citations_types=["document"],
        expected_answer_contains=["infield fly"],
        min_confidence="high",
        max_latency_ms=3000,
        should_warn=True,
        description="RAG: Infield fly rule",
    ),
    EvalCase(
        case_id="rag_002",
        query="Explain the designated hitter rule",
        mode="rag",
        expected_citations_types=["document"],
        expected_answer_contains=["designated hitter"],
        min_confidence="high",
        max_latency_ms=3000,
        should_warn=True,
        description="RAG: Designated hitter rule",
    ),
    EvalCase(
        case_id="rag_003",
        query="Tell me about World Series history",
        mode="rag",
        expected_citations_types=["document"],
        expected_answer_contains=["World Series"],
        min_confidence="high",
        max_latency_ms=3000,
        should_warn=True,
        description="RAG: World Series history",
    ),
    EvalCase(
        case_id="rag_004",
        query="When does the infield fly rule apply?",
        mode="rag",
        expected_citations_types=["document"],
        expected_answer_contains=["infield fly"],
        min_confidence="high",
        max_latency_ms=3000,
        should_warn=True,
        description="RAG: Infield fly conditions",
    ),
    EvalCase(
        case_id="rag_005",
        query="What happens if a DH moves to defense?",
        mode="rag",
        expected_citations_types=["document"],
        expected_answer_contains=["designated"],
        min_confidence="medium",
        max_latency_ms=3000,
        should_warn=True,
        description="RAG: DH rule edge case",
    ),
]

# Hybrid test cases
HYBRID_CASES = [
    EvalCase(
        case_id="hybrid_001",
        query="Summarize game 777428 and add context",
        mode="hybrid",
        context={"game_pk": 777428},
        expected_citations_types=["tool"],
        expected_answer_contains=["game"],
        min_confidence="medium",
        max_latency_ms=5000,
        should_warn=True,
        description="Hybrid: Game + synthesis",
    ),
    EvalCase(
        case_id="hybrid_002",
        query="Mike Trout stats for 2024 and historical context",
        mode="hybrid",
        context={"player_id": 545361, "season": 2024},
        expected_citations_types=["tool", "document"],
        expected_answer_contains=["545361"],
        min_confidence="high",
        max_latency_ms=5000,
        should_warn=True,
        description="Hybrid: Player stats + history",
    ),
    EvalCase(
        case_id="hybrid_003",
        query="Infield fly rule and recent games",
        mode="hybrid",
        expected_citations_types=["document"],
        expected_answer_contains=["infield fly"],
        min_confidence="medium",
        max_latency_ms=5000,
        should_warn=True,
        description="Hybrid: Rule + data (no tool match)",
    ),
    EvalCase(
        case_id="hybrid_004",
        query="Designated hitter and current game",
        mode="hybrid",
        expected_citations_types=["document"],
        expected_answer_contains=["designated"],
        min_confidence="medium",
        max_latency_ms=5000,
        should_warn=True,
        description="Hybrid: Rule + live query",
    ),
    EvalCase(
        case_id="hybrid_005",
        query="Game 777428 with baseball context",
        mode="hybrid",
        context={"game_pk": 777428},
        expected_citations_types=["tool"],
        expected_answer_contains=["game"],
        min_confidence="medium",
        max_latency_ms=5000,
        should_warn=True,
        description="Hybrid: Game + rule context",
    ),
]

ALL_CASES = TOOL_CASES + RAG_CASES + HYBRID_CASES


def get_eval_cases(mode: Optional[str] = None) -> list[EvalCase]:
    """Return all eval cases, optionally filtered by mode."""
    if mode is None:
        return ALL_CASES
    return [case for case in ALL_CASES if case.mode == mode]
