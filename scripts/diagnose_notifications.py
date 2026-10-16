"""
Answer "why didn't I get a notification?" from the server's side.

Prints what the API currently knows - every registered subscription, its
cursor, and which scoring plays are still pending for it. A device that has
stopped receiving alerts usually has NO ROW HERE AT ALL: the browser rotated
its push endpoint, the watcher deleted the row it could no longer reach, and
nothing told the browser. The toggle keeps reading as on.

    PYTHONPATH=. venv/bin/python -m scripts.diagnose_notifications
"""

import asyncio

from sqlalchemy import select

from app.database import get_session_maker
from app.models.notifications import PushSubscription
from app.services.mlb_client import get_mlb_client
from app.services.scoring_watcher import _collect_scoring_plays


async def main() -> None:
    session_maker = get_session_maker()

    async with session_maker() as db:
        rows = list(await db.scalars(select(PushSubscription)))

    if not rows:
        print("No subscriptions registered.")

        return

    print(f"{len(rows)} subscription(s)\n")

    for row in rows:
        print(
            f"  id={row.id} game={row.game_pk} cursor={row.last_at_bat_index} "
            f"device={row.device_type} endpoint=...{row.endpoint[-12:]}"
        )

    client = get_mlb_client()

    for game_pk in sorted({row.game_pk for row in rows}):
        try:
            feed = await client.get_game_feed_raw(game_pk)
        except Exception as exc:  # noqa: BLE001 - diagnostic script
            print(f"\ngame {game_pk}: feed fetch failed: {exc}")

            continue

        status = feed.get("gameData", {}).get("status", {})
        scoring, _ = _collect_scoring_plays(feed.get("liveData", {}).get("plays", {}))

        print(
            f"\ngame {game_pk}: {status.get('abstractGameState')} / "
            f"{status.get('detailedState')} (coded {status.get('codedGameState')})"
        )
        print(f"  scoring plays: {scoring or 'none yet'}")

        for row in rows:
            if row.game_pk != game_pk:
                continue

            pending = [i for i in scoring if i > row.last_at_bat_index]

            print(f"  id={row.id} pending={pending or 'none - caught up'}")


if __name__ == "__main__":
    asyncio.run(main())
