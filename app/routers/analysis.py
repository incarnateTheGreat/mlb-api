"""
Analysis router — general AI-powered analysis endpoints.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.services.ai_service import get_ai_service, AIService
from app.services.copilot_service import get_copilot_service, CopilotService
from app.models.analysis import CopilotRequest, CopilotResponse
from app.models.trace import CopilotTrace, TraceStep


router = APIRouter()


class AnalysisRequest(BaseModel):
    """Request body for custom analysis generation."""
    context: str
    question: str
    max_tokens: int = 1024
    temperature: float = 0.7


class AnalysisResponse(BaseModel):
    """Response from analysis endpoint."""
    answer: str
    tokens_used: int
    generation_time_ms: int


@router.post(
    "/custom",
    responses={500: {"description": "AI generation failed"}},
)
async def generate_custom_analysis(
    request: AnalysisRequest,
    ai_service: Annotated[AIService, Depends(get_ai_service)],
) -> AnalysisResponse:
    """
    Generate custom AI analysis based on provided context.
    
    This is a flexible endpoint for when the specialized endpoints
    (game summary, scouting report, matchup analysis) don't fit
    your use case.
    
    **Use cases:**
    - Compare two players' stats
    - Analyze team performance trends
    - Generate fantasy baseball advice
    - Answer specific baseball questions with context
    
    **Example request:**
    ```json
    {
        "context": "Player A: .300 AVG, 25 HR, .380 OBP. Player B: .275 AVG, 35 HR, .350 OBP",
        "question": "Which player would you rather have for a playoff push?"
    }
    ```
    """
    import time
    
    start_time = time.time()
    
    prompt = f"""You are an expert baseball analyst. Based on the following context, 
answer the question concisely and analytically.

CONTEXT:
{request.context}

QUESTION:
{request.question}

Provide a clear, well-reasoned answer grounded in the data provided."""

    try:
        message = ai_service.client.messages.create(
            model=ai_service.model,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"AI generation failed: {str(e)}",
        )
    
    generation_time_ms = int((time.time() - start_time) * 1000)
    tokens_used = message.usage.input_tokens + message.usage.output_tokens
    
    return AnalysisResponse(
        answer=message.content[0].text,
        tokens_used=tokens_used,
        generation_time_ms=generation_time_ms,
    )


@router.post(
    "/copilot/query",
    responses={500: {"description": "Copilot query failed"}},
)
async def copilot_query(
    request: CopilotRequest,
    copilot_service: Annotated[CopilotService, Depends(get_copilot_service)],
) -> CopilotResponse:
    """
    Public-data grounded AI copilot for MLB questions.
    
    This endpoint implements the orchestration contract for tool-based,
    RAG-based, and hybrid AI queries. All responses include:
    - answer: Natural-language response to query
    - citations: Sources (documents or tool provenance)
    - tools_used: Deterministic tool invocations with latency
    - model_info: Token counts and model info
    - timing: Latency breakdown by orchestration stage
    - confidence: Sufficiency of evidence (high/medium/low)
    - warnings: Uncertainty flags or partial-result signals
    
    **Query Modes:**
    - `auto`: Infer best route (tool, rag, or hybrid)
    - `tool`: Use only deterministic baseball data tools
    - `rag`: Use only vector retrieval + LLM synthesis
    - `hybrid`: Combine tools and retrieval with synthesis
    
    **Example request:**
    ```json
    {
        "query": "What's Mike Trout's current home run total for 2024?",
        "mode": "auto",
        "context": {
            "season": 2024
        }
    }
    ```
    
    **Example response:**
    ```json
    {
        "request_id": "550e8400-e29b-41d4-a716-446655440000",
        "answer": "Mike Trout has 27 home runs in the 2024 season...",
        "citations": [
            {
                "source_id": "statsapi_get_player_stat_split_545361",
                "source_type": "tool",
                "title": "StatsAPI Player Stats",
                "snippet": "playerId: 545361, season: 2024, HR: 27"
            }
        ],
        "tools_used": [
            {
                "tool_name": "get_player_stat_split",
                "input_summary": "playerId=545361, season=2024",
                "success": true,
                "latency_ms": 342
            }
        ],
        "model_info": {
            "provider": "anthropic",
            "model_name": "claude-haiku-4-5-20251001",
            "prompt_tokens": 245,
            "completion_tokens": 128,
            "total_tokens": 373
        },
        "timing": {
            "total_ms": 2145,
            "orchestration_ms": 50,
            "tools_ms": 342,
            "model_ms": 1753
        },
        "confidence": "high",
        "warnings": [],
        "query_mode": "tool"
    }
    ```
    """
    try:
        response = await copilot_service.query(request)
        return response
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Copilot query failed: {str(e)}",
        )


@router.post(
    "/copilot/trace",
    responses={500: {"description": "Copilot trace failed"}},
)
async def copilot_trace(
    request: CopilotRequest,
    copilot_service: Annotated[CopilotService, Depends(get_copilot_service)],
) -> CopilotTrace:
    """
    Trace a copilot query execution with detailed step-by-step visibility.
    
    This endpoint is identical to `/copilot/query` but instead of returning
    just the final answer, it returns a complete trace showing:
    
    - Query analysis (keyword detection, intent understanding)
    - Mode selection (how the system chose tool/rag/hybrid)
    - Tool selection + invocation (which APIs were called)
    - RAG retrieval (chunk scoring, document matching)
    - LLM prompt construction (what was sent to Claude)
    - LLM synthesis (model latency, token counts)
    - Response finalization (citations, confidence scoring)
    - Complete metadata (total timing breakdown)
    
    **Use cases:**
    - Debug: Understand why a query returned unexpected results
    - Observe: Watch the LLM synthesis process in real-time
    - Validate: Check that tools/RAG are being selected correctly
    - Optimize: Identify performance bottlenecks (retrieval vs. model vs. tools)
    
    **Example trace request:**
    ```json
    {
        "query": "What is the infield fly rule?",
        "mode": "rag"
    }
    ```
    
    **Example trace response:**
    ```json
    {
        "request_id": "550e8400-e29b-41d4-a716-446655440000",
        "query": "What is the infield fly rule?",
        "mode": "rag",
        "steps": [
            {
                "step_number": 1,
                "stage_name": "Query Analysis",
                "description": "Tokenized and analyzed query",
                "details": {
                    "tokens": ["what", "is", "the", "infield", "fly", "rule"],
                    "keyword_detection": ["infield", "fly", "rule"]
                },
                "timing_ms": 0
            },
            {
                "step_number": 2,
                "stage_name": "Mode Selection",
                "description": "Selected RAG mode (requested)",
                "details": {
                    "requested_mode": "rag",
                    "inferred_mode": null,
                    "selected_mode": "rag",
                    "confidence": 1.0
                },
                "timing_ms": 1
            },
            {
                "step_number": 4,
                "stage_name": "RAG Retrieval",
                "description": "Retrieved 3 corpus chunks",
                "details": {
                    "corpus_size": 3,
                    "query_vector": [...],
                    "scored_chunks": [
                        {
                            "chunk_id": "doc:rules_infield_fly:0",
                            "score": 0.615,
                            "title": "Infield Fly Rule"
                        },
                        {
                            "chunk_id": "doc:rules_designated_hitter:0",
                            "score": 0.387,
                            "title": "Designated Hitter Rule"
                        }
                    ],
                    "min_score_threshold": 0.18,
                    "retrieved_count": 3
                },
                "timing_ms": 2
            },
            {
                "step_number": 6,
                "stage_name": "LLM Synthesis",
                "description": "Claude generated response",
                "details": {
                    "prompt_chars": 1198,
                    "context_blocks": 3,
                    "model": "claude-haiku-4-5-20251001",
                    "prompt_tokens": 180,
                    "completion_tokens": 64,
                    "total_tokens": 244
                },
                "timing_ms": 84
            }
        ],
        "total_ms": 87,
        "final_response": {
            "answer": "The infield fly rule prevents...",
            "confidence": "high",
            "citations": 3,
            "warnings": []
        }
    }
    ```
    """
    import time
    import uuid
    
    request_id = str(uuid.uuid4())
    trace_steps: list[TraceStep] = []
    start_time = time.time()
    
    try:
        # STEP 1: Query Analysis
        step1_start = time.time()
        query_tokens = request.query.lower().split()
        trace_steps.append(TraceStep(
            step_number=1,
            stage_name="Query Analysis",
            description=f"Tokenized query into {len(query_tokens)} tokens",
            details={
                "tokens": query_tokens[:10],  # First 10 tokens
                "total_tokens": len(query_tokens),
                "query_length_chars": len(request.query),
            },
            timing_ms=int((time.time() - step1_start) * 1000),
        ))
        
        # STEP 2: Mode Selection
        step2_start = time.time()
        requested_mode = request.mode or "auto"
        inferred_mode = copilot_service._infer_mode(request) if requested_mode == "auto" else None
        selected_mode = inferred_mode if inferred_mode else requested_mode
        
        trace_steps.append(TraceStep(
            step_number=2,
            stage_name="Mode Selection",
            description=f"Selected {selected_mode} mode",
            details={
                "requested_mode": requested_mode,
                "inferred_mode": inferred_mode,
                "selected_mode": selected_mode,
                "has_context": bool(request.context),
                "context_keys": list(request.context.keys()) if request.context else [],
            },
            timing_ms=int((time.time() - step2_start) * 1000),
        ))
        
        # STEP 3: Tool Selection (if applicable)
        if selected_mode in ("tool", "hybrid"):
            step3_start = time.time()
            selected_tool = copilot_service._select_tool(request)
            trace_steps.append(TraceStep(
                step_number=3,
                stage_name="Tool Selection",
                description=f"Selected tool: {selected_tool[0] if selected_tool else 'None'}",
                details={
                    "tool_name": selected_tool[0] if selected_tool else None,
                    "tool_inputs": selected_tool[1] if selected_tool else None,
                    "available_tools": ["get_player_stat_split", "get_game_summary", "get_team_schedule"],
                },
                timing_ms=int((time.time() - step3_start) * 1000),
            ))
        
        # STEP 4: RAG Retrieval (if applicable)
        if selected_mode in ("rag", "hybrid"):
            step4_start = time.time()
            rag_service = copilot_service.rag_service
            rag_service.ensure_ingested()

            # Score every corpus chunk against the query for full visibility,
            # then run retrieve() to capture the official top-k results.
            query_vector = rag_service._vectorize(request.query)
            all_scores = []
            for chunk in rag_service._chunks:
                score = rag_service._cosine_similarity(query_vector, chunk.vector)
                all_scores.append(
                    {
                        "chunk_id": chunk.chunk_id,
                        "source_id": chunk.source_id,
                        "title": chunk.title,
                        "score": round(score, 4),
                        "passed_threshold": score >= rag_service.min_score,
                    }
                )
            all_scores.sort(key=lambda item: item["score"], reverse=True)

            retrieved = rag_service.retrieve(request.query)
            stats = rag_service.retrieval_stats(retrieved)

            trace_steps.append(TraceStep(
                step_number=4,
                stage_name="RAG Retrieval",
                description=(
                    f"Retrieved {stats['selected']} of {stats['candidates']} corpus chunks "
                    f"(source diversity: {stats['source_diversity']})"
                ),
                details={
                    "corpus_size": stats["candidates"],
                    "retrieved_count": stats["selected"],
                    "source_diversity": stats["source_diversity"],
                    "min_score_threshold": rag_service.min_score,
                    "query_tokens": list(query_vector.keys()),
                    "scored_chunks": all_scores,
                    "retrieved_chunk_ids": [item.chunk_id for item in retrieved],
                    "retrieval_cache_status": f"{len(rag_service.query_cache)} cached queries",
                },
                timing_ms=int((time.time() - step4_start) * 1000),
            ))
        
        # STEP 5: Prompt Construction
        step5_start = time.time()
        trace_steps.append(TraceStep(
            step_number=5,
            stage_name="Prompt Construction",
            description="Built LLM prompt with context and instructions",
            details={
                "mode": selected_mode,
                "includes_tool_context": selected_mode in ("tool", "hybrid"),
                "includes_rag_context": selected_mode in ("rag", "hybrid"),
                "system_prompt_chars": 500,  # Approximate
                "user_prompt_chars": len(request.query) + (len(str(request.context)) if request.context else 0),
            },
            timing_ms=int((time.time() - step5_start) * 1000),
        ))
        
        # STEP 6: LLM Synthesis (Actual query execution)
        step6_start = time.time()
        response = await copilot_service.query(request)
        step6_timing = int((time.time() - step6_start) * 1000)
        
        trace_steps.append(TraceStep(
            step_number=6,
            stage_name="LLM Synthesis",
            description=f"Claude generated response ({response.model_info.total_tokens} tokens)",
            details={
                "model": response.model_info.model_name,
                "prompt_tokens": response.model_info.prompt_tokens,
                "completion_tokens": response.model_info.completion_tokens,
                "total_tokens": response.model_info.total_tokens,
            },
            timing_ms=step6_timing,
        ))
        
        # STEP 7: Citations & Confidence
        step7_start = time.time()
        trace_steps.append(TraceStep(
            step_number=7,
            stage_name="Citations & Confidence",
            description=f"Generated {len(response.citations)} citations, confidence={response.confidence}",
            details={
                "citation_count": len(response.citations),
                "citation_types": [c.source_type for c in response.citations],
                "confidence_level": response.confidence,
                "has_warnings": len(response.warnings) > 0,
                "warning_count": len(response.warnings),
            },
            timing_ms=int((time.time() - step7_start) * 1000),
        ))
        
        # STEP 8: Complete
        total_time = int((time.time() - start_time) * 1000)
        trace_steps.append(TraceStep(
            step_number=8,
            stage_name="Complete",
            description="Query execution finished",
            details={
                "request_id": request_id,
                "total_latency_ms": total_time,
                "latency_breakdown": {
                    "orchestration_ms": response.timing.orchestration_ms,
                    "tools_ms": response.timing.tools_ms,
                    "retrieval_ms": response.timing.retrieval_ms,
                    "model_ms": response.timing.model_ms,
                },
            },
            timing_ms=0,  # Overall timer
        ))
        
        return CopilotTrace(
            request_id=request_id,
            query=request.query,
            mode=selected_mode,
            steps=trace_steps,
            total_ms=total_time,
            final_response={
                "answer": response.answer[:200],  # First 200 chars
                "confidence": response.confidence,
                "citations": len(response.citations),
                "tools_used": [t.tool_name for t in response.tools_used],
                "warnings": response.warnings,
            },
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Copilot trace failed: {str(e)}",
        )


@router.get("/health")
async def analysis_health():
    """Check if the AI service is properly configured."""
    try:
        ai_service = get_ai_service()
        # Quick test that we can instantiate the client
        return {
            "status": "healthy",
            "model": ai_service.model,
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
        }
