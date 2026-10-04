"""
Search router — unified player and team lookup.

Unlike the other routers, this one isn't a REST resource collection. It's a
capability, so it returns a heterogeneous list: players and teams together,
discriminated by a `type` field the client switches on to decide where to
route and which image to render.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.search import SearchResult
from app.services import search_service


router = APIRouter()


# Registered as "" rather than "/" so the canonical path is /search with no
# trailing slash. The frontend reaches this through a splat proxy route that
# drops trailing slashes, and FastAPI's redirect_slashes would answer with a
# 307 whose Location points at the API's own origin — defeating the proxy.
@router.get("")
async def search(
    q: Annotated[
        str, Query(min_length=1, max_length=100, description="Search text")
    ],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    db: Annotated[AsyncSession, Depends(get_db)] = None,
) -> list[SearchResult]:
    """
    Fuzzy search across every MLB player and the 30 current franchises.

    Matching is typo-tolerant and partial, so "clev" returns the Guardians
    and "Cleveland" returns both the Guardians and players named Cleveland.
    """
    return await search_service.search(db, q, limit)
