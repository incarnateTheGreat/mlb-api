"""
Analysis router — general AI-powered analysis endpoints.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.services.ai_service import get_ai_service, AIService


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


@router.post("/custom", response_model=AnalysisResponse)
async def generate_custom_analysis(
    request: AnalysisRequest,
    ai_service: AIService = Depends(get_ai_service),
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
