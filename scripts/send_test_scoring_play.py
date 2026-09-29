"""
Send a real scoring play notification from a completed game.

Lets you verify the full push path out of season, without waiting for a live
game. It runs the production code in app/services/scoring_watcher.py - the
same feed parsing, payload building and delivery - by inserting a subscription
whose cursor is deliberately rewound one scoring play.

Usage:

    1. On any page of the site, grab this browser's push subscription:

       copy(JSON.stringify(
         await (await navigator.serviceWorker.ready)
           .pushManager.getSubscription()
       ))

       If that prints null, click the notification toggle on a game page
       first so the browser creates a subscription.

    2. Save it and run:

       pbpaste > /tmp/sub.json
       ./venv/bin/python scripts/send_test_scoring_play.py /tmp/sub.json

    Pass a specific game with --game-pk, otherwise the most recent completed
    game with scoring plays is used.
"""

import argparse
import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from app.database import get_session_maker
from app.models.notifications import PushSubscription
from app.services import push_service
from app.services.mlb_client import get_mlb_client
from app.services.scoring_watcher import _process_game


async def find_recent_finished_game(days_back: int = 10) -> int | None:
    """Most recent completed game that actually had runs scored."""
    client = get_mlb_client()

    for offset in range(days_back):
        day = date.today() - timedelta(days=offset)

        try:
            schedule = await client.get_schedule(time_zone="UTC", date=day)
        except Exception as exc:  # noqa: BLE001 - diagnostic script
            print(f"  could not fetch {day}: {exc}")

            continue

        dates = schedule.get("dates", [])

        if not dates:
            continue

        for game in dates[0].get("games", []):
            if game.get("status", {}).get("abstractGameState") != "Final":
                continue

            game_pk = game.get("gamePk")
            feed = await client.get_game_feed_raw(game_pk)
            scoring = feed.get("liveData", {}).get("plays", {}).get("scoringPlays", [])

            if scoring:
                away = game["teams"]["away"]["team"]["name"]
                home = game["teams"]["home"]["team"]["name"]
                print(f"Using {away} @ {home} ({day}), gamePk={game_pk}")

                return game_pk

    return None


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subscription", help="Path to the subscription JSON file")
    parser.add_argument("--game-pk", type=int, help="Specific completed game to replay")
    args = parser.parse_args()

    if not push_service.is_push_configured():
        print("VAPID keys are not configured. Check .env.")

        return 1

    raw = json.loads(Path(args.subscription).read_text())
    endpoint = raw["endpoint"]
    keys = raw["keys"]

    game_pk = args.game_pk or await find_recent_finished_game()

    if game_pk is None:
        print("No recent completed game with scoring plays found.")

        return 1

    client = get_mlb_client()
    feed = await client.get_game_feed_raw(game_pk)
    scoring = sorted(feed.get("liveData", {}).get("plays", {}).get("scoringPlays", []))

    if not scoring:
        print(f"Game {game_pk} has no scoring plays.")

        return 1

    # Rewind one play so exactly one notification is sent rather than replaying
    # the whole game.
    cursor = scoring[-2] if len(scoring) > 1 else scoring[-1] - 1

    print(f"Scoring plays: {scoring}")
    print(f"Seeding cursor at {cursor} so atBatIndex {scoring[-1]} is sent.")

    session_maker = get_session_maker()

    async with session_maker() as db:
        # Replace any existing row for this browser/game pair.
        await db.execute(
            delete(PushSubscription).where(
                PushSubscription.endpoint == endpoint,
                PushSubscription.game_pk == game_pk,
            )
        )

        db.add(
            PushSubscription(
                endpoint=endpoint,
                p256dh=keys["p256dh"],
                auth=keys["auth"],
                game_pk=game_pk,
                last_at_bat_index=cursor,
            )
        )

        await db.commit()

        print("Running one watcher tick...")

        # The real production path: parses the feed, builds the payload,
        # encrypts and delivers, then cleans up because the game is Final.
        await _process_game(db, game_pk)

        remaining = await db.scalar(
            select(PushSubscription).where(
                PushSubscription.endpoint == endpoint,
                PushSubscription.game_pk == game_pk,
            )
        )

    if remaining is None:
        print("Done - notification sent and the finished game was cleaned up.")
    else:
        print(f"Done - cursor is now {remaining.last_at_bat_index}.")

    print("If nothing appeared, check macOS Settings > Notifications > your browser.")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
