"""
Unified player/team search backed by Postgres trigram matching.

Uses word_similarity (the <% operator) rather than plain similarity (%).
Plain similarity normalizes over the whole target string, so a short query
scores badly against a long name purely because the name is long —
measured locally, 'cle' vs 'Cleveland Guardians' scores 0.14 with % but
0.75 with <%. Autocomplete needs the latter: it compares the query against
the best-matching word extent in the target, so trailing words stop
counting against the score.

A literal substring match adds a score boost so exact typing outranks
typo-corrected matches, and acts as a floor if the trigram threshold is ever
tuned too high. Both predicates are index-backed by the GIN trigram indexes
on player_index.full_name and team_index.search_text.
"""

from typing import Any

from cachetools import TTLCache
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.search import SearchResult

# Below this, word_similarity matches are noise rather than typos. Measured
# reference points: legitimate matches score 0.75–0.80, including the typo
# 'ohtan' -> 'Shohei Ohtani'. Raise this if the dropdown feels noisy.
WORD_SIMILARITY_THRESHOLD = 0.45

# Boost applied when the query appears literally in the target, so exact
# typing sorts above fuzzy corrections of the same score.
EXACT_MATCH_BOOST = 0.3

# Repeated prefixes recur constantly as someone types and backspaces. This
# keeps most keystrokes from reaching Neon at all, which matters on a plan
# that scales compute to zero after inactivity.
_search_cache: TTLCache = TTLCache(maxsize=500, ttl=300)


# pg_trgm's threshold is a per-connection GUC. set_config(..., true) scopes
# it to the current transaction, so a pooled connection handed to another
# request afterwards is unaffected. SET LOCAL can't be used here because
# Postgres doesn't accept bind parameters in a SET statement.
SET_THRESHOLD_SQL = text(
    "SELECT set_config('pg_trgm.word_similarity_threshold', :threshold, true)"
)


SEARCH_SQL = text(
    """
    WITH matches AS (
        SELECT
            'team'::text        AS kind,
            t.id                AS id,
            t.name              AS name,
            t.slug              AS slug,
            NULL::varchar       AS position,
            NULL::varchar       AS team_name,
            NULL::integer       AS last_season,
            LEAST(
                1.0,
                word_similarity(:q, t.search_text)
                + CASE WHEN t.search_text ILIKE :contains THEN :boost ELSE 0 END
            )::real             AS score
        FROM team_index t
        WHERE :q <% t.search_text
           OR t.search_text ILIKE :contains

        UNION ALL

        SELECT
            'player'::text,
            p.id,
            p.full_name,
            NULL::varchar,
            p.position,
            p.current_team_name,
            p.last_season,
            LEAST(
                1.0,
                word_similarity(:q, p.full_name)
                + CASE WHEN p.full_name ILIKE :contains THEN :boost ELSE 0 END
            )::real
        FROM player_index p
        WHERE :q <% p.full_name
           OR p.full_name ILIKE :contains
    )
    SELECT *
    FROM matches
    ORDER BY
        score DESC,
        -- Short queries match very broadly (query-length normalization means
        -- 'c' scores 0.5 against anything starting with C), so these
        -- tiebreaks are what keep results stable and sensible rather than
        -- arbitrary. Teams first, then most recent players, then alphabetical.
        CASE kind WHEN 'team' THEN 0 ELSE 1 END,
        last_season DESC NULLS LAST,
        name
    LIMIT :limit
    """
)


def _player_image(player_id: int) -> str:
    return f"https://midfield.mlbstatic.com/v1/people/{player_id}/silo/60?zoom=1.2"


def _team_image(team_id: int) -> str:
    return f"https://midfield.mlbstatic.com/v1/team/{team_id}/spots/96"


def _to_result(row: Any) -> SearchResult:
    is_team = row.kind == "team"

    return SearchResult(
        id=row.id,
        type=row.kind,
        name=row.name,
        slug=row.slug,
        image_url=_team_image(row.id) if is_team else _player_image(row.id),
        position=row.position,
        team_name=row.team_name,
        last_season=row.last_season,
        score=row.score,
    )


async def search(db: AsyncSession, query: str, limit: int = 10) -> list[SearchResult]:
    """Search players and teams, returning merged results ranked by score."""
    normalized = query.strip()

    if not normalized:
        return []

    cache_key = (normalized.lower(), limit)
    cached = _search_cache.get(cache_key)

    if cached is not None:
        return cached

    await db.execute(
        SET_THRESHOLD_SQL, {"threshold": str(WORD_SIMILARITY_THRESHOLD)}
    )

    rows = await db.execute(
        SEARCH_SQL,
        {
            "q": normalized,
            # Contains rather than prefix, so "clev" also finds the surname
            # in "Tyler Cleveland".
            "contains": f"%{normalized}%",
            "boost": EXACT_MATCH_BOOST,
            "limit": limit,
        },
    )

    results = [_to_result(row) for row in rows]
    _search_cache[cache_key] = results

    return results
