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

import pytest

from app.services import push_service
from app.services import scoring_watcher
from app.services.scoring_watcher import (
    _build_final_payload,
    _build_payload,
    _handle_final,
    _ordinal,
)


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
        assert payload["requireInteraction"] is True

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
        assert payload["requireInteraction"] is True

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
        assert payload["requireInteraction"] is True

    def test_final_missing_runs(self):
        """Handles missing run data in final payload."""
        feed = {"liveData": {"linescore": {"teams": {}}}}
        payload = _build_final_payload(feed, game_pk=666, away_abbr="HOU", home_abbr="SEA")

        assert payload["title"] == "Final"
        assert payload["body"] == "HOU 0 – SEA 0"  # Defaults to 0 runs


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
