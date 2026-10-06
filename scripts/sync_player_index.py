"""
Download every MLB player and the 30 current teams into Neon.

MLB's API has no name search, so the only way to search by name is to hold
the corpus ourselves. /sports/{sportId}/players requires a season, so
covering retired players means walking every season since 1876 — roughly 150
requests. That is why this is a deliberate, occasional job rather than
something the API does at startup.

Run after the API has booted at least once, so init_db() has created the
tables and installed pg_trgm:

    python -m scripts.sync_player_index

In production this runs weekly from .github/workflows/sync-player-index.yml,
which can also be dispatched manually to populate a freshly deployed
database. Safe to re-run: every write is an upsert, so a partial failure
just means running it again.

Uses the app's own MLBStatsClient rather than a fresh httpx client so it
inherits the same TLS settings, headers and connection pooling — one place
to change if any of that needs to change.
"""

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.database import get_session_maker
from app.models.search import PlayerIndex, TeamIndex
from app.services.mlb_client import TEAM_INDEX, MLBStatsClient, get_mlb_client

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

FIRST_SEASON = 1876
SPORT_ID = 1

# Deliberately low. This is MLB's server, not ours, and the job is not
# time-sensitive — a few extra minutes is a fair trade for being a good
# citizen of an API we don't pay for.
CONCURRENCY = 4

# Postgres caps a statement at 65535 bind parameters. At ~7 columns per row
# this stays comfortably under that.
CHUNK_SIZE = 1000


def _now() -> datetime:
    """Current UTC time as a naive datetime, matching the column type."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def fetch_season(
    client: MLBStatsClient,
    season: int,
    semaphore: asyncio.Semaphore,
) -> tuple[int, list[dict[str, Any]]]:
    """Fetch one season's players. A failed season is skipped, not fatal."""
    async with semaphore:
        try:
            data = await client._get(
                f"/sports/{SPORT_ID}/players", params={"season": season}
            )
        except Exception as exc:
            logger.warning("  %s: request failed (%s), skipping", season, exc)
            return season, []

        people = data.get("people", [])
        logger.info("  %s: %d players", season, len(people))

        return season, people


async def collect_players(client: MLBStatsClient) -> dict[int, dict[str, Any]]:
    """
    Walk every season, keeping the most recent record per player.

    A player appears in one row per season they played. We want one row
    total, carrying their latest position and team.
    """
    seasons = range(FIRST_SEASON, date.today().year + 1)
    semaphore = asyncio.Semaphore(CONCURRENCY)
    players: dict[int, dict[str, Any]] = {}

    tasks = [fetch_season(client, season, semaphore) for season in seasons]

    # as_completed lets each season's payload be merged and freed as it
    # arrives, rather than holding all ~150 responses in memory at once.
    for coroutine in asyncio.as_completed(tasks):
        season, people = await coroutine

        for person in people:
            player_id = person.get("id")
            full_name = person.get("fullName")

            if not player_id or not full_name:
                continue

            existing = players.get(player_id)

            # Completion order is nondeterministic, so compare seasons
            # explicitly instead of relying on last-write-wins.
            if existing is not None and existing["last_season"] > season:
                continue

            team = person.get("currentTeam") or {}
            position = person.get("primaryPosition") or {}

            players[player_id] = {
                "id": player_id,
                "full_name": full_name,
                "position": position.get("abbreviation"),
                "current_team_id": team.get("id"),
                "current_team_name": team.get("name"),
                "last_season": season,
            }

    return players


async def collect_teams(client: MLBStatsClient) -> list[dict[str, Any]]:
    """
    Fetch the 30 franchises the frontend has pages for.

    TEAM_INDEX is the source of truth for which teams are routable; anything
    MLB returns that isn't in it gets dropped.
    """
    slug_by_id = {team_id: slug for slug, team_id in TEAM_INDEX.items()}
    payload = await client._get("/teams", params={"sportId": SPORT_ID})

    teams: list[dict[str, Any]] = []

    for team in payload.get("teams", []):
        team_id = team.get("id")
        slug = slug_by_id.get(team_id)

        if not slug:
            continue

        # Collecting every variant means "Cleveland", "Guardians", "CLE" and
        # "guardians" all trigram-match the same row.
        variants = {
            team.get("name", ""),
            team.get("locationName", ""),
            team.get("clubName", ""),
            team.get("shortName", ""),
            team.get("franchiseName", ""),
            team.get("abbreviation", ""),
            slug,
        }

        teams.append(
            {
                "id": team_id,
                "slug": slug,
                "name": team.get("name", slug.title()),
                "search_text": " ".join(sorted(v for v in variants if v)),
            }
        )

    return teams


async def upsert_players(players: list[dict[str, Any]]) -> None:
    session_maker = get_session_maker()
    now = _now()

    async with session_maker() as session:
        for start in range(0, len(players), CHUNK_SIZE):
            chunk = [
                {**row, "updated_at": now}
                for row in players[start : start + CHUNK_SIZE]
            ]

            statement = insert(PlayerIndex).values(chunk)
            statement = statement.on_conflict_do_update(
                index_elements=[PlayerIndex.id],
                set_={
                    "full_name": statement.excluded.full_name,
                    "position": statement.excluded.position,
                    "current_team_id": statement.excluded.current_team_id,
                    "current_team_name": statement.excluded.current_team_name,
                    "last_season": statement.excluded.last_season,
                    "updated_at": statement.excluded.updated_at,
                },
            )

            await session.execute(statement)

        await session.commit()


async def upsert_teams(teams: list[dict[str, Any]]) -> None:
    if not teams:
        logger.warning("No teams collected — skipping team upsert.")
        return

    session_maker = get_session_maker()
    now = _now()

    async with session_maker() as session:
        statement = insert(TeamIndex).values(
            [{**row, "updated_at": now} for row in teams]
        )
        statement = statement.on_conflict_do_update(
            index_elements=[TeamIndex.id],
            set_={
                "slug": statement.excluded.slug,
                "name": statement.excluded.name,
                "search_text": statement.excluded.search_text,
                "updated_at": statement.excluded.updated_at,
            },
        )

        await session.execute(statement)
        await session.commit()


async def main() -> None:
    client = get_mlb_client()

    try:
        logger.info("Fetching teams...")
        teams = await collect_teams(client)
        await upsert_teams(teams)
        logger.info("Upserted %d teams.\n", len(teams))

        logger.info("Fetching players by season (this takes a few minutes)...")
        players = await collect_players(client)
    finally:
        await client.close()

    if not players:
        logger.error("No players collected — aborting without writing.")
        return

    logger.info("\nUpserting %d unique players...", len(players))
    await upsert_players(list(players.values()))

    session_maker = get_session_maker()

    async with session_maker() as session:
        player_count = await session.scalar(
            select(func.count()).select_from(PlayerIndex)
        )
        team_count = await session.scalar(
            select(func.count()).select_from(TeamIndex)
        )

    logger.info(
        "\nDone. player_index: %s rows, team_index: %s rows.",
        player_count,
        team_count,
    )


if __name__ == "__main__":
    asyncio.run(main())
