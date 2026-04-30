"""
Sabermetric calculations using Python/NumPy.

This is where Python shines over JavaScript — NumPy operations are
vectorized and run in optimized C, making bulk stat calculations
significantly faster than iterating in JS.

Key concept: NumPy arrays are like typed arrays in JS, but with
broadcast operations built-in. Instead of array.map(), you do
direct operations on the array: `arr * 2` multiplies every element.
"""

from typing import Optional
import numpy as np

from app.models.player import (
    BattingStats,
    AdvancedBattingStats,
    PitchingStats,
    AdvancedPitchingStats,
)


# ============================================================================
# League average constants (2024 MLB averages — update annually)
# ============================================================================

# wOBA weights — these change slightly each year
WOBA_WEIGHTS = {
    "uBB": 0.690,   # Unintentional walk
    "HBP": 0.722,   # Hit by pitch
    "1B": 0.888,    # Single
    "2B": 1.271,    # Double
    "3B": 1.616,    # Triple
    "HR": 2.101,    # Home run
}

# League averages for rates
LEAGUE_AVG = {
    "wOBA": 0.315,
    "OBP": 0.320,
    "SLG": 0.405,
    "wOBA_scale": 1.226,  # wOBA scale factor
    "R_per_PA": 0.110,    # Runs per plate appearance
    "ERA": 4.17,
    "FIP_constant": 3.20,  # FIP constant (changes yearly)
}


def calculate_woba(
    singles: int,
    doubles: int,
    triples: int,
    home_runs: int,
    walks: int,
    hbp: int,
    at_bats: int,
    sacrifice_flies: int = 0,
) -> Optional[float]:
    """
    Calculate Weighted On-Base Average (wOBA).
    
    wOBA is like OBP but weights each outcome by its run value.
    A single isn't worth as much as a double, which isn't worth
    as much as a home run — wOBA captures this.
    
    Formula:
    wOBA = (w_uBB*uBB + w_HBP*HBP + w_1B*1B + w_2B*2B + w_3B*3B + w_HR*HR) /
           (AB + BB - IBB + SF + HBP)
    """
    # Denominator is plate appearances (approximated)
    denominator = at_bats + walks + hbp + sacrifice_flies
    
    if denominator == 0:
        return None
    
    numerator = (
        WOBA_WEIGHTS["uBB"] * walks +
        WOBA_WEIGHTS["HBP"] * hbp +
        WOBA_WEIGHTS["1B"] * singles +
        WOBA_WEIGHTS["2B"] * doubles +
        WOBA_WEIGHTS["3B"] * triples +
        WOBA_WEIGHTS["HR"] * home_runs
    )
    
    return round(numerator / denominator, 3)


def calculate_wrc_plus(
    woba: float,
    park_factor: float = 1.0,
) -> int:
    """
    Calculate Weighted Runs Created Plus (wRC+).
    
    wRC+ normalizes wOBA to a scale where 100 is league average.
    A wRC+ of 120 means the player creates 20% more runs than average.
    
    This is similar to how you might normalize a score to a percentile,
    but specifically calibrated for run production context.
    """
    if woba is None:
        return 100  # Default to league average
    
    # Simplified wRC+ (full formula adjusts for park and league)
    # wRC+ = ((wRAA/PA + lg_R/PA) / (lg_wRC/PA)) * 100
    
    woba_diff = woba - LEAGUE_AVG["wOBA"]
    wrc_per_pa = (woba_diff / WOBA_WEIGHTS["1B"]) * LEAGUE_AVG["R_per_PA"]
    league_wrc_per_pa = LEAGUE_AVG["R_per_PA"]
    
    # Apply park factor
    wrc_per_pa_adjusted = wrc_per_pa / park_factor
    
    wrc_plus = ((wrc_per_pa_adjusted + league_wrc_per_pa) / league_wrc_per_pa) * 100
    
    return round(wrc_plus)


def calculate_ops_plus(
    obp: float,
    slg: float,
    park_factor: float = 1.0,
) -> int:
    """
    Calculate OPS+ (adjusted OPS).
    
    OPS+ = 100 * (OBP/lgOBP + SLG/lgSLG - 1)
    
    Adjusted for park factor. 100 is league average.
    """
    if obp == 0 and slg == 0:
        return 100
    
    # Adjust for park factor
    obp_adj = obp / park_factor
    slg_adj = slg / park_factor
    
    ops_plus = 100 * (
        (obp_adj / LEAGUE_AVG["OBP"]) + 
        (slg_adj / LEAGUE_AVG["SLG"]) - 
        1
    )
    
    return round(ops_plus)


def calculate_iso(slg: float, avg: float) -> float:
    """
    Calculate Isolated Power (ISO).
    
    ISO = SLG - AVG
    
    Measures raw power independent of batting average.
    League average is around .140-.150.
    """
    return round(slg - avg, 3)


def calculate_babip(
    hits: int,
    home_runs: int,
    at_bats: int,
    strikeouts: int,
    sacrifice_flies: int = 0,
) -> Optional[float]:
    """
    Calculate Batting Average on Balls In Play (BABIP).
    
    BABIP = (H - HR) / (AB - K - HR + SF)
    
    BABIP is useful for detecting luck/regression. League average
    is around .300. Extreme BABIPs tend to regress to the mean.
    """
    denominator = at_bats - strikeouts - home_runs + sacrifice_flies
    
    if denominator <= 0:
        return None
    
    return round((hits - home_runs) / denominator, 3)


def calculate_fip(
    home_runs: int,
    walks: int,
    hbp: int,
    strikeouts: int,
    innings_pitched: float,
) -> Optional[float]:
    """
    Calculate Fielding Independent Pitching (FIP).
    
    FIP = ((13*HR + 3*(BB+HBP) - 2*K) / IP) + FIP_constant
    
    FIP estimates what a pitcher's ERA should look like based only
    on strikeouts, walks, HBPs, and home runs — things the pitcher
    controls independently of the defense behind them.
    """
    if innings_pitched <= 0:
        return None
    
    fip = (
        ((13 * home_runs) + (3 * (walks + hbp)) - (2 * strikeouts))
        / innings_pitched
    ) + LEAGUE_AVG["FIP_constant"]
    
    return round(fip, 2)


def calculate_era_plus(era: float, park_factor: float = 1.0) -> int:
    """
    Calculate ERA+ (adjusted ERA).
    
    ERA+ = 100 * (lgERA / ERA) * park_factor
    
    ERA+ of 100 is league average. Higher is better (unlike ERA).
    An ERA+ of 150 means the pitcher is 50% better than average.
    """
    if era <= 0:
        return 999  # Cap at a reasonable max for near-zero ERA
    
    era_plus = 100 * (LEAGUE_AVG["ERA"] / era) * park_factor
    
    return min(round(era_plus), 999)


def calculate_k_rate(strikeouts: int, plate_appearances: int) -> Optional[float]:
    """Calculate strikeout rate (K/PA)."""
    if plate_appearances == 0:
        return None
    return round(strikeouts / plate_appearances, 3)


def calculate_bb_rate(walks: int, plate_appearances: int) -> Optional[float]:
    """Calculate walk rate (BB/PA)."""
    if plate_appearances == 0:
        return None
    return round(walks / plate_appearances, 3)


# ============================================================================
# Bulk processing with NumPy
# ============================================================================

def calculate_rolling_stats(
    game_stats: list[dict],
    window: int = 15,
) -> dict:
    """
    Calculate rolling batting stats over a window of games.
    
    This demonstrates NumPy's power — instead of nested loops,
    we use vectorized window operations that run in optimized C.
    
    Args:
        game_stats: List of per-game stat dictionaries
        window: Number of games in the rolling window
    
    Returns:
        Rolling averages for key stats
    """
    if not game_stats or len(game_stats) < window:
        return {}
    
    # Convert to NumPy arrays for vectorized operations
    hits = np.array([g.get("hits", 0) for g in game_stats])
    at_bats = np.array([g.get("atBats", 0) for g in game_stats])
    home_runs = np.array([g.get("homeRuns", 0) for g in game_stats])
    rbi = np.array([g.get("rbi", 0) for g in game_stats])
    
    # Use NumPy's convolve for rolling sums (much faster than Python loops)
    # np.ones(window) creates a window of 1s for the convolution
    kernel = np.ones(window)
    
    rolling_hits = np.convolve(hits, kernel, mode="valid")
    rolling_abs = np.convolve(at_bats, kernel, mode="valid")
    rolling_hrs = np.convolve(home_runs, kernel, mode="valid")
    rolling_rbi = np.convolve(rbi, kernel, mode="valid")
    
    # Get the most recent window's stats
    recent_hits = int(rolling_hits[-1])
    recent_abs = int(rolling_abs[-1])
    recent_hrs = int(rolling_hrs[-1])
    recent_rbi = int(rolling_rbi[-1])
    
    # Calculate rolling average
    rolling_avg = recent_hits / recent_abs if recent_abs > 0 else 0.0
    
    # Compare to season average to determine trend
    season_hits = hits.sum()
    season_abs = at_bats.sum()
    season_avg = season_hits / season_abs if season_abs > 0 else 0.0
    
    # Determine trend
    diff = rolling_avg - season_avg
    if diff > 0.030:  # 30 points above season average
        trend = "hot"
        trend_desc = f"Batting {rolling_avg:.3f} over last {window} games, {abs(diff)*1000:.0f} points above season average"
    elif diff < -0.030:  # 30 points below
        trend = "cold"
        trend_desc = f"Batting {rolling_avg:.3f} over last {window} games, {abs(diff)*1000:.0f} points below season average"
    else:
        trend = "neutral"
        trend_desc = f"Batting {rolling_avg:.3f} over last {window} games, consistent with season average"
    
    return {
        "window": window,
        "rolling_avg": round(rolling_avg, 3),
        "rolling_hrs": recent_hrs,
        "rolling_rbi": recent_rbi,
        "trend": trend,
        "trend_description": trend_desc,
    }


def enhance_batting_stats(basic: BattingStats) -> AdvancedBattingStats:
    """
    Take basic batting stats and add advanced sabermetric calculations.
    
    This is the main entry point for the sabermetrics service —
    feed it stats from the MLB API, get back enriched data.
    """
    # Calculate singles (hits - XBH)
    singles = basic.hits - basic.doubles - basic.triples - basic.home_runs
    
    # Plate appearances (approximated)
    pa = basic.at_bats + basic.walks
    
    # Calculate wOBA
    woba = calculate_woba(
        singles=singles,
        doubles=basic.doubles,
        triples=basic.triples,
        home_runs=basic.home_runs,
        walks=basic.walks,
        hbp=0,  # HBP not in basic stats
        at_bats=basic.at_bats,
    )
    
    # Calculate other advanced stats
    iso = calculate_iso(basic.slugging_percentage, basic.batting_average)
    babip = calculate_babip(
        hits=basic.hits,
        home_runs=basic.home_runs,
        at_bats=basic.at_bats,
        strikeouts=basic.strikeouts,
    )
    
    wrc_plus = calculate_wrc_plus(woba) if woba else None
    ops_plus = calculate_ops_plus(basic.on_base_percentage, basic.slugging_percentage)
    
    bb_rate = calculate_bb_rate(basic.walks, pa)
    k_rate = calculate_k_rate(basic.strikeouts, pa)
    
    return AdvancedBattingStats(
        # Copy basic stats
        games=basic.games,
        at_bats=basic.at_bats,
        runs=basic.runs,
        hits=basic.hits,
        doubles=basic.doubles,
        triples=basic.triples,
        home_runs=basic.home_runs,
        rbi=basic.rbi,
        stolen_bases=basic.stolen_bases,
        caught_stealing=basic.caught_stealing,
        walks=basic.walks,
        strikeouts=basic.strikeouts,
        batting_average=basic.batting_average,
        on_base_percentage=basic.on_base_percentage,
        slugging_percentage=basic.slugging_percentage,
        ops=basic.ops,
        # Add advanced stats
        woba=woba,
        wrc_plus=wrc_plus,
        ops_plus=ops_plus,
        iso=iso,
        babip=babip,
        bb_rate=bb_rate,
        k_rate=k_rate,
    )


def enhance_pitching_stats(basic: PitchingStats) -> AdvancedPitchingStats:
    """
    Take basic pitching stats and add advanced sabermetric calculations.
    """
    # Calculate FIP
    fip = calculate_fip(
        home_runs=basic.home_runs,
        walks=basic.walks,
        hbp=0,
        strikeouts=basic.strikeouts,
        innings_pitched=basic.innings_pitched,
    )
    
    # Calculate ERA+
    era_plus = calculate_era_plus(basic.era) if basic.era > 0 else None
    
    # Rate stats
    k_9 = (basic.strikeouts / basic.innings_pitched * 9) if basic.innings_pitched > 0 else None
    bb_9 = (basic.walks / basic.innings_pitched * 9) if basic.innings_pitched > 0 else None
    hr_9 = (basic.home_runs / basic.innings_pitched * 9) if basic.innings_pitched > 0 else None
    k_bb = (basic.strikeouts / basic.walks) if basic.walks > 0 else None
    
    return AdvancedPitchingStats(
        # Copy basic stats
        games=basic.games,
        games_started=basic.games_started,
        wins=basic.wins,
        losses=basic.losses,
        saves=basic.saves,
        holds=basic.holds,
        innings_pitched=basic.innings_pitched,
        hits=basic.hits,
        runs=basic.runs,
        earned_runs=basic.earned_runs,
        walks=basic.walks,
        strikeouts=basic.strikeouts,
        home_runs=basic.home_runs,
        era=basic.era,
        whip=basic.whip,
        # Add advanced stats
        fip=round(fip, 2) if fip else None,
        xfip=None,  # Requires batted ball data not in basic stats
        era_plus=era_plus,
        k_9=round(k_9, 2) if k_9 else None,
        bb_9=round(bb_9, 2) if bb_9 else None,
        hr_9=round(hr_9, 2) if hr_9 else None,
        k_bb_ratio=round(k_bb, 2) if k_bb else None,
    )
