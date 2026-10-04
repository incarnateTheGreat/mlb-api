"""
Search index tables and response models.

The player/team index is reference data, not cache — it has no TTL and is
refreshed deliberately by scripts/sync_player_index.py. That's why it gets
its own tables rather than living in cached_responses.

MLB's Stats API has no name search: /sports/{sportId}/players requires a
season and returns everyone in it. Covering retired players therefore means
holding the corpus ourselves, which is what these tables are for.

Fuzzy matching is done in Postgres via pg_trgm, so both tables carry a GIN
trigram index on the column that gets searched.
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import Column, DateTime, Index, Integer, String

from app.database import Base


class PlayerIndex(Base):
    """One row per player who has ever appeared in an MLB season."""

    __tablename__ = "player_index"

    # MLB's own person id. Not autoincrement — we're mirroring their ids so
    # a search result can link straight to /player/{id}.
    id = Column(Integer, primary_key=True, autoincrement=False)
    full_name = Column(String(255), nullable=False)
    position = Column(String(10))
    current_team_id = Column(Integer)
    current_team_name = Column(String(100))
    # Most recent season this player appeared in. Used to disambiguate
    # duplicate names in the dropdown and to break ranking ties.
    last_season = Column(Integer, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index(
            "ix_player_index_name_trgm",
            "full_name",
            postgresql_using="gin",
            postgresql_ops={"full_name": "gin_trgm_ops"},
        ),
    )


class TeamIndex(Base):
    """
    The 30 current franchises.

    Deliberately scoped to teams that have a page in the frontend. Returning
    a defunct club we can't route the user to is worse than not returning it.
    """

    __tablename__ = "team_index"

    id = Column(Integer, primary_key=True, autoincrement=False)
    slug = Column(String(50), nullable=False, unique=True)
    name = Column(String(100), nullable=False)
    # Every name variant joined into one string, so a trigram match on any of
    # them ("Cleveland", "Guardians", "CLE") hits the row.
    search_text = Column(String(500), nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index(
            "ix_team_index_search_trgm",
            "search_text",
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        ),
    )


class SearchResult(BaseModel):
    """A single player or team match returned to the client."""

    # Serialized as camelCase so the response matches the frontend's
    # conventions directly and no mapping layer is needed there.
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: int
    type: Literal["player", "team"]
    name: str
    # Teams route by slug (/team/{slug}/info); players route by id.
    slug: Optional[str] = None
    image_url: str
    # Players only — these populate the secondary line in the dropdown and
    # are what lets the UI tell two players with the same name apart.
    position: Optional[str] = None
    team_name: Optional[str] = None
    last_season: Optional[int] = None
    score: float
