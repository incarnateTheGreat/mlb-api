"""
Base HTTP client for MLB Stats API.

This module contains the core HTTP client setup and shared methods
that all domain-specific mixins inherit from.
"""

from typing import Any, Optional

import httpx

from app.config import get_settings


class BaseMLBClient:
    """
    Base async HTTP client with connection management.
    
    Provides three HTTP clients:
    - v1 API (statsapi.mlb.com) for schedule, players, etc.
    - v1.1 API (ws.statsapi.mlb.com) for live game data
    - GraphQL (data-graph.mlb.com) for video/article content
    """
    
    USER_AGENT = "mlb-api/1.0"
    
    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.mlb_stats_api_base_url  # v1 API
        self.live_url = settings.mlb_stats_api_live_url  # v1.1 API
        self.graphql_url = "https://data-graph.mlb.com/graphql/"
        self._client_v1: Optional[httpx.AsyncClient] = None
        self._client_live: Optional[httpx.AsyncClient] = None
        self._client_graphql: Optional[httpx.AsyncClient] = None
    
    async def _get_client_v1(self) -> httpx.AsyncClient:
        """Get client for v1 API (schedule, players, etc.)."""
        if self._client_v1 is None or self._client_v1.is_closed:
            self._client_v1 = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=30.0,
                headers={"User-Agent": self.USER_AGENT},
                verify=False,
            )
        return self._client_v1
    
    async def _get_client_live(self) -> httpx.AsyncClient:
        """Get client for v1.1 API (live game data, boxscores)."""
        if self._client_live is None or self._client_live.is_closed:
            self._client_live = httpx.AsyncClient(
                base_url=self.live_url,
                timeout=30.0,
                headers={"User-Agent": self.USER_AGENT},
                verify=False,
            )
        return self._client_live
    
    async def _get_client_graphql(self) -> httpx.AsyncClient:
        """Get client for MLB GraphQL API (content, videos, articles)."""
        if self._client_graphql is None or self._client_graphql.is_closed:
            self._client_graphql = httpx.AsyncClient(
                base_url=self.graphql_url,
                timeout=30.0,
                headers={
                    "User-Agent": self.USER_AGENT,
                    "Apollo-Require-Preflight": "true",
                },
                verify=False,
            )
        return self._client_graphql
    
    async def close(self) -> None:
        """Close all HTTP client connections."""
        if self._client_v1 is not None:
            await self._client_v1.aclose()
            self._client_v1 = None
        if self._client_live is not None:
            await self._client_live.aclose()
            self._client_live = None
        if self._client_graphql is not None:
            await self._client_graphql.aclose()
            self._client_graphql = None
    
    async def _get(self, endpoint: str, params: Optional[dict] = None) -> dict[str, Any]:
        """Make a GET request to the v1 MLB Stats API."""
        client = await self._get_client_v1()
        response = await client.get(endpoint, params=params)
        response.raise_for_status()
        return response.json()
    
    async def _get_live(self, endpoint: str, params: Optional[dict] = None) -> dict[str, Any]:
        """Make a GET request to the v1.1 MLB Stats API (live data)."""
        client = await self._get_client_live()
        response = await client.get(endpoint, params=params)
        response.raise_for_status()
        return response.json()
