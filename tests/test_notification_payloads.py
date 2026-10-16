"""
Test notification payload formatting (scoring plays and finals).

Validates that the new notification format includes:
- Score in the title
- Inning information
- No truncation for titles
- Description truncation
- Final game notifications
"""

from unittest.mock import AsyncMock, Mock, patch

import logging
import sys

import pytest

from app.routers.notifications import classify_device
from app.services import push_service
from app.services import scoring_watcher
from app.services.scoring_watcher import (
    _build_final_payload,
    _build_payload,
    _extract_team_ids,
    _handle_final,
    _ordinal,
)
from app.services.team_icons import matchup_icon_url, team_logo_url


class TestPushUrgency:
    """Every push must go out at high urgency.

    Android defers normal-urgency messages while the device is in Doze, which
    delays alerts on a locked phone until the user reopens the app.
    """

    def test_send_sync_sets_high_urgency(self):
        subscription_info = {
            "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
            "keys": {"p256dh": "key", "auth": "auth"},
        }

        with patch.object(push_service, "webpush") as mock_webpush:
            push_service._send_sync(subscription_info, '{"title": "test"}')

        headers = mock_webpush.call_args.kwargs["headers"]

        assert headers["Urgency"] == "high"


class TestOrdinal:
    """Test ordinal suffix generation."""

    def test_ordinal_first(self):
        assert _ordinal(1) == "1st"

    def test_ordinal_second(self):
        assert _ordinal(2) == "2nd"

    def test_ordinal_third(self):
        assert _ordinal(3) == "3rd"

    def test_ordinal_fourth(self):
        assert _ordinal(4) == "4th"

    def test_ordinal_eleventh(self):
        assert _ordinal(11) == "11th"

    def test_ordinal_twelfth(self):
        assert _ordinal(12) == "12th"

    def test_ordinal_thirteenth(self):
        assert _ordinal(13) == "13th"

    def test_ordinal_twenty_first(self):
        assert _ordinal(21) == "21st"


class TestBuildScoringPlayPayload:
    """Test scoring play notification formatting."""

    def test_top_of_inning_away_scores(self):
        """Away team scores in top of inning."""
        play = {
            "about": {"inning": 5, "isTopInning": True},
            "result": {"description": "Single to left field", "awayScore": 4, "homeScore": 2},
            "atBatIndex": 42,
        }
        payload = _build_payload(play, game_pk=123, away_abbr="PIT", home_abbr="CHI")

        assert payload["title"] == "PIT scores! PIT 4 - CHI 2"
        assert payload["body"] == "Top 5th · Single to left field"
        assert payload["gamePk"] == 123
        assert payload["atBatIndex"] == 42
        # No device given, so the alert must not ask to persist. See
        # TestPersistentAlertByDevice.
        assert payload["requireInteraction"] is False

    def test_bottom_of_inning_home_scores(self):
        """Home team scores in bottom of inning."""
        play = {
            "about": {"inning": 3, "isTopInning": False},
            "result": {"description": "Homer to left", "awayScore": 1, "homeScore": 2},
            "atBatIndex": 15,
        }
        payload = _build_payload(play, game_pk=456, away_abbr="NYY", home_abbr="BOS")

        assert payload["title"] == "BOS scores! NYY 1 - BOS 2"
        assert payload["body"] == "Bot 3rd · Homer to left"
        assert payload["requireInteraction"] is False

    def test_description_truncation(self):
        """Long descriptions get truncated to 120 chars."""
        long_desc = "A" * 150  # Much longer than 120 chars
        play = {
            "about": {"inning": 1, "isTopInning": True},
            "result": {"description": long_desc, "awayScore": 1, "homeScore": 0},
            "atBatIndex": 1,
        }
        payload = _build_payload(play, game_pk=789, away_abbr="LAD", home_abbr="SFG")

        # Body should be truncated
        parts = payload["body"].split(" · ")
        description_part = parts[1]
        assert len(description_part) == 120  # Exactly 120 chars (117 + "...")

    def test_no_truncation_for_title(self):
        """Title with score is always complete (no truncation)."""
        play = {
            "about": {"inning": 9, "isTopInning": False},
            "result": {"description": "Some play happened", "awayScore": 5, "homeScore": 4},
            "atBatIndex": 200,
        }
        payload = _build_payload(play, game_pk=999, away_abbr="TB", home_abbr="NYM")

        # Title should always be complete with score
        assert "TB 5 - NYM 4" in payload["title"]
        assert payload["title"].startswith("NYM scores!")

    def test_missing_optional_fields(self):
        """Handles missing optional fields gracefully."""
        play = {
            "about": {},  # No inning, isTopInning
            "result": {"awayScore": 0, "homeScore": 0},
            "atBatIndex": 0,
        }
        payload = _build_payload(play, game_pk=111, away_abbr="MIL", home_abbr="PHI")

        # Should default to Top 0th and "Run scored"
        assert "scores!" in payload["title"]
        assert "Top" in payload["body"]


class TestBuildFinalPayload:
    """Test game-final notification formatting."""

    def test_final_notification(self):
        """Final game notification shows score."""
        feed = {
            "liveData": {
                "linescore": {
                    "teams": {
                        "away": {"runs": 5},
                        "home": {"runs": 3},
                    }
                }
            }
        }
        payload = _build_final_payload(feed, game_pk=555, away_abbr="ATL", home_abbr="WSH")

        assert payload["title"] == "Final"
        assert payload["body"] == "ATL 5 – WSH 3"
        assert payload["gamePk"] == 555
        assert payload["atBatIndex"] == -1
        assert payload["requireInteraction"] is False

    def test_final_missing_runs(self):
        """Handles missing run data in final payload."""
        feed = {"liveData": {"linescore": {"teams": {}}}}
        payload = _build_final_payload(feed, game_pk=666, away_abbr="HOU", home_abbr="SEA")

        assert payload["title"] == "Final"
        assert payload["body"] == "HOU 0 – SEA 0"  # Defaults to 0 runs


class TestNotificationIcon:
    """The icon identifies the team at a glance.

    Android renders `icon` as the notification's large icon. iOS and macOS
    Safari ignore it and fall back to the manifest icon, so every case here
    must also be safe when the field is simply dropped.
    """

    def test_extract_team_ids(self):
        feed = {"gameData": {"teams": {"away": {"id": 134}, "home": {"id": 112}}}}

        assert _extract_team_ids(feed) == (134, 112)

    def test_extract_team_ids_missing(self):
        assert _extract_team_ids({}) == (None, None)

    def test_team_logo_url_none(self):
        assert team_logo_url(None) is None

    def test_team_logo_url_shape(self):
        """
        Points at MLB's CDN for the given team.

        The size is deliberately not pinned here - it is derived from the
        canvas so the logo is never upscaled, and tests/test_team_icons.py
        covers that. Hard-coding it again would just duplicate the constant.
        """
        assert team_logo_url(134).startswith(
            "https://midfield.mlbstatic.com/v1/team/134/spots/"
        )

    def test_matchup_icon_url_shape(self):
        """Back team first, front team second - the front one is drawn on top."""
        assert (
            matchup_icon_url(134, 112)
            == "/api/notifications/matchup-icon/134/112.png"
        )

    def test_matchup_icon_url_is_relative(self):
        """The service worker resolves this against the frontend origin, where
        `/api/*` proxies to this API. An absolute URL would need the API's
        public hostname as another environment variable."""
        assert matchup_icon_url(134, 112).startswith("/api/")

    def test_matchup_icon_url_falls_back_to_single_logo(self):
        """One known ID still beats no icon at all."""
        assert matchup_icon_url(None, 112) == team_logo_url(112)
        assert matchup_icon_url(134, None) == team_logo_url(134)

    def test_matchup_icon_url_none_when_both_unknown(self):
        assert matchup_icon_url(None, None) is None

    def test_top_inning_puts_away_logo_in_front(self):
        """Away team bats in the top, so the away logo leads the pair."""
        play = {
            "about": {"inning": 5, "isTopInning": True},
            "result": {"description": "Single", "awayScore": 4, "homeScore": 2},
            "atBatIndex": 42,
        }
        payload = _build_payload(
            play,
            game_pk=123,
            away_abbr="PIT",
            home_abbr="CHC",
            away_id=134,
            home_id=112,
        )

        assert payload["icon"] == matchup_icon_url(112, 134)

    def test_bottom_inning_puts_home_logo_in_front(self):
        play = {
            "about": {"inning": 3, "isTopInning": False},
            "result": {"description": "Homer", "awayScore": 1, "homeScore": 2},
            "atBatIndex": 15,
        }
        payload = _build_payload(
            play,
            game_pk=456,
            away_abbr="PIT",
            home_abbr="CHC",
            away_id=134,
            home_id=112,
        )

        assert payload["icon"] == matchup_icon_url(134, 112)

    def test_icon_omitted_when_team_ids_unknown(self):
        """No ID in the feed means no icon key at all, not a null."""
        play = {
            "about": {"inning": 1, "isTopInning": True},
            "result": {"description": "Single", "awayScore": 1, "homeScore": 0},
            "atBatIndex": 1,
        }
        payload = _build_payload(play, game_pk=1, away_abbr="LAD", home_abbr="SFG")

        assert "icon" not in payload

    def test_icon_falls_back_to_single_logo_with_one_id(self):
        """A half-populated feed still gets the scoring team's logo."""
        play = {
            "about": {"inning": 1, "isTopInning": True},
            "result": {"description": "Single", "awayScore": 1, "homeScore": 0},
            "atBatIndex": 1,
        }
        payload = _build_payload(
            play,
            game_pk=1,
            away_abbr="LAD",
            home_abbr="SFG",
            away_id=119,
        )

        assert payload["icon"] == team_logo_url(119)

    def test_final_puts_winner_in_front_when_away_wins(self):
        payload = _build_final_payload(
            _feed_with_score(away=5, home=3),
            game_pk=555,
            away_abbr="ATL",
            home_abbr="WSH",
            away_id=144,
            home_id=120,
        )

        assert payload["icon"] == matchup_icon_url(120, 144)

    def test_final_puts_winner_in_front_when_home_wins(self):
        payload = _build_final_payload(
            _feed_with_score(away=2, home=6),
            game_pk=555,
            away_abbr="ATL",
            home_abbr="WSH",
            away_id=144,
            home_id=120,
        )

        assert payload["icon"] == matchup_icon_url(144, 120)

    def test_final_icon_omitted_when_team_ids_unknown(self):
        payload = _build_final_payload(
            _feed_with_score(), game_pk=555, away_abbr="ATL", home_abbr="WSH"
        )

        assert "icon" not in payload


def _subscription(endpoint: str = "https://push.example/abc"):
    """A stand-in for a PushSubscription row."""
    return Mock(endpoint=endpoint, p256dh="p", auth="a", game_pk=1)

def _feed_with_score(away: int = 5, home: int = 3) -> dict:
    return {
        "liveData": {
            "linescore": {"teams": {"away": {"runs": away}, "home": {"runs": home}}}
        }
    }


class TestHandleFinal:
    """Ending a game must notify exactly once, and only for real endings."""

    def setup_method(self):
        scoring_watcher._final_attempts.clear()

    @pytest.mark.parametrize("coded_state", ["O", "F"])
    async def test_genuine_final_notifies_and_clears(self, coded_state):
        db = AsyncMock()
        subscription = _subscription()

        with patch.object(
            scoring_watcher.push_service, "send_push", AsyncMock(return_value="sent")
        ) as send:
            await _handle_final(
                db,
                1,
                _feed_with_score(),
                {"codedGameState": coded_state, "detailedState": "Final"},
                [subscription],
                "PIT",
                "CHI",
            )

        assert send.await_count == 1
        db.delete.assert_awaited_once_with(subscription)

    @pytest.mark.parametrize(
        ("coded_state", "detailed"),
        [("D", "Postponed"), ("C", "Cancelled"), ("Q", "Forfeit")],
    )
    async def test_non_result_endings_do_not_notify(self, coded_state, detailed):
        """Postponed/cancelled games report Final but have no score to push."""
        db = AsyncMock()
        subscription = _subscription()

        with patch.object(
            scoring_watcher.push_service, "send_push", AsyncMock(return_value="sent")
        ) as send:
            await _handle_final(
                db,
                1,
                _feed_with_score(0, 0),
                {"codedGameState": coded_state, "detailedState": detailed},
                [subscription],
                "PIT",
                "CHI",
            )

        assert send.await_count == 0
        # Still stop tracking - the game is not coming back.
        db.delete.assert_awaited_once_with(subscription)

    async def test_failed_send_keeps_subscription_for_retry(self):
        db = AsyncMock()
        subscription = _subscription()

        with patch.object(
            scoring_watcher.push_service, "send_push", AsyncMock(return_value="failed")
        ):
            await _handle_final(
                db,
                1,
                _feed_with_score(),
                {"codedGameState": "F", "detailedState": "Final"},
                [subscription],
                "PIT",
                "CHI",
            )

        db.delete.assert_not_awaited()
        assert scoring_watcher._final_attempts[1] == 1

    async def test_gives_up_after_max_attempts(self):
        db = AsyncMock()
        subscription = _subscription()
        scoring_watcher._final_attempts[1] = (
            scoring_watcher.MAX_FINAL_SEND_ATTEMPTS - 1
        )

        with patch.object(
            scoring_watcher.push_service, "send_push", AsyncMock(return_value="failed")
        ):
            await _handle_final(
                db,
                1,
                _feed_with_score(),
                {"codedGameState": "F", "detailedState": "Final"},
                [subscription],
                "PIT",
                "CHI",
            )

        # Stop retrying so the watcher does not poll a finished game forever.
        db.delete.assert_awaited_once_with(subscription)
        assert 1 not in scoring_watcher._final_attempts

    async def test_expired_subscription_is_dropped_not_retried(self):
        db = AsyncMock()
        subscription = _subscription()

        with patch.object(
            scoring_watcher.push_service, "send_push", AsyncMock(return_value="expired")
        ):
            await _handle_final(
                db,
                1,
                _feed_with_score(),
                {"codedGameState": "O", "detailedState": "Game Over"},
                [subscription],
                "PIT",
                "CHI",
            )

        db.delete.assert_awaited_once_with(subscription)
        assert 1 not in scoring_watcher._final_attempts


class TestIntegrationNotificationFormat:
    """Integration tests for realistic game scenarios."""

    def test_realistic_scoring_sequence(self):
        """Simulates a realistic sequence of scoring plays."""
        plays = [
            {
                "about": {"inning": 1, "isTopInning": True},
                "result": {
                    "description": "Ronny Simon scores on a sac fly to center field",
                    "awayScore": 1,
                    "homeScore": 0,
                },
                "atBatIndex": 5,
            },
            {
                "about": {"inning": 3, "isTopInning": False},
                "result": {
                    "description": "Mitch Garver homers",
                    "awayScore": 1,
                    "homeScore": 1,
                },
                "atBatIndex": 20,
            },
            {
                "about": {"inning": 5, "isTopInning": True},
                "result": {
                    "description": "Spencer Horwitz scores on a sac fly to center field",
                    "awayScore": 2,
                    "homeScore": 1,
                },
                "atBatIndex": 35,
            },
        ]

        for play in plays:
            payload = _build_payload(play, game_pk=999, away_abbr="PIT", home_abbr="CHI")

            # All should have proper format
            assert "PIT" in payload["title"]
            assert "CHI" in payload["title"]
            assert "scores!" in payload["title"]
            assert "-" in payload["title"]  # Score separator
            # Check for ordinal suffix (st, nd, rd, or th)
            assert any(suffix in payload["body"] for suffix in ["st", "nd", "rd", "th"])


def _wild_pitch_play(at_bat_index: int = 60, complete: bool = False) -> dict:
    """A real shape: the run scores on a wild pitch, the at-bat ends in a K.

    Taken from game 849811, where this produced the notification
    "MIL scores!" / "Christian Yelich strikes out swinging."
    """
    return {
        "atBatIndex": at_bat_index,
        "about": {
            "inning": 8,
            "isTopInning": True,
            # False even once complete: the plate appearance was not the
            # scoring event.
            "isScoringPlay": False,
            "isComplete": complete,
        },
        "result": {
            "awayScore": 5,
            "homeScore": 2,
            # Absent while the at-bat is still in progress.
            **(
                {"description": "Christian Yelich strikes out swinging."}
                if complete
                else {}
            ),
        },
        "runners": [
            {
                "movement": {"start": "3B", "end": "score"},
                "details": {"event": "Wild Pitch", "isScoringEvent": True},
            }
        ],
        "playEvents": [
            {"details": {"event": "Mound Visit", "isScoringPlay": False}},
            {
                "details": {
                    "isScoringPlay": True,
                    "description": (
                        "Wild pitch by pitcher Justin Wrobleski. "
                        "Brice Turang scores. William Contreras to 2nd."
                    ),
                }
            },
        ],
    }


def _live_feed(plays: list[dict], scoring_plays: list[int]) -> dict:
    """A minimal in-progress feed wrapping the given plays."""
    return {
        "gameData": {
            "status": {"abstractGameState": "Live", "codedGameState": "I"},
            "teams": {
                "away": {"abbreviation": "MIL", "id": 158},
                "home": {"abbreviation": "LAD", "id": 119},
            },
        },
        "liveData": {
            "plays": {
                "allPlays": plays,
                "scoringPlays": scoring_plays,
                "currentPlay": plays[-1] if plays else {},
            }
        },
    }


def _live_subscription(cursor: int, game_pk: int = 1):
    return Mock(
        endpoint="https://push.example/abc",
        p256dh="p",
        auth="a",
        game_pk=game_pk,
        last_at_bat_index=cursor,
    )


class TestRunScoredDetection:
    """A run is a run however it got there."""

    def test_wild_pitch_run_is_detected_before_the_at_bat_ends(self):
        assert scoring_watcher._has_run_scored(_wild_pitch_play()) is True

    def test_at_bat_with_no_runner_reaching_home_is_not_scoring(self):
        play = {
            "runners": [
                {"movement": {"start": "1B", "end": "2B"}, "details": {}},
                {"movement": {"start": None, "end": "1B"}, "details": {}},
            ]
        }

        assert scoring_watcher._has_run_scored(play) is False

    def test_play_without_runners_is_not_scoring(self):
        assert scoring_watcher._has_run_scored({}) is False


class TestScoringDescription:
    """The body must narrate the RUN, not merely the plate appearance."""

    def test_wild_pitch_run_is_described_by_its_play_event(self):
        description = scoring_watcher._scoring_description(_wild_pitch_play())

        assert "Wild pitch" in description
        assert "Brice Turang scores" in description

    def test_completed_strikeout_still_describes_the_run(self):
        """The regression: once complete, `result.description` is a strikeout."""
        play = _wild_pitch_play(complete=True)

        description = scoring_watcher._scoring_description(play)

        assert "strikes out" not in description
        assert "Brice Turang scores" in description

    def test_batter_driven_run_uses_the_at_bat_description(self):
        play = {
            "about": {"isScoringPlay": True},
            "result": {"description": "Garrett Mitchell homers (1)."},
            "playEvents": [],
        }

        assert (
            scoring_watcher._scoring_description(play)
            == "Garrett Mitchell homers (1)."
        )

    def test_latest_scoring_event_wins_when_several_runs_score(self):
        play = {
            "about": {"isScoringPlay": False},
            "result": {},
            "playEvents": [
                {"details": {"isScoringPlay": True, "description": "First run."}},
                {"details": {"isScoringPlay": True, "description": "Second run."}},
            ],
        }

        assert scoring_watcher._scoring_description(play) == "Second run."

    def test_falls_back_rather_than_announcing_nothing(self):
        assert scoring_watcher._scoring_description({}) == "Run scored"


class TestInFlightScoringPlay:
    """A run must be announced when it scores, not when the at-bat ends.

    `scoringPlays` is keyed by completed at-bat, so relying on it alone left
    a measured 46s silence after a wild pitch - long enough for a subscriber
    to see the run on TV and conclude notifications were broken.
    """

    async def test_run_is_announced_while_the_at_bat_is_still_in_progress(self):
        play = _wild_pitch_play(complete=False)
        feed = _live_feed([play], scoring_plays=[])
        subscription = _live_subscription(cursor=50)

        db = AsyncMock()
        db.scalars = AsyncMock(return_value=[subscription])

        with patch.object(
            scoring_watcher,
            "get_mlb_client",
            return_value=Mock(get_game_feed_raw=AsyncMock(return_value=feed)),
        ):
            with patch.object(
                scoring_watcher.push_service,
                "send_push",
                AsyncMock(return_value="sent"),
            ) as send:
                await scoring_watcher._process_game(db, 1)

        assert send.await_count == 1
        assert "Wild pitch" in send.await_args.kwargs["payload"]["body"]
        # Cursor moves so the same run is not repeated when the at-bat lands
        # in `scoringPlays` moments later.
        assert subscription.last_at_bat_index == 60

    async def test_completed_at_bat_is_not_announced_twice(self):
        play = _wild_pitch_play(complete=True)
        feed = _live_feed([play], scoring_plays=[60])
        # Cursor already advanced by the in-flight announcement above.
        subscription = _live_subscription(cursor=60)

        db = AsyncMock()
        db.scalars = AsyncMock(return_value=[subscription])

        with patch.object(
            scoring_watcher,
            "get_mlb_client",
            return_value=Mock(get_game_feed_raw=AsyncMock(return_value=feed)),
        ):
            with patch.object(
                scoring_watcher.push_service,
                "send_push",
                AsyncMock(return_value="sent"),
            ) as send:
                await scoring_watcher._process_game(db, 1)

        assert send.await_count == 0

    async def test_in_progress_at_bat_without_a_run_is_ignored(self):
        play = {
            "atBatIndex": 61,
            "about": {"inning": 8, "isTopInning": True, "isComplete": False},
            "result": {"awayScore": 5, "homeScore": 2},
            "runners": [{"movement": {"start": None, "end": "1B"}, "details": {}}],
            "playEvents": [],
        }
        feed = _live_feed([play], scoring_plays=[])
        subscription = _live_subscription(cursor=60)

        db = AsyncMock()
        db.scalars = AsyncMock(return_value=[subscription])

        with patch.object(
            scoring_watcher,
            "get_mlb_client",
            return_value=Mock(get_game_feed_raw=AsyncMock(return_value=feed)),
        ):
            with patch.object(
                scoring_watcher.push_service,
                "send_push",
                AsyncMock(return_value="sent"),
            ) as send:
                await scoring_watcher._process_game(db, 1)

        assert send.await_count == 0
        assert subscription.last_at_bat_index == 60


class TestUndeliveredPlayIsVisible:
    """A missed scoring alert must leave a trace.

    When a game ends, `_handle_final` deletes every subscription row - and
    with it the cursor, which is the only record of what was delivered. A
    scoring play still pending at that moment is discarded with no retry, so
    without a log there is no way to tell afterwards whether a push was never
    sent or simply never displayed.
    """

    async def test_failed_send_is_logged_with_the_play_index(self, caplog):
        play = {
            "atBatIndex": 69,
            "about": {"inning": 9, "isTopInning": True, "isScoringPlay": True},
            "result": {
                "description": "Joey Ortiz singles. Cooper Pratt scores.",
                "awayScore": 6,
                "homeScore": 2,
            },
            "runners": [{"movement": {"end": "score"}, "details": {}}],
        }
        subscription = _live_subscription(cursor=60)

        db = AsyncMock()

        with caplog.at_level(logging.WARNING, logger="app.services.scoring_watcher"):
            with patch.object(
                scoring_watcher.push_service,
                "send_push",
                AsyncMock(return_value="failed"),
            ):
                await scoring_watcher._notify_subscriber(
                    db, subscription, {69: play}, [69], "MIL", "LAD"
                )

        assert "69" in caplog.text
        # The cursor must not advance past an undelivered play.
        assert subscription.last_at_bat_index == 60

    async def test_successful_send_is_logged(self, caplog):
        play = {
            "atBatIndex": 69,
            "about": {"inning": 9, "isTopInning": True, "isScoringPlay": True},
            "result": {"description": "Ortiz singles.", "awayScore": 6, "homeScore": 2},
            "runners": [{"movement": {"end": "score"}, "details": {}}],
        }
        subscription = _live_subscription(cursor=60)

        db = AsyncMock()

        with caplog.at_level(logging.INFO, logger="app.services.scoring_watcher"):
            with patch.object(
                scoring_watcher.push_service,
                "send_push",
                AsyncMock(return_value="sent"),
            ):
                await scoring_watcher._notify_subscriber(
                    db, subscription, {69: play}, [69], "MIL", "LAD"
                )

        assert "sent" in caplog.text
        assert "69" in caplog.text
        assert subscription.last_at_bat_index == 69

    async def test_game_ending_with_an_undelivered_play_is_an_error(self, caplog):
        """The exact shape of the 6-2 Final arriving without the 6-2 run."""
        play = {
            "atBatIndex": 69,
            "about": {"inning": 9, "isTopInning": True, "isScoringPlay": True},
            "result": {"description": "Ortiz singles.", "awayScore": 6, "homeScore": 2},
            "runners": [{"movement": {"end": "score"}, "details": {}}],
        }
        feed = _live_feed([play], scoring_plays=[69])
        feed["gameData"]["status"] = {
            "abstractGameState": "Final",
            "codedGameState": "O",
            "detailedState": "Game Over",
        }
        feed["liveData"]["linescore"] = {
            "teams": {"away": {"runs": 6}, "home": {"runs": 2}}
        }

        # Behind the final play: the scoring alert never got through.
        subscription = _live_subscription(cursor=60)

        db = AsyncMock()
        db.scalars = AsyncMock(return_value=[subscription])

        with caplog.at_level(logging.ERROR, logger="app.services.scoring_watcher"):
            with patch.object(
                scoring_watcher,
                "get_mlb_client",
                return_value=Mock(get_game_feed_raw=AsyncMock(return_value=feed)),
            ):
                with patch.object(
                    scoring_watcher.push_service,
                    "send_push",
                    AsyncMock(side_effect=["failed", "sent"]),
                ):
                    await scoring_watcher._process_game(db, 1)

        assert "never delivered" in caplog.text
        assert "69" in caplog.text


class TestLogStreamRouting:
    """Routine records and problems must land on different streams.

    Railway derives a log's severity from the stream it arrived on, so
    emitting everything to stderr tagged every delivery record "error" and
    buried the undelivered-play ERROR those records exist to surface.
    """

    def setup_method(self):
        self._app_logger = logging.getLogger("app")
        self._saved = list(self._app_logger.handlers)

        self._app_logger.handlers.clear()

    def teardown_method(self):
        self._app_logger.handlers.clear()
        self._app_logger.handlers.extend(self._saved)

    def _streams(self):
        from app.main import _configure_logging

        _configure_logging()

        # Compared by identity: pytest swaps sys.stdout/sys.stderr for capture
        # objects, so the stream's `name` is not reliable here.
        return {
            id(handler.stream): handler
            for handler in logging.getLogger("app").handlers
        }

    def test_info_goes_to_stdout_and_warnings_to_stderr(self):
        streams = self._streams()

        assert id(sys.stdout) in streams
        assert id(sys.stderr) in streams

        info = logging.LogRecord("app.x", logging.INFO, "f", 1, "m", None, None)
        error = logging.LogRecord("app.x", logging.ERROR, "f", 1, "m", None, None)

        stdout = streams[id(sys.stdout)]
        stderr = streams[id(sys.stderr)]

        # Routine delivery records: stdout only.
        # `Handler.filter` returns the record itself when it passes, so this
        # is a truthiness check rather than an identity one.
        assert stdout.filter(info)
        assert stderr.level > logging.INFO

        # Problems: stderr only, so the severity Railway reports is accurate.
        assert not stdout.filter(error)
        assert error.levelno >= stderr.level

    def test_reentering_lifespan_does_not_stack_handlers(self):
        from app.main import _configure_logging

        _configure_logging()
        _configure_logging()

        assert len(logging.getLogger("app").handlers) == 2


class TestSeedOnlySubscribe:
    """A re-registration must not disturb the delivery cursor.

    The browser rotates its push endpoint and the client re-registers to
    repair it. That request knows only what the page is showing, which can
    already include a play whose push is still in flight - adopting it as the
    cursor would mark that play delivered and skip it.
    """

    def test_seed_only_defaults_to_false(self):
        from app.routers.notifications import SubscribeRequest

        body = SubscribeRequest(
            subscription={
                "endpoint": "https://fcm.googleapis.com/fcm/send/abc",
                "keys": {"p256dh": "k", "auth": "a"},
            },
            game_pk=849810,
            last_at_bat_index=39,
        )

        # Opting in explicitly still seeds the cursor.
        assert body.seed_only is False

    def test_seed_only_round_trips(self):
        from app.routers.notifications import SubscribeRequest

        body = SubscribeRequest(
            subscription={
                "endpoint": "https://fcm.googleapis.com/fcm/send/abc",
                "keys": {"p256dh": "k", "auth": "a"},
            },
            game_pk=849810,
            last_at_bat_index=39,
            seed_only=True,
        )

        assert body.seed_only is True


class TestPushEndpointValidation:
    """A stored endpoint is a URL the watcher later POSTs to.

    An attacker able to register an arbitrary one would have the server make
    requests on their behalf, reaching anything the host can see.
    """

    def test_a_real_push_endpoint_is_accepted(self):
        from app.routers.notifications import validate_push_endpoint

        endpoint = "https://fcm.googleapis.com/fcm/send/cTzG-abc123"

        assert validate_push_endpoint(endpoint) == endpoint

    @pytest.mark.parametrize(
        "endpoint",
        [
            "http://fcm.googleapis.com/fcm/send/abc",  # plaintext
            "https://169.254.169.254/latest/meta-data/",  # cloud metadata
            "https://127.0.0.1/fcm/send/abc",  # loopback
            "https://localhost/fcm/send/abc",
            "https://internal-api/fcm/send/abc",  # single label
            "https://db.internal/fcm/send/abc",
            "https://printer.local/fcm/send/abc",
            "file:///etc/passwd",
        ],
    )
    def test_unreachable_or_internal_endpoints_are_rejected(self, endpoint):
        from app.routers.notifications import validate_push_endpoint

        with pytest.raises(ValueError):
            validate_push_endpoint(endpoint)

    def test_the_subscribe_payload_enforces_it(self):
        from pydantic import ValidationError

        from app.routers.notifications import SubscriptionPayload

        with pytest.raises(ValidationError):
            SubscriptionPayload(
                endpoint="https://169.254.169.254/latest/meta-data/",
                keys={"p256dh": "k", "auth": "a"},
            )


class TestRotateSubscription:
    """A rotated endpoint must carry its games - and its cursors - across."""

    @staticmethod
    def _row(game_pk: int, cursor: int, endpoint: str):
        row = Mock()
        row.game_pk = game_pk
        row.last_at_bat_index = cursor
        row.endpoint = endpoint
        row.p256dh = "old-p"
        row.auth = "old-a"

        return row

    @staticmethod
    def _body(old: str, new: str):
        from app.routers.notifications import RotateRequest

        return RotateRequest(
            old_endpoint=old,
            subscription={
                "endpoint": new,
                "keys": {"p256dh": "new-p", "auth": "new-a"},
            },
        )

    async def test_rows_move_and_keep_their_cursor(self):
        from app.routers.notifications import rotate

        old = "https://fcm.googleapis.com/fcm/send/old"
        new = "https://fcm.googleapis.com/fcm/send/new"

        row = self._row(849810, 44, old)

        db = AsyncMock()
        db.scalars = AsyncMock(side_effect=[[row], []])

        result = await rotate(self._body(old, new), db)

        assert result == {"status": "rotated", "moved": 1}
        assert row.endpoint == new
        assert row.p256dh == "new-p"
        # The whole point: a fresh subscribe would have had to guess this.
        assert row.last_at_bat_index == 44

    async def test_every_game_the_browser_follows_moves(self):
        from app.routers.notifications import rotate

        old = "https://fcm.googleapis.com/fcm/send/old"
        new = "https://fcm.googleapis.com/fcm/send/new"

        rows = [self._row(1, 5, old), self._row(2, 9, old)]

        db = AsyncMock()
        db.scalars = AsyncMock(side_effect=[rows, []])

        result = await rotate(self._body(old, new), db)

        assert result["moved"] == 2
        assert all(row.endpoint == new for row in rows)

    async def test_colliding_rows_merge_on_the_furthest_cursor(self):
        """The browser may already have registered its new address.

        (endpoint, game_pk) is unique, so the rows have to be reconciled
        rather than both kept.
        """
        from app.routers.notifications import rotate

        old = "https://fcm.googleapis.com/fcm/send/old"
        new = "https://fcm.googleapis.com/fcm/send/new"

        stale = self._row(849810, 44, old)
        current = self._row(849810, 13, new)

        db = AsyncMock()
        db.scalars = AsyncMock(side_effect=[[stale], [current]])

        result = await rotate(self._body(old, new), db)

        assert result["moved"] == 0
        # Taking the lower cursor would replay plays the user already saw.
        assert current.last_at_bat_index == 44
        db.delete.assert_awaited_once_with(stale)

    async def test_an_unknown_old_endpoint_is_not_an_error(self):
        """The service worker cannot know whether the rows still exist."""
        from app.routers.notifications import rotate

        db = AsyncMock()
        db.scalars = AsyncMock(return_value=[])

        result = await rotate(
            self._body(
                "https://fcm.googleapis.com/fcm/send/gone",
                "https://fcm.googleapis.com/fcm/send/new",
            ),
            db,
        )

        assert result == {"status": "unknown", "moved": 0}

    async def test_an_unchanged_endpoint_still_refreshes_keys(self):
        from app.routers.notifications import rotate

        same = "https://fcm.googleapis.com/fcm/send/same"

        db = AsyncMock()

        result = await rotate(self._body(same, same), db)

        assert result == {"status": "unchanged", "moved": 0}
        db.execute.assert_awaited_once()


class TestPersistentAlertByDevice:
    """macOS Chrome shows nothing at all for a payload asking to persist.

    Measured against a live endpoint: the same notification was accepted by
    the push service with and without `requireInteraction`, and only the one
    without ever appeared. Every manual test had been going through
    /notifications/test, which never set the flag - which is why the path
    looked healthy while real scoring plays did not arrive.
    """

    @staticmethod
    def _play():
        return {
            "about": {"inning": 9, "isTopInning": True},
            "result": {
                "description": "David Hamilton homers (1).",
                "awayScore": 6,
                "homeScore": 4,
            },
            "atBatIndex": 68,
        }

    def test_mobile_keeps_the_alert_on_screen(self):
        payload = _build_payload(
            self._play(), 849810, "MIL", "LAD", device_type="mobile"
        )

        assert payload["requireInteraction"] is True

    def test_desktop_does_not_ask_to_persist(self):
        payload = _build_payload(
            self._play(), 849810, "MIL", "LAD", device_type="desktop"
        )

        assert payload["requireInteraction"] is False

    def test_an_unclassified_device_does_not_ask_to_persist(self):
        """Omitting it costs auto-dismiss; setting it wrongly costs the alert."""
        payload = _build_payload(
            self._play(), 849810, "MIL", "LAD", device_type=None
        )

        assert payload["requireInteraction"] is False

    def test_the_final_follows_the_same_rule(self):
        feed = {
            "liveData": {
                "linescore": {"teams": {"away": {"runs": 6}, "home": {"runs": 4}}}
            }
        }

        mobile = _build_final_payload(
            feed, 849810, "MIL", "LAD", device_type="mobile"
        )
        desktop = _build_final_payload(
            feed, 849810, "MIL", "LAD", device_type="desktop"
        )

        assert mobile["requireInteraction"] is True
        assert desktop["requireInteraction"] is False

    async def test_each_subscriber_gets_a_payload_for_its_own_device(self):
        """One play, two devices, two different flags."""
        play = self._play()
        sent = []

        async def _capture(endpoint, p256dh, auth, payload):
            sent.append((endpoint, payload["requireInteraction"]))

            return "sent"

        for device, endpoint in (("mobile", "m"), ("desktop", "d")):
            subscription = Mock(
                endpoint=endpoint,
                p256dh="p",
                auth="a",
                game_pk=849810,
                last_at_bat_index=60,
                device_type=device,
            )

            with patch.object(
                scoring_watcher.push_service, "send_push", AsyncMock(side_effect=_capture)
            ):
                await scoring_watcher._notify_subscriber(
                    AsyncMock(), subscription, {68: play}, [68], "MIL", "LAD"
                )

        assert sent == [("m", True), ("d", False)]


class TestClassifyDevice:
    """Test device classification from Client Hints and User-Agent."""
    def test_client_hint_mobile(self):
        """Sec-CH-UA-Mobile: ?1 means mobile, regardless of User-Agent."""
        desktop_ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
        assert classify_device(desktop_ua, "?1") == "mobile"

    def test_client_hint_desktop(self):
        """Sec-CH-UA-Mobile: ?0 means desktop, regardless of User-Agent."""
        mobile_ua = "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Mobile"
        assert classify_device(mobile_ua, "?0") == "desktop"

    @pytest.mark.parametrize(
        "user_agent",
        [
            "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148",
            "Mozilla/5.0 (Android 13; Mobile; rv:120.0) Gecko/120.0 Firefox/120.0",
        ],
    )
    def test_mobile_user_agents(self, user_agent):
        """Recognizes mobile User-Agents when no Client Hint is present."""
        assert classify_device(user_agent, None) == "mobile"

    @pytest.mark.parametrize(
        "user_agent",
        [
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ],
    )
    def test_desktop_user_agents(self, user_agent):
        """Recognizes desktop User-Agents when no Client Hint is present."""
        assert classify_device(user_agent, None) == "desktop"

    def test_missing_user_agent_is_unknown(self):
        """None User-Agent with no Client Hint → None."""
        assert classify_device(None, None) is None
        assert classify_device("", None) is None

    def test_malformed_hint_falls_through_to_user_agent(self):
        """Invalid Client Hint falls back to User-Agent sniffing."""
        assert classify_device("iPhone", "garbage") == "mobile"
