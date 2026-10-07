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


def _game(game_type, state, winner_id):
    def side(team_id):
        return {"team": {"id": team_id}, "isWinner": team_id == winner_id}

    return {
        "gameType": game_type,
        "status": {"abstractGameState": state},
        "teams": {"away": side(BREWERS), "home": side(PADRES)},
    }


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
