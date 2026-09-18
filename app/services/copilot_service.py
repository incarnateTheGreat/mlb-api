"""
Copilot orchestration service for public-data grounded AI queries.

This service implements the core orchestration logic:
- Route detection (tool, rag, hybrid)
- Tool execution
- RAG retrieval (placeholder for Week 3)
- Model synthesis
- Citation tracking
- Observability telemetry
"""

import time
import uuid
from pathlib import Path
from typing import Any, Optional

import anthropic

from app.config import get_settings
from app.models.analysis import (
    CopilotRequest,
    CopilotResponse,
    Citation,
    ToolCall,
    ModelInfo,
    TimingInfo,
)
from app.services.rag_service import get_rag_service, RetrievalResult
from app.services.tools import get_tool_registry, ToolType


class CopilotService:
    """
    Main orchestration service for copilot query resolution.
    
    Handles request routing, tool/RAG dispatch, and response formatting.
    """
    
    def __init__(self) -> None:
        settings = get_settings()
        # Let Anthropic manage HTTP client internally
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model = settings.copilot_model
        self.fallback_model = settings.copilot_fallback_model
        self.max_latency_ms = settings.copilot_max_total_latency_ms
        self.model_timeout_ms = settings.copilot_model_timeout_ms
        self.model_retries = settings.copilot_model_retries
        self.model_retry_backoff_ms = settings.copilot_model_retry_backoff_ms
        self.mock_llm_enabled = settings.copilot_mock_llm_enabled
        self.mock_llm_latency_ms = settings.copilot_mock_llm_latency_ms
        self.settings = settings
        self.tool_registry = get_tool_registry()
        self.rag_service = get_rag_service()
    
    async def query(self, request: CopilotRequest) -> CopilotResponse:
        """
        Process a copilot query end-to-end.
        
        1. Generates a request ID for tracing
        2. Determines orchestration mode (tool/rag/hybrid)
        3. Executes appropriate subsystems
        4. Synthesizes response with citations and metadata
        5. Returns structured CopilotResponse
        
        Args:
            request: Validated CopilotRequest with query, context, mode, etc.
        
        Returns:
            CopilotResponse with answer, citations, tools, metadata, timing.
        """
        request_id = str(uuid.uuid4())
        start_time = time.time()
        orchestration_start = time.time()

        query_mode = request.mode
        if query_mode == "auto":
            query_mode = self._infer_mode(request.query)

        orchestration_ms = int((time.time() - orchestration_start) * 1000)
        citations: list[Citation] = []
        warnings: list[str] = []
        if self.mock_llm_enabled:
            warnings.append("Mock LLM mode enabled; synthesis output is simulated")

        tools_ms = 0
        retrieval_ms = 0
        model_ms = 0
        answer = ""
        confidence = "low"
        tools_used: list[ToolCall] = []
        model_info = ModelInfo(
            provider="anthropic",
            model_name=self.model,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )

        if query_mode in {"tool", "hybrid"}:
            (
                answer,
                confidence,
                tools_used,
                tool_citations,
                tools_ms,
                tool_warnings,
            ) = await self._run_tool_phase(request)
            citations.extend(tool_citations)
            warnings.extend(tool_warnings)

        if query_mode in {"rag", "hybrid"}:
            max_tokens = max(100, min(request.max_tokens, self.settings.copilot_max_tokens))
            (
                answer,
                confidence,
                rag_citations,
                retrieval_ms,
                model_ms,
                model_info,
                rag_warnings,
            ) = self._run_rag_phase(
                request=request,
                query_mode=query_mode,
                tools_used=tools_used,
                tool_answer=answer,
                max_tokens=max_tokens,
                temperature=request.temperature,
            )
            citations.extend(rag_citations)
            warnings.extend(rag_warnings)
        
        elapsed_ms = int((time.time() - start_time) * 1000)
        
        # Build response
        response = CopilotResponse(
            request_id=request_id,
            answer=answer,
            citations=citations,
            tools_used=tools_used,
            model_info=model_info,
            timing=TimingInfo(
                total_ms=elapsed_ms,
                orchestration_ms=orchestration_ms,
                tools_ms=tools_ms,
                retrieval_ms=retrieval_ms,
                model_ms=model_ms,
                synthesis_ms=0,
            ),
            confidence=confidence,
            warnings=warnings,
            query_mode=query_mode,
        )
        
        return response

    async def _run_tool_phase(
        self,
        request: CopilotRequest,
    ) -> tuple[str, str, list[ToolCall], list[Citation], int, list[str]]:
        """Execute tool selection and invocation for tool/hybrid modes."""
        warnings: list[str] = []
        tools_used: list[ToolCall] = []
        citations: list[Citation] = []

        selected_tool = self._select_tool(request)
        if selected_tool is None:
            warnings.append("No matching deterministic tool found for query/context")
            return (
                "I could not determine which MLB data tool to run. Provide game_id, team_id + date range, or player_id + season in context.",
                "low",
                tools_used,
                citations,
                0,
                warnings,
            )

        tool_name, tool_inputs = selected_tool
        tool_start = time.time()
        tool_result = await self.tool_registry.execute_tool_cached(tool_name, **tool_inputs)
        tools_ms = int((time.time() - tool_start) * 1000)

        tools_used.append(
            ToolCall(
                tool_name=tool_name,
                input_summary=", ".join([f"{k}={v}" for k, v in tool_inputs.items()]),
                success=bool(tool_result.get("success")),
                error_message=tool_result.get("error"),
                latency_ms=int(tool_result.get("latency_ms", tools_ms)),
                cached=bool(tool_result.get("cached", False)),
            )
        )

        if not tool_result.get("success"):
            warnings.append(f"Tool execution failed: {tool_result.get('error', 'unknown error')}")
            return (
                "I could not complete the MLB data lookup due to a tool failure.",
                "low",
                tools_used,
                citations,
                tools_ms,
                warnings,
            )

        citations.append(
            Citation(
                source_id=f"tool:{tool_name}",
                source_type="tool",
                title=f"MLB StatsAPI via {tool_name}",
                url="https://statsapi.mlb.com/",
                snippet=f"Tool {tool_name} executed with {tool_inputs}",
            )
        )
        answer = self._format_tool_answer(tool_name, tool_result.get("data", {}))
        return (answer, "high", tools_used, citations, tools_ms, warnings)

    def _run_rag_phase(
        self,
        request: CopilotRequest,
        query_mode: str,
        tools_used: list[ToolCall],
        tool_answer: str,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, str, list[Citation], int, int, ModelInfo, list[str]]:
        """Execute retrieval and model synthesis for rag/hybrid modes."""
        warnings: list[str] = []

        retrieval_start = time.time()
        retrieval_results = self.rag_service.retrieve(request.query, top_k=self.settings.rag_top_k)
        retrieval_ms = int((time.time() - retrieval_start) * 1000)

        rag_citations = [
            Citation(
                source_id=item.source_id,
                source_type="document",
                title=item.title,
                url=item.url,
                snippet=item.snippet,
                section=item.section,
            )
            for item in retrieval_results
        ]

        stats = self.rag_service.retrieval_stats(retrieval_results)
        if stats["selected"] == 0:
            warnings.append("No relevant RAG chunks retrieved; confidence reduced")

        model_start = time.time()
        used_tool_fallback = False
        if query_mode == "rag":
            llm_answer, model_info, llm_warning = self._generate_rag_answer(
                request.query,
                retrieval_results,
                max_tokens,
                temperature,
            )
            confidence = "high" if retrieval_results else "low"
        else:
            llm_answer, model_info, llm_warning = self._generate_hybrid_answer(
                request.query,
                retrieval_results,
                tools_used,
                tool_answer,
                max_tokens,
                temperature,
            )
            confidence = "high" if retrieval_results and tools_used else "medium"
            if not retrieval_results:
                warnings.append("Hybrid response used tool data with limited document grounding")
            used_tool_fallback = bool(llm_warning and "returned tool answer" in llm_warning)

        model_ms = int((time.time() - model_start) * 1000)
        if llm_warning:
            warnings.append(llm_warning)

        if used_tool_fallback:
            # If synthesis failed and we returned only tool output, do not attach doc citations.
            rag_citations = []

        answer = llm_answer or tool_answer
        return (answer, confidence, rag_citations, retrieval_ms, model_ms, model_info, warnings)
    
    def _infer_mode(self, query: str) -> str:
        """
        Infer the best orchestration mode for a query.
        
        Heuristics (Week 2 placeholder):
        - Keywords like "live", "score", "stats" → tool
        - Keywords like "rule", "history", "explain" → rag
        - Mixed → hybrid
        
        Args:
            query: User's natural-language query
        
        Returns:
            Mode string: 'tool', 'rag', or 'hybrid'
        """
        query_lower = query.lower()
        
        tool_keywords = {"live", "score", "stats", "game", "player", "today"}
        rag_keywords = {"rule", "history", "explain", "how", "why", "definition"}
        
        has_tool = any(kw in query_lower for kw in tool_keywords)
        has_rag = any(kw in query_lower for kw in rag_keywords)
        
        if has_tool and has_rag:
            return "hybrid"
        elif has_tool:
            return "tool"
        elif has_rag:
            return "rag"
        else:
            return "tool"  # Default to tool

    def _select_tool(self, request: CopilotRequest) -> Optional[tuple[str, dict[str, Any]]]:
        """Select a deterministic tool and normalized arguments from request context/query."""
        context = request.context or {}
        query_lower = request.query.lower()
        
        # Detect if user wants play-by-play details
        wants_plays = any(keyword in query_lower for keyword in [
            "play-by-play", "play by play", "moment", "key moment", 
            "detailed", "what happened", "inning", "inning-by-inning"
        ])

        game_pk = context.get("game_pk") or context.get("game_id")
        if isinstance(game_pk, int):
            tool_inputs = {"game_pk": game_pk}
            if wants_plays:
                tool_inputs["include_play_by_play"] = True
            return (ToolType.GET_GAME_SUMMARY.value, tool_inputs)

        team_id = context.get("team_id")
        start_date = context.get("start_date")
        end_date = context.get("end_date")
        if isinstance(team_id, int) and isinstance(start_date, str) and isinstance(end_date, str):
            return (
                ToolType.GET_TEAM_SCHEDULE.value,
                {"team_id": team_id, "start_date": start_date, "end_date": end_date},
            )

        player_id = context.get("player_id")
        season = context.get("season")
        split_type = context.get("split_type", "season")
        if isinstance(player_id, int) and isinstance(season, int):
            return (
                ToolType.GET_PLAYER_STAT_SPLIT.value,
                {"player_id": player_id, "season": season, "split_type": str(split_type)},
            )

        if "schedule" in query_lower and isinstance(team_id, int):
            return (
                ToolType.GET_TEAM_SCHEDULE.value,
                {
                    "team_id": team_id,
                    "start_date": str(context.get("start_date", "2026-03-01")),
                    "end_date": str(context.get("end_date", "2026-10-01")),
                },
            )

        if "stat" in query_lower and isinstance(player_id, int) and isinstance(season, int):
            return (
                ToolType.GET_PLAYER_STAT_SPLIT.value,
                {"player_id": player_id, "season": season, "split_type": str(split_type)},
            )
        
        # Detect historical game queries like "1993 World Series" or "2024 ALDS Game 3"
        historical_event = context.get("event_type")
        historical_season = context.get("season")
        historical_game_number = context.get("game_number")
        
        if historical_event and historical_season:
            tool_inputs = {
                "event_type": historical_event,
                "season": historical_season,
            }
            if historical_game_number:
                tool_inputs["game_number"] = historical_game_number
            return (ToolType.GET_HISTORICAL_GAMES.value, tool_inputs)
        
        # Auto-detect historic event patterns in query (e.g., "1993 World Series", "2024 ALDS")
        import re
        
        # Pattern: YYYY World Series, YYYY ALCS, etc.
        event_patterns = [
            (r"(\d{4})\s+(world\s+series)", "world series"),
            (r"(\d{4})\s+(alcs)", "alcs"),
            (r"(\d{4})\s+(alds)", "alds"),
            (r"(\d{4})\s+(nlcs)", "nlcs"),
            (r"(\d{4})\s+(nlds)", "nlds"),
            (r"(\d{4})\s+(playoffs?)", "playoffs"),
        ]
        
        for pattern, event in event_patterns:
            match = re.search(pattern, query_lower)
            if match:
                season_year = int(match.group(1))
                
                # Look for game number (Game 1, Game 6, etc.)
                game_match = re.search(r"game\s+(\d)", query_lower)
                game_number = int(game_match.group(1)) if game_match else None
                
                tool_inputs = {
                    "event_type": event,
                    "season": season_year,
                }
                if game_number:
                    tool_inputs["game_number"] = game_number
                
                return (ToolType.GET_HISTORICAL_GAMES.value, tool_inputs)

        return None

    def _format_tool_answer(self, tool_name: str, data: dict[str, Any]) -> str:
        """Render a concise natural-language answer from tool output."""
        if tool_name == ToolType.GET_GAME_SUMMARY.value:
            home = data.get("home_team", {})
            away = data.get("away_team", {})
            status = data.get("status", {})
            
            answer = (
                f"{away.get('name', 'Away')} {away.get('runs', 0)} - "
                f"{home.get('name', 'Home')} {home.get('runs', 0)}. "
                f"Status: {status.get('detailed_state', 'Unknown')}."
            )
            
            # Include play-by-play summary if available
            plays_summary = data.get("plays_summary")
            if plays_summary and plays_summary.get("key_moments"):
                moments = plays_summary.get("key_moments", [])
                key_count = plays_summary.get("key_moments_count", 0)
                answer += f" Game had {plays_summary.get('total_plays', 0)} plays with {key_count} key moments."
                
                # Add brief summary of first few moments
                for moment in moments[:3]:
                    moment_type = moment.get("type", "")
                    if moment_type == "home_run":
                        answer += f" {moment.get('player')} hit a home run in inning {moment.get('inning')}."
                    elif moment_type == "scoring_play":
                        inning = moment.get("inning")
                        away_score = moment.get("away_score")
                        home_score = moment.get("home_score")
                        answer += f" Scoring play in inning {inning}: {away_score}-{home_score}."
            
            return answer

        if tool_name == ToolType.GET_TEAM_SCHEDULE.value:
            count = data.get("games_count", 0)
            start = data.get("start_date")
            end = data.get("end_date")
            return f"Found {count} games for team {data.get('team_id')} between {start} and {end}."

        if tool_name == ToolType.GET_PLAYER_STAT_SPLIT.value:
            stats = data.get("stats", {})
            hr = stats.get("homeRuns", "N/A")
            avg = stats.get("avg", "N/A")
            rbi = stats.get("rbi", "N/A")
            return (
                f"Player {data.get('player_id')} {data.get('season')} "
                f"{data.get('stats_group', 'hitting')} stats include HR={hr}, AVG={avg}, RBI={rbi}."
            )
        
        if tool_name == ToolType.GET_HISTORICAL_GAMES.value:
            event_type = data.get("event_type", "Unknown")
            season = data.get("season", "")
            games = data.get("games", [])
            games_count = data.get("games_count", 0)
            
            if games_count == 0:
                return f"No {event_type} games found for {season}."
            elif games_count == 1:
                game = games[0]
                return (
                    f"Found {season} {event_type} Game {data.get('game_number', 1)}: "
                    f"{game.get('away_team', 'Away')} @ {game.get('home_team', 'Home')}. "
                    f"Score: {game.get('away_score', 0)}-{game.get('home_score', 0)} ({game.get('status', 'Unknown')}). "
                    f"Game ID: {game.get('game_pk')}."
                )
            else:
                game_list = "; ".join([
                    f"Game {i+1}: {g.get('away_team', 'Away')} @ {g.get('home_team', 'Home')} "
                    f"({g.get('away_score', 0)}-{g.get('home_score', 0)})"
                    for i, g in enumerate(games[:5])
                ])
                return f"Found {games_count} {season} {event_type} games: {game_list}"

        return "Tool execution completed successfully."

    def _generate_rag_answer(
        self,
        query: str,
        results: list[RetrievalResult],
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, ModelInfo, Optional[str]]:
        """Generate a grounded answer from retrieved docs using Anthropic."""
        if self.mock_llm_enabled:
            return self._mock_rag_answer(query, results)

        if not results:
            return (
                "I could not find enough supporting evidence in the current public corpus.",
                ModelInfo(provider="anthropic", model_name=self.model),
                "Evidence was weak; response marked uncertain",
            )

        context_blocks = []
        for index, item in enumerate(results, start=1):
            context_blocks.append(
                f"[{index}] Title: {item.title}\nURL: {item.url}\nSection: {item.section}\nSnippet: {item.snippet}"
            )

        prompt = (
            "You are a baseball analyst. Use only the provided sources to answer the user query. "
            "If evidence is weak, say you are uncertain. Keep the answer concise.\n\n"
            f"User query: {query}\n\n"
            "Sources:\n"
            + "\n\n".join(context_blocks)
        )

        generated_text, model_info, warning = self._call_llm_with_resilience(
            prompt=prompt,
            max_tokens=min(max_tokens, 700),
            temperature=temperature,
        )
        if generated_text:
            return (generated_text, model_info, warning)

        fallback = " ".join(item.snippet for item in results[:2])
        return (
            f"I could not complete model synthesis, but retrieved evidence indicates: {fallback}",
            model_info,
            warning,
        )

    def _generate_hybrid_answer(
        self,
        query: str,
        results: list[RetrievalResult],
        tools_used: list[ToolCall],
        tool_answer: str,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, ModelInfo, Optional[str]]:
        """
        Generate a combined tool + doc answer using Anthropic.
        
        Provides explicit guidance on separating:
        - Direct factual output from tools (game scores, player stats)
        - Contextual/explanatory information from documents (rules, history)
        
        Falls back gracefully to tool answer if synthesis fails.
        """
        if self.mock_llm_enabled:
            return self._mock_hybrid_answer(query, results, tools_used, tool_answer)

        # Format document context with clear structure
        context_blocks = []
        for index, item in enumerate(results, start=1):
            context_blocks.append(
                f"[DOC {index}] Title: {item.title}\n"
                f"URL: {item.url}\n"
                f"Section: {item.section or 'N/A'}\n"
                f"Content: {item.snippet}"
            )

        # Summarize tool invocations
        tool_trace = "\n".join(
            [
                f"  {tool.tool_name}: {'✓ success' if tool.success else '✗ failed'} "
                f"(cached={tool.cached}, {tool.latency_ms}ms)"
                for tool in tools_used
            ]
        )

        # Build prompt with explicit synthesis guidance
        documents_section = "\n\n".join(context_blocks) if context_blocks else "(no documents retrieved)"
        
        prompt = (
            "You are a baseball expert assistant. Your task is to synthesize a response combining:\n"
            "1. Direct facts from live MLB data (tool output)\n"
            "2. Explanatory context from baseball knowledge (documents)\n\n"
            
            "SYNTHESIS RULES:\n"
            "- State tool facts directly: 'The score is ...' (from tool)\n"
            "- Use documents for context: 'According to MLB rules, ...' (cite [DOC N])\n"
            "- Separate facts from analysis: distinguish what happened vs why\n"
            "- If documents don't support a claim, mark it as tool-only: 'The data shows ...'\n"
            "- If synthesis fails, return the tool answer as-is\n\n"
            
            "USER QUERY:\n"
            f"{query}\n\n"
            
            "LIVE DATA (from MLB StatsAPI):\n"
            f"{tool_answer}\n"
            f"Tool executions: {tool_trace or 'none'}\n\n"
            
            "REFERENCE DOCUMENTS (for context and rules):\n"
            f"{documents_section}\n\n"
            
            "Generate your response now, clearly separating tool facts from document-backed explanations."
        )

        generated_text, model_info, warning = self._call_llm_with_resilience(
            prompt=prompt,
            max_tokens=min(max_tokens, 900),
            temperature=temperature,
        )
        
        if generated_text:
            return (generated_text, model_info, warning)

        # Graceful fallback: return tool answer if synthesis fails
        fallback_warning = (
            f"{warning}; returning tool answer without document synthesis"
            if warning
            else "Hybrid synthesis failed; returning tool answer"
        )
        return (tool_answer, model_info, fallback_warning)

    def _call_llm_with_resilience(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> tuple[Optional[str], ModelInfo, Optional[str]]:
        """Call Anthropic with retries, bounded backoff, and optional fallback model."""
        models = self._candidate_models()
        deadline = self._model_deadline()
        attempts_per_model = max(1, self.model_retries + 1)
        failures: list[str] = []

        for model_name in models:
            text, model_info, model_failures, timed_out = self._attempt_model_with_retries(
                model_name=model_name,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                attempts_per_model=attempts_per_model,
                deadline=deadline,
            )
            failures.extend(model_failures)

            if text:
                return (text, model_info, None)

            if timed_out:
                warning = self._build_failure_warning(failures, timed_out=True)
                return (None, model_info, warning)

        warning = self._build_failure_warning(failures, timed_out=False)
        return (None, ModelInfo(provider="anthropic", model_name=models[-1]), warning)

    def _candidate_models(self) -> list[str]:
        """Return primary model and optional fallback model list."""
        models = [self.model]
        if self.fallback_model and self.fallback_model != self.model:
            models.append(self.fallback_model)
        return models

    def _model_deadline(self) -> Optional[float]:
        """Return absolute model stage deadline timestamp, if configured."""
        if self.model_timeout_ms <= 0:
            return None
        return time.time() + (self.model_timeout_ms / 1000)

    def _attempt_model_with_retries(
        self,
        model_name: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        attempts_per_model: int,
        deadline: Optional[float],
    ) -> tuple[Optional[str], ModelInfo, list[str], bool]:
        """Attempt one model with bounded retries and backoff."""
        failures: list[str] = []
        model_info = ModelInfo(provider="anthropic", model_name=model_name)

        for attempt in range(1, attempts_per_model + 1):
            if self._deadline_expired(deadline):
                return (None, model_info, failures, True)

            try:
                message = self.client.messages.create(
                    model=model_name,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                usage = message.usage
                content_text = "\n".join(
                    block.text for block in message.content if hasattr(block, "text") and block.text
                ).strip()
                return (
                    content_text,
                    ModelInfo(
                        provider="anthropic",
                        model_name=model_name,
                        prompt_tokens=usage.input_tokens,
                        completion_tokens=usage.output_tokens,
                        total_tokens=usage.input_tokens + usage.output_tokens,
                    ),
                    failures,
                    False,
                )
            except Exception as exc:
                error_detail = f"{exc.__class__.__name__}: {str(exc)}"
                failures.append(f"{model_name} attempt {attempt}: {error_detail}")
                if attempt == attempts_per_model:
                    break

                backoff_seconds = self._bounded_backoff_seconds(attempt, deadline)
                if backoff_seconds is None:
                    return (None, model_info, failures, True)
                if backoff_seconds > 0:
                    time.sleep(backoff_seconds)

        return (None, model_info, failures, False)

    def _deadline_expired(self, deadline: Optional[float]) -> bool:
        """Return True when model deadline has elapsed."""
        return deadline is not None and time.time() >= deadline

    def _bounded_backoff_seconds(self, attempt: int, deadline: Optional[float]) -> Optional[float]:
        """Return capped backoff by remaining deadline, or None when no time remains."""
        backoff_seconds = (self.model_retry_backoff_ms / 1000) * (2 ** (attempt - 1))
        if deadline is None:
            return backoff_seconds

        remaining = deadline - time.time()
        if remaining <= 0:
            return None
        return min(backoff_seconds, remaining)

    def _build_failure_warning(self, failures: list[str], timed_out: bool) -> str:
        """Build a concise warning from collected model invocation failures."""
        prefix = "Model timeout budget exhausted" if timed_out else "Model synthesis failed after retries"
        if not failures:
            return prefix

        summarized = "; ".join(failures[:4])
        if len(failures) > 4:
            summarized = f"{summarized}; +{len(failures) - 4} more"
        return f"{prefix}: {summarized}"

    def _mock_rag_answer(
        self,
        query: str,
        results: list[RetrievalResult],
    ) -> tuple[str, ModelInfo, Optional[str]]:
        """Deterministic synthetic answer for local/testing runs without provider dependency."""
        if self.mock_llm_latency_ms > 0:
            time.sleep(self.mock_llm_latency_ms / 1000)

        if not results:
            return (
                "Mock LLM: I could not find strong evidence in the local corpus for this query.",
                ModelInfo(
                    provider="anthropic",
                    model_name=f"{self.model} (mock)",
                    prompt_tokens=72,
                    completion_tokens=20,
                    total_tokens=92,
                ),
                None,
            )

        sources = ", ".join(item.title for item in results[:2])
        answer = (
            f"Mock LLM answer for query '{query}'. "
            f"Based on retrieved sources: {sources}."
        )
        return (
            answer,
            ModelInfo(
                provider="anthropic",
                model_name=f"{self.model} (mock)",
                prompt_tokens=180,
                completion_tokens=64,
                total_tokens=244,
            ),
            None,
        )

    def _mock_hybrid_answer(
        self,
        query: str,
        results: list[RetrievalResult],
        tools_used: list[ToolCall],
        tool_answer: str,
    ) -> tuple[str, ModelInfo, Optional[str]]:
        """Deterministic synthetic hybrid answer combining tool and RAG context."""
        if self.mock_llm_latency_ms > 0:
            time.sleep(self.mock_llm_latency_ms / 1000)

        doc_note = "no document evidence"
        if results:
            doc_note = ", ".join(item.title for item in results[:2])

        tool_count = len(tools_used)
        answer = (
            f"Mock hybrid answer for query '{query}'. "
            f"Tool summary: {tool_answer} "
            f"Documents consulted: {doc_note}. "
            f"Tools executed: {tool_count}."
        )
        return (
            answer,
            ModelInfo(
                provider="anthropic",
                model_name=f"{self.model} (mock)",
                prompt_tokens=210,
                completion_tokens=78,
                total_tokens=288,
            ),
            None,
        )


def get_copilot_service() -> CopilotService:
    """
    Factory for dependency injection.
    
    Usage: service = Depends(get_copilot_service)
    """
    return CopilotService()
