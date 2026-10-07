"""
Matchups router — batter vs pitcher analysis endpoints.
"""

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.models.analysis import MatchupAnalysis
from app.services.mlb_client import get_mlb_client, MLBStatsClient
from app.services.ai_service import get_ai_service, AIService
from app.services.matchup_context import (
    build_h2h_context,
    build_platoon_context,
    composite_confidence,
    render_matchup_facts,
)


router = APIRouter()


def _parse_rate(raw: Any) -> Optional[float]:
    """MLB returns rate stats as strings, and as '.---' when undefined."""
    if raw in (None, "", ".---", "-.--"):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


@router.get("/{batter_id}/vs/{pitcher_id}", response_model=dict)
async def get_batter_vs_pitcher(
    batter_id: int,
    pitcher_id: int,
    season: int = Query(default=2026, ge=1900, le=2100),
    include_analysis: bool = Query(
        default=True,
        description="Include AI-generated matchup analysis",
    ),
    mlb_client: MLBStatsClient = Depends(get_mlb_client),
    ai_service: AIService = Depends(get_ai_service),
) -> dict:
    """
    Get batter vs pitcher matchup data and analysis.
    
    Returns career head-to-head stats and platoon splits, both regressed for
    sample size, plus optionally an AI-generated narrative built from those
    numbers.

    Every statistic here is computed deterministically. The AI only narrates
    them, so the matchup still resolves fully when `include_analysis=false`
    or when generation fails.
    
    This is useful for:
    - Pre-game matchup previews
    - In-game at-bat context
    - Fantasy baseball research
    """
    try:
        batter, pitcher = await asyncio.gather(
            mlb_client.get_player(batter_id),
            mlb_client.get_player(pitcher_id),
        )
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Player not found: {str(e)}",
        )
    
    # Fan out the remaining lookups concurrently — they are independent.
    (
        batter_stats,
        pitcher_stats,
        h2h_raw,
        batter_splits,
    ) = await asyncio.gather(
        mlb_client.get_player_stats(batter_id, season, "hitting"),
        mlb_client.get_player_stats(pitcher_id, season, "pitching"),
        mlb_client.get_batter_vs_pitcher(batter_id, pitcher_id),
        mlb_client.get_platoon_splits(batter_id, season, "hitting"),
        return_exceptions=True,
    )

    # A failed split should degrade that one signal, not the whole response.
    batter_stats = batter_stats if isinstance(batter_stats, dict) else {}
    pitcher_stats = pitcher_stats if isinstance(pitcher_stats, dict) else {}
    h2h_raw = h2h_raw if isinstance(h2h_raw, dict) else {}
    batter_splits = batter_splits if isinstance(batter_splits, dict) else {}

    h2h = build_h2h_context(
        h2h_stat=h2h_raw,
        batter_season_avg=_parse_rate(batter_stats.get("avg")),
    )
    platoon = build_platoon_context(
        splits=batter_splits,
        opponent_hand=pitcher.pitch_hand,
        overall_ops=_parse_rate(batter_stats.get("ops")),
    )
    confidence = composite_confidence(h2h, platoon)

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
        "historical_matchup": h2h,
        "platoon_split": platoon,
        "confidence": confidence,
    }
    
    if include_analysis:
        try:
            analysis, metadata = await ai_service.generate_matchup_analysis(
                batter_name=batter.full_name,
                batter_id=batter_id,
                batter_stats=batter_stats,
                pitcher_name=pitcher.full_name,
                pitcher_id=pitcher_id,
                pitcher_stats=pitcher_stats,
                historical_matchup=h2h,
                matchup_facts=render_matchup_facts(
                    batter_name=batter.full_name,
                    pitcher_name=pitcher.full_name,
                    h2h=h2h,
                    platoon=platoon,
                    confidence=confidence,
                ),
                computed_confidence=confidence,
            )
            result["analysis"] = analysis.model_dump()
            result["metadata"] = metadata.model_dump()
        except Exception as e:
            result["analysis_error"] = str(e)
    
    return result
