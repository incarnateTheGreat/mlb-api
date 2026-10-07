"""
Game trivia — the surrounding facts about a matchup.

Everything here is pure shaping of upstream payloads so it can be tested
without the network. The router does the fetching and hands the raw
responses in.
"""

from typing import Any, Optional


def _split_record(team_record: dict[str, Any], split_type: str) -> Optional[str]:
    """Pull one of the split records (lastTen, oneRun, home, away) as 'W-L'."""
    for split in (team_record.get("records") or {}).get("splitRecords", []):
        if split.get("type") == split_type:
            return f"{split.get('wins')}-{split.get('losses')}"
    return None


def _as_int(value: Any) -> Optional[int]:
    """Standings mixes ints and numeric strings for ranks."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def summarize_team_record(team_record: dict[str, Any]) -> dict[str, Any]:
    """Reduce a standings entry to the handful of facts worth showing."""
    division = (team_record.get("team") or {}).get("division") or {}

    return {
        "wins": team_record.get("wins"),
        "losses": team_record.get("losses"),
        "pct": team_record.get("winningPercentage"),
        "division_name": division.get("nameShort") or division.get("name"),
        "division_rank": _as_int(team_record.get("divisionRank")),
        "games_back": team_record.get("gamesBack"),
        "streak": (team_record.get("streak") or {}).get("streakCode"),
        "last_ten": _split_record(team_record, "lastTen"),
        "one_run": _split_record(team_record, "oneRun"),
        "run_differential": team_record.get("runDifferential"),
    }


def find_team_record(
    standings: dict[str, Any], team_id: int
) -> Optional[dict[str, Any]]:
    """Locate one team inside a league-wide standings response."""
    for record in standings.get("records", []):
        for team_record in record.get("teamRecords", []):
            if (team_record.get("team") or {}).get("id") == team_id:
                return team_record
    return None


def _completed_regular_season_games(schedule: dict[str, Any]):
    """Yield finished regular-season games from a schedule response."""
    for date_entry in schedule.get("dates", []):
        for game in date_entry.get("games", []):
            if game.get("gameType") != "R":
                continue
            if game.get("status", {}).get("abstractGameState") != "Final":
                continue
            yield game


def _winning_team_id(game: dict[str, Any]) -> Optional[int]:
    """The id of whichever side the schedule marked as the winner."""
    for side in ("away", "home"):
        entry = game.get("teams", {}).get(side, {})
        if entry.get("isWinner"):
            return (entry.get("team") or {}).get("id")
    return None


def _series_leader(
    away_wins: int, home_wins: int, away_id: int, home_id: int
) -> Optional[int]:
    if away_wins > home_wins:
        return away_id
    if home_wins > away_wins:
        return home_id
    return None


def summarize_season_series(
    schedule: dict[str, Any],
    away_id: int,
    home_id: int,
) -> Optional[dict[str, Any]]:
    """
    Tally completed regular-season meetings between the two clubs.

    Postseason games are excluded — the current playoff series is already
    reported separately, and mixing the two would double-count it.
    """
    away_wins = 0
    home_wins = 0

    for game in _completed_regular_season_games(schedule):
        winner_id = _winning_team_id(game)
        if winner_id == away_id:
            away_wins += 1
        elif winner_id == home_id:
            home_wins += 1

    if away_wins + home_wins == 0:
        return None

    return {
        "games_played": away_wins + home_wins,
        "away_wins": away_wins,
        "home_wins": home_wins,
        "leader_team_id": _series_leader(
            away_wins, home_wins, away_id, home_id
        ),
    }


def build_trivia(
    standings: dict[str, Any],
    series_schedule: dict[str, Any],
    away_id: int,
    home_id: int,
) -> dict[str, Any]:
    """Assemble the trivia block from already-fetched upstream payloads."""
    teams: dict[str, Any] = {}
    for side, team_id in (("away", away_id), ("home", home_id)):
        team_record = find_team_record(standings, team_id)
        teams[side] = summarize_team_record(team_record) if team_record else None

    return {
        "season_series": summarize_season_series(
            series_schedule, away_id, home_id
        ),
        "teams": teams,
    }
