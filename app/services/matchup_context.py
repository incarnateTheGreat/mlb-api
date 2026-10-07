"""
Matchup context — deterministic interpretation of matchup splits.

Raw batter-vs-pitcher lines are almost always tiny samples. A typical
career head-to-head is under 20 at-bats, where a .389 average and a .150
average are statistically indistinguishable. Feeding those raw numbers to
an LLM produces confident nonsense, so every rate here is regressed toward
a sensible prior before it reaches a prompt or a UI.

Nothing in this module calls an AI model or the network. Every function is
pure, so the same inputs always produce the same output and the whole thing
is unit-testable.
"""

from dataclasses import dataclass
from typing import Any, Optional

# ============================================================================
# Shrinkage constants
# ============================================================================

# Regression constant for batting average, in at-bats. At exactly this many
# at-bats the observed line and the prior carry equal weight. Anchored to the
# commonly cited ~60 AB stabilization point for batting average.
K_BATTING_AVG = 60

# Regression constant for OPS in platoon splits, in plate appearances.
# OPS stabilizes more slowly than batting average.
K_OPS = 200

# A regressed rate this far from its prior is indistinguishable from noise.
NOISE_THRESHOLD_AVG = 0.030
NOISE_THRESHOLD_OPS = 0.060

# Deltas within this band of a threshold are reported as borderline rather
# than snapping to one side. Without it a four-ten-thousandths difference
# silently flips a label, which is more precision than the data supports.
BORDERLINE_BAND = 0.008

# Head-to-head sample tiers, as (exclusive upper bound, label).
H2H_TIERS = ((15, "negligible"), (40, "limited"), (80, "moderate"))
PLATOON_TIERS = ((50, "negligible"), (150, "limited"), (350, "moderate"))

TRUSTWORTHY_TIERS = ("moderate", "meaningful")


# ============================================================================
# Core regression
# ============================================================================

@dataclass(frozen=True)
class RegressedRate:
    """A rate statistic after shrinking toward a prior."""

    observed: float
    regressed: float
    prior: float
    sample: int
    sample_weight: float
    delta: float
    signal_strength: float


def regress_rate(
    successes: float,
    trials: int,
    prior: float,
    k: int,
    noise_threshold: float,
) -> RegressedRate:
    """
    Shrink an observed rate toward a prior using added pseudo-observations.

    Conceptually: pretend the player also had `k` trials in which he performed
    exactly at the prior rate, then average everything together. Small samples
    get pulled most of the way back to the prior; large samples barely move.

    `signal_strength` is a continuous 0-1 measure of how far the regressed
    rate sits from the prior, scaled against the noise threshold. Callers
    should prefer it over the discrete labels when rendering.
    """
    if trials <= 0:
        return RegressedRate(
            observed=0.0,
            regressed=prior,
            prior=prior,
            sample=0,
            sample_weight=0.0,
            delta=0.0,
            signal_strength=0.0,
        )

    observed = successes / trials
    regressed = (successes + k * prior) / (trials + k)
    weight = trials / (trials + k)
    delta = regressed - prior

    # 1.0 means "exactly at the noise threshold"; above that is real movement.
    signal_strength = abs(delta) / noise_threshold if noise_threshold else 0.0

    return RegressedRate(
        observed=round(observed, 4),
        regressed=round(regressed, 4),
        prior=round(prior, 4),
        sample=trials,
        sample_weight=round(weight, 4),
        delta=round(delta, 4),
        signal_strength=round(signal_strength, 3),
    )


def _tier(sample: int, tiers: tuple[tuple[int, str], ...]) -> str:
    """Bucket a sample size into a human-readable reliability tier."""
    for upper_bound, label in tiers:
        if sample < upper_bound:
            return label
    return "meaningful"


def _classify(
    rate: RegressedRate,
    tier: str,
    noise_threshold: float,
    favors_when_high: str,
    favors_when_low: str,
) -> tuple[str, bool]:
    """
    Turn a regressed rate into a lean label.

    Two questions get asked, in order. First, did anything survive the
    regression — is the regressed rate meaningfully different from the prior?
    If not, the split says nothing. Second, is the sample large enough to
    trust what survived? If not, the result is flagged rather than asserted.

    Returns (lean, borderline).
    """
    magnitude = abs(rate.delta)
    borderline = abs(magnitude - noise_threshold) < BORDERLINE_BAND

    if magnitude < noise_threshold:
        return "neutral", borderline

    if tier not in TRUSTWORTHY_TIERS:
        return "inconclusive", borderline

    lean = favors_when_high if rate.delta > 0 else favors_when_low
    return lean, borderline


# ============================================================================
# Head-to-head
# ============================================================================

def build_h2h_context(
    h2h_stat: dict[str, Any],
    batter_season_avg: Optional[float],
) -> Optional[dict[str, Any]]:
    """
    Interpret a career batter-vs-pitcher line.

    Args:
        h2h_stat: Raw `vsPlayerTotal` stat block from the Stats API.
        batter_season_avg: The batter's current-season average, used as the
            prior. Without it there is nothing to regress toward.

    Returns None when the two players have never faced each other or when
    the prior is unavailable — callers should render that as "first look"
    rather than as a neutral matchup.
    """
    at_bats = h2h_stat.get("atBats") or 0
    if not at_bats or batter_season_avg is None:
        return None

    hits = h2h_stat.get("hits") or 0

    rate = regress_rate(
        successes=hits,
        trials=at_bats,
        prior=batter_season_avg,
        k=K_BATTING_AVG,
        noise_threshold=NOISE_THRESHOLD_AVG,
    )
    tier = _tier(at_bats, H2H_TIERS)
    lean, borderline = _classify(
        rate,
        tier,
        NOISE_THRESHOLD_AVG,
        favors_when_high="batter",
        favors_when_low="pitcher",
    )

    return {
        "at_bats": at_bats,
        "hits": hits,
        "home_runs": h2h_stat.get("homeRuns") or 0,
        "strikeouts": h2h_stat.get("strikeOuts") or 0,
        "walks": h2h_stat.get("baseOnBalls") or 0,
        "observed_avg": rate.observed,
        "regressed_avg": rate.regressed,
        "season_avg": rate.prior,
        "delta": rate.delta,
        "sample_weight": rate.sample_weight,
        "signal_strength": rate.signal_strength,
        "reliability": tier,
        "lean": lean,
        "borderline": borderline,
    }


# ============================================================================
# Platoon splits
# ============================================================================

def _parse_ops(stat: dict[str, Any]) -> Optional[float]:
    """MLB returns rate stats as strings, and as '.---' when undefined."""
    raw = stat.get("ops")
    if raw in (None, "", ".---", "-.--"):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _plate_appearances(stat: dict[str, Any]) -> int:
    """Hitters report plateAppearances; pitchers report battersFaced."""
    return stat.get("plateAppearances") or stat.get("battersFaced") or 0


def build_platoon_context(
    splits: dict[str, dict[str, Any]],
    opponent_hand: str,
    overall_ops: Optional[float],
) -> Optional[dict[str, Any]]:
    """
    Interpret a player's platoon split against the hand he will actually face.

    Platoon splits carry far more evidence than head-to-head lines — hundreds
    of plate appearances rather than a dozen — but single-season splits still
    need regressing. Reverse splits in particular are notorious for being
    noise that vanishes with more data.

    Works from either side: a batter outperforming his norm and a pitcher
    allowing more than his norm both favor the batter, so the direction of a
    positive delta is the same either way.

    Args:
        splits: {"vl": {...}, "vr": {...}} from get_platoon_splits().
        opponent_hand: "L" or "R" — the hand of the opposing player.
        overall_ops: The player's overall season OPS, used as the prior.

    Returns None when the relevant split or the prior is missing.
    """
    code = "vl" if opponent_hand.upper().startswith("L") else "vr"
    stat = splits.get(code)
    if not stat or overall_ops is None:
        return None

    split_ops = _parse_ops(stat)
    plate_appearances = _plate_appearances(stat)
    if split_ops is None or not plate_appearances:
        return None

    # regress_rate works in successes/trials, so reconstruct a total from
    # the rate. OPS is not a true ratio of events, but the shrinkage
    # arithmetic only needs a consistent scale.
    rate = regress_rate(
        successes=split_ops * plate_appearances,
        trials=plate_appearances,
        prior=overall_ops,
        k=K_OPS,
        noise_threshold=NOISE_THRESHOLD_OPS,
    )
    tier = _tier(plate_appearances, PLATOON_TIERS)

    lean, borderline = _classify(
        rate,
        tier,
        NOISE_THRESHOLD_OPS,
        favors_when_high="batter",
        favors_when_low="pitcher",
    )

    return {
        "vs_hand": opponent_hand.upper(),
        "plate_appearances": plate_appearances,
        "observed_ops": rate.observed,
        "regressed_ops": rate.regressed,
        "overall_ops": rate.prior,
        "delta": rate.delta,
        "sample_weight": rate.sample_weight,
        "signal_strength": rate.signal_strength,
        "reliability": tier,
        "lean": lean,
        "borderline": borderline,
    }


# ============================================================================
# Composite confidence
# ============================================================================

# Relative say each signal gets in the overall confidence number. Platoon
# splits carry the most evidence, head-to-head the least.
SIGNAL_WEIGHTS = {"platoon": 0.6, "h2h": 0.4}


def composite_confidence(
    h2h: Optional[dict[str, Any]],
    platoon: Optional[dict[str, Any]],
) -> float:
    """
    Compute overall confidence in the matchup read from the available signals.

    This replaces asking the model to self-report a confidence score. An LLM
    will happily return 0.9 on an eleven at-bat sample; this will not.

    Confidence starts at 0.5 (a coin flip) and rises only as far as the
    evidence supports.
    """
    total = 0.0
    used = 0.0

    for key, signal in (("h2h", h2h), ("platoon", platoon)):
        if signal is None:
            continue
        weight = SIGNAL_WEIGHTS[key]
        # Strength is capped at 2x the noise threshold so one loud signal
        # cannot run the number up on its own.
        strength = min(signal["signal_strength"] / 2.0, 1.0)
        total += weight * signal["sample_weight"] * strength
        used += weight

    if not used:
        return 0.5

    return round(0.5 + (total / used) * 0.45, 2)


# ============================================================================
# Prompt rendering
# ============================================================================

def _guidance(signal: dict[str, Any]) -> str:
    if signal["reliability"] in TRUSTWORTHY_TIERS:
        return "This sample is large enough to inform your call."
    return (
        "Mention as color only. Do not base the advantage call on it."
    )


def render_matchup_facts(
    batter_name: str,
    pitcher_name: str,
    h2h: Optional[dict[str, Any]],
    platoon: Optional[dict[str, Any]],
    confidence: float,
) -> str:
    """
    Render the fact block injected into the AI prompt.

    Every number here is already computed. The model's job is to narrate
    these facts, not to derive them, so the prompt presents conclusions
    rather than raw stat dumps.
    """
    lines = [f"MATCHUP: {batter_name} vs {pitcher_name}", ""]

    if h2h is None:
        lines.append(
            "HEAD-TO-HEAD: no prior plate appearances between these two. "
            "This is a first look — do not invent history."
        )
    else:
        borderline_note = (
            " (borderline — sits right at the noise threshold)"
            if h2h["borderline"]
            else ""
        )
        lines.extend(
            [
                f"HEAD-TO-HEAD (career): {h2h['hits']}-for-{h2h['at_bats']}, "
                f"{h2h['home_runs']} HR, {h2h['strikeouts']} K, "
                f"{h2h['walks']} BB",
                f"  raw average: {h2h['observed_avg']:.3f}",
                f"  regressed expectation: {h2h['regressed_avg']:.3f} "
                f"(season {h2h['season_avg']:.3f})",
                f"  sample: {h2h['at_bats']} AB — {h2h['reliability']} "
                f"({h2h['sample_weight']:.0%} weight)",
                f"  lean: {h2h['lean']}{borderline_note}",
                f"  {_guidance(h2h)}",
            ]
        )

    lines.append("")

    if platoon is None:
        lines.append("PLATOON SPLIT: unavailable.")
    else:
        lines.extend(
            [
                f"PLATOON SPLIT (vs {platoon['vs_hand']}HP): "
                f"{platoon['plate_appearances']} PA",
                f"  raw OPS: {platoon['observed_ops']:.3f}",
                f"  regressed OPS: {platoon['regressed_ops']:.3f} "
                f"(overall {platoon['overall_ops']:.3f})",
                f"  sample: {platoon['reliability']} "
                f"({platoon['sample_weight']:.0%} weight)",
                f"  lean: {platoon['lean']}",
                f"  {_guidance(platoon)}",
            ]
        )

    lines.extend(
        [
            "",
            f"COMPUTED CONFIDENCE: {confidence}",
            "",
            "RULES:",
            "- The numbers above are already regressed for sample size. "
            "Use them as given; do not re-derive or re-weight them.",
            "- Weigh the platoon split above the head-to-head line. It "
            "carries far more plate appearances.",
            "- Never state a conclusion the supplied numbers do not support.",
            "- Do not report your own confidence score. It is supplied.",
        ]
    )

    return "\n".join(lines)
