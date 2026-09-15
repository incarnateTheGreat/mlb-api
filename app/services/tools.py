"""Deterministic MLB data tools used by Copilot orchestration."""

import asyncio
import hashlib
import json
import time
from datetime import date
from enum import Enum
from typing import Any, Optional

from cachetools import TTLCache

from app.services.mlb_client import get_mlb_client


class ToolType(str, Enum):
    """Enumeration of available tool types."""
    GET_GAME_SUMMARY = "get_game_summary"
    GET_TEAM_SCHEDULE = "get_team_schedule"
    GET_PLAYER_STAT_SPLIT = "get_player_stat_split"


class ToolError(Exception):
    """Base exception for tool execution failures."""
    pass


class Tool:
    """Base class for MLB tools."""
    
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.mlb_client = get_mlb_client()
        self.timeout_seconds = 10.0
    
    async def execute(self, **kwargs) -> dict[str, Any]:
        """
        Execute the tool with the given inputs.
        
        Must be implemented by subclasses.
        
        Returns:
            dict with 'success', 'data', and optional 'error' keys
        """
        raise NotImplementedError


class GetGameSummaryTool(Tool):
    """
    Fetch game summary by game ID.
    
    Args:
        game_pk (int): The game ID from MLB StatsAPI
    
    Returns:
        Game boxscore, score, status, and key performers
    """
    
    def __init__(self) -> None:
        super().__init__(
            name=ToolType.GET_GAME_SUMMARY.value,
            description="Fetch game summary, score, and status by game ID"
        )
    
    async def execute(self, game_pk: int, **kwargs) -> dict[str, Any]:
        """Execute the game summary tool."""
        start_time = time.time()
        
        try:
            # Validate input
            if not isinstance(game_pk, int) or game_pk <= 0:
                raise ToolError(f"Invalid game_pk: {game_pk}. Must be positive integer.")
            
            boxscore = await asyncio.wait_for(
                self.mlb_client.get_game_boxscore(game_pk),
                timeout=self.timeout_seconds,
            )
            
            # Normalize output
            result = {
                "success": True,
                "data": {
                    "game_id": boxscore.game_id,
                    "game_date": boxscore.game_date.isoformat() if boxscore.game_date else None,
                    "status": {
                        "abstract_state": boxscore.status.abstract_state,
                        "detailed_state": boxscore.status.detailed_state,
                        "status_code": boxscore.status.status_code,
                    },
                    "home_team": {
                        "id": boxscore.home.team.id,
                        "name": boxscore.home.team.name,
                        "abbreviation": boxscore.home.team.abbreviation,
                        "runs": boxscore.home.runs,
                        "hits": boxscore.home.hits,
                        "errors": boxscore.home.errors,
                    },
                    "away_team": {
                        "id": boxscore.away.team.id,
                        "name": boxscore.away.team.name,
                        "abbreviation": boxscore.away.team.abbreviation,
                        "runs": boxscore.away.runs,
                        "hits": boxscore.away.hits,
                        "errors": boxscore.away.errors,
                    },
                    "top_performers": [p.model_dump(mode="json") for p in boxscore.top_performers],
                },
                "latency_ms": int((time.time() - start_time) * 1000),
            }
            return result
        
        except ToolError as e:
            return {
                "success": False,
                "error": str(e),
                "latency_ms": int((time.time() - start_time) * 1000),
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Unexpected error: {str(e)}",
                "latency_ms": int((time.time() - start_time) * 1000),
            }


class GetTeamScheduleTool(Tool):
    """
    Fetch team schedule for a date range.
    
    Args:
        team_id (int): The MLB team ID
        start_date (str): Start date in YYYY-MM-DD format
        end_date (str): End date in YYYY-MM-DD format
    
    Returns:
        List of games in chronological order with dates and opponents
    """
    
    def __init__(self) -> None:
        super().__init__(
            name=ToolType.GET_TEAM_SCHEDULE.value,
            description="Fetch team schedule for a date range"
        )
    
    async def execute(
        self,
        team_id: int,
        start_date: str,
        end_date: str,
        **kwargs,
    ) -> dict[str, Any]:
        """Execute the team schedule tool."""
        start_time = time.time()
        
        try:
            # Validate inputs
            if not isinstance(team_id, int) or team_id <= 0:
                raise ToolError(f"Invalid team_id: {team_id}. Must be positive integer.")
            
            if not isinstance(start_date, str) or not isinstance(end_date, str):
                raise ToolError("start_date and end_date must be strings in YYYY-MM-DD format")

            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
            if start > end:
                raise ToolError("start_date must be less than or equal to end_date")
            
            schedule = await asyncio.wait_for(
                self.mlb_client.get_schedule_range(start_date=start, end_date=end),
                timeout=self.timeout_seconds,
            )
            
            team_games: list[dict[str, Any]] = []
            for day in schedule.get("dates", []):
                for game in day.get("games", []):
                    home_team = game.get("teams", {}).get("home", {}).get("team", {})
                    away_team = game.get("teams", {}).get("away", {}).get("team", {})
                    if home_team.get("id") != team_id and away_team.get("id") != team_id:
                        continue

                    team_games.append(
                        {
                            "game_pk": game.get("gamePk"),
                            "game_date": game.get("gameDate"),
                            "game_type": game.get("gameType"),
                            "status": game.get("status", {}).get("detailedState"),
                            "home_team": home_team.get("name"),
                            "away_team": away_team.get("name"),
                        }
                    )
            
            result = {
                "success": True,
                "data": {
                    "team_id": team_id,
                    "start_date": start_date,
                    "end_date": end_date,
                    "games_count": len(team_games),
                    "games": team_games,
                },
                "latency_ms": int((time.time() - start_time) * 1000),
            }
            return result
        
        except ToolError as e:
            return {
                "success": False,
                "error": str(e),
                "latency_ms": int((time.time() - start_time) * 1000),
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Unexpected error: {str(e)}",
                "latency_ms": int((time.time() - start_time) * 1000),
            }


class GetPlayerStatSplitTool(Tool):
    """
    Fetch player statistics for a specific split (season, vs team, etc).
    
    Args:
        player_id (int): The MLB player ID
        season (int): The season year (e.g., 2024)
        split_type (str): Type of split (season, month, team, etc)
    
    Returns:
        Player statistical line for the requested split
    """
    
    def __init__(self) -> None:
        super().__init__(
            name=ToolType.GET_PLAYER_STAT_SPLIT.value,
            description="Fetch player stats for a specific split (season, team, etc)"
        )
    
    async def execute(
        self,
        player_id: int,
        season: int,
        split_type: str = "season",
        **kwargs
    ) -> dict[str, Any]:
        """Execute the player stat split tool."""
        start_time = time.time()
        
        try:
            # Validate inputs
            if not isinstance(player_id, int) or player_id <= 0:
                raise ToolError(f"Invalid player_id: {player_id}. Must be positive integer.")
            
            if not isinstance(season, int) or season < 1900 or season > 2100:
                raise ToolError(f"Invalid season: {season}. Must be reasonable year.")
            
            if not isinstance(split_type, str):
                raise ToolError("split_type must be a string")
            
            group = "hitting"
            if split_type.lower() in {"pitching", "pitcher"}:
                group = "pitching"

            stats = await asyncio.wait_for(
                self.mlb_client.get_player_stats(
                    player_id=player_id,
                    season=season,
                    group=group,
                ),
                timeout=self.timeout_seconds,
            )
            
            result = {
                "success": True,
                "data": {
                    "player_id": player_id,
                    "season": season,
                    "split_type": split_type,
                    "stats_group": group,
                    "stats": stats,
                },
                "latency_ms": int((time.time() - start_time) * 1000),
            }
            return result
        
        except ToolError as e:
            return {
                "success": False,
                "error": str(e),
                "latency_ms": int((time.time() - start_time) * 1000),
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Unexpected error: {str(e)}",
                "latency_ms": int((time.time() - start_time) * 1000),
            }


class ToolRegistry:
    """
    Registry and dispatcher for all available tools.
    
    Provides a central location for tool registration, discovery, and execution.
    """
    
    def __init__(self) -> None:
        """Initialize the tool registry with all available tools."""
        self.tools: dict[str, Tool] = {}
        self.cache: TTLCache = TTLCache(maxsize=500, ttl=180)
        self._register_default_tools()
    
    def _register_default_tools(self) -> None:
        """Register all default tools."""
        game_summary_tool = GetGameSummaryTool()
        team_schedule_tool = GetTeamScheduleTool()
        player_stats_tool = GetPlayerStatSplitTool()
        
        self.register(game_summary_tool)
        self.register(team_schedule_tool)
        self.register(player_stats_tool)
    
    def register(self, tool: Tool) -> None:
        """
        Register a tool in the registry.
        
        Args:
            tool: A Tool instance to register
        """
        self.tools[tool.name] = tool
    
    def get_tool(self, name: str) -> Optional[Tool]:
        """
        Retrieve a tool by name.
        
        Args:
            name: The tool name
        
        Returns:
            Tool instance or None if not found
        """
        return self.tools.get(name)
    
    def list_tools(self) -> list[dict[str, str]]:
        """
        List all available tools with descriptions.
        
        Returns:
            List of dicts with name and description
        """
        return [
            {"name": tool.name, "description": tool.description}
            for tool in self.tools.values()
        ]
    
    async def execute_tool(self, tool_name: str, **kwargs) -> dict[str, Any]:
        """
        Execute a tool by name with the given inputs.
        
        Args:
            tool_name: Name of the tool to execute
            **kwargs: Arguments to pass to the tool
        
        Returns:
            Tool execution result with success/error status
        """
        tool = self.get_tool(tool_name)
        if tool is None:
            return {
                "success": False,
                "error": f"Tool '{tool_name}' not found",
            }
        
        return await tool.execute(**kwargs)

    async def execute_tool_cached(self, tool_name: str, **kwargs) -> dict[str, Any]:
        """Execute tool with normalized cache lookup and cache hit metadata."""
        cache_key = self._make_cache_key(tool_name, kwargs)
        if cache_key in self.cache:
            cached_result = dict(self.cache[cache_key])
            cached_result["cached"] = True
            return cached_result

        result = await self.execute_tool(tool_name, **kwargs)
        result["cached"] = False
        if result.get("success"):
            self.cache[cache_key] = dict(result)
        return result

    def _make_cache_key(self, tool_name: str, kwargs: dict[str, Any]) -> str:
        normalized = json.dumps({"tool_name": tool_name, "kwargs": kwargs}, sort_keys=True, default=str)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# Global tool registry instance
_tool_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """
    Get or create the global tool registry.
    
    Lazy-loads tools on first call.
    """
    global _tool_registry
    if _tool_registry is None:
        _tool_registry = ToolRegistry()
    return _tool_registry
