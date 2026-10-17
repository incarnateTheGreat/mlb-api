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

import asyncio
import logging
import sys
from datetime import datetime, timezone

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


class TestWatcherIdles:
    """The watcher must not poll a serverless database around the clock.

    Querying every few seconds through the long stretches when nobody is
    subscribed keeps the compute permanently awake, which is enough on its own
    to exhaust a monthly allowance and take the whole app offline. Standing
    down is only safe if something reliably wakes it again, so these tests
    pin both halves: that it stops, and that it restarts.
    """

    @pytest.fixture(autouse=True)
    def _fast_and_isolated(self):
        """Give each test a short poll interval and its own wake-up event.

        `asyncio.Event` binds to the first loop that awaits it, and every test
        gets a fresh loop, so a module-level event shared between tests fails
        with "bound to a different event loop". Production has one loop for
        the process lifetime, so only the tests need this.
        """
        settings = Mock()
        settings.watcher_poll_seconds = 0.001
        settings.watcher_lead_seconds = 600

        with (
            patch.object(scoring_watcher, "get_settings", return_value=settings),
            patch.object(
                scoring_watcher, "_subscriptions_changed", asyncio.Event()
            ),
        ):
            yield

    async def _run_until(self, condition, timeout=2.0):
        """Run the watcher until `condition` holds, then cancel it."""
        task = asyncio.create_task(scoring_watcher.run_scoring_watcher())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        try:
            while not condition():
                if task.done():
                    # Surfaces the real error instead of a bare timeout.
                    await task

                    raise AssertionError("watcher exited on its own")

                if loop.time() > deadline:
                    raise AssertionError("condition never became true")

                await asyncio.sleep(0.01)
        finally:
            task.cancel()

            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_stops_querying_when_nothing_is_subscribed(self):
        """An empty tick parks the loop instead of scheduling another query."""
        ticks = 0

        async def fake_tick():
            nonlocal ticks
            ticks += 1

            return None

        with patch.object(scoring_watcher, "_tick", fake_tick):
            task = asyncio.create_task(scoring_watcher.run_scoring_watcher())

            try:
                # Many poll intervals: had it stayed in the loop, the tick
                # count would run into the dozens.
                await asyncio.sleep(0.2)
            finally:
                task.cancel()

                try:
                    await task
                except asyncio.CancelledError:
                    pass

        assert ticks == 1, f"idle watcher issued {ticks} queries, expected 1"

    @pytest.mark.asyncio
    async def test_subscribing_wakes_the_idle_watcher(self):
        """Without this the watcher stays parked and nothing is ever sent."""
        ticks = 0

        async def fake_tick():
            nonlocal ticks
            ticks += 1

            return None

        with patch.object(scoring_watcher, "_tick", fake_tick):
            task = asyncio.create_task(scoring_watcher.run_scoring_watcher())
            loop = asyncio.get_running_loop()

            try:
                while ticks < 1:
                    await asyncio.sleep(0.01)

                scoring_watcher.notify_subscriptions_changed()

                deadline = loop.time() + 2.0

                while ticks < 2:
                    if loop.time() > deadline:
                        raise AssertionError("watcher never woke up")

                    await asyncio.sleep(0.01)
            finally:
                task.cancel()

                try:
                    await task
                except asyncio.CancelledError:
                    pass

        assert ticks >= 2

    @pytest.mark.asyncio
    async def test_keeps_polling_while_a_game_is_subscribed(self):
        """A live game must still be polled on every interval."""
        ticks = 0

        async def fake_tick():
            nonlocal ticks
            ticks += 1

            return 0.001

        with patch.object(scoring_watcher, "_tick", fake_tick):
            await self._run_until(lambda: ticks >= 3)

        assert ticks >= 3

    @pytest.mark.asyncio
    async def test_a_failing_tick_does_not_park_the_watcher(self):
        """A database error must not be mistaken for "nothing subscribed".

        Parking on an exception would be unrecoverable: the only thing that
        wakes the watcher is a subscribe call, and the user has no reason to
        make one when their toggle already reads as on.
        """
        ticks = 0

        async def failing_tick():
            nonlocal ticks
            ticks += 1

            raise RuntimeError("connection refused")

        with patch.object(scoring_watcher, "_tick", failing_tick):
            await self._run_until(lambda: ticks >= 3)

        assert ticks >= 3, "watcher gave up after a transient failure"

    @pytest.mark.asyncio
    async def test_a_long_wait_is_interrupted_by_a_new_subscription(self):
        """Subscribing must not have to wait out a sleep set days ago.

        The watcher sleeps until the next scheduled first pitch. Subscribing
        to a game that starts sooner has to cut that short, or the alert
        arrives after the game it was for.
        """
        ticks = 0

        async def fake_tick():
            nonlocal ticks
            ticks += 1

            # Far longer than the test could ever wait for.
            return 3600.0

        with patch.object(scoring_watcher, "_tick", fake_tick):
            task = asyncio.create_task(scoring_watcher.run_scoring_watcher())
            loop = asyncio.get_running_loop()

            try:
                while ticks < 1:
                    await asyncio.sleep(0.01)

                scoring_watcher.notify_subscriptions_changed()

                deadline = loop.time() + 2.0

                while ticks < 2:
                    if loop.time() > deadline:
                        raise AssertionError(
                            "watcher slept through a new subscription"
                        )

                    await asyncio.sleep(0.01)
            finally:
                task.cancel()

                try:
                    await task
                except asyncio.CancelledError:
                    pass

        assert ticks >= 2


class TestWatcherWaitsForFirstPitch:
    """A subscription days ahead of a game must not hold the database awake.

    Subscribing is deliberately cheap and open-ended - a whole homestand can
    be armed at once - so "has a subscriber" cannot mean "poll now". Ten games
    over ten days would otherwise keep a serverless compute awake for ten
    days, which exceeds a monthly allowance on its own.
    """

    @pytest.fixture(autouse=True)
    def _clean_caches(self):
        """Schedule state is module-level; tests must not leak into each other."""
        scoring_watcher._due_games.clear()
        scoring_watcher._start_times.clear()

        settings = Mock()
        settings.watcher_poll_seconds = 10
        settings.watcher_lead_seconds = 600

        with patch.object(
            scoring_watcher, "get_settings", return_value=settings
        ):
            yield

        scoring_watcher._due_games.clear()
        scoring_watcher._start_times.clear()

    def _client_returning(self, starts):
        """An MLB client whose schedule call returns `starts`."""
        client = Mock()
        client.get_game_start_times = AsyncMock(return_value=starts)

        return client

    @pytest.mark.asyncio
    async def test_a_game_days_away_is_not_polled(self):
        """The whole point: a future game must produce no feed requests."""
        now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
        client = self._client_returning({849817: "2026-10-26T00:00:00Z"})

        with patch.object(
            scoring_watcher, "get_mlb_client", return_value=client
        ):
            due, next_start = await scoring_watcher._partition_games(
                [849817], now
            )

        assert due == []
        assert next_start == datetime(2026, 10, 26, 0, 0, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_a_game_inside_the_lead_window_is_polled(self):
        """Polling has to start before first pitch, not at it."""
        now = datetime(2026, 10, 17, 23, 55, tzinfo=timezone.utc)
        client = self._client_returning({849817: "2026-10-18T00:00:00Z"})

        with patch.object(
            scoring_watcher, "get_mlb_client", return_value=client
        ):
            due, next_start = await scoring_watcher._partition_games(
                [849817], now
            )

        assert due == [849817]
        assert next_start is None

    @pytest.mark.asyncio
    async def test_an_unknown_start_time_is_polled(self):
        """Fail open: being early wastes compute, being wrong misses the game."""
        now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
        client = self._client_returning({})

        with patch.object(
            scoring_watcher, "get_mlb_client", return_value=client
        ):
            due, _ = await scoring_watcher._partition_games([849817], now)

        assert due == [849817]

    @pytest.mark.asyncio
    async def test_a_schedule_failure_is_polled(self):
        """An MLB API outage must not silently disable notifications."""
        now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
        client = Mock()
        client.get_game_start_times = AsyncMock(
            side_effect=RuntimeError("statsapi down")
        )

        with patch.object(
            scoring_watcher, "get_mlb_client", return_value=client
        ):
            due, _ = await scoring_watcher._partition_games([849817], now)

        assert due == [849817]

    @pytest.mark.asyncio
    async def test_every_pending_game_costs_one_request(self):
        """Arming a homestand must not mean a request per game."""
        now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
        game_pks = [849810 + n for n in range(10)]
        client = self._client_returning(
            {game_pk: "2026-10-26T00:00:00Z" for game_pk in game_pks}
        )

        with patch.object(
            scoring_watcher, "get_mlb_client", return_value=client
        ):
            await scoring_watcher._partition_games(game_pks, now)

        assert client.get_game_start_times.await_count == 1
        assert sorted(
            client.get_game_start_times.await_args.args[0]
        ) == game_pks

    @pytest.mark.asyncio
    async def test_a_live_game_stops_re_reading_the_schedule(self):
        """Once due, a game stays due - no lookup on every tick for hours."""
        now = datetime(2026, 10, 17, 23, 55, tzinfo=timezone.utc)
        client = self._client_returning({849817: "2026-10-18T00:00:00Z"})

        with patch.object(
            scoring_watcher, "get_mlb_client", return_value=client
        ):
            for _ in range(5):
                due, _ = await scoring_watcher._partition_games([849817], now)

        assert due == [849817]
        assert client.get_game_start_times.await_count == 1

    @pytest.mark.asyncio
    async def test_cached_state_is_dropped_when_a_game_ends(self):
        """Rows vanish at game end; the caches must not grow forever."""
        scoring_watcher._due_games.add(849817)
        scoring_watcher._start_times[849817] = (None, 0.0)

        scoring_watcher._forget_unsubscribed_games(set())

        assert scoring_watcher._due_games == set()
        assert scoring_watcher._start_times == {}

    @pytest.mark.asyncio
    async def test_a_naive_start_time_is_treated_as_utc(self):
        """Comparing naive and aware datetimes raises; statsapi returns UTC."""
        now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
        # No trailing "Z" - the parse yields a naive datetime.
        client = self._client_returning({849817: "2026-10-26T00:00:00"})

        with patch.object(
            scoring_watcher, "get_mlb_client", return_value=client
        ):
            due, next_start = await scoring_watcher._partition_games(
                [849817], now
            )

        assert due == []
        assert next_start is not None
        assert next_start.tzinfo is not None


class TestPostponedGameStartTimes:
    """A postponed game keeps its gamePk and gains a second schedule entry.

    Seen live when TB @ CLE 849817 was rained out: the schedule returned the
    abandoned Oct 17 date AND the Oct 18 make-up under the same gamePk.
    Collapsing those by gamePk makes the answer depend on array order, and
    choosing the abandoned date puts first pitch in the past - which the
    watcher reads as "already started" and polls against until the real game.
    """

    def _client_returning(self, dates):
        """A ScheduleMixin bound to a canned /schedule response."""
        from app.services.mlb_client.schedule import ScheduleMixin

        client = ScheduleMixin()
        client._get = AsyncMock(return_value={"dates": dates})

        return client

    @pytest.mark.asyncio
    async def test_the_make_up_date_wins_over_the_rained_out_one(self):
        """The exact shape statsapi returned for the rained-out 849817."""
        client = self._client_returning(
            [
                {
                    "games": [
                        {
                            "gamePk": 849817,
                            "gameDate": "2026-10-17T00:00:00Z",
                            "status": {"codedGameState": "D"},
                        }
                    ]
                },
                {
                    "games": [
                        {
                            "gamePk": 849817,
                            "gameDate": "2026-10-18T21:00:00Z",
                            "status": {"codedGameState": "S"},
                        }
                    ]
                },
            ]
        )

        starts = await client.get_game_start_times([849817])

        assert starts == {849817: "2026-10-18T21:00:00Z"}

    @pytest.mark.asyncio
    async def test_order_does_not_decide_the_answer(self):
        """Same two entries reversed must give the same start time."""
        client = self._client_returning(
            [
                {
                    "games": [
                        {
                            "gamePk": 849817,
                            "gameDate": "2026-10-18T21:00:00Z",
                            "status": {"codedGameState": "S"},
                        }
                    ]
                },
                {
                    "games": [
                        {
                            "gamePk": 849817,
                            "gameDate": "2026-10-17T00:00:00Z",
                            "status": {"codedGameState": "D"},
                        }
                    ]
                },
            ]
        )

        starts = await client.get_game_start_times([849817])

        assert starts == {849817: "2026-10-18T21:00:00Z"}

    @pytest.mark.asyncio
    async def test_a_postponement_with_no_make_up_date_is_still_reported(self):
        """Better a known stale date than an unknown one, which polls."""
        client = self._client_returning(
            [
                {
                    "games": [
                        {
                            "gamePk": 849817,
                            "gameDate": "2026-10-17T00:00:00Z",
                            "status": {"codedGameState": "D"},
                        }
                    ]
                }
            ]
        )

        starts = await client.get_game_start_times([849817])

        assert starts == {849817: "2026-10-17T00:00:00Z"}

    @pytest.mark.asyncio
    async def test_an_ordinary_game_is_unaffected(self):
        """The common case must not be disturbed by the postponement handling."""
        client = self._client_returning(
            [
                {
                    "games": [
                        {
                            "gamePk": 849816,
                            "gameDate": "2026-10-18T00:00:00Z",
                            "status": {"codedGameState": "S"},
                        }
                    ]
                }
            ]
        )

        starts = await client.get_game_start_times([849816])

        assert starts == {849816: "2026-10-18T00:00:00Z"}

    @pytest.mark.asyncio
    async def test_no_games_means_no_request(self):
        """An empty subscription set must not hit statsapi at all."""
        client = self._client_returning([])

        assert await client.get_game_start_times([]) == {}
        client._get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_doubleheader_takes_the_earlier_game(self):
        """Two playable dates: poll from the earlier, never sleep past it."""
        client = self._client_returning(
            [
                {
                    "games": [
                        {
                            "gamePk": 849817,
                            "gameDate": "2026-10-18T21:00:00Z",
                            "status": {"codedGameState": "S"},
                        },
                        {
                            "gamePk": 849817,
                            "gameDate": "2026-10-18T17:00:00Z",
                            "status": {"codedGameState": "S"},
                        },
                    ]
                }
            ]
        )

        starts = await client.get_game_start_times([849817])

        assert starts == {849817: "2026-10-18T17:00:00Z"}
