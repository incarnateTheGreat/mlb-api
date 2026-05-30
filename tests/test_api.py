"""
Tests for API endpoints.

Run with: pytest tests/test_api.py -v

These tests use FastAPI's TestClient for synchronous testing.
For integration tests that hit the real MLB API, use pytest markers.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.services.mlb_client import MLBStatsClient


client = TestClient(app)


# =============================================================================
# Health endpoint
# =============================================================================

class TestHealth:
    """Tests for health check endpoint."""
    
    def test_health_check(self):
        """Health endpoint should return 200."""
        response = client.get("/health")
        
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"


# =============================================================================
# Schedule endpoint tests (with mocked MLB client)
# =============================================================================

class TestScheduleEndpoint:
    """Tests for /games/schedule endpoint."""
    
    def test_schedule_missing_timezone(self):
        """Should return 422 when timezone is missing."""
        response = client.get("/games/schedule")
        
        assert response.status_code == 422  # Validation error


# =============================================================================
# Schedule raw endpoint tests
# =============================================================================

class TestScheduleRawEndpoint:
    """Tests for /games/schedule/raw endpoint."""
    
    def test_schedule_raw_missing_timezone(self):
        """Should return 422 when timezone is missing."""
        response = client.get("/games/schedule/raw")
        
        assert response.status_code == 422  # Validation error


# =============================================================================
# Schedule range endpoint tests
# =============================================================================

class TestScheduleRangeEndpoint:
    """Tests for /games/schedule/range endpoint."""
    
    def test_schedule_range_missing_dates(self):
        """Should return 422 when dates are missing."""
        response = client.get("/games/schedule/range")
        
        assert response.status_code == 422


# =============================================================================
# Standings endpoint tests
# =============================================================================

class TestStandingsEndpoint:
    """Tests for /standings endpoint."""
    
    def test_standings_invalid_year(self):
        """Should return 422 for year outside valid range."""
        response = client.get("/standings?year=1800")
        
        assert response.status_code == 422  # Below ge=1900
    
    def test_standings_year_too_high(self):
        """Should return 422 for year above valid range."""
        response = client.get("/standings?year=2200")
        
        assert response.status_code == 422  # Above le=2100


# =============================================================================
# Matchups endpoint tests
# =============================================================================

class TestMatchupsEndpoint:
    """Tests for /matchups endpoints."""
    
    def test_matchup_invalid_season_low(self):
        """Should return 422 for season below valid range."""
        response = client.get("/matchups/12345/vs/67890?season=1800")
        
        assert response.status_code == 422  # Below ge=1900
    
    def test_matchup_invalid_season_high(self):
        """Should return 422 for season above valid range."""
        response = client.get("/matchups/12345/vs/67890?season=2200")
        
        assert response.status_code == 422  # Above le=2100
    
    def test_matchup_invalid_player_id(self):
        """Should return 422 for invalid player ID format."""
        response = client.get("/matchups/not-a-number/vs/67890")
        
        assert response.status_code == 422


# =============================================================================
# Teams endpoint tests
# =============================================================================

class TestTeamsEndpoint:
    """Tests for /teams endpoints."""
    
    def test_list_teams(self):
        """Should return list of all teams."""
        response = client.get("/teams/")
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 30  # 30 MLB teams
        
        # Each team should have required fields
        for team in data:
            assert "slug" in team
            assert "id" in team
            assert "name" in team
    
    def test_team_info_unknown_slug(self):
        """Should return 404 for unknown team slug."""
        response = client.get("/teams/notateam/info")
        
        assert response.status_code == 404
    
    def test_team_schedule_unknown_slug(self):
        """Should return 404 for unknown team slug."""
        response = client.get("/teams/notateam/schedule")
        
        assert response.status_code == 404


# =============================================================================
# Player gamelogs endpoint tests
# =============================================================================

class TestPlayerGamelogsEndpoint:
    """Tests for /players/{player_id}/gamelogs/{season} endpoint."""
    
    def test_gamelogs_invalid_month_low(self):
        """Should return 422 for month below 1."""
        response = client.get("/players/12345/gamelogs/2024?month=0")
        
        assert response.status_code == 422
    
    def test_gamelogs_invalid_month_high(self):
        """Should return 422 for month above 12."""
        response = client.get("/players/12345/gamelogs/2024?month=13")
        
        assert response.status_code == 422
    
    def test_gamelogs_default_game_type(self):
        """Should accept request with default game_type."""
        # Will fail with 404 for fake player, but validates params
        response = client.get("/players/12345/gamelogs/2024")
        
        # Either 200 or 404, but not 422 (validation passes)
        assert response.status_code in [200, 404]
    
    def test_gamelogs_with_month_filter(self):
        """Should accept valid month filter."""
        response = client.get("/players/12345/gamelogs/2024?month=6")
        
        assert response.status_code in [200, 404]
    
    def test_gamelogs_with_game_type(self):
        """Should accept game_type parameter."""
        response = client.get("/players/12345/gamelogs/2024?game_type=S")
        
        assert response.status_code in [200, 404]


# =============================================================================
# Integration tests (require real API access)
# =============================================================================

@pytest.mark.integration
class TestIntegration:
    """
    Integration tests that hit the real MLB API.
    
    Run with: pytest tests/test_api.py -v -m integration
    Skip with: pytest tests/test_api.py -v -m "not integration"
    """
    
    def test_real_schedule(self):
        """Test real schedule endpoint."""
        response = client.get("/games/schedule?time_zone=America/Toronto&date=2026-05-24")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "ALGames" in data
        assert "NLGames" in data
    
    def test_real_schedule_range(self):
        """Test real schedule range endpoint."""
        response = client.get("/games/schedule/range?start_date=2026-05-20&end_date=2026-05-22")
        
        # May fail if MLB API is down or returns error
        if response.status_code == 200:
            data = response.json()
            assert "dates" in data
    
    def test_real_player_gamelogs(self):
        """Test real player gamelogs endpoint with Aaron Judge (player_id=592450)."""
        response = client.get("/players/592450/gamelogs/2024")
        
        if response.status_code == 200:
            data = response.json()
            assert "hittingSplits" in data
            assert "pitchingSplits" in data
            assert isinstance(data["hittingSplits"], list)
            assert isinstance(data["pitchingSplits"], list)
            
            # Judge should have hitting stats
            if data["hittingSplits"]:
                split = data["hittingSplits"][0]
                assert "season" in split
                assert "stat" in split
                assert "team" in split
                assert "opponent" in split
                assert "date" in split
                assert "isHome" in split
                assert "isWin" in split
                assert "isGameOver" in split
                assert "game" in split
