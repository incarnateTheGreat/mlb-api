"""
Tests for the club leader board.

The payload fragments mirror the real 2025 Brewers and Yankees responses,
because the thing most likely to go wrong here is reading the right
category from the wrong stat group — and that only shows up when the
fixture carries all three groups the way the live endpoint does.
"""

from app.services.game_trivia import build_trivia, summarize_leaders


def _group(category, stat_group, *leaders):
    return {
        "leaderCategory": category,
        "statGroup": stat_group,
        "leaders": [
            {
                "rank": rank,
                "value": value,
                "person": {"id": player_id, "fullName": name},
            }
            for rank, player_id, name, value in leaders
        ],
    }


def _yankees_board():
    """
    The real shape: every category repeated for every stat group.

    Judge's 160 strikeouts are how often he struck out; Wells' 133 home
    runs were hit off the pitchers he caught. Both sit under the same
    category name as the figures actually wanted.
    """
    return [
        _group("homeRuns", "hitting", (1, 592450, "Aaron Judge", "53")),
        _group("homeRuns", "pitching", (1, 607074, "Carlos Rodón", "22")),
        _group("homeRuns", "catching", (1, 669224, "Austin Wells", "133")),
        _group("battingAverage", "hitting", (1, 592450, "Aaron Judge", ".331")),
        _group("battingAverage", "pitching", (1, 607074, "Carlos Rodón", ".188")),
        _group("runsBattedIn", "hitting", (1, 592450, "Aaron Judge", "114")),
        _group("strikeouts", "hitting", (1, 592450, "Aaron Judge", "160")),
        _group("strikeouts", "pitching", (1, 607074, "Carlos Rodón", "203")),
        _group("strikeouts", "catching", (1, 669224, "Austin Wells", "997")),
        _group("earnedRunAverage", "pitching", (1, 608331, "Max Fried", "2.86")),
        _group("earnedRunAverage", "catching", (1, 669224, "Austin Wells", "4.18")),
        _group("saves", "pitching", (1, 642207, "Devin Williams", "18")),
    ]


def _by_label(leaders):
    return {leader["label"]: leader for leader in leaders}


class TestSummarizeLeaders:
    def test_reads_batting_categories_from_the_hitting_group(self):
        leaders = _by_label(summarize_leaders(_yankees_board()))

        assert leaders["HR"]["name"] == "Aaron Judge"
        assert leaders["HR"]["value"] == "53"

    def test_does_not_credit_a_pitcher_with_the_home_runs_he_allowed(self):
        # Carlos Rodón gave up 22. Reading the category without checking
        # the stat group would have him leading the club in home runs —
        # he does lead it in strikeouts, so the check has to be specific.
        leaders = _by_label(summarize_leaders(_yankees_board()))

        assert leaders["HR"]["name"] != "Carlos Rodón"
        assert leaders["HR"]["value"] != "22"

    def test_does_not_credit_a_batter_with_striking_people_out(self):
        # Judge struck out 160 times; Rodón struck out 203 batters.
        leaders = _by_label(summarize_leaders(_yankees_board()))

        assert leaders["K"]["name"] == "Carlos Rodón"
        assert leaders["K"]["value"] == "203"

    def test_ignores_the_catching_group_entirely(self):
        # Austin Wells' 133 "home runs" and 997 "strikeouts" are the
        # totals of the pitchers he caught.
        leaders = summarize_leaders(_yankees_board())

        assert all(leader["name"] != "Austin Wells" for leader in leaders)

    def test_reads_era_from_the_pitching_group(self):
        leaders = _by_label(summarize_leaders(_yankees_board()))

        assert leaders["ERA"]["name"] == "Max Fried"
        assert leaders["ERA"]["value"] == "2.86"

    def test_keeps_the_closer(self):
        leaders = _by_label(summarize_leaders(_yankees_board()))

        assert leaders["SV"]["name"] == "Devin Williams"

    def test_leads_with_the_bat(self):
        labels = [leader["label"] for leader in summarize_leaders(_yankees_board())]

        assert labels == ["HR", "RBI", "AVG", "K", "ERA", "SV"]

    def test_carries_the_player_id_for_linking(self):
        leaders = _by_label(summarize_leaders(_yankees_board()))

        assert leaders["HR"]["player_id"] == 592450

    def test_leaves_the_value_formatted_as_the_sport_writes_it(self):
        # ".331" and "2.86" — parsing to a float would drop the convention
        # of omitting the leading zero on a rate.
        leaders = _by_label(summarize_leaders(_yankees_board()))

        assert leaders["AVG"]["value"] == ".331"
        assert leaders["ERA"]["value"] == "2.86"

    def test_drops_a_category_the_club_is_tied_in(self):
        # Frelick and Turang both hit .288 for the 2025 Brewers. There is
        # no honest way to name one of them "the leader".
        board = [
            _group(
                "battingAverage",
                "hitting",
                (1, 670712, "Sal Frelick", ".288"),
                (1, 668930, "Brice Turang", ".288"),
            )
        ]

        assert summarize_leaders(board) == []

    def test_keeps_a_category_where_only_the_runner_up_is_shared(self):
        board = [
            _group(
                "homeRuns",
                "hitting",
                (1, 592885, "Christian Yelich", "29"),
                (2, 694192, "Jackson Chourio", "21"),
                (2, 668930, "Brice Turang", "21"),
            )
        ]

        assert summarize_leaders(board)[0]["name"] == "Christian Yelich"

    def test_ignores_categories_it_was_not_asked_about(self):
        board = [_group("stolenBases", "hitting", (1, 668930, "Brice Turang", "41"))]

        assert summarize_leaders(board) == []

    def test_survives_a_group_with_no_leaders(self):
        board = [_group("homeRuns", "hitting")]

        assert summarize_leaders(board) == []

    def test_survives_a_leader_with_no_name(self):
        board = [{"leaderCategory": "homeRuns", "statGroup": "hitting",
                  "leaders": [{"rank": 1, "value": "53", "person": {}}]}]

        assert summarize_leaders(board) == []

    def test_survives_an_empty_board(self):
        assert summarize_leaders([]) == []


class TestLeadersInTrivia:
    def _standings(self):
        return {
            "records": [
                {
                    "teamRecords": [
                        {
                            "team": {"id": 147, "name": "Yankees", "division": {}},
                            "wins": 94,
                            "losses": 68,
                        },
                        {
                            "team": {"id": 139, "name": "Rays", "division": {}},
                            "wins": 98,
                            "losses": 64,
                        },
                    ]
                }
            ]
        }

    def test_attaches_each_board_to_its_own_club(self):
        trivia = build_trivia(
            self._standings(),
            {"dates": []},
            away_id=139,
            home_id=147,
            home_leaders=_yankees_board(),
        )

        assert trivia["teams"]["home"]["leaders"][0]["name"] == "Aaron Judge"
        assert trivia["teams"]["away"]["leaders"] == []

    def test_still_builds_the_block_without_any_leaders(self):
        # The leader requests are allowed to fail on their own.
        trivia = build_trivia(
            self._standings(), {"dates": []}, away_id=139, home_id=147
        )

        assert trivia["teams"]["home"]["leaders"] == []
        assert trivia["teams"]["home"]["wins"] == 94
