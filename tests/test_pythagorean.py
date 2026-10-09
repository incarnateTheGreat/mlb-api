"""
Tests for Pythagorean expectation and the standings enrichment around it.

Figures are from the real 2025 season, so a wrong exponent or a transposed
run total shows up as a record nobody recognises.
"""

from app.routers.standings import enrich_with_pythagorean
from app.services.sabermetrics import calculate_pythagorean_record


def _team_record(**overrides):
    record = {
        "name": "Toronto Blue Jays",
        "wins": 94,
        "losses": 68,
        "runsScored": 798,
        "runsAllowed": 721,
    }
    record.update(overrides)
    return record


def _standings(*team_records):
    return {"records": [{"teamRecords": list(team_records)}]}


class TestPythagoreanRecord:
    def test_matches_the_published_expected_record(self):
        # Toronto went 94-68 on 798 runs scored and 721 allowed. MLB's own
        # expected record for that season was 88-74.
        expected = calculate_pythagorean_record(798, 721, 94, 68)

        assert expected["record"] == "88-74"

    def test_reports_the_gap_between_record_and_runs(self):
        expected = calculate_pythagorean_record(798, 721, 94, 68)

        # Six wins more than the run totals account for.
        assert expected["luck"] == 6

    def test_marks_a_club_that_underperformed_its_runs(self):
        # Atlanta in 2025: outscored their opponents and still finished under
        # .500, the clearest case for showing this at all.
        expected = calculate_pythagorean_record(711, 736, 76, 86)

        assert expected["luck"] < 0

    def test_expected_wins_and_losses_cover_every_game(self):
        expected = calculate_pythagorean_record(798, 721, 94, 68)

        assert expected["wins"] + expected["losses"] == 162

    def test_a_club_that_outscores_everyone_is_expected_to_win_more(self):
        strong = calculate_pythagorean_record(900, 600, 81, 81)
        weak = calculate_pythagorean_record(600, 900, 81, 81)

        assert strong["pct"] > 0.5 > weak["pct"]

    def test_even_run_totals_expect_an_even_record(self):
        expected = calculate_pythagorean_record(700, 700, 81, 81)

        assert expected["pct"] == 0.5
        assert expected["record"] == "81-81"

    def test_weights_the_margin_sublinearly(self):
        # The point of the 1.83 exponent: a 300-run edge is worth less than
        # squaring the totals would suggest, which is why a club can beat
        # its expectation without being lucky in any mystical sense.
        expected = calculate_pythagorean_record(900, 600, 81, 81)

        assert expected["pct"] < 900**2 / (900**2 + 600**2)

    def test_returns_nothing_before_a_game_is_played(self):
        assert calculate_pythagorean_record(0, 0, 0, 0) is None

    def test_returns_nothing_when_no_runs_were_recorded(self):
        assert calculate_pythagorean_record(0, 0, 5, 5) is None

    def test_handles_a_club_that_has_not_scored(self):
        expected = calculate_pythagorean_record(0, 12, 0, 3)

        assert expected["pct"] == 0.0
        assert expected["record"] == "0-3"


class TestStandingsEnrichment:
    def test_attaches_an_expected_record_to_each_club(self):
        standings = _standings(_team_record(), _team_record(name="New York Yankees"))
        enrich_with_pythagorean(standings)

        for team_record in standings["records"][0]["teamRecords"]:
            assert team_record["pythagorean"]["record"] == "88-74"

    def test_coerces_the_string_figures_the_feed_sometimes_sends(self):
        standings = _standings(_team_record(wins="94", runsScored="798"))
        enrich_with_pythagorean(standings)

        assert standings["records"][0]["teamRecords"][0]["pythagorean"]["luck"] == 6

    def test_leaves_a_record_without_run_totals_alone(self):
        standings = _standings(_team_record(runsScored=None, runsAllowed=None))
        enrich_with_pythagorean(standings)

        assert standings["records"][0]["teamRecords"][0]["pythagorean"] is None

    def test_survives_a_payload_with_no_records(self):
        standings = {}
        enrich_with_pythagorean(standings)

        assert standings == {}

    def test_can_run_twice_over_a_cached_payload(self):
        standings = _standings(_team_record())
        enrich_with_pythagorean(standings)
        first = standings["records"][0]["teamRecords"][0]["pythagorean"]
        enrich_with_pythagorean(standings)

        assert standings["records"][0]["teamRecords"][0]["pythagorean"] == first
