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


# =============================================================================
# Team Schedule Utilities
# =============================================================================

# Default pitcher stats for missing data
DEFAULT_PITCHER_STATS = {
    "wins": 0,
    "losses": 0,
    "era": "-.--",
    "inningsPitched": "0.0",
    "strikeOuts": 0,
    "baseOnBalls": 0,
    "whip": "-.--",
    "saves": 0,
}


def game_type_bucket(game_type: str) -> str:
    """
    Categorize game type into Spring Training or Regular/Post.
    
    "S"/"E" → Spring Training ("S")
    Everything else → Regular/Post Season ("R")
    """
    return "S" if game_type in ("S", "E") else "R"


def get_date_range_for_month(year: int, month: Optional[str] = None) -> dict[str, str]:
    """
    Get start/end dates for a month or full year.
    
    Args:
        year: Season year
        month: Month name (e.g., "January") or "All" for full year
    
    Returns:
        Dict with "startDate" and "endDate" in ISO format
    """
    from datetime import date
    from calendar import monthrange
    
    if month is None or month == "All":
        return {
            "startDate": f"{year}-01-01",
            "endDate": f"{year}-12-31",
        }
    
    # Map month name to number
    month_names = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ]
    
    try:
        month_num = month_names.index(month) + 1
    except ValueError:
        # Invalid month name, return full year
        return {
            "startDate": f"{year}-01-01",
            "endDate": f"{year}-12-31",
        }
    
    last_day = monthrange(year, month_num)[1]
    
    return {
        "startDate": f"{year}-{month_num:02d}-01",
        "endDate": f"{year}-{month_num:02d}-{last_day}",
    }


def extract_pitcher_ids_from_schedule(schedule_response: dict) -> dict[str, set[int]]:
    """
    Extract pitcher IDs from schedule response for batch stats fetching.
    
    Separates pitchers by game type (Spring Training vs Regular/Post).
    
    Returns:
        Dict with sets of pitcher IDs:
        - springPitcherIds: Probable pitchers + decision pitchers from spring games
        - regularPitcherIds: Same for regular/post season
        - springDecisionIds: Decision pitchers only (for game logs)
        - regularDecisionIds: Same for regular/post season
    """
    spring_pitcher_ids: set[int] = set()
    regular_pitcher_ids: set[int] = set()
    spring_decision_ids: set[int] = set()
    regular_decision_ids: set[int] = set()
    
    for date_entry in schedule_response.get("dates", []):
        for game in date_entry.get("games", []):
            bucket = game_type_bucket(game.get("gameType", "R"))
            pitcher_set = spring_pitcher_ids if bucket == "S" else regular_pitcher_ids
            decision_set = spring_decision_ids if bucket == "S" else regular_decision_ids
            
            teams = game.get("teams", {})
            
            # Probable pitchers
            for side in ["home", "away"]:
                pitcher = teams.get(side, {}).get("probablePitcher", {})
                if pitcher.get("id"):
                    pitcher_set.add(pitcher["id"])
            
            # Decision pitchers
            decisions = game.get("decisions", {})
            for role in ["winner", "loser", "save"]:
                pitcher = decisions.get(role, {})
                if pitcher.get("id"):
                    pitcher_set.add(pitcher["id"])
                    decision_set.add(pitcher["id"])
    
    return {
        "springPitcherIds": spring_pitcher_ids,
        "regularPitcherIds": regular_pitcher_ids,
        "springDecisionIds": spring_decision_ids,
        "regularDecisionIds": regular_decision_ids,
    }


def build_pitcher_stats_map(api_response: dict, bucket: str) -> dict[str, dict]:
    """
    Build a map of pitcher stats from the batch API response.
    
    Args:
        api_response: Response from /people endpoint with stats
        bucket: "S" or "R" for game type
    
    Returns:
        Dict keyed by "{pitcherId}_{bucket}" -> stat dict
    """
    stats_map: dict[str, dict] = {}
    
    for person in api_response.get("people", []):
        stat_data = None
        
        for stat_group in person.get("stats", []):
            if stat_group.get("group", {}).get("displayName") == "pitching":
                splits = stat_group.get("splits", [])
                if splits:
                    stat_data = splits[0].get("stat")
                break
        
        key = f"{person.get('id')}_{bucket}"
        stats_map[key] = stat_data or DEFAULT_PITCHER_STATS
    
    return stats_map


def build_game_log_record_map(api_response: dict) -> dict[str, dict]:
    """
    Build cumulative W-L-S records from game logs.
    
    Args:
        api_response: Response from /people endpoint with gameLog stats
    
    Returns:
        Dict keyed by "{pitcherId}_{gamePk}" -> { wins, losses, saves }
    """
    record_map: dict[str, dict] = {}
    
    for person in api_response.get("people", []):
        # Find the pitching game log
        game_log = None
        for stat_group in person.get("stats", []):
            if (
                stat_group.get("group", {}).get("displayName") == "pitching" and
                stat_group.get("type", {}).get("displayName") == "gameLog"
            ):
                game_log = stat_group
                break
        
        if not game_log:
            continue
        
        # Sort splits by date
        splits = sorted(
            game_log.get("splits", []),
            key=lambda s: s.get("date", "")
        )
        
        # Accumulate wins/losses/saves
        total_wins = 0
        total_losses = 0
        total_saves = 0
        
        for split in splits:
            stat = split.get("stat", {})
            total_wins += stat.get("wins", 0)
            total_losses += stat.get("losses", 0)
            total_saves += stat.get("saves", 0)
            
            game_pk = split.get("game", {}).get("gamePk")
            if game_pk:
                key = f"{person.get('id')}_{game_pk}"
                record_map[key] = {
                    "wins": total_wins,
                    "losses": total_losses,
                    "saves": total_saves,
                }
    
    return record_map


def enrich_probable_pitchers(
    teams: dict,
    stats_map: dict[str, dict],
    game_type: str,
) -> dict:
    """
    Add stats to probable pitchers.
    
    Args:
        teams: Teams object from game
        stats_map: Pitcher stats map from build_pitcher_stats_map
        game_type: Game type code (e.g., "R", "S")
    
    Returns:
        Dict with "away" and "home" probable pitcher data (or None)
    """
    bucket = game_type_bucket(game_type)
    result = {}
    
    for side in ["away", "home"]:
        team = teams.get(side, {})
        pitcher = team.get("probablePitcher")
        
        if pitcher:
            pitcher_id = pitcher.get("id")
            stats_key = f"{pitcher_id}_{bucket}"
            result[side] = {
                **pitcher,
                "abbreviation": team.get("team", {}).get("abbreviation"),
                "stats": stats_map.get(stats_key),
            }
        else:
            result[side] = None
    
    return result


def enrich_decisions(
    decisions: Optional[dict],
    stats_map: dict[str, dict],
    game_type: str,
    game_pk: int,
    game_log_map: dict[str, dict],
) -> Optional[dict]:
    """
    Add stats and records to decision pitchers (winner, loser, save).
    
    Args:
        decisions: Decisions object from game (may be None)
        stats_map: Pitcher stats map
        game_type: Game type code
        game_pk: Game ID
        game_log_map: Game log record map
    
    Returns:
        Dict with enriched winner/loser/save, or None if no decisions
    """
    if not decisions:
        return None
    
    bucket = game_type_bucket(game_type)
    result = {}
    
    for role in ["winner", "loser"]:
        pitcher = decisions.get(role)
        if pitcher:
            pitcher_id = pitcher.get("id")
            stats_key = f"{pitcher_id}_{bucket}"
            record_key = f"{pitcher_id}_{game_pk}"
            result[role] = {
                **pitcher,
                "stats": stats_map.get(stats_key),
                "recordAtGame": game_log_map.get(record_key),
            }
    
    # Save is optional
    save_pitcher = decisions.get("save")
    if save_pitcher:
        pitcher_id = save_pitcher.get("id")
        stats_key = f"{pitcher_id}_{bucket}"
        record_key = f"{pitcher_id}_{game_pk}"
        result["save"] = {
            **save_pitcher,
            "stats": stats_map.get(stats_key),
            "recordAtGame": game_log_map.get(record_key),
        }
    
    return result


def calculate_range_record(
    dates: list[dict],
    team_id: int,
    month: Optional[str] = None,
) -> dict:
    """
    Calculate win-loss record for completed games in a date range.
    
    Args:
        dates: List of date objects from schedule response
        team_id: Team ID to calculate record for
        month: Optional month name (for display purposes)
    
    Returns:
        Dict with wins, losses, and month
    """
    wins = 0
    losses = 0
    
    for date_entry in dates:
        for game in date_entry.get("games", []):
            # Only count completed games
            if game.get("status", {}).get("abstractGameCode") != "F":
                continue
            
            teams = game.get("teams", {})
            
            # Find which side this team is on
            team = None
            if teams.get("away", {}).get("team", {}).get("id") == team_id:
                team = teams["away"]
            elif teams.get("home", {}).get("team", {}).get("id") == team_id:
                team = teams["home"]
            
            if team and team.get("isWinner"):
                wins += 1
            elif team:
                losses += 1
    
    return {
        "wins": wins,
        "losses": losses,
        "month": month,
    }


def get_first_pitch_time(game_date: str, timezone: str) -> str:
    """
    Format game start time in the specified timezone.
    
    Args:
        game_date: ISO datetime string
        timezone: IANA timezone (e.g., "America/Toronto")
    
    Returns:
        Formatted time string (e.g., "7:05 PM EST")
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    
    try:
        dt = datetime.fromisoformat(game_date.replace("Z", "+00:00"))
        tz = ZoneInfo(timezone)
        local_dt = dt.astimezone(tz)
        return local_dt.strftime("%-I:%M %p %Z")
    except Exception:
        return game_date


def refine_game_date(game_date: str) -> str:
    """
    Format game date for display (e.g., "Sat, May 29").
    
    Args:
        game_date: ISO datetime string
    
    Returns:
        Formatted date string
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    
    try:
        dt = datetime.fromisoformat(game_date.replace("Z", "+00:00"))
        tz = ZoneInfo("America/Toronto")
        local_dt = dt.astimezone(tz)
        return local_dt.strftime("%a, %b %-d")
    except Exception:
        return game_date


def process_team_schedule(
    schedule_response: dict,
    team_id: int,
    stats_map: dict[str, dict],
    game_log_map: dict[str, dict],
    timezone: str = "America/Toronto",
    month: Optional[str] = None,
) -> dict:
    """
    Process raw schedule response into refined game data.
    
    This is the main transformation function that:
    - Flattens the dates/games structure
    - Enriches with pitcher stats
    - Calculates records
    - Formats dates and times
    
    Args:
        schedule_response: Raw MLB API schedule response
        team_id: Team ID
        stats_map: Combined pitcher stats map (spring + regular)
        game_log_map: Combined game log record map
        timezone: Timezone for first pitch times
        month: Month filter (for record calculation)
    
    Returns:
        Dict with processed games and metadata
    """
    dates = schedule_response.get("dates", [])
    
    # Filter completed games for record calculation
    completed_dates = [
        d for d in dates
        if d.get("games", [{}])[0].get("status", {}).get("abstractGameCode") == "F"
    ]
    range_record = calculate_range_record(completed_dates, team_id, month)
    
    # Process each game
    games = []
    selected_team = None
    
    for date_entry in dates:
        for game in date_entry.get("games", []):
            game_pk = game.get("gamePk")
            game_date = game.get("gameDate", "")
            game_type = game.get("gameType", "R")
            status = game.get("status", {})
            linescore = game.get("linescore", {})
            teams = game.get("teams", {})
            decisions = game.get("decisions")
            
            # Find selected team data
            for side in ["home", "away"]:
                if teams.get(side, {}).get("team", {}).get("id") == team_id:
                    selected_team = teams[side]
                    break
            
            # Find opponent
            opponent = None
            for side in ["home", "away"]:
                team_data = teams.get(side, {})
                if team_data.get("team", {}).get("id") != team_id:
                    opponent = {
                        "id": team_data.get("team", {}).get("id"),
                        "team": side,
                        "clubName": team_data.get("team", {}).get("clubName"),
                        "abbreviation": team_data.get("team", {}).get("abbreviation"),
                        "isWinner": team_data.get("isWinner"),
                    }
                    break
            
            abstract_game_state = status.get("abstractGameState")
            is_active_or_final = abstract_game_state in ("Live", "Suspended", "Final")
            
            games.append({
                "gamePk": game_pk,
                "gameDate": game_date,
                "gameType": game_type,
                "officialDate": refine_game_date(game_date),
                "abstractGameState": abstract_game_state,
                "currentInning": linescore.get("currentInning"),
                "inningState": f"{linescore.get('inningState', '')} {linescore.get('currentInning', '')}".strip(),
                "opponent": opponent,
                "detailedState": status.get("detailedState"),
                "probablePitchers": enrich_probable_pitchers(teams, stats_map, game_type),
                "decisions": enrich_decisions(
                    decisions, stats_map, game_type, game_pk, game_log_map
                ),
                "firstPitch": get_first_pitch_time(game_date, timezone),
                "scoreline": (
                    f"{linescore.get('teams', {}).get('away', {}).get('runs')}-"
                    f"{linescore.get('teams', {}).get('home', {}).get('runs')}"
                    if is_active_or_final else None
                ),
                "leagueRecord": (
                    f"{selected_team.get('leagueRecord', {}).get('wins')}-"
                    f"{selected_team.get('leagueRecord', {}).get('losses')}"
                    if is_active_or_final and selected_team else None
                ),
            })
    
    return {
        "games": games,
        "monthRecord": range_record,
        "selectedTeam": selected_team,
    }

