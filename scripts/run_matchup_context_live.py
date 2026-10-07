"""
Run the head-to-head regularization layer against a real game.

Fetches probable pitchers for a gamePk, derives each side's likely lineup
from their most recent completed game, pulls career batter-vs-pitcher splits
from the Stats API, and feeds them through build_context().

No AI calls — this is the deterministic layer only.

Run: venv/bin/python scripts/run_matchup_context_live.py [gamePk]
"""

import asyncio
import sys
from typing import Any, Optional

import httpx
from mock_matchup_context import H2HLine, build_context, render_prompt_block

API = "https://statsapi.mlb.com/api/v1"
SEASON = 2026

# Keep concurrent upstream calls polite — this fans out to ~36 requests.
CONCURRENCY = 6


async def get_json(
    client: httpx.AsyncClient, path: str, params: dict[str, Any]
) -> dict[str, Any]:
    resp = await client.get(f"{API}{path}", params=params, timeout=25.0)
    resp.raise_for_status()
    return resp.json()


async def get_game_matchup(
    client: httpx.AsyncClient, game_pk: int
) -> dict[str, Any]:
    """Probable pitchers and team ids for the game."""
    data = await get_json(
        client,
        "/schedule",
        {"gamePk": game_pk, "sportId": 1, "hydrate": "probablePitcher,team"},
    )
    game = data["dates"][0]["games"][0]
    out = {"status": game["status"]["detailedState"]}

    for side in ("away", "home"):
        team = game["teams"][side]
        pitcher = team.get("probablePitcher", {})
        out[side] = {
            "team_id": team["team"]["id"],
            "team_name": team["team"]["name"],
            "pitcher_id": pitcher.get("id"),
            "pitcher_name": pitcher.get("fullName"),
        }

    return out


async def get_recent_lineup(
    client: httpx.AsyncClient, team_id: int
) -> list[dict[str, Any]]:
    """
    Derive a probable lineup from the team's most recent completed game.

    Used when lineups haven't been posted yet. Returns the batting order
    (starters only) from that game's boxscore.
    """
    sched = await get_json(
        client,
        "/schedule",
        {
            "teamId": team_id,
            "sportId": 1,
            "startDate": "2026-09-01",
            "endDate": "2026-10-06",
            "gameType": "R,F,D,L,W,P",
        },
    )

    finals = [
        g
        for d in sched.get("dates", [])
        for g in d.get("games", [])
        if g["status"]["abstractGameState"] == "Final"
    ]
    if not finals:
        return []

    last = finals[-1]
    box = await get_json(client, f"/game/{last['gamePk']}/boxscore", {})

    side = (
        "home"
        if box["teams"]["home"]["team"]["id"] == team_id
        else "away"
    )
    team_box = box["teams"][side]

    lineup = []
    for pid in team_box.get("battingOrder", [])[:9]:
        player = team_box["players"].get(f"ID{pid}", {})
        lineup.append(
            {
                "id": pid,
                "name": player.get("person", {}).get("fullName", "Unknown"),
                "position": player.get("position", {}).get(
                    "abbreviation", ""
                ),
            }
        )

    return lineup


async def get_h2h(
    client: httpx.AsyncClient, batter_id: int, pitcher_id: int
) -> dict[str, Any]:
    """Career batter-vs-pitcher totals."""
    data = await get_json(
        client,
        f"/people/{batter_id}/stats",
        {
            "stats": "vsPlayerTotal",
            "group": "hitting",
            "opposingPlayerId": pitcher_id,
            "sportId": 1,
        },
    )

    for block in data.get("stats", []):
        for split in block.get("splits", []):
            stat = split.get("stat", {})
            # The career block is the one with accumulated at-bats.
            if stat.get("atBats") is not None:
                return stat

    return {}


async def get_season_avg(
    client: httpx.AsyncClient, batter_id: int
) -> Optional[float]:
    data = await get_json(
        client,
        f"/people/{batter_id}/stats",
        {"stats": "season", "season": SEASON, "group": "hitting"},
    )

    for block in data.get("stats", []):
        for split in block.get("splits", []):
            avg = split.get("stat", {}).get("avg")
            if avg not in (None, ".---"):
                return float(avg)

    return None


async def build_row(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    batter: dict[str, Any],
    pitcher_name: str,
    pitcher_id: int,
) -> Optional[tuple[H2HLine, dict]]:
    async with sem:
        h2h, season_avg = await asyncio.gather(
            get_h2h(client, batter["id"], pitcher_id),
            get_season_avg(client, batter["id"]),
        )

    at_bats = h2h.get("atBats", 0)
    if not at_bats or season_avg is None:
        return None

    line = H2HLine(
        batter=batter["name"],
        pitcher=pitcher_name,
        at_bats=at_bats,
        hits=h2h.get("hits", 0),
        home_runs=h2h.get("homeRuns", 0),
        strikeouts=h2h.get("strikeOuts", 0),
        walks=h2h.get("baseOnBalls", 0),
        season_avg=season_avg,
    )

    return line, build_context(line)


async def analyze_side(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    label: str,
    lineup: list[dict[str, Any]],
    pitcher_name: str,
    pitcher_id: int,
) -> None:
    results = await asyncio.gather(
        *(
            build_row(client, sem, b, pitcher_name, pitcher_id)
            for b in lineup
        )
    )
    rows = [r for r in results if r is not None]

    print()
    print("=" * 78)
    print(f"  {label} vs {pitcher_name}")
    print("=" * 78)

    if not rows:
        print("  No career head-to-head data for this lineup.")
        return

    header = (
        f"  {'Batter':<22}{'H2H':>9}{'Raw':>8}{'Reg':>8}"
        f"{'Szn':>8}{'Reliability':>14}{'Lean':>14}"
    )
    print(header)
    print("  " + "-" * 74)

    for line, ctx in sorted(rows, key=lambda r: -r[0].at_bats):
        h2h_line = f"{line.hits}-{line.at_bats}"
        print(
            f"  {line.batter[:21]:<22}{h2h_line:>9}"
            f"{ctx['observed_avg']:>8.3f}{ctx['regressed_avg']:>8.3f}"
            f"{ctx['season_avg']:>8.3f}{ctx['reliability']:>14}"
            f"{ctx['h2h_lean']:>14}"
        )

    # Show the full prompt block for the largest sample on this side.
    biggest = max(rows, key=lambda r: r[0].at_bats)
    print()
    print(f"  --- prompt block ({biggest[0].batter}) ---")
    for row in render_prompt_block(*biggest).splitlines():
        print(f"  {row}")


async def main() -> None:
    game_pk = int(sys.argv[1]) if len(sys.argv) > 1 else 849819
    sem = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient() as client:
        matchup = await get_game_matchup(client, game_pk)

        print(f"Game {game_pk} — {matchup['status']}")
        print(
            f"{matchup['away']['team_name']} "
            f"({matchup['away']['pitcher_name']}) @ "
            f"{matchup['home']['team_name']} "
            f"({matchup['home']['pitcher_name']})"
        )

        away_lineup, home_lineup = await asyncio.gather(
            get_recent_lineup(client, matchup["away"]["team_id"]),
            get_recent_lineup(client, matchup["home"]["team_id"]),
        )
        print("(lineups not posted — using each team's most recent order)")

        # Each lineup faces the OPPOSING probable pitcher.
        await analyze_side(
            client,
            sem,
            matchup["away"]["team_name"],
            away_lineup,
            matchup["home"]["pitcher_name"],
            matchup["home"]["pitcher_id"],
        )
        await analyze_side(
            client,
            sem,
            matchup["home"]["team_name"],
            home_lineup,
            matchup["away"]["pitcher_name"],
            matchup["away"]["pitcher_id"],
        )


if __name__ == "__main__":
    asyncio.run(main())
