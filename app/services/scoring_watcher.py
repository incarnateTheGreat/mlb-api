"""
Background watcher that turns scoring plays into push notifications.

This is what makes locked-phone alerts possible. Nobody's browser is connected
when a run scores, so the server has to notice on its own: it polls the MLB
feed for games that have at least one subscriber, diffs the scoring plays
against what each subscriber has already been sent, and pushes the difference.

Polling is gated on the clock as well as on subscriptions. A subscription may
be registered days before first pitch, and the database it would be polling is
serverless - billed for the time it stays awake - so the watcher sleeps until
shortly before the game starts rather than idling against it.

Runs as a single asyncio task from the FastAPI lifespan. That assumes one
replica - see the note in `run_scoring_watcher` if that ever changes.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_session_maker
from app.models.notifications import PushSubscription
from app.services import db_metrics, push_service
from app.services.mlb_client import get_mlb_client
from app.services.team_icons import matchup_icon_url

logger = logging.getLogger(__name__)

# Games that actually finished. Postponed ("D"), cancelled ("C") and forfeited
# ("Q"/"R") games also report abstractGameState "Final" but have no result to
# announce - see https://statsapi.mlb.com/api/v1/gameStatus
GENUINE_FINAL_CODES = frozenset({"O", "F"})  # "Game Over", "Final"

# Ticks to keep retrying a failed final notification. Uncapped, a permanently
# failing endpoint would hold its row - and so keep the watcher polling a
# finished game - forever.
MAX_FINAL_SEND_ATTEMPTS = 6

# game_pk -> consecutive ticks on which the final notification failed. Held in
# memory deliberately: losing it on restart just means a few extra retries.
_final_attempts: dict[int, int] = {}

# Raised when a subscription is registered, so the watcher can wait on this
# instead of asking the database every few seconds whether anything changed.
#
# The database is serverless and bills for the time it stays awake. A query
# every 10s around the clock never lets it suspend, which on its own is enough
# to burn a whole monthly compute allowance on empty polls - most of them at
# hours when no game is being played. So when nothing is subscribed the
# watcher has to go completely silent rather than merely poll more slowly.
#
# In-process signal, which holds only because the watcher is single-replica
# (see `run_scoring_watcher`). It is a latency optimisation, not the source of
# truth: the watcher always re-reads the database before acting on it.
_subscriptions_changed = asyncio.Event()

# Games whose lead time has passed. A game only ever crosses that line once,
# so remembering it keeps a live game from re-reading the schedule every tick.
_due_games: set[int] = set()

# game_pk -> (first pitch or None, monotonic time it was read). Start times
# move - rain delays, doubleheaders, suspended games - so this expires rather
# than being read once and trusted for days.
_start_times: dict[int, tuple[datetime | None, float]] = {}
START_TIME_TTL_SECONDS = 1800

# Longest the watcher will sleep while only future games are subscribed.
# Bounds the damage if a start time moves earlier after it was cached, and
# guarantees the schedule is re-read at least this often.
MAX_IDLE_SLEEP_SECONDS = 6 * 60 * 60


def notify_subscriptions_changed() -> None:
    """Wake an idle watcher after a subscription is written."""
    _subscriptions_changed.set()


def _forget_unsubscribed_games(subscribed: set[int]) -> None:
    """Drop cached schedule state for games nobody is subscribed to.

    Rows are deleted when a game ends, so without this both caches would grow
    for the life of the process.
    """
    _due_games.intersection_update(subscribed)

    for game_pk in [pk for pk in _start_times if pk not in subscribed]:
        del _start_times[game_pk]


async def _load_start_times(game_pks: list[int]) -> None:
    """Cache first-pitch times for any game whose entry is missing or stale.

    One request covers every game that needs one, so a week of subscriptions
    costs the same single call as a single game.
    """
    now = asyncio.get_running_loop().time()

    wanted = [
        game_pk
        for game_pk in game_pks
        if game_pk not in _due_games
        and (
            game_pk not in _start_times
            or now - _start_times[game_pk][1] >= START_TIME_TTL_SECONDS
        )
    ]

    if not wanted:
        return

    try:
        raw_times = await get_mlb_client().get_game_start_times(wanted)
    except Exception:
        # Leave the cache alone so the next tick retries. Callers treat a
        # missing entry as "start unknown", which polls rather than skips.
        logger.exception("Could not read start times for games %s", wanted)

        return

    for game_pk in wanted:
        raw = raw_times.get(game_pk)
        start: datetime | None = None

        if raw:
            try:
                start = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                logger.warning(
                    "Unparseable start time %r for game %s", raw, game_pk
                )

        if start is not None and start.tzinfo is None:
            # statsapi returns UTC; an aware value is required to compare.
            start = start.replace(tzinfo=timezone.utc)

        _start_times[game_pk] = (start, now)


async def _partition_games(
    game_pks: list[int], now: datetime
) -> tuple[list[int], datetime | None]:
    """Split subscribed games into "poll now" and "wake up for later".

    Returns the games to process and the earliest first pitch still ahead of
    us, which is how long the watcher may safely sleep.
    """
    await _load_start_times(game_pks)

    lead = timedelta(seconds=get_settings().watcher_lead_seconds)
    due: list[int] = []
    next_start: datetime | None = None

    for game_pk in game_pks:
        if game_pk in _due_games:
            due.append(game_pk)

            continue

        cached = _start_times.get(game_pk)
        start = cached[0] if cached else None

        # An unknown start time is polled, not skipped. Being early wastes a
        # little compute; being wrong misses the game entirely.
        if start is None or now >= start - lead:
            _due_games.add(game_pk)
            due.append(game_pk)
        elif next_start is None or start < next_start:
            next_start = start

    return due, next_start


def _ordinal(n: int) -> str:
    """Convert int to ordinal suffix (1st, 2nd, 3rd, 4th, etc.)."""
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    suffixes = {1: "st", 2: "nd", 3: "rd"}
    return f"{n}{suffixes.get(n % 10, 'th')}"


def _extract_team_abbreviations(feed: dict[str, Any]) -> tuple[str, str]:
    """Away and home abbreviations, falling back to empty strings."""
    teams = feed.get("gameData", {}).get("teams", {})

    away = teams.get("away", {}).get("abbreviation", "") or ""
    home = teams.get("home", {}).get("abbreviation", "") or ""

    return away, home


def _extract_team_ids(feed: dict[str, Any]) -> tuple[int | None, int | None]:
    """Away and home MLB team IDs, or None when the feed omits them."""
    teams = feed.get("gameData", {}).get("teams", {})

    away = teams.get("away", {}).get("id")
    home = teams.get("home", {}).get("id")

    return away, home


def _has_run_scored(play: dict[str, Any]) -> bool:
    """True when at least one runner has reached home during this at-bat.

    Reads the runner movements rather than `about.isScoringPlay`, because the
    latter is only set once the at-bat is complete. A run that crosses the
    plate mid-plate-appearance - a wild pitch, passed ball, balk, steal of
    home or error - shows up here immediately.
    """
    return any(
        (runner.get("movement") or {}).get("end") == "score"
        for runner in play.get("runners", []) or []
    )


def _scoring_description(play: dict[str, Any]) -> str:
    """The sentence describing how the run actually scored.

    `result.description` narrates the PLATE APPEARANCE, which is only the
    scoring event when the batter drove the run in. When a runner scores on a
    wild pitch (or similar) the plate appearance may end in, say, a strikeout,
    and the run is described by the play event instead. Announcing
    "MIL scores!" over "Christian Yelich strikes out swinging." is the
    symptom of reading the wrong one.

    An in-progress at-bat has no `result.description` yet, so this is also
    what makes an in-flight run announceable at all.
    """
    # `about.isScoringPlay` means the plate appearance itself was the scoring
    # event, so its own description already names the run.
    if play.get("about", {}).get("isScoringPlay"):
        description = play.get("result", {}).get("description")

        if description:
            return description

    # Otherwise the run came from a play event. Take the latest scoring one:
    # with more than one, the newest is what the subscriber has not seen.
    for event in reversed(play.get("playEvents", []) or []):
        details = event.get("details", {})

        if details.get("isScoringPlay") and details.get("description"):
            return details["description"]

    return play.get("result", {}).get("description") or "Run scored"


def _wants_persistent_alert(device_type: str | None) -> bool:
    """Whether to ask for a notification that stays on screen until dismissed.

    `requireInteraction` keeps an alert that arrived mid-inning from vanishing
    before the user looks, which is worth having on a phone. macOS Chrome,
    though, does not display a notification that asks for it AT ALL - measured
    by sending the same payload to the same endpoint twice, once with the flag
    and once without: the push service accepted both, only the one without
    ever appeared.

    Staying on screen is a nicety. Being shown is the entire point, so the
    flag is only set where it is known to be harmless.

    Unknown devices are treated as desktop: omitting it costs an alert that
    auto-dismisses, while setting it wrongly costs the alert entirely.
    """
    return device_type == "mobile"


def _build_payload(
    play: dict[str, Any],
    game_pk: int,
    away_abbr: str,
    home_abbr: str,
    away_id: int | None = None,
    home_id: int | None = None,
    device_type: str | None = None,
) -> dict[str, Any]:
    """
    Shape one scoring play into a push payload.

    Kept small deliberately - encrypted payloads are capped at ~4KB.
    """
    about = play.get("about", {})
    result = play.get("result", {})

    description = _scoring_description(play)
    away_score = result.get("awayScore", 0)
    home_score = result.get("homeScore", 0)

    # Extract inning and half-inning info
    inning = about.get("inning", 0)
    is_top = about.get("isTopInning", True)
    half = "Top" if is_top else "Bot"

    # The batting team is the one that scored.
    scoring_team = away_abbr if is_top else home_abbr
    scoring_id = away_id if is_top else home_id
    fielding_id = home_id if is_top else away_id

    # Truncate description if too long
    if len(description) > 120:
        description = description[:117] + "..."

    payload = {
        "title": f"{scoring_team} scores! {away_abbr} {away_score} - {home_abbr} {home_score}" if scoring_team else "Scoring play",
        "body": f"{half} {_ordinal(inning)} · {description}",
        "gamePk": game_pk,
        "atBatIndex": play.get("atBatIndex", -1),
        "requireInteraction": _wants_persistent_alert(device_type),
    }

    icon = matchup_icon_url(fielding_id, scoring_id)

    # Omitted rather than sent as null so the service worker's `||` fallback
    # to the app icon stays simple.
    if icon:
        payload["icon"] = icon

    return payload


async def _notify_subscriber(
    db: AsyncSession,
    subscription: PushSubscription,
    plays_by_index: dict[int, dict[str, Any]],
    scoring_indexes: list[int],
    away_abbr: str,
    home_abbr: str,
    away_id: int | None = None,
    home_id: int | None = None,
) -> None:
    """Send any scoring plays this subscriber hasn't seen, then move its cursor."""
    pending = [
        index for index in scoring_indexes if index > subscription.last_at_bat_index
    ]

    if not pending:
        return

    highest_sent = subscription.last_at_bat_index

    for index in pending:
        play = plays_by_index.get(index)

        if play is None:
            continue

        payload = _build_payload(
            play,
            subscription.game_pk,
            away_abbr,
            home_abbr,
            away_id,
            home_id,
            subscription.device_type,
        )

        result = await push_service.send_push(
            endpoint=subscription.endpoint,
            p256dh=subscription.p256dh,
            auth=subscription.auth,
            payload=payload,
        )

        # Every outcome is logged. The cursor alone cannot explain a missed
        # alert after the fact: a game ending deletes the row, taking the only
        # evidence with it. Without this there is no way to tell "the push was
        # never sent" from "the device never showed it".
        logger.info(
            "Scoring play push: game=%s at_bat=%s endpoint=%s result=%s",
            subscription.game_pk,
            index,
            subscription.endpoint[:40],
            result,
        )

        if result == "expired":
            # The push service no longer knows this address, so the row can
            # never be delivered to again. Logged loudly because the browser
            # is NOT told: it still holds a (rotated) subscription and the
            # toggle still reads as on, so from the device's side the alerts
            # simply stop. Silently dropping the row here is what makes that
            # look like notifications working intermittently.
            logger.warning(
                "Dropping expired subscription: game=%s endpoint=%s cursor=%s",
                subscription.game_pk,
                subscription.endpoint[:40],
                subscription.last_at_bat_index,
            )

            await db.delete(subscription)

            return

        if result == "failed":
            # Leave the cursor where it is so the next tick retries this play.
            logger.warning(
                "Scoring play %s for game %s undelivered; will retry next tick",
                index,
                subscription.game_pk,
            )

            break

        highest_sent = index

    if highest_sent != subscription.last_at_bat_index:
        subscription.last_at_bat_index = highest_sent


def _build_final_payload(
    feed: dict[str, Any],
    game_pk: int,
    away_abbr: str,
    home_abbr: str,
    away_id: int | None = None,
    home_id: int | None = None,
    device_type: str | None = None,
) -> dict[str, Any]:
    """
    Shape game-final notification.

    Sent once when game reaches abstract state "Final".
    """
    linescore = feed.get("liveData", {}).get("linescore", {}).get("teams", {})
    away_score = linescore.get("away", {}).get("runs", 0)
    home_score = linescore.get("home", {}).get("runs", 0)

    payload = {
        "title": "Final",
        "body": f"{away_abbr} {away_score} – {home_abbr} {home_score}",
        "gamePk": game_pk,
        "atBatIndex": -1,
        "requireInteraction": _wants_persistent_alert(device_type),
    }

    # The winner's logo in front. A tie here means a suspended game resumed to
    # an equal score; home is an arbitrary but harmless choice rather than a
    # branch.
    away_won = away_score > home_score
    icon = matchup_icon_url(
        home_id if away_won else away_id,
        away_id if away_won else home_id,
    )

    if icon:
        payload["icon"] = icon

    return payload


async def _handle_final(
    db: AsyncSession,
    game_pk: int,
    feed: dict[str, Any],
    status: dict[str, Any],
    subscriptions: list[PushSubscription],
    away_abbr: str,
    home_abbr: str,
    away_id: int | None = None,
    home_id: int | None = None,
) -> None:
    """Announce the result if there is one, then stop tracking the game."""
    coded_state = status.get("codedGameState", "")

    if coded_state not in GENUINE_FINAL_CODES:
        # Postponed, cancelled or forfeited. There is no score worth pushing,
        # but the game is not coming back either, so drop the rows.
        for subscription in subscriptions:
            await db.delete(subscription)

        _final_attempts.pop(game_pk, None)

        logger.info(
            "Game %s ended as %r; cleared %d subscriptions without notifying",
            game_pk,
            status.get("detailedState", coded_state),
            len(subscriptions),
        )

        return

    attempts = _final_attempts.get(game_pk, 0) + 1
    give_up = attempts >= MAX_FINAL_SEND_ATTEMPTS
    retry_wanted = False

    for subscription in subscriptions:
        # Built per subscriber: whether a notification may ask to stay on
        # screen depends on the device receiving it.
        payload = _build_final_payload(
            feed,
            game_pk,
            away_abbr,
            home_abbr,
            away_id,
            home_id,
            subscription.device_type,
        )

        result = await push_service.send_push(
            endpoint=subscription.endpoint,
            p256dh=subscription.p256dh,
            auth=subscription.auth,
            payload=payload,
        )

        if result == "failed" and not give_up:
            # Keep the row so the next tick retries. The game stays final, so
            # the retry sends an identical payload.
            retry_wanted = True

            continue

        if result == "failed":
            logger.error(
                "Giving up on final notification for game %s after %d attempts",
                game_pk,
                attempts,
            )
        elif result == "sent":
            logger.info(
                "Sent final for game %s to %s",
                game_pk,
                subscription.endpoint[:40],
            )

        await db.delete(subscription)

    if retry_wanted:
        _final_attempts[game_pk] = attempts

        logger.warning(
            "Final notification for game %s incomplete; retrying (%d/%d)",
            game_pk,
            attempts,
            MAX_FINAL_SEND_ATTEMPTS,
        )
    else:
        _final_attempts.pop(game_pk, None)

        logger.info("Game %s final, notified and cleared subscriptions", game_pk)


def _collect_scoring_plays(
    plays: dict[str, Any],
) -> tuple[list[int], dict[int, dict[str, Any]]]:
    """The at-bat indexes worth announcing, and the plays behind them.

    `scoringPlays` is keyed by COMPLETED at-bat, so a run that scores part way
    through a plate appearance stays invisible until that at-bat ends -
    measured at 46s for a wild pitch, and longer behind a slow at-bat. The
    subscriber sees the run on TV well before the alert arrives, which reads
    as the notifications being broken, so an in-progress play is included as
    soon as a runner reaches home.

    Advancing the cursor to that index is also what stops the duplicate: the
    at-bat lands in `scoringPlays` moments later, by which point it is no
    longer greater than the cursor. The flip side is that a SECOND run later
    in the same at-bat is not announced separately.
    """
    all_plays = plays.get("allPlays", []) or []
    scoring_indexes = sorted(plays.get("scoringPlays", []) or [])

    plays_by_index = {
        play.get("atBatIndex"): play
        for play in all_plays
        if play.get("atBatIndex") is not None
    }

    current_play = plays.get("currentPlay") or {}
    current_index = current_play.get("atBatIndex")

    if (
        current_index is not None
        and current_index not in scoring_indexes
        and _has_run_scored(current_play)
    ):
        scoring_indexes.append(current_index)
        scoring_indexes.sort()

        # `currentPlay` is normally repeated inside `allPlays`, but it is the
        # authoritative copy of an at-bat still in progress.
        plays_by_index[current_index] = current_play

    return scoring_indexes, plays_by_index


def _log_undelivered_plays(
    game_pk: int,
    subscriptions: list[PushSubscription],
    scoring_indexes: list[int],
) -> None:
    """Record any scoring play a subscriber never received, before cleanup.

    `_handle_final` deletes every row, and the cursor goes with it, so a play
    still pending at that moment is discarded with no retry and no evidence.
    The subscriber gets the Final but never the run that decided it - which
    afterwards is indistinguishable from the device not displaying it.
    """
    if not scoring_indexes:
        return

    newest = scoring_indexes[-1]
    behind = [s for s in subscriptions if s.last_at_bat_index < newest]

    if behind:
        logger.error(
            "Game %s ending with %d subscriber(s) still behind play %s; "
            "those scoring alerts were never delivered",
            game_pk,
            len(behind),
            newest,
        )


async def _process_game(db: AsyncSession, game_pk: int) -> None:
    """Check one game and notify every subscriber that is behind."""
    subscriptions = list(
        await db.scalars(
            select(PushSubscription).where(PushSubscription.game_pk == game_pk)
        )
    )

    if not subscriptions:
        return

    client = get_mlb_client()

    try:
        feed = await client.get_game_feed_raw(game_pk)
    except Exception:
        logger.exception("Failed to fetch feed for game %s", game_pk)

        return

    status = feed.get("gameData", {}).get("status", {})
    abstract_state = status.get("abstractGameState", "")

    scoring_indexes, plays_by_index = _collect_scoring_plays(
        feed.get("liveData", {}).get("plays", {})
    )

    away_abbr, home_abbr = _extract_team_abbreviations(feed)
    away_id, home_id = _extract_team_ids(feed)

    for subscription in subscriptions:
        # A brand new subscription starts at -1, which would otherwise replay
        # every run scored so far. Seed it to the current state instead.
        if subscription.last_at_bat_index < 0:
            subscription.last_at_bat_index = (
                scoring_indexes[-1] if scoring_indexes else 0
            )

            continue

        await _notify_subscriber(
            db,
            subscription,
            plays_by_index,
            scoring_indexes,
            away_abbr,
            home_abbr,
            away_id,
            home_id,
        )

    # Once a game ends there is nothing left to announce, so notify and stop
    # tracking it.
    if abstract_state == "Final":
        _log_undelivered_plays(game_pk, subscriptions, scoring_indexes)

        await _handle_final(
            db,
            game_pk,
            feed,
            status,
            subscriptions,
            away_abbr,
            home_abbr,
            away_id,
            home_id,
        )

    await db.commit()


async def _tick() -> float | None:
    """
    Poll every subscribed game that has started or is about to.

    Returns how many seconds to wait before the next pass, or None when there
    is nothing to wait for and the watcher should park entirely.
    """
    session_maker = get_session_maker()
    now = datetime.now(timezone.utc)
    settings = get_settings()

    async with session_maker() as db:
        game_pks = list(
            await db.scalars(select(PushSubscription.game_pk).distinct())
        )

        _forget_unsubscribed_games(set(game_pks))

        if not game_pks:
            return None

        due, next_start = await _partition_games(game_pks, now)

        for game_pk in due:
            await _process_game(db, game_pk)

    if due:
        return float(settings.watcher_poll_seconds)

    if next_start is None:
        # Every game is in the future but none has a usable start time. They
        # were treated as due, so this is unreachable; poll rather than park.
        return float(settings.watcher_poll_seconds)

    lead = timedelta(seconds=settings.watcher_lead_seconds)
    delay = (next_start - lead - now).total_seconds()

    return max(0.0, min(delay, MAX_IDLE_SLEEP_SECONDS))


async def run_scoring_watcher() -> None:
    """
    Poll subscribed games, and only while they are actually being played.

    The serverless database bills for the time it stays awake and suspends
    after a few minutes idle, so every query outside a live game is pure
    waste. Two things keep the loop quiet: with nothing subscribed it parks on
    `_subscriptions_changed` and issues no queries at all, and with only
    future games subscribed it sleeps until shortly before first pitch.

    That second case is what makes subscribing to a whole homestand safe. Ten
    games across ten days would otherwise keep the database awake for ten
    days, which on its own exceeds a monthly compute allowance.

    Single-replica assumption: if the API is ever scaled out, every replica
    would run this loop and subscribers would get duplicate notifications.
    Guard it with a Postgres advisory lock before scaling. The wake-up signal
    and the schedule cache are in-process too, so a second replica would also
    miss subscriptions registered by the first.
    """
    settings = get_settings()
    interval = settings.watcher_poll_seconds

    logger.info("Scoring play watcher started (every %ss)", interval)

    while True:
        # Cleared before the tick, never after: a subscription registered
        # while the tick is running would otherwise be wiped out by the clear
        # and leave the watcher idle with work already waiting for it.
        _subscriptions_changed.clear()

        try:
            delay = await _tick()
        except asyncio.CancelledError:
            logger.info("Scoring play watcher stopped")

            raise
        except Exception:
            # Never let one bad tick kill the loop. Retry on the normal
            # interval rather than parking, so a transient database error
            # cannot strand the watcher with nothing left to wake it.
            logger.exception("Scoring watcher tick failed")

            delay = float(interval)

        if delay is None:
            logger.info(
                "No subscriptions - watcher idle until one is registered. "
                "Database usage: %s",
                db_metrics.snapshot().summary(),
            )

            await _subscriptions_changed.wait()

            continue

        if delay <= interval:
            await asyncio.sleep(delay)

            continue

        logger.info(
            "Nothing due for %.1f min - sleeping until the next game starts. "
            "Database usage: %s",
            delay / 60,
            db_metrics.snapshot().summary(),
        )

        # Interruptible: subscribing to a game that starts shortly must not
        # have to wait out a sleep scheduled for days from now.
        try:
            await asyncio.wait_for(_subscriptions_changed.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
