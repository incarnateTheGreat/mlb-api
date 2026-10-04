"""
Tests for the unified player/team search.

The SQL itself needs a live Postgres with pg_trgm, so these cover the parts
that can be verified without one: the pre-query guards, the caching layer,
and the row-to-model mapping. The trigram behaviour is verified separately
against real data.

Run with: pytest tests/test_search.py -v
"""

from types import SimpleNamespace

import pytest

from app.models.search import SearchResult
from app.services import search_service


@pytest.fixture(autouse=True)
def clear_cache():
    """Each test starts with an empty cache so results don't leak across."""
    search_service._search_cache.clear()
    yield
    search_service._search_cache.clear()


class RecordingSession:
    """Minimal AsyncSession stand-in that records queries and returns rows."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((statement, params))

        # The first call is the threshold GUC, the second is the search.
        if len(self.calls) == 1:
            return None

        return iter(self.rows)


def make_row(**overrides):
    """Build a row shaped like what SEARCH_SQL returns."""
    defaults = {
        "kind": "player",
        "id": 660271,
        "name": "Shohei Ohtani",
        "slug": None,
        "position": "TWP",
        "team_name": "Los Angeles Dodgers",
        "last_season": 2026,
        "score": 0.83,
    }

    return SimpleNamespace(**{**defaults, **overrides})


# =============================================================================
# Empty and whitespace queries
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "   ", "\t", "\n  "])
async def test_blank_query_returns_empty_without_querying(query):
    """A blank query must not reach the database at all."""
    session = RecordingSession()

    results = await search_service.search(session, query)

    assert results == []
    assert session.calls == []


# =============================================================================
# Result mapping
# =============================================================================


@pytest.mark.asyncio
async def test_player_row_maps_to_player_result():
    session = RecordingSession([make_row()])

    results = await search_service.search(session, "ohtani")

    assert len(results) == 1
    result = results[0]

    assert isinstance(result, SearchResult)
    assert result.type == "player"
    assert result.name == "Shohei Ohtani"
    assert result.position == "TWP"
    assert result.last_season == 2026
    # Players route by id, so they carry no slug.
    assert result.slug is None
    assert str(result.id) in result.image_url
    assert "/people/" in result.image_url


@pytest.mark.asyncio
async def test_team_row_maps_to_team_result():
    session = RecordingSession(
        [
            make_row(
                kind="team",
                id=114,
                name="Cleveland Guardians",
                slug="guardians",
                position=None,
                team_name=None,
                last_season=None,
                score=1.0,
            )
        ]
    )

    results = await search_service.search(session, "cleveland")

    result = results[0]

    assert result.type == "team"
    # Teams route by slug.
    assert result.slug == "guardians"
    assert "/team/" in result.image_url
    assert result.last_season is None


@pytest.mark.asyncio
async def test_mixed_rows_preserve_query_order():
    """Ranking happens in SQL, so the service must not reorder rows."""
    session = RecordingSession(
        [
            make_row(kind="team", id=114, name="Cleveland Guardians", slug="guardians"),
            make_row(kind="player", id=112400, name="Reggie Cleveland"),
        ]
    )

    results = await search_service.search(session, "cleveland")

    assert [r.type for r in results] == ["team", "player"]


# =============================================================================
# Query parameters
# =============================================================================


@pytest.mark.asyncio
async def test_query_is_trimmed_and_wrapped_for_contains_match():
    session = RecordingSession()

    await search_service.search(session, "  cleveland  ", limit=5)

    _statement, params = session.calls[1]

    assert params["q"] == "cleveland"
    # Contains, not prefix, so a surname mid-string still matches.
    assert params["contains"] == "%cleveland%"
    assert params["limit"] == 5


@pytest.mark.asyncio
async def test_threshold_is_set_before_the_search_runs():
    """
    The GUC must be set first, and transaction-locally, so a pooled
    connection handed to another request isn't affected.
    """
    session = RecordingSession()

    await search_service.search(session, "cleveland")

    threshold_statement, threshold_params = session.calls[0]
    sql = str(threshold_statement)

    assert "set_config" in sql
    assert "pg_trgm.word_similarity_threshold" in sql
    assert threshold_params["threshold"] == str(
        search_service.WORD_SIMILARITY_THRESHOLD
    )


# =============================================================================
# Caching
# =============================================================================


@pytest.mark.asyncio
async def test_repeated_query_is_served_from_cache():
    """Typing and backspacing repeats prefixes; those must not re-query."""
    session = RecordingSession([make_row()])

    first = await search_service.search(session, "ohtani")
    calls_after_first = len(session.calls)

    second = await search_service.search(session, "ohtani")

    assert second == first
    assert len(session.calls) == calls_after_first


@pytest.mark.asyncio
async def test_cache_is_case_insensitive():
    session = RecordingSession([make_row()])

    await search_service.search(session, "Ohtani")
    calls_after_first = len(session.calls)

    await search_service.search(session, "oHTaNi")

    assert len(session.calls) == calls_after_first


@pytest.mark.asyncio
async def test_different_limit_is_cached_separately():
    """A larger limit needs its own query, not the shorter cached list."""
    session = RecordingSession([make_row()])

    await search_service.search(session, "ohtani", limit=5)
    calls_after_first = len(session.calls)

    await search_service.search(session, "ohtani", limit=10)

    assert len(session.calls) > calls_after_first
