"""
Tests for game_trivia — the pure shaping of standings and season-series data.

Payload fragments are trimmed copies of real MLB responses for the 2026
Brewers and Padres, so field names and value types match production.
"""

from app.services.game_trivia import (
    build_trivia,
    find_team_record,
    summarize_season_series,
    summarize_team_record,
)

BREWERS, PADRES = 158, 135


def _team_record(team_id, name, division_short, **overrides):
    record = {
        "team": {
            "id": team_id,
            "name": name,
            "division": {"name": f"National League {name}", "nameShort": division_short},
        },
        "wins": 103,
        "losses": 59,
        "winningPercentage": ".636",
        "divisionRank": "1",
        "gamesBack": "-",
        "streak": {"streakCode": "W5", "streakType": "wins", "streakNumber": 5},
        "runDifferential": 214,
        "records": {
            "splitRecords": [
                {"type": "home", "wins": 55, "losses": 26},
                {"type": "lastTen", "wins": 8, "losses": 2},
                {"type": "oneRun", "wins": 27, "losses": 20},
            ]
        },
    }
    record.update(overrides)
    return record


def _standings(*team_records):
    return {"records": [{"teamRecords": list(team_records)}]}


def _game(
    game_type,
    state,
    winner_id,
    brewers_score=None,
    padres_score=None,
    date="2026-06-06",
    innings=9,
    winning_pitcher=None,
    hosted_by=PADRES,
):
    """
    One meeting between the two clubs.

    `hosted_by` flips which club occupies the home slot, because a
    head-to-head schedule spans both ballparks. Scores are always given
    per club so a flipped venue cannot silently swap them.
    """

    def side(team_id, score):
        return {
            "team": {"id": team_id},
            "isWinner": team_id == winner_id,
            "score": score,
        }

    brewers = side(BREWERS, brewers_score)
    padres = side(PADRES, padres_score)
    away, home = (brewers, padres) if hosted_by == PADRES else (padres, brewers)

    game = {
        "gameType": game_type,
        "status": {"abstractGameState": state},
        "officialDate": date,
        "teams": {"away": away, "home": home},
        "linescore": {"currentInning": innings, "scheduledInnings": 9},
    }
    if winning_pitcher:
        game["decisions"] = {"winner": {"fullName": winning_pitcher}}
    return game


def _schedule(*games):
    return {"dates": [{"games": list(games)}]}


class TestSummarizeTeamRecord:
    def test_extracts_the_headline_facts(self):
        summary = summarize_team_record(_team_record(BREWERS, "Brewers", "NL Central"))

        assert summary["wins"] == 103
        assert summary["losses"] == 59
        assert summary["pct"] == ".636"
        assert summary["streak"] == "W5"
        assert summary["run_differential"] == 214

    def test_prefers_the_short_division_name(self):
        summary = summarize_team_record(_team_record(BREWERS, "Brewers", "NL Central"))
        assert summary["division_name"] == "NL Central"

    def test_falls_back_to_the_long_division_name(self):
        record = _team_record(BREWERS, "Brewers", "NL Central")
        del record["team"]["division"]["nameShort"]

        assert summarize_team_record(record)["division_name"] == (
            "National League Brewers"
        )

    def test_coerces_the_string_rank_to_an_int(self):
        # Standings returns divisionRank as a string, which would sort wrongly.
        summary = summarize_team_record(_team_record(BREWERS, "Brewers", "NL Central"))
        assert summary["division_rank"] == 1

    def test_pulls_named_split_records(self):
        summary = summarize_team_record(_team_record(BREWERS, "Brewers", "NL Central"))

        assert summary["last_ten"] == "8-2"
        assert summary["one_run"] == "27-20"

    def test_tolerates_missing_splits(self):
        record = _team_record(BREWERS, "Brewers", "NL Central", records={})
        summary = summarize_team_record(record)

        assert summary["last_ten"] is None
        assert summary["one_run"] is None

    def test_tolerates_a_missing_streak(self):
        record = _team_record(BREWERS, "Brewers", "NL Central")
        del record["streak"]

        assert summarize_team_record(record)["streak"] is None


class TestFindTeamRecord:
    def test_finds_a_team_anywhere_in_the_standings(self):
        standings = _standings(
            _team_record(PADRES, "Padres", "NL West"),
            _team_record(BREWERS, "Brewers", "NL Central"),
        )

        found = find_team_record(standings, BREWERS)
        assert found["team"]["id"] == BREWERS

    def test_returns_none_for_an_absent_team(self):
        standings = _standings(_team_record(PADRES, "Padres", "NL West"))
        assert find_team_record(standings, BREWERS) is None


class TestSummarizeSeasonSeries:
    def test_counts_wins_for_each_side(self):
        series = summarize_season_series(
            _schedule(
                _game("R", "Final", PADRES),
                _game("R", "Final", PADRES),
                _game("R", "Final", BREWERS),
            ),
            away_id=BREWERS,
            home_id=PADRES,
        )

        assert series["away_wins"] == 1
        assert series["home_wins"] == 2
        assert series["games_played"] == 3
        assert series["leader_team_id"] == PADRES

    def test_excludes_postseason_games(self):
        # The playoff series is reported separately, so counting those here
        # would show the same games twice.
        series = summarize_season_series(
            _schedule(
                _game("R", "Final", BREWERS),
                _game("D", "Final", BREWERS),
                _game("D", "Final", BREWERS),
            ),
            away_id=BREWERS,
            home_id=PADRES,
        )

        assert series["games_played"] == 1

    def test_excludes_spring_training_games(self):
        series = summarize_season_series(
            _schedule(_game("R", "Final", BREWERS), _game("S", "Final", PADRES)),
            away_id=BREWERS,
            home_id=PADRES,
        )

        assert series["games_played"] == 1

    def test_ignores_games_still_in_progress(self):
        series = summarize_season_series(
            _schedule(
                _game("R", "Final", BREWERS),
                _game("R", "In Progress", PADRES),
            ),
            away_id=BREWERS,
            home_id=PADRES,
        )

        assert series["games_played"] == 1

    def test_reports_no_leader_when_the_series_is_split(self):
        series = summarize_season_series(
            _schedule(_game("R", "Final", BREWERS), _game("R", "Final", PADRES)),
            away_id=BREWERS,
            home_id=PADRES,
        )

        assert series["leader_team_id"] is None

    def test_returns_none_when_they_have_not_met(self):
        assert (
            summarize_season_series(_schedule(), away_id=BREWERS, home_id=PADRES)
            is None
        )


class TestSeasonSeriesShape:
    """
    The facts that stop every matchup reading the same way.

    A 3-3 split says nothing on its own. Whether those six games were
    one-run thrillers or blowouts is the part worth printing.
    """

    def _six_game_series(self):
        # The real 2025 Brewers/Padres series. June was played in
        # Milwaukee and September in San Diego, so the home slot flips
        # halfway through — scores must follow the clubs, not the slots.
        return _schedule(
            _game("R", "Final", PADRES, 0, 2, date="2025-06-06", hosted_by=BREWERS),
            _game("R", "Final", BREWERS, 4, 3, date="2025-06-07", hosted_by=BREWERS),
            _game("R", "Final", PADRES, 0, 1, date="2025-06-08", hosted_by=BREWERS),
            _game(
                "R", "Final", PADRES, 4, 5, date="2025-09-22", innings=11
            ),
            _game("R", "Final", PADRES, 0, 7, date="2025-09-23"),
            _game("R", "Final", BREWERS, 3, 1, date="2025-09-24"),
        )

    def test_counts_wins_by_club_not_by_home_slot(self):
        series = summarize_season_series(
            self._six_game_series(), away_id=BREWERS, home_id=PADRES
        )

        assert series["away_wins"] == 2
        assert series["home_wins"] == 4

    def test_totals_runs_for_each_club_across_both_ballparks(self):
        series = summarize_season_series(
            self._six_game_series(), away_id=BREWERS, home_id=PADRES
        )

        # Attributing by slot instead of by club would give 13/17 here.
        assert series["away_runs"] == 11
        assert series["home_runs"] == 19

    def test_counts_one_run_games(self):
        series = summarize_season_series(
            self._six_game_series(), away_id=BREWERS, home_id=PADRES
        )

        # 4-3, 0-1 and 4-5 were each decided by a single run.
        assert series["one_run_games"] == 3

    def test_counts_shutouts(self):
        series = summarize_season_series(
            self._six_game_series(), away_id=BREWERS, home_id=PADRES
        )

        # 0-2, 0-1 and 0-7.
        assert series["shutouts"] == 3

    def test_counts_extra_inning_games(self):
        series = summarize_season_series(
            self._six_game_series(), away_id=BREWERS, home_id=PADRES
        )

        assert series["extra_inning_games"] == 1

    def test_identifies_the_most_lopsided_meeting(self):
        series = summarize_season_series(
            self._six_game_series(), away_id=BREWERS, home_id=PADRES
        )

        assert series["largest_margin"]["margin"] == 7
        assert series["largest_margin"]["date"] == "2025-09-23"
        assert series["largest_margin"]["winner_team_id"] == PADRES

    def test_reports_the_most_recent_meeting(self):
        series = summarize_season_series(
            self._six_game_series(), away_id=BREWERS, home_id=PADRES
        )

        assert series["last_meeting"]["date"] == "2025-09-24"
        assert series["last_meeting"]["winner_team_id"] == BREWERS
        assert series["last_meeting"]["away_team_score"] == 3
        assert series["last_meeting"]["home_team_score"] == 1

    def test_records_which_club_hosted_each_meeting(self):
        series = summarize_season_series(
            self._six_game_series(), away_id=BREWERS, home_id=PADRES
        )

        assert series["last_meeting"]["hosted_by_team_id"] == PADRES

    def test_orders_meetings_by_date_regardless_of_payload_order(self):
        # A rescheduled game can arrive out of sequence.
        series = summarize_season_series(
            _schedule(
                _game("R", "Final", BREWERS, 3, 1, date="2025-09-24"),
                _game("R", "Final", PADRES, 2, 5, date="2025-06-06"),
            ),
            away_id=BREWERS,
            home_id=PADRES,
        )

        assert series["last_meeting"]["date"] == "2025-09-24"

    def test_survives_games_with_no_score_recorded(self):
        # Wins still tally; run-derived facts simply skip the bad row.
        series = summarize_season_series(
            _schedule(
                _game("R", "Final", BREWERS, 2, 0, date="2025-06-06"),
                _game("R", "Final", PADRES, None, None, date="2025-06-07"),
            ),
            away_id=BREWERS,
            home_id=PADRES,
        )

        assert series["games_played"] == 2
        assert series["away_runs"] == 2
        assert series["shutouts"] == 1

    def test_carries_the_winning_pitcher_when_present(self):
        series = summarize_season_series(
            _schedule(
                _game(
                    "R",
                    "Final",
                    BREWERS,
                    3,
                    1,
                    date="2025-09-24",
                    winning_pitcher="Aaron Ashby",
                )
            ),
            away_id=BREWERS,
            home_id=PADRES,
        )

        assert series["last_meeting"]["winning_pitcher"] == "Aaron Ashby"

    def test_a_blowout_series_looks_different_from_a_tight_one(self):
        # Same 1-1 record, opposite character. This is the whole point.
        tight = summarize_season_series(
            _schedule(
                _game("R", "Final", BREWERS, 2, 1, date="2025-06-06"),
                _game("R", "Final", PADRES, 1, 2, date="2025-06-07"),
            ),
            away_id=BREWERS,
            home_id=PADRES,
        )
        blowout = summarize_season_series(
            _schedule(
                _game("R", "Final", BREWERS, 11, 1, date="2025-06-06"),
                _game("R", "Final", PADRES, 0, 9, date="2025-06-07"),
            ),
            away_id=BREWERS,
            home_id=PADRES,
        )

        assert tight["away_wins"] == blowout["away_wins"]
        assert tight["one_run_games"] == 2
        assert blowout["one_run_games"] == 0
        assert blowout["largest_margin"]["margin"] == 10


class TestBuildTrivia:
    def test_assembles_both_sides_and_the_series(self):
        trivia = build_trivia(
            _standings(
                _team_record(BREWERS, "Brewers", "NL Central"),
                _team_record(PADRES, "Padres", "NL West", wins=91, losses=71),
            ),
            _schedule(_game("R", "Final", PADRES)),
            away_id=BREWERS,
            home_id=PADRES,
        )

        assert trivia["teams"]["away"]["wins"] == 103
        assert trivia["teams"]["home"]["wins"] == 91
        assert trivia["season_series"]["home_wins"] == 1

    def test_reports_a_missing_team_as_none(self):
        trivia = build_trivia(
            _standings(_team_record(BREWERS, "Brewers", "NL Central")),
            _schedule(),
            away_id=BREWERS,
            home_id=PADRES,
        )

        assert trivia["teams"]["away"] is not None
        assert trivia["teams"]["home"] is None
        assert trivia["season_series"] is None
