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


def _scores_by_team(game: dict[str, Any]) -> dict[int, int]:
    """
    Map team id to final score for one meeting.

    Keyed by team rather than by slot on purpose. A head-to-head schedule
    covers meetings at *both* ballparks, so a game's "away"/"home" slots
    describe that night's venue, not the fixture being previewed. Summing
    the slots directly would add one club's runs in June to the other's in
    September.
    """
    scores: dict[int, int] = {}
    for side in ("away", "home"):
        entry = (game.get("teams") or {}).get(side) or {}
        team_id = (entry.get("team") or {}).get("id")
        score = entry.get("score")
        if isinstance(team_id, int) and isinstance(score, int):
            scores[team_id] = score
    return scores


def _went_extra_innings(game: dict[str, Any]) -> bool:
    """True when the game ran past its scheduled length."""
    linescore = game.get("linescore") or {}
    played = linescore.get("currentInning")
    scheduled = linescore.get("scheduledInnings")
    if isinstance(played, int) and isinstance(scheduled, int):
        return played > scheduled
    return False


def _hosting_team_id(game: dict[str, Any]) -> Optional[int]:
    """Which club hosted this particular meeting."""
    entry = (game.get("teams") or {}).get("home") or {}
    return (entry.get("team") or {}).get("id")


def _meeting(
    game: dict[str, Any], away_id: int, home_id: int
) -> dict[str, Any]:
    """
    One meeting reduced to the facts a caption might quote.

    Scores are labelled by the clubs in *today's* fixture, so the caller
    can render them without re-deriving who was hosting back then.
    """
    scores = _scores_by_team(game)
    return {
        "date": game.get("officialDate"),
        "away_team_score": scores.get(away_id),
        "home_team_score": scores.get(home_id),
        "winner_team_id": _winning_team_id(game),
        "hosted_by_team_id": _hosting_team_id(game),
        "extra_innings": _went_extra_innings(game),
        "winning_pitcher": (
            (game.get("decisions") or {}).get("winner") or {}
        ).get("fullName"),
    }


def summarize_season_series(
    schedule: dict[str, Any],
    away_id: int,
    home_id: int,
) -> Optional[dict[str, Any]]:
    """
    Tally completed regular-season meetings between the two clubs.

    The win-loss count alone tends to read the same for every matchup, so
    the shape of the series is summarised too: how many games were tight,
    how many were shutouts, how often the host won, how lopsided the worst
    one got. Those are what distinguish a 3-3 split decided by one run five
    times from a 3-3 split of blowouts.

    These are deliberately raw counts. Deciding which of them is unusual
    enough to be worth a sentence needs a league baseline, and that is a
    presentation question — the caller makes it.

    All of it comes from the payload the caller already fetched. The
    schedule is hydrated with linescores and decisions regardless, so none
    of these facts costs an extra request.

    Postseason games are excluded — the current playoff series is already
    reported separately, and mixing the two would double-count it.
    """
    away_wins = 0
    home_wins = 0
    away_runs = 0
    home_runs = 0
    one_run_games = 0
    shutouts = 0
    extra_inning_games = 0
    host_wins = 0
    meetings: list[dict[str, Any]] = []
    widest: Optional[dict[str, Any]] = None
    widest_margin = -1

    for game in _completed_regular_season_games(schedule):
        winner_id = _winning_team_id(game)
        if winner_id == away_id:
            away_wins += 1
        elif winner_id == home_id:
            home_wins += 1
        else:
            # No recorded winner means the row is unusable for a series tally.
            continue

        meeting = _meeting(game, away_id, home_id)
        meetings.append(meeting)

        if meeting["extra_innings"]:
            extra_inning_games += 1
        if meeting["hosted_by_team_id"] == winner_id:
            host_wins += 1

        away_score = meeting["away_team_score"]
        home_score = meeting["home_team_score"]
        if away_score is None or home_score is None:
            continue

        away_runs += away_score
        home_runs += home_score

        margin = abs(away_score - home_score)
        if margin == 1:
            one_run_games += 1
        if min(away_score, home_score) == 0:
            shutouts += 1
        if margin > widest_margin:
            widest_margin = margin
            widest = {**meeting, "margin": margin}

    if away_wins + home_wins == 0:
        return None

    # The schedule arrives in date order, but sort defensively: a
    # doubleheader or a rescheduled game can land out of sequence.
    meetings.sort(key=lambda m: (m["date"] or "", m["winner_team_id"] or 0))

    return {
        "games_played": away_wins + home_wins,
        "away_wins": away_wins,
        "home_wins": home_wins,
        "leader_team_id": _series_leader(
            away_wins, home_wins, away_id, home_id
        ),
        "away_runs": away_runs,
        "home_runs": home_runs,
        "one_run_games": one_run_games,
        "shutouts": shutouts,
        "extra_inning_games": extra_inning_games,
        "host_wins": host_wins,
        "largest_margin": widest,
        "last_meeting": meetings[-1] if meetings else None,
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
