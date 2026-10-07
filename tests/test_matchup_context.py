"""
Tests for the matchup context regression layer.

These are pure-function tests — no network, no AI. The whole point of
matchup_context is that it behaves deterministically, so it should be
pinned down accordingly.
"""

import pytest

from app.services.matchup_context import (
    BORDERLINE_BAND,
    K_BATTING_AVG,
    NOISE_THRESHOLD_AVG,
    build_expected_rate,
    build_h2h_context,
    build_platoon_context,
    composite_confidence,
    log5_probability,
    regress_rate,
    render_matchup_facts,
)

# ============================================================================
# regress_rate
# ============================================================================

def test_regressed_rate_sits_between_observed_and_prior():
    rate = regress_rate(
        successes=7, trials=18, prior=0.257, k=K_BATTING_AVG,
        noise_threshold=NOISE_THRESHOLD_AVG,
    )

    assert rate.observed == pytest.approx(0.3889, abs=1e-3)
    assert rate.prior < rate.regressed < rate.observed


def test_small_sample_lands_close_to_prior():
    """11 AB should barely move the needle off the season average."""
    rate = regress_rate(
        successes=4, trials=11, prior=0.274, k=K_BATTING_AVG,
        noise_threshold=NOISE_THRESHOLD_AVG,
    )

    assert abs(rate.regressed - rate.prior) < 0.020
    assert rate.sample_weight < 0.20


def test_large_sample_retains_most_of_the_signal():
    """400 AB should mostly speak for itself."""
    rate = regress_rate(
        successes=60, trials=400, prior=0.274, k=K_BATTING_AVG,
        noise_threshold=NOISE_THRESHOLD_AVG,
    )

    assert rate.sample_weight > 0.85
    assert abs(rate.regressed - rate.observed) < 0.025


def test_sample_weight_is_half_at_the_regression_constant():
    """By construction, k trials of real data equal k of prior."""
    rate = regress_rate(
        successes=20, trials=K_BATTING_AVG, prior=0.250, k=K_BATTING_AVG,
        noise_threshold=NOISE_THRESHOLD_AVG,
    )

    assert rate.sample_weight == pytest.approx(0.5)


def test_zero_trials_returns_the_prior_untouched():
    rate = regress_rate(
        successes=0, trials=0, prior=0.274, k=K_BATTING_AVG,
        noise_threshold=NOISE_THRESHOLD_AVG,
    )

    assert rate.regressed == 0.274
    assert rate.sample_weight == 0.0
    assert rate.signal_strength == 0.0


# ============================================================================
# build_h2h_context
# ============================================================================

def _h2h(at_bats, hits, **extra):
    return {"atBats": at_bats, "hits": hits, **extra}


def test_h2h_returns_none_when_never_faced():
    """No prior history must be distinguishable from a neutral matchup."""
    assert build_h2h_context(_h2h(0, 0), batter_season_avg=0.274) is None
    assert build_h2h_context({}, batter_season_avg=0.274) is None


def test_h2h_returns_none_without_a_prior():
    assert build_h2h_context(_h2h(20, 8), batter_season_avg=None) is None


def test_hot_small_sample_is_not_trusted():
    """4-for-11 reads .364 raw but must not assert a batter advantage."""
    ctx = build_h2h_context(_h2h(11, 4, homeRuns=2), batter_season_avg=0.274)

    assert ctx["observed_avg"] == pytest.approx(0.3636, abs=1e-3)
    assert ctx["reliability"] == "negligible"
    assert ctx["lean"] in ("neutral", "inconclusive")
    assert ctx["lean"] != "batter"


def test_cold_small_sample_is_not_trusted():
    """0-for-7 must not assert a pitcher advantage either."""
    ctx = build_h2h_context(_h2h(7, 0), batter_season_avg=0.260)

    assert ctx["observed_avg"] == 0.0
    assert ctx["regressed_avg"] > 0.200
    assert ctx["lean"] != "pitcher"


def test_large_sample_can_assert_a_lean():
    """13-for-84 is enough evidence to favor the pitcher."""
    ctx = build_h2h_context(_h2h(84, 13, strikeOuts=29), batter_season_avg=0.274)

    assert ctx["reliability"] == "meaningful"
    assert ctx["lean"] == "pitcher"


def test_similar_raw_averages_diverge_after_regression():
    """
    The core claim: .364 on 11 AB and .368 on 38 AB look alike raw but
    carry different evidence, so they must not resolve the same way.
    """
    tiny = build_h2h_context(_h2h(11, 4), batter_season_avg=0.274)
    larger = build_h2h_context(_h2h(38, 14), batter_season_avg=0.274)

    assert abs(tiny["observed_avg"] - larger["observed_avg"]) < 0.010
    assert larger["regressed_avg"] > tiny["regressed_avg"]
    assert larger["sample_weight"] > tiny["sample_weight"]


def test_borderline_flag_marks_threshold_straddlers():
    """
    A delta sitting within the borderline band must be flagged rather than
    silently snapping to one side of the threshold.
    """
    # Construct a line whose regressed delta lands right at the threshold.
    prior = 0.250
    trials = 40
    target_delta = NOISE_THRESHOLD_AVG
    successes = (target_delta + prior) * (trials + K_BATTING_AVG) - K_BATTING_AVG * prior

    ctx = build_h2h_context(_h2h(trials, round(successes)), batter_season_avg=prior)

    assert abs(abs(ctx["delta"]) - NOISE_THRESHOLD_AVG) < BORDERLINE_BAND
    assert ctx["borderline"] is True


def test_signal_strength_is_continuous():
    """
    signal_strength must keep moving where the discrete label does not,
    so the UI can render shades rather than a hard cliff.
    """
    weak = build_h2h_context(_h2h(30, 8), batter_season_avg=0.274)
    strong = build_h2h_context(_h2h(30, 3), batter_season_avg=0.274)

    assert strong["signal_strength"] > weak["signal_strength"]


# ============================================================================
# build_platoon_context
# ============================================================================

def _split(pa, ops):
    return {"plateAppearances": pa, "ops": str(ops)}


def test_platoon_picks_the_split_matching_the_opposing_hand():
    splits = {"vl": _split(200, 0.681), "vr": _split(400, 0.850)}

    vs_lefty = build_platoon_context(splits, "L", overall_ops=0.780)
    vs_righty = build_platoon_context(splits, "R", overall_ops=0.780)

    assert vs_lefty["observed_ops"] == pytest.approx(0.681, abs=1e-3)
    assert vs_righty["observed_ops"] == pytest.approx(0.850, abs=1e-3)


def test_platoon_returns_none_when_split_missing():
    assert build_platoon_context({}, "L", overall_ops=0.780) is None
    assert build_platoon_context({"vr": _split(400, 0.850)}, "L", 0.780) is None


def test_platoon_handles_undefined_rate_strings():
    """MLB sends '.---' for undefined rates; that must not crash."""
    splits = {"vl": {"plateAppearances": 3, "ops": ".---"}}

    assert build_platoon_context(splits, "L", overall_ops=0.780) is None


def test_platoon_accepts_pitcher_batters_faced():
    """Pitchers report battersFaced rather than plateAppearances."""
    splits = {"vl": {"battersFaced": 378, "ops": "0.511"}}

    ctx = build_platoon_context(splits, "L", overall_ops=0.545)

    assert ctx["plate_appearances"] == 378


def test_large_platoon_sample_outweighs_small_h2h():
    """
    Platoon splits carry hundreds of plate appearances against a handful of
    head-to-head at-bats, and the sample weights must reflect that.
    """
    h2h = build_h2h_context(_h2h(8, 1), batter_season_avg=0.251)
    platoon = build_platoon_context(
        {"vl": _split(378, 0.511)}, "L", overall_ops=0.780
    )

    assert platoon["sample_weight"] > h2h["sample_weight"]


# ============================================================================
# composite_confidence
# ============================================================================

def test_confidence_is_a_coin_flip_without_signals():
    assert composite_confidence(None, None) == 0.5


def test_confidence_never_exceeds_one():
    h2h = build_h2h_context(_h2h(400, 40), batter_season_avg=0.300)
    platoon = build_platoon_context(
        {"vl": _split(800, 0.400)}, "L", overall_ops=0.900
    )

    assert composite_confidence(h2h, platoon) <= 1.0


def test_tiny_sample_does_not_produce_high_confidence():
    """The whole reason confidence is computed instead of asked for."""
    h2h = build_h2h_context(_h2h(11, 4, homeRuns=2), batter_season_avg=0.274)

    assert composite_confidence(h2h, None) < 0.60


def test_more_evidence_raises_confidence():
    small = build_h2h_context(_h2h(12, 2), batter_season_avg=0.274)
    large = build_h2h_context(_h2h(120, 20), batter_season_avg=0.274)

    assert composite_confidence(large, None) > composite_confidence(small, None)


# ============================================================================
# render_matchup_facts
# ============================================================================

def test_prompt_block_states_first_look_when_no_history():
    block = render_matchup_facts(
        "A. Batter", "B. Pitcher", h2h=None, platoon=None, confidence=0.5
    )

    assert "first look" in block
    assert "do not invent history" in block


def test_prompt_block_suppresses_untrustworthy_samples():
    h2h = build_h2h_context(_h2h(11, 4), batter_season_avg=0.274)
    block = render_matchup_facts("A", "B", h2h=h2h, platoon=None, confidence=0.52)

    assert "Mention as color only" in block
    assert "Do not report your own confidence score" in block


def test_prompt_block_endorses_trustworthy_samples():
    h2h = build_h2h_context(_h2h(84, 13), batter_season_avg=0.274)
    block = render_matchup_facts("A", "B", h2h=h2h, platoon=None, confidence=0.66)

    assert "large enough to inform your call" in block


def test_prompt_block_carries_the_computed_confidence():
    block = render_matchup_facts(
        "A", "B", h2h=None, platoon=None, confidence=0.61
    )

    assert "COMPUTED CONFIDENCE: 0.61" in block


# ============================================================================
# log5
# ============================================================================

LEAGUE_AVG = 0.244


def test_average_batter_vs_average_pitcher_returns_the_league_rate():
    """The defining identity of log5 — if it fails, the formula is wrong."""
    result = log5_probability(LEAGUE_AVG, LEAGUE_AVG, LEAGUE_AVG)

    assert result == pytest.approx(LEAGUE_AVG, abs=1e-9)


def test_league_average_pitcher_leaves_the_batter_unchanged():
    """A perfectly average opponent should add no information either way."""
    result = log5_probability(0.310, LEAGUE_AVG, LEAGUE_AVG)

    assert result == pytest.approx(0.310, abs=1e-9)


def test_league_average_batter_inherits_the_pitcher_rate():
    """The mirror image of the previous case."""
    result = log5_probability(LEAGUE_AVG, 0.210, LEAGUE_AVG)

    assert result == pytest.approx(0.210, abs=1e-9)


def test_tough_pitcher_pulls_the_expectation_down():
    strong = log5_probability(0.300, 0.200, LEAGUE_AVG)

    assert strong is not None
    assert strong < 0.300


def test_weak_pitcher_pushes_the_expectation_up():
    weak = log5_probability(0.300, 0.300, LEAGUE_AVG)

    assert weak is not None
    assert weak > 0.300


def test_result_always_stays_a_probability():
    """Extremes must not escape 0..1 — the reason odds are used at all."""
    for batter in (0.001, 0.2, 0.5, 0.9, 0.999):
        for pitcher in (0.001, 0.2, 0.5, 0.9, 0.999):
            result = log5_probability(batter, pitcher, LEAGUE_AVG)
            assert result is not None
            assert 0.0 < result < 1.0


def test_log5_is_symmetric_between_batter_and_pitcher():
    """Neither side is privileged by the formula."""
    a = log5_probability(0.280, 0.230, LEAGUE_AVG)
    b = log5_probability(0.230, 0.280, LEAGUE_AVG)

    assert a == pytest.approx(b)


@pytest.mark.parametrize(
    "batter, pitcher, league",
    [
        (None, 0.25, 0.244),
        (0.25, None, 0.244),
        (0.25, 0.25, None),
        (0.0, 0.25, 0.244),  # zero produces infinite odds
        (1.0, 0.25, 0.244),
        (0.25, 0.25, 0.0),
    ],
)
def test_log5_refuses_unusable_inputs(batter, pitcher, league):
    assert log5_probability(batter, pitcher, league) is None


def test_expected_rate_reports_its_inputs():
    block = build_expected_rate(0.230, 0.256, 0.244)

    assert block is not None
    # Inputs are echoed so the UI can explain why the number moved.
    assert block["batter_rate"] == 0.230
    assert block["pitcher_rate"] == 0.256
    assert block["league_rate"] == 0.244
    # May allows slightly above league average, so the batter gains a little.
    assert block["expected_avg"] > 0.230
    assert block["delta_vs_batter"] > 0


def test_expected_rate_is_none_without_a_pitcher_rate():
    assert build_expected_rate(0.250, None, 0.244) is None
