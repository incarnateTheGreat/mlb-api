"""
Mock for the head-to-head regularization layer — no network calls.

Demonstrates why raw batter-vs-pitcher lines need shrinking before they go
anywhere near an AI prompt. Two matchups can show nearly identical raw
batting averages while carrying completely different amounts of evidence.

Run: venv/bin/python scripts/mock_matchup_context.py
"""

from dataclasses import dataclass

# Shrinkage constant for batting average — roughly "how many at-bats of prior
# belief we hold before the observed head-to-head line moves us". ~60 AB is
# the commonly cited stabilization point for batting average.
K_BA = 60

# Reliability tiers keyed on head-to-head sample size.
TIER_CUTOFFS = ((15, "negligible"), (40, "limited"), (80, "moderate"))
TRUSTWORTHY_TIERS = ("moderate", "meaningful")

# A regressed delta smaller than this is indistinguishable from noise.
NOISE_THRESHOLD = 0.030


@dataclass
class H2HLine:
    """Raw head-to-head line, shaped like the Stats API vsPlayerTotal split."""

    batter: str
    pitcher: str
    at_bats: int
    hits: int
    home_runs: int
    strikeouts: int
    walks: int
    season_avg: float


def reliability_tier(at_bats: int) -> str:
    """Bucket a sample size into a human-readable reliability tier."""
    for cutoff, tier in TIER_CUTOFFS:
        if at_bats < cutoff:
            return tier
    return "meaningful"


def build_context(line: H2HLine) -> dict:
    """
    Turn a raw head-to-head line into an interpreted one.

    This is the function that would become app/services/matchup_context.py
    and feed the `historical_matchup` argument that generate_matchup_analysis
    already accepts.
    """
    observed = line.hits / line.at_bats if line.at_bats else 0.0

    # Bayesian shrink toward the batter's own season rate.
    regressed = (line.hits + K_BA * line.season_avg) / (line.at_bats + K_BA)

    # How much the observed sample actually counts for.
    weight = line.at_bats / (line.at_bats + K_BA)

    delta = regressed - line.season_avg
    tier = reliability_tier(line.at_bats)

    # Confidence is COMPUTED, not asked of the model. An LLM asked to
    # self-report would happily hand back 0.9 on an 11 at-bat sample.
    magnitude = min(abs(delta) / 0.100, 1.0)
    confidence = round(0.5 + (weight * magnitude * 0.4), 2)

    # The lean is gated on reliability, not just magnitude. A sample we tell
    # the model to ignore must not also hand it a verdict to act on.
    if abs(delta) < NOISE_THRESHOLD:
        signal = "no meaningful deviation from expectation"
        h2h_lean = "neutral"
    elif tier not in TRUSTWORTHY_TIERS:
        direction = "above" if delta > 0 else "below"
        signal = (
            f"raw line sits {direction} his norm, but the sample is too "
            f"small to separate it from noise"
        )
        h2h_lean = "inconclusive"
    elif delta > 0:
        signal = f"batter trends {abs(delta) * 1000:.0f} pts above his norm"
        h2h_lean = "batter"
    else:
        signal = f"batter trends {abs(delta) * 1000:.0f} pts below his norm"
        h2h_lean = "pitcher"

    return {
        "sample_size": line.at_bats,
        "reliability": tier,
        "sample_weight": round(weight, 3),
        "observed_avg": round(observed, 3),
        "regressed_avg": round(regressed, 3),
        "season_avg": line.season_avg,
        "delta": round(delta, 3),
        "signal": signal,
        "h2h_lean": h2h_lean,
        "confidence": confidence,
    }


def render_prompt_block(line: H2HLine, ctx: dict) -> str:
    """Render the fact block that replaces the json.dumps blob in the prompt."""
    guidance = (
        "This sample is large enough to inform your call."
        if ctx["reliability"] in TRUSTWORTHY_TIERS
        else "Mention as color only. Base your advantage call on platoon "
        "splits and recent form instead."
    )

    return "\n".join(
        [
            f"HEAD-TO-HEAD (career): {line.hits}-for-{line.at_bats}, "
            f"{line.home_runs} HR, {line.strikeouts} K, {line.walks} BB",
            f"RAW LINE READS: {ctx['observed_avg']:.3f}",
            f"SAMPLE RELIABILITY: {ctx['reliability']} "
            f"({ctx['sample_size']} AB, weight {ctx['sample_weight']:.0%})",
            f"REGRESSED EXPECTATION: {ctx['regressed_avg']:.3f} "
            f"(season {ctx['season_avg']:.3f})",
            f"SIGNAL: {ctx['signal']}",
            f"HEAD-TO-HEAD LEAN: {ctx['h2h_lean']}",
            f"GUIDANCE: {guidance}",
        ]
    )


SCENARIOS = [
    # Tiny sample that looks spectacular.
    H2HLine("J. Soto", "Z. Wheeler", 11, 4, 2, 3, 2, 0.274),
    # Nearly identical raw average, but 3.5x the evidence.
    H2HLine("M. Betts", "L. Webb", 38, 14, 3, 9, 4, 0.274),
    # Large sample that genuinely favors the pitcher.
    H2HLine("A. Judge", "F. Valdez", 84, 13, 1, 29, 5, 0.274),
]


def main() -> None:
    for line in SCENARIOS:
        ctx = build_context(line)

        print("=" * 62)
        print(f"  {line.batter}  vs  {line.pitcher}")
        print("=" * 62)
        print(
            f"  raw line      {line.hits}-for-{line.at_bats} "
            f"({ctx['observed_avg']:.3f})  {line.home_runs} HR"
        )
        print(f"  season avg    {ctx['season_avg']:.3f}")
        print(
            f"  regressed     {ctx['regressed_avg']:.3f}   "
            f"(delta {ctx['delta']:+.3f})"
        )
        print(
            f"  reliability   {ctx['reliability']}  "
            f"(weight {ctx['sample_weight']:.1%})"
        )
        print(
            f"  -> h2h lean   {ctx['h2h_lean']}  "
            f"@ confidence {ctx['confidence']}"
        )
        print()
        print("  --- prompt block ---")
        for row in render_prompt_block(line, ctx).splitlines():
            print(f"  {row}")
        print()


if __name__ == "__main__":
    main()
