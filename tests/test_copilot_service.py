"""Unit tests for copilot orchestration including Week 3 mock LLM mode."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.models.analysis import CopilotRequest
from app.services.copilot_service import CopilotService
from app.services.rag_service import RetrievalResult
from app.services.tools import ToolType


class DummyToolRegistry:
    """Simple async stub for deterministic tool execution tests."""

    def __init__(self, result: dict):
        self.result = result
        self.last_tool_name = None
        self.last_kwargs = None

    async def execute_tool_cached(self, tool_name: str, **kwargs) -> dict:
        self.last_tool_name = tool_name
        self.last_kwargs = kwargs
        return self.result


class DummyRAGService:
    """Simple retrieval stub for deterministic RAG tests."""

    def __init__(self, results: list[RetrievalResult]):
        self._results = results

    def retrieve(self, query: str, top_k=None) -> list[RetrievalResult]:
        return self._results

    def retrieval_stats(self, results: list[RetrievalResult]) -> dict[str, int]:
        return {
            "candidates": 3,
            "selected": len(results),
            "source_diversity": len({item.source_id for item in results}),
        }


@pytest.mark.asyncio
async def test_tool_mode_executes_player_stats_tool() -> None:
    """Tool mode should execute selected tool and return grounded metadata."""
    with patch("app.services.copilot_service.anthropic.Anthropic", return_value=MagicMock()):
        service = CopilotService()

    tool_result = {
        "success": True,
        "latency_ms": 55,
        "cached": True,
        "data": {
            "player_id": 545361,
            "season": 2024,
            "stats_group": "hitting",
            "stats": {"homeRuns": 27, "avg": ".285", "rbi": 78},
        },
    }
    service.tool_registry = DummyToolRegistry(tool_result)

    request = CopilotRequest(
        query="What are Mike Trout's 2024 stats?",
        mode="tool",
        context={"player_id": 545361, "season": 2024, "split_type": "hitting"},
    )

    response = await service.query(request)

    assert response.query_mode == "tool"
    assert response.confidence == "high"
    assert len(response.tools_used) == 1
    assert response.tools_used[0].tool_name == ToolType.GET_PLAYER_STAT_SPLIT.value
    assert response.tools_used[0].cached is True
    assert len(response.citations) == 1
    assert response.citations[0].source_type == "tool"
    assert "HR=27" in response.answer


@pytest.mark.asyncio
async def test_tool_mode_without_context_returns_warning() -> None:
    """Tool mode should surface guidance when no tool can be selected."""
    with patch("app.services.copilot_service.anthropic.Anthropic", return_value=MagicMock()):
        service = CopilotService()

    request = CopilotRequest(query="Tell me something", mode="tool")
    response = await service.query(request)

    assert response.query_mode == "tool"
    assert response.confidence == "low"
    assert len(response.tools_used) == 0
    assert any("No matching deterministic tool" in warning for warning in response.warnings)


@pytest.mark.asyncio
async def test_rag_mode_with_mock_llm_returns_tokens() -> None:
    """RAG mode should emit synthetic model tokens when mock LLM mode is enabled."""
    with patch("app.services.copilot_service.anthropic.Anthropic", return_value=MagicMock()):
        service = CopilotService()
    service.mock_llm_enabled = True
    service.mock_llm_latency_ms = 0
    service.rag_service = DummyRAGService(
        [
            RetrievalResult(
                chunk_id="doc:rules_infield_fly:0",
                source_id="doc:rules_infield_fly",
                title="Infield Fly Rule",
                url="https://www.mlb.com/glossary/rules/infield-fly-rule",
                section="Infield Fly Rule",
                snippet="The infield fly rule applies with runners on first and second and fewer than two outs.",
                score=0.72,
            )
        ]
    )

    request = CopilotRequest(query="Explain the infield fly rule", mode="rag")
    response = await service.query(request)

    assert response.query_mode == "rag"
    assert response.confidence == "high"
    assert response.model_info.total_tokens > 0
    assert "mock" in response.model_info.model_name.lower()
    assert len(response.citations) == 1
    assert response.citations[0].source_type == "document"
    assert not any("synthesis failed" in warning.lower() for warning in response.warnings)


@pytest.mark.asyncio
async def test_hybrid_mode_with_mock_llm_combines_tool_and_docs() -> None:
    """Hybrid mode should include tool and doc citations plus synthetic LLM tokens in mock mode."""
    with patch("app.services.copilot_service.anthropic.Anthropic", return_value=MagicMock()):
        service = CopilotService()
    service.mock_llm_enabled = True
    service.mock_llm_latency_ms = 0
    service.tool_registry = DummyToolRegistry(
        {
            "success": True,
            "latency_ms": 40,
            "cached": False,
            "data": {
                "game_id": 777428,
                "status": {"detailed_state": "Final"},
                "home_team": {"name": "Los Angeles Angels", "runs": 2},
                "away_team": {"name": "Houston Astros", "runs": 3},
            },
        }
    )
    service.rag_service = DummyRAGService(
        [
            RetrievalResult(
                chunk_id="doc:history_world_series:0",
                source_id="doc:history_world_series",
                title="World Series Historical Context",
                url="https://www.mlb.com/history",
                section="World Series Historical Context",
                snippet="The World Series is the championship series of MLB.",
                score=0.41,
            )
        ]
    )

    request = CopilotRequest(
        query="Summarize this game and add context",
        mode="hybrid",
        context={"game_pk": 777428},
    )
    response = await service.query(request)

    assert response.query_mode == "hybrid"
    assert response.model_info.total_tokens > 0
    assert len(response.tools_used) == 1
    source_types = {citation.source_type for citation in response.citations}
    assert "tool" in source_types
    assert "document" in source_types
    assert "Mock hybrid answer" in response.answer


@pytest.mark.asyncio
async def test_mock_llm_warning_is_exposed() -> None:
    """Mock mode should advertise that synthesis content is simulated."""
    with patch("app.services.copilot_service.anthropic.Anthropic", return_value=MagicMock()):
        service = CopilotService()
    service.mock_llm_enabled = True
    service.mock_llm_latency_ms = 0
    service.rag_service = DummyRAGService([])

    request = CopilotRequest(query="Explain this rule", mode="rag")
    response = await service.query(request)

    assert any("Mock LLM mode enabled" in warning for warning in response.warnings)


@pytest.mark.asyncio
async def test_rag_retries_then_recovers() -> None:
    """RAG synthesis should retry transient model failures and recover when a later attempt succeeds."""
    with patch("app.services.copilot_service.anthropic.Anthropic", return_value=MagicMock()):
        service = CopilotService()
    service.mock_llm_enabled = False
    service.model_retries = 1
    service.model_retry_backoff_ms = 0
    service.rag_service = DummyRAGService(
        [
            RetrievalResult(
                chunk_id="doc:rules_infield_fly:0",
                source_id="doc:rules_infield_fly",
                title="Infield Fly Rule",
                url="https://www.mlb.com/glossary/rules/infield-fly-rule",
                section="Infield Fly Rule",
                snippet="The infield fly rule applies with runners on first and second.",
                score=0.72,
            )
        ]
    )

    attempts = {"count": 0}

    def flaky_create(**kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("transient")
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=12, output_tokens=6),
            content=[SimpleNamespace(text="Recovered answer")],
        )

    service.client.messages.create = flaky_create

    request = CopilotRequest(query="Explain the infield fly rule", mode="rag")
    response = await service.query(request)

    assert attempts["count"] == 2
    assert response.answer == "Recovered answer"
    assert response.model_info.total_tokens == 18
