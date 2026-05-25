"""
Utility functions for schedule data transformation.

These functions process MLB API responses server-side,
reducing the work needed in the Remix frontend.
"""

from typing import Optional

# League IDs
LEAGUE_AL = 103  # American League
LEAGUE_NL = 104  # National League
LEAGUE_WBC = 160  # World Baseball Classic


def sort_games_by_fav_team(games: list[dict], fav_team: int) -> list[dict]:
    """
    Sort games with favorite team first.
    
    Args:
        games: List of game dicts from MLB API
        fav_team: Team ID to prioritize
        
    Returns:
        Games sorted with fav_team games first, with favTeamSelected flag set
    """
    fav_games = []
    other_games = []
    
    for game in games:
        is_fav_team = (
            game.get("teams", {}).get("away", {}).get("team", {}).get("id") == fav_team or
            game.get("teams", {}).get("home", {}).get("team", {}).get("id") == fav_team
        )
        
        if is_fav_team:
            game["favTeamSelected"] = True
            fav_games.append(game)
        else:
            other_games.append(game)
    
    return fav_games + other_games


def filter_games_by_league(games: list[dict], league_id: int) -> list[dict]:
    """Filter games where the home team belongs to a specific league."""
    return [
        game for game in games
        if game.get("teams", {}).get("home", {}).get("team", {}).get("league", {}).get("id") == league_id
    ]


def get_al_games(games: list[dict], fav_team: Optional[int] = None) -> list[dict]:
    """Get American League games, optionally sorted by favorite team."""
    al_games = filter_games_by_league(games, LEAGUE_AL)
    if fav_team:
        return sort_games_by_fav_team(al_games, fav_team)
    return al_games


def get_nl_games(games: list[dict], fav_team: Optional[int] = None) -> list[dict]:
    """Get National League games, optionally sorted by favorite team."""
    nl_games = filter_games_by_league(games, LEAGUE_NL)
    if fav_team:
        return sort_games_by_fav_team(nl_games, fav_team)
    return nl_games


def get_wbc_games(games: list[dict], fav_team: Optional[int] = None) -> list[dict]:
    """Get World Baseball Classic games, optionally sorted by favorite team."""
    wbc_games = filter_games_by_league(games, LEAGUE_WBC)
    if fav_team:
        return sort_games_by_fav_team(wbc_games, fav_team)
    return wbc_games


def is_postseason(games: list[dict]) -> bool:
    """Check if any game indicates postseason."""
    if not games:
        return False
    return games[0].get("statusFlags", {}).get("isPostSeason", False)


def count_completed_games(games: list[dict]) -> int:
    """Count games that are final."""
    return sum(1 for game in games if game.get("statusFlags", {}).get("isFinal", False))


def process_schedule_response(
    data: dict,
    fav_team: Optional[int] = None,
) -> dict:
    """
    Process the full MLB schedule response into a structured format.
    
    Args:
        data: Raw MLB API schedule response
        fav_team: Optional team ID to prioritize in sorting
    
    Returns:
        Dict with AL/NL/WBC games, postseason flag, and completed count
    """
    # Extract games from nested response
    dates = data.get("dates", [])
    games = dates[0].get("games", []) if dates else []
    
    return {
        "ALGames": get_al_games(games, fav_team),
        "NLGames": get_nl_games(games, fav_team),
        "WBCGames": get_wbc_games(games, fav_team),
        "isPostSeason": is_postseason(games),
        "completedGames": count_completed_games(games),
        "totalGames": len(games),
    }
