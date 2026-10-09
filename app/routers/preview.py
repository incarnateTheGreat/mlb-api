"""
Preview router — pre-game matchup previews.

Assembles the lineup-versus-probable-pitcher table that drives a pre-game
view. Every number is regressed for sample size by matchup_context before it
leaves this module, so the response is usable with or without AI narration.
"""

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.services.game_trivia import build_trivia
from app.services.matchup_context import (
    build_expected_rate,
    build_h2h_context,
    build_platoon_context,
    composite_confidence,
)
from app.services.mlb_client import MLBStatsClient, get_mlb_client

router = APIRouter()

# The preview fans out to roughly four upstream calls per batter. Cap the
# concurrency so a single request cannot saturate the MLB API connection pool.
_MAX_CONCURRENT_LOOKUPS = 8


def _parse_rate(raw: Any) -> Optional[float]:
    """MLB returns rate stats as strings, and as '.---' when undefined."""
    if raw in (None, "", ".---", "-.--"):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


async def _lineup_from_recent_game(
    mlb_client: MLBStatsClient,
    team_id: int,
    season: int,
) -> list[dict[str, Any]]:
    """
    Derive a probable lineup from the team's most recent completed game.

    Lineups are not posted until a few hours before first pitch, so a preview
    requested earlier falls back to whoever started last time out.
    """
    schedule = await mlb_client._get(
        "/schedule",
        params={
            "teamId": team_id,
            "sportId": 1,
            "season": season,
            "gameType": "R,F,D,L,W,P",
        },
    )

    finals = [
        game
        for day in schedule.get("dates", [])
        for game in day.get("games", [])
        if game.get("status", {}).get("abstractGameState") == "Final"
    ]
    if not finals:
        return []

    boxscore = await mlb_client._get(
        f"/game/{finals[-1]['gamePk']}/boxscore", params={}
    )

    for side in ("home", "away"):
        team_box = boxscore.get("teams", {}).get(side, {})
        if team_box.get("team", {}).get("id") != team_id:
            continue

        lineup = []
        for order, player_id in enumerate(team_box.get("battingOrder", [])[:9], 1):
            player = team_box.get("players", {}).get(f"ID{player_id}", {})
            lineup.append(
                {
                    "id": player_id,
                    "name": player.get("person", {}).get("fullName", "Unknown"),
                    "position": player.get("position", {}).get("abbreviation", ""),
                    "batting_order": order,
                }
            )
        return lineup

    return []


async def _build_lineup_row(
    mlb_client: MLBStatsClient,
    semaphore: asyncio.Semaphore,
    batter: dict[str, Any],
    pitcher_id: int,
    pitcher_hand: str,
    season: int,
    pitcher_avg_against: Optional[float] = None,
    league_avg: Optional[float] = None,
) -> dict[str, Any]:
    """Assemble one batter's regressed matchup line against the starter."""
    async with semaphore:
        season_stats, h2h_raw, splits = await asyncio.gather(
            mlb_client.get_player_stats(batter["id"], season, "hitting"),
            mlb_client.get_batter_vs_pitcher(batter["id"], pitcher_id),
            mlb_client.get_platoon_splits(batter["id"], season, "hitting"),
            return_exceptions=True,
        )

    season_stats = season_stats if isinstance(season_stats, dict) else {}
    h2h_raw = h2h_raw if isinstance(h2h_raw, dict) else {}
    splits = splits if isinstance(splits, dict) else {}

    h2h = build_h2h_context(
        h2h_stat=h2h_raw,
        batter_season_avg=_parse_rate(season_stats.get("avg")),
    )
    platoon = build_platoon_context(
        splits=splits,
        opponent_hand=pitcher_hand,
        overall_ops=_parse_rate(season_stats.get("ops")),
    )

    season_avg = _parse_rate(season_stats.get("avg"))

    # Prefer the head-to-head-regressed rate as the batter input: it already
    # folds in whatever evidence this pairing has produced. Without history,
    # the season average is the honest starting point.
    batter_rate = h2h["regressed_avg"] if h2h else season_avg

    expected = build_expected_rate(
        batter_rate=batter_rate,
        pitcher_rate=pitcher_avg_against,
        league_rate=league_avg,
    )

    return {
        **batter,
        "season_avg": season_avg,
        "season_ops": _parse_rate(season_stats.get("ops")),
        "head_to_head": h2h,
        "platoon_split": platoon,
        "expected": expected,
        "confidence": composite_confidence(h2h, platoon),
    }


async def _pitcher_avg_against(
    mlb_client: MLBStatsClient, pitcher_id: int, season: int
) -> Optional[float]:
    """
    The batting average this pitcher has allowed, for the log5 blend.

    Built on every batter he has faced this season, which is why it carries
    far more evidence than any single head-to-head sample.
    """
    try:
        stats = await mlb_client.get_player_stats(pitcher_id, season, "pitching")
    except Exception:
        return None

    return _parse_rate((stats or {}).get("avg"))


async def _build_side(
    mlb_client: MLBStatsClient,
    semaphore: asyncio.Semaphore,
    lineup: list[dict[str, Any]],
    pitcher: dict[str, Any],
    season: int,
    league_avg: Optional[float] = None,
) -> list[dict[str, Any]]:
    if not pitcher.get("id"):
        return []

    # One lookup for the whole lineup rather than one per batter.
    pitcher_avg = await _pitcher_avg_against(mlb_client, pitcher["id"], season)

    return list(
        await asyncio.gather(
            *(
                _build_lineup_row(
                    mlb_client,
                    semaphore,
                    batter,
                    pitcher["id"],
                    pitcher["hand"],
                    season,
                    pitcher_avg_against=pitcher_avg,
                    league_avg=league_avg,
                )
                for batter in lineup
            )
        )
    )


def _extract_sides(game: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Pull team ids and probable pitchers out of the schedule payload."""
    sides: dict[str, dict[str, Any]] = {}

    for side in ("away", "home"):
        team = game["teams"][side]
        probable = team.get("probablePitcher") or {}
        sides[side] = {
            "team_id": team["team"]["id"],
            "team_name": team["team"].get("name", ""),
            "pitcher": {
                "id": probable.get("id"),
                "name": probable.get("fullName"),
                # Hand is hydrated inconsistently; resolved separately.
                "hand": (probable.get("pitchHand") or {}).get("code"),
            },
        }

    return sides


async def _fill_missing_hands(
    mlb_client: MLBStatsClient, sides: dict[str, dict[str, Any]]
) -> None:
    """Look up pitching hand for any starter the schedule did not hydrate."""
    missing = [
        s for s in sides.values() if s["pitcher"]["id"] and not s["pitcher"]["hand"]
    ]
    if not missing:
        return

    bios = await asyncio.gather(
        *(mlb_client.get_player(s["pitcher"]["id"]) for s in missing),
        return_exceptions=True,
    )
    for side_data, bio in zip(missing, bios):
        side_data["pitcher"]["hand"] = getattr(bio, "pitch_hand", "R")


def _parse_posted_lineups(
    posted: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Shape the schedule's hydrated lineups into preview rows."""
    lineups: dict[str, list[dict[str, Any]]] = {}

    for side, key in (("away", "awayPlayers"), ("home", "homePlayers")):
        lineups[side] = [
            {
                "id": player.get("id"),
                "name": player.get("fullName")
                or player.get("useName")
                or "Unknown",
                "position": (player.get("primaryPosition") or {}).get(
                    "abbreviation", ""
                ),
                "batting_order": order,
            }
            for order, player in enumerate(posted.get(key, [])[:9], 1)
        ]

    return lineups


async def _resolve_lineups(
    mlb_client: MLBStatsClient,
    game: dict[str, Any],
    sides: dict[str, dict[str, Any]],
    season: int,
) -> tuple[dict[str, list[dict[str, Any]]], bool]:
    """
    Return (lineups, lineups_posted).

    Prefers the officially posted lineup and falls back to each team's most
    recent completed batting order when it has not been announced yet.
    """
    posted = game.get("lineups") or {}

    if posted.get("awayPlayers") or posted.get("homePlayers"):
        return _parse_posted_lineups(posted), True

    away_lineup, home_lineup = await asyncio.gather(
        _lineup_from_recent_game(mlb_client, sides["away"]["team_id"], season),
        _lineup_from_recent_game(mlb_client, sides["home"]["team_id"], season),
    )
    return {"away": away_lineup, "home": home_lineup}, False


async def _fetch_trivia(
    mlb_client: MLBStatsClient,
    away_id: int,
    home_id: int,
    season: int,
) -> Optional[dict[str, Any]]:
    """
    Standings and season-series context for the two clubs.

    Trivia is garnish, so an upstream failure here returns None rather than
    taking down the matchup table the caller actually asked for.

    Leaders are two extra requests, one per club, so they go out alongside
    the other two rather than after them. They are also the only part
    allowed to fail on its own: standings and the series carry the facts
    the panel is built around, while a missing leader board just means one
    fewer line.
    """
    standings, series, away_leaders, home_leaders = await asyncio.gather(
        mlb_client._get(
            "/standings",
            params={
                "leagueId": "103,104",
                "season": season,
                "standingsTypes": "regularSeason",
                "hydrate": "team(division)",
            },
        ),
        mlb_client.get_head_to_head_schedule(away_id, home_id, season),
        mlb_client.get_team_leaders(away_id, season),
        mlb_client.get_team_leaders(home_id, season),
        return_exceptions=True,
    )

    if not isinstance(standings, dict) or not isinstance(series, dict):
        return None

    return build_trivia(
        standings,
        series,
        away_id,
        home_id,
        away_leaders=away_leaders if isinstance(away_leaders, list) else None,
        home_leaders=home_leaders if isinstance(home_leaders, list) else None,
    )


@router.get("/{game_id}/preview")
async def get_game_preview(
    game_id: int,
    season: int = Query(default=2026, ge=1900, le=2100),
    mlb_client: MLBStatsClient = Depends(get_mlb_client),
) -> dict:
    """
    Pre-game preview: each lineup versus the opposing probable pitcher.

    Returns a row per batter with career head-to-head and platoon splits,
    both regressed toward the batter's own season rates so small samples do
    not read as signal.

    When lineups have not been posted yet, each side falls back to the
    batting order from that team's most recent completed game. The
    `lineups_posted` flag reports which source was used.
    """
    try:
        schedule = await mlb_client._get(
            "/schedule",
            params={
                "gamePk": game_id,
                "sportId": 1,
                "hydrate": "probablePitcher,lineups,team",
            },
        )
        game = schedule["dates"][0]["games"][0]
    except (KeyError, IndexError):
        raise HTTPException(status_code=404, detail=f"Game {game_id} not found")
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"Upstream error fetching game: {str(e)}"
        )

    sides = _extract_sides(game)
    await _fill_missing_hands(mlb_client, sides)
    lineups, lineups_posted = await _resolve_lineups(
        mlb_client, game, sides, season
    )

    # One league baseline for the whole request. A failure here only costs
    # the log5 column, so it must not take down the rest of the preview.
    try:
        league_rates = await mlb_client.get_league_batting_rates(season)
    except Exception:
        league_rates = {}
    league_avg = league_rates.get("avg")

    # Each lineup faces the opposing team's starter.
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_LOOKUPS)
    away_rows, home_rows, trivia = await asyncio.gather(
        _build_side(
            mlb_client,
            semaphore,
            lineups["away"],
            sides["home"]["pitcher"],
            season,
            league_avg=league_avg,
        ),
        _build_side(
            mlb_client,
            semaphore,
            lineups["home"],
            sides["away"]["pitcher"],
            season,
            league_avg=league_avg,
        ),
        _fetch_trivia(
            mlb_client, sides["away"]["team_id"], sides["home"]["team_id"], season
        ),
    )

    return {
        "game_id": game_id,
        "status": game.get("status", {}).get("detailedState"),
        "game_date": game.get("gameDate"),
        "lineups_posted": lineups_posted,
        "trivia": trivia,
        "away": {
            "team_id": sides["away"]["team_id"],
            "team_name": sides["away"]["team_name"],
            "facing": sides["home"]["pitcher"],
            "lineup": away_rows,
        },
        "home": {
            "team_id": sides["home"]["team_id"],
            "team_name": sides["home"]["team_name"],
            "facing": sides["away"]["pitcher"],
            "lineup": home_rows,
        },
    }
