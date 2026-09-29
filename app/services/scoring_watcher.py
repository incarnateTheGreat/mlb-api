"""
Background watcher that turns scoring plays into push notifications.

This is what makes locked-phone alerts possible. Nobody's browser is connected
when a run scores, so the server has to notice on its own: it polls the MLB
feed for games that have at least one subscriber, diffs the scoring plays
against what each subscriber has already been sent, and pushes the difference.

Runs as a single asyncio task from the FastAPI lifespan. That assumes one
replica - see the note in `run_scoring_watcher` if that ever changes.
"""

import asyncio
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_session_maker
from app.models.notifications import PushSubscription
from app.services import push_service
from app.services.mlb_client import get_mlb_client

logger = logging.getLogger(__name__)


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


def _build_payload(
    play: dict[str, Any],
    game_pk: int,
    away_abbr: str,
    home_abbr: str,
) -> dict[str, Any]:
    """
    Shape one scoring play into a push payload.

    Kept small deliberately - encrypted payloads are capped at ~4KB.
    """
    about = play.get("about", {})
    result = play.get("result", {})

    description = result.get("description", "Run scored")
    away_score = result.get("awayScore", 0)
    home_score = result.get("homeScore", 0)

    # Extract inning and half-inning info
    inning = about.get("inning", 0)
    is_top = about.get("isTopInning", True)
    half = "Top" if is_top else "Bot"

    # The batting team is the one that scored.
    scoring_team = away_abbr if is_top else home_abbr

    # Truncate description if too long
    if len(description) > 120:
        description = description[:117] + "..."

    return {
        "title": f"{scoring_team} scores! {away_abbr} {away_score} - {home_abbr} {home_score}" if scoring_team else "Scoring play",
        "body": f"{half} {_ordinal(inning)} · {description}",
        "gamePk": game_pk,
        "atBatIndex": play.get("atBatIndex", -1),
        "requireInteraction": True,
    }


async def _notify_subscriber(
    db: AsyncSession,
    subscription: PushSubscription,
    plays_by_index: dict[int, dict[str, Any]],
    scoring_indexes: list[int],
    away_abbr: str,
    home_abbr: str,
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

        payload = _build_payload(play, subscription.game_pk, away_abbr, home_abbr)

        result = await push_service.send_push(
            endpoint=subscription.endpoint,
            p256dh=subscription.p256dh,
            auth=subscription.auth,
            payload=payload,
        )

        if result == "expired":
            # The browser install is gone; stop tracking it entirely.
            await db.delete(subscription)

            return

        if result == "failed":
            # Leave the cursor where it is so the next tick retries this play.
            break

        highest_sent = index

    if highest_sent != subscription.last_at_bat_index:
        subscription.last_at_bat_index = highest_sent


def _build_final_payload(
    feed: dict[str, Any],
    game_pk: int,
    away_abbr: str,
    home_abbr: str,
) -> dict[str, Any]:
    """
    Shape game-final notification.

    Sent once when game reaches abstract state "Final".
    """
    linescore = feed.get("liveData", {}).get("linescore", {}).get("teams", {})
    away_score = linescore.get("away", {}).get("runs", 0)
    home_score = linescore.get("home", {}).get("runs", 0)

    return {
        "title": "Final",
        "body": f"{away_abbr} {away_score} – {home_abbr} {home_score}",
        "gamePk": game_pk,
        "atBatIndex": -1,
        "requireInteraction": True,
    }


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

    plays = feed.get("liveData", {}).get("plays", {})
    all_plays = plays.get("allPlays", []) or []
    scoring_indexes = sorted(plays.get("scoringPlays", []) or [])

    plays_by_index = {
        play.get("atBatIndex"): play
        for play in all_plays
        if play.get("atBatIndex") is not None
    }

    away_abbr, home_abbr = _extract_team_abbreviations(feed)

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
        )

    # Once a game ends, send final notification and drop all subscriptions.
    if abstract_state == "Final":
        final_payload = _build_final_payload(feed, game_pk, away_abbr, home_abbr)

        for subscription in subscriptions:
            result = await push_service.send_push(
                endpoint=subscription.endpoint,
                p256dh=subscription.p256dh,
                auth=subscription.auth,
                payload=final_payload,
            )

            if result != "expired":
                logger.debug("Sent final notification to subscription %s", subscription.endpoint[:40])

            await db.delete(subscription)

        logger.info("Game %s final, sent and cleared %d subscriptions", game_pk, len(subscriptions))

    await db.commit()


async def _tick() -> None:
    """One pass over every game that currently has subscribers."""
    session_maker = get_session_maker()

    async with session_maker() as db:
        game_pks = list(
            await db.scalars(select(PushSubscription.game_pk).distinct())
        )

        for game_pk in game_pks:
            await _process_game(db, game_pk)


async def run_scoring_watcher() -> None:
    """
    Poll subscribed games forever.

    Single-replica assumption: if the API is ever scaled out, every replica
    would run this loop and subscribers would get duplicate notifications.
    Guard it with a Postgres advisory lock before scaling.
    """
    settings = get_settings()
    interval = settings.watcher_poll_seconds

    logger.info("Scoring play watcher started (every %ss)", interval)

    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            logger.info("Scoring play watcher stopped")

            raise
        except Exception:
            # Never let one bad tick kill the loop.
            logger.exception("Scoring watcher tick failed")

        await asyncio.sleep(interval)
