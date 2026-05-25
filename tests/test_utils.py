"""
Tests for schedule utility functions.

Run with: pytest tests/test_utils.py -v
"""

import pytest
from app.utils import (
    sort_games_by_fav_team,
    filter_games_by_league,
    get_al_games,
    get_nl_games,
    get_wbc_games,
    is_postseason,
    count_completed_games,
    process_schedule_response,
    LEAGUE_AL,
    LEAGUE_NL,
    LEAGUE_WBC,
)


# =============================================================================
# Test fixtures - sample game data
# =============================================================================

def make_game(game_pk: int, home_team_id: int, away_team_id: int, league_id: int, is_final: bool = False, is_post: bool = False) -> dict:
    """Helper to create a game dict matching MLB API structure."""
    return {
        "gamePk": game_pk,
        "teams": {
            "home": {
                "team": {
                    "id": home_team_id,
                    "league": {"id": league_id},
                }
            },
            "away": {
                "team": {
                    "id": away_team_id,
                }
            },
        },
        "statusFlags": {
            "isFinal": is_final,
            "isPostSeason": is_post,
        },
    }


@pytest.fixture
def sample_games():
    """Sample games across different leagues."""
    return [
        make_game(1, home_team_id=147, away_team_id=111, league_id=LEAGUE_AL),  # Yankees vs Red Sox (AL)
        make_game(2, home_team_id=141, away_team_id=110, league_id=LEAGUE_AL, is_final=True),  # Blue Jays vs Orioles (AL)
        make_game(3, home_team_id=121, away_team_id=144, league_id=LEAGUE_NL),  # Mets vs Braves (NL)
        make_game(4, home_team_id=119, away_team_id=137, league_id=LEAGUE_NL, is_final=True),  # Dodgers vs Giants (NL)
        make_game(5, home_team_id=999, away_team_id=998, league_id=LEAGUE_WBC),  # WBC game
    ]


@pytest.fixture
def postseason_games():
    """Sample postseason games."""
    return [
        make_game(1, home_team_id=147, away_team_id=111, league_id=LEAGUE_AL, is_post=True),
    ]


# =============================================================================
# sort_games_by_fav_team tests
# =============================================================================

class TestSortGamesByFavTeam:
    """Tests for sort_games_by_fav_team function."""
    
    def test_fav_team_moves_to_front(self, sample_games):
        """Favorite team games should appear first."""
        result = sort_games_by_fav_team(sample_games, fav_team=141)  # Blue Jays
        
        assert result[0]["gamePk"] == 2  # Blue Jays game first
        assert result[0].get("favTeamSelected") is True
    
    def test_away_team_also_matches(self, sample_games):
        """Should match when fav team is away."""
        result = sort_games_by_fav_team(sample_games, fav_team=111)  # Red Sox (away in game 1)
        
        assert result[0]["gamePk"] == 1
        assert result[0].get("favTeamSelected") is True
    
    def test_no_fav_team_preserves_order(self, sample_games):
        """When fav team not in games, order preserved."""
        result = sort_games_by_fav_team(sample_games, fav_team=999999)
        
        # Order should be same as original
        assert [g["gamePk"] for g in result] == [1, 2, 3, 4, 5]
    
    def test_empty_list(self):
        """Should handle empty list."""
        result = sort_games_by_fav_team([], fav_team=141)
        assert result == []
    
    def test_multiple_fav_team_games(self):
        """Multiple games with fav team should all move to front."""
        games = [
            make_game(1, home_team_id=100, away_team_id=200, league_id=LEAGUE_AL),
            make_game(2, home_team_id=141, away_team_id=200, league_id=LEAGUE_AL),  # Jays home
            make_game(3, home_team_id=100, away_team_id=141, league_id=LEAGUE_AL),  # Jays away
            make_game(4, home_team_id=100, away_team_id=200, league_id=LEAGUE_AL),
        ]
        
        result = sort_games_by_fav_team(games, fav_team=141)
        
        # Both Jays games should be first
        assert result[0]["gamePk"] == 2
        assert result[1]["gamePk"] == 3
        assert result[0].get("favTeamSelected") is True
        assert result[1].get("favTeamSelected") is True


# =============================================================================
# filter_games_by_league tests
# =============================================================================

class TestFilterGamesByLeague:
    """Tests for filter_games_by_league function."""
    
    def test_filter_al_games(self, sample_games):
        """Should filter only AL games."""
        result = filter_games_by_league(sample_games, LEAGUE_AL)
        
        assert len(result) == 2
        assert all(g["teams"]["home"]["team"]["league"]["id"] == LEAGUE_AL for g in result)
    
    def test_filter_nl_games(self, sample_games):
        """Should filter only NL games."""
        result = filter_games_by_league(sample_games, LEAGUE_NL)
        
        assert len(result) == 2
        assert all(g["teams"]["home"]["team"]["league"]["id"] == LEAGUE_NL for g in result)
    
    def test_filter_wbc_games(self, sample_games):
        """Should filter only WBC games."""
        result = filter_games_by_league(sample_games, LEAGUE_WBC)
        
        assert len(result) == 1
    
    def test_no_matches(self, sample_games):
        """Should return empty list when no matches."""
        result = filter_games_by_league(sample_games, 999999)
        
        assert result == []
    
    def test_empty_list(self):
        """Should handle empty list."""
        result = filter_games_by_league([], LEAGUE_AL)
        assert result == []
    
    def test_missing_nested_keys(self):
        """Should handle missing nested keys gracefully."""
        games = [{"teams": {}}, {"gamePk": 1}]
        result = filter_games_by_league(games, LEAGUE_AL)
        
        assert result == []  # No crash, just empty


# =============================================================================
# get_al_games / get_nl_games / get_wbc_games tests
# =============================================================================

class TestLeagueHelpers:
    """Tests for league-specific helper functions."""
    
    def test_get_al_games(self, sample_games):
        """Should get AL games."""
        result = get_al_games(sample_games)
        assert len(result) == 2
    
    def test_get_al_games_with_fav_team(self, sample_games):
        """Should get AL games sorted by fav team."""
        result = get_al_games(sample_games, fav_team=141)
        
        assert len(result) == 2
        assert result[0]["gamePk"] == 2  # Blue Jays first
    
    def test_get_nl_games(self, sample_games):
        """Should get NL games."""
        result = get_nl_games(sample_games)
        assert len(result) == 2
    
    def test_get_wbc_games(self, sample_games):
        """Should get WBC games."""
        result = get_wbc_games(sample_games)
        assert len(result) == 1


# =============================================================================
# is_postseason tests
# =============================================================================

class TestIsPostseason:
    """Tests for is_postseason function."""
    
    def test_postseason_true(self, postseason_games):
        """Should return True for postseason games."""
        assert is_postseason(postseason_games) is True
    
    def test_postseason_false(self, sample_games):
        """Should return False for regular season games."""
        assert is_postseason(sample_games) is False
    
    def test_empty_list(self):
        """Should return False for empty list."""
        assert is_postseason([]) is False


# =============================================================================
# count_completed_games tests
# =============================================================================

class TestCountCompletedGames:
    """Tests for count_completed_games function."""
    
    def test_count_finals(self, sample_games):
        """Should count games marked as final."""
        result = count_completed_games(sample_games)
        assert result == 2  # Games 2 and 4 are final
    
    def test_no_finals(self):
        """Should return 0 when no finals."""
        games = [
            make_game(1, 100, 200, LEAGUE_AL, is_final=False),
            make_game(2, 100, 200, LEAGUE_AL, is_final=False),
        ]
        assert count_completed_games(games) == 0
    
    def test_empty_list(self):
        """Should return 0 for empty list."""
        assert count_completed_games([]) == 0


# =============================================================================
# process_schedule_response tests
# =============================================================================

class TestProcessScheduleResponse:
    """Tests for process_schedule_response function."""
    
    def test_full_response(self, sample_games):
        """Should process full schedule response."""
        raw_data = {"dates": [{"games": sample_games}]}
        
        result = process_schedule_response(raw_data)
        
        assert len(result["ALGames"]) == 2
        assert len(result["NLGames"]) == 2
        assert len(result["WBCGames"]) == 1
        assert result["isPostSeason"] is False
        assert result["completedGames"] == 2
        assert result["totalGames"] == 5
    
    def test_with_fav_team(self, sample_games):
        """Should sort by fav team."""
        raw_data = {"dates": [{"games": sample_games}]}
        
        result = process_schedule_response(raw_data, fav_team=141)
        
        # Blue Jays game should be first in AL
        assert result["ALGames"][0]["gamePk"] == 2
    
    def test_empty_dates(self):
        """Should handle empty dates."""
        result = process_schedule_response({"dates": []})
        
        assert result["ALGames"] == []
        assert result["NLGames"] == []
        assert result["WBCGames"] == []
        assert result["totalGames"] == 0
    
    def test_missing_dates_key(self):
        """Should handle missing dates key."""
        result = process_schedule_response({})
        
        assert result["totalGames"] == 0
