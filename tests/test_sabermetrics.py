"""
Tests for the sabermetrics service.

Run with: pytest tests/
"""

import pytest
from app.services.sabermetrics import (
    calculate_woba,
    calculate_iso,
    calculate_babip,
    calculate_fip,
    calculate_ops_plus,
    calculate_wrc_plus,
)


class TestWoba:
    """Tests for wOBA calculation."""
    
    def test_woba_basic(self):
        """Test basic wOBA calculation."""
        woba = calculate_woba(
            singles=100,
            doubles=25,
            triples=3,
            home_runs=20,
            walks=50,
            hbp=5,
            at_bats=450,
            sacrifice_flies=5,
        )
        assert woba is not None
        assert 0.3 <= woba <= 0.5  # Reasonable range for good hitter
    
    def test_woba_zero_denominator(self):
        """Test wOBA returns None when no plate appearances."""
        woba = calculate_woba(
            singles=0,
            doubles=0,
            triples=0,
            home_runs=0,
            walks=0,
            hbp=0,
            at_bats=0,
            sacrifice_flies=0,
        )
        assert woba is None


class TestIso:
    """Tests for ISO (Isolated Power) calculation."""
    
    def test_iso_power_hitter(self):
        """Test ISO for a power hitter."""
        iso = calculate_iso(slg=0.550, avg=0.280)
        assert iso == 0.270  # High ISO indicates power
    
    def test_iso_contact_hitter(self):
        """Test ISO for a contact hitter."""
        iso = calculate_iso(slg=0.350, avg=0.310)
        assert iso == 0.040  # Low ISO indicates contact approach


class TestBabip:
    """Tests for BABIP calculation."""
    
    def test_babip_normal(self):
        """Test BABIP in normal range."""
        babip = calculate_babip(
            hits=150,
            home_runs=25,
            at_bats=500,
            strikeouts=100,
            sacrifice_flies=5,
        )
        assert babip is not None
        assert 0.250 <= babip <= 0.350  # Normal BABIP range
    
    def test_babip_invalid_denominator(self):
        """Test BABIP with invalid inputs."""
        babip = calculate_babip(
            hits=5,
            home_runs=5,
            at_bats=10,
            strikeouts=5,
            sacrifice_flies=0,
        )
        # Denominator: 10 - 5 - 5 + 0 = 0
        assert babip is None


class TestFip:
    """Tests for FIP calculation."""
    
    def test_fip_ace(self):
        """Test FIP for an ace-level pitcher."""
        fip = calculate_fip(
            home_runs=10,
            walks=30,
            hbp=5,
            strikeouts=200,
            innings_pitched=180.0,
        )
        assert fip is not None
        assert fip < 3.5  # Elite FIP
    
    def test_fip_zero_innings(self):
        """Test FIP with no innings pitched."""
        fip = calculate_fip(
            home_runs=0,
            walks=0,
            hbp=0,
            strikeouts=0,
            innings_pitched=0,
        )
        assert fip is None


class TestOpsPlus:
    """Tests for OPS+ calculation."""
    
    def test_ops_plus_league_average(self):
        """Test OPS+ for league average performance."""
        # Using league average OBP (.320) and SLG (.405)
        ops_plus = calculate_ops_plus(obp=0.320, slg=0.405)
        assert 95 <= ops_plus <= 105  # Close to 100 (league average)
    
    def test_ops_plus_elite(self):
        """Test OPS+ for elite hitter."""
        ops_plus = calculate_ops_plus(obp=0.420, slg=0.600)
        assert ops_plus > 150  # Well above average


class TestWrcPlus:
    """Tests for wRC+ calculation."""
    
    def test_wrc_plus_average(self):
        """Test wRC+ for average wOBA."""
        wrc_plus = calculate_wrc_plus(woba=0.315)  # League average
        assert 95 <= wrc_plus <= 105
    
    def test_wrc_plus_elite(self):
        """Test wRC+ for elite wOBA."""
        wrc_plus = calculate_wrc_plus(woba=0.400)
        assert wrc_plus > 100  # Better than league average
