"""
Matchups router — batter vs pitcher analysis endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.models.analysis import MatchupAnalysis
from app.services.mlb_client import get_mlb_client, MLBStatsClient
from app.services.ai_service import get_ai_service, AIService


router = APIRouter()


@router.get("/{batter_id}/vs/{pitcher_id}", response_model=dict)
async def get_batter_vs_pitcher(
    batter_id: int,
    pitcher_id: int,
    season: int = Query(default=2024, ge=1900, le=2100),
    include_analysis: bool = Query(
        default=True,
        description="Include AI-generated matchup analysis",
    ),
    mlb_client: MLBStatsClient = Depends(get_mlb_client),
    ai_service: AIService = Depends(get_ai_service),
) -> dict:
    """
    Get batter vs pitcher matchup data and analysis.
    
    Returns historical matchup stats (if available) and optionally
    an AI-generated analysis of the matchup with predictions.
    
    This is useful for:
    - Pre-game matchup previews
    - In-game at-bat context
    - Fantasy baseball research
    """
    # Fetch both players' info and stats
    try:
        batter = await mlb_client.get_player(batter_id)
        pitcher = await mlb_client.get_player(pitcher_id)
        
        batter_stats = await mlb_client.get_player_stats(batter_id, season, "hitting")
        pitcher_stats = await mlb_client.get_player_stats(pitcher_id, season, "pitching")
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Player not found: {str(e)}",
        )
    
    result = {
        "batter": {
            "id": batter_id,
            "name": batter.full_name,
            "bat_side": batter.bat_side,
            "season_stats": batter_stats,
        },
        "pitcher": {
            "id": pitcher_id,
            "name": pitcher.full_name,
            "pitch_hand": pitcher.pitch_hand,
            "season_stats": pitcher_stats,
        },
        # Note: Historical head-to-head stats would require additional API calls
        # or a database of historical matchups. Placeholder for now.
        "historical_matchup": None,
    }
    
    if include_analysis:
        try:
            analysis, metadata = await ai_service.generate_matchup_analysis(
                batter_name=batter.full_name,
                batter_id=batter_id,
                batter_stats=batter_stats or {},
                pitcher_name=pitcher.full_name,
                pitcher_id=pitcher_id,
                pitcher_stats=pitcher_stats or {},
            )
            result["analysis"] = analysis.model_dump()
            result["metadata"] = metadata.model_dump()
        except Exception as e:
            result["analysis_error"] = str(e)
    
    return result
