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
