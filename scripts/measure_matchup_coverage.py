"""
Measure how often the matchup signals actually say something.

The thresholds in app/services/matchup_context.py — NOISE_THRESHOLD_AVG,
NOISE_THRESHOLD_OPS, the sample tiers, TRUSTWORTHY_TIERS — were set by eye.
They are a volume-versus-precision dial: loosen them and the UI asserts more
leans, more of which are wrong; tighten them and it goes quiet. Nobody has
measured where that dial currently sits, so this script measures it.

Two outputs:

1. Coverage — over a real slate, what fraction of lineup rows produce a
   usable lean versus "neutral" (nothing survived regression) or
   "inconclusive" (something survived but the sample is too small)? The
   number that matters is the silence rate: rows where both signals say
   nothing, which is a row the panel renders empty.

2. A threshold sweep — the same collected rows re-classified under candidate
   thresholds. Because the regressed delta and the sample tier are fixed
   properties of the data, alternate thresholds can be evaluated offline
   without re-fetching anything. This turns "0.030 feels about right" into
   "0.030 leaves 71% of rows silent, 0.020 leaves 58%".

What this deliberately does NOT do is tell you which threshold is *correct*.
That needs ground truth — whether a lean actually predicted the outcome —
which means a backtest over completed games, and is a separate job. This
only reports how loud each setting is, which is the prerequisite: there is
no point backtesting a threshold that never fires.

Usage:

    python -m scripts.measure_matchup_coverage --date 2026-09-15
    python -m scripts.measure_matchup_coverage --date 2026-09-15 --days 3
    python -m scripts.measure_matchup_coverage --date 2026-09-15 --max-games 4

Each game costs roughly 70 upstream calls, so this is deliberately
sequential and capped. Treat it as an occasional job, not a health check.
"""

import argparse
import asyncio
import logging
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

from app.routers.preview import get_game_preview
from app.services.mlb_client import MLBStatsClient, get_mlb_client

# Imported rather than reimplemented so the sweep can never drift from the
# classifier the API actually runs. These are private to matchup_context
# because nothing in the app should reach for them; a measurement script is
# the one legitimate exception.
from app.services.matchup_context import (
    H2H_TIERS,
    NOISE_THRESHOLD_AVG,
    NOISE_THRESHOLD_OPS,
    PLATOON_TIERS,
    TRUSTWORTHY_TIERS,
    _classify,
    RegressedRate,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Candidate noise thresholds for the sweep, with the live value included so
# the current setting appears in the same table as the alternatives.
AVG_CANDIDATES = (0.010, 0.020, NOISE_THRESHOLD_AVG, 0.040, 0.050)
OPS_CANDIDATES = (0.030, 0.045, NOISE_THRESHOLD_OPS, 0.075, 0.090)

# A lean that commits to a side. Everything else is the panel declining to
# answer, for one of two different reasons worth counting separately.
DECISIVE = ("batter", "pitcher")

# Be a visible, slow client against an API we do not pay for.
PAUSE_BETWEEN_GAMES = 1.0


def _dates(start: date, days: int) -> list[str]:
    return [(start + timedelta(days=offset)).isoformat() for offset in range(days)]


async def _games_on(
    client: MLBStatsClient, day: str
) -> list[dict[str, Any]]:
    """Every game on a date, in schedule order."""
    schedule = await client._get(
        "/schedule", params={"sportId": 1, "date": day}
    )
    return [
        game
        for entry in schedule.get("dates", [])
        for game in entry.get("games", [])
    ]


def _observe(row: dict[str, Any], side: str, game_id: int) -> dict[str, Any]:
    """
    Reduce a preview row to just what the sweep needs.

    The delta and the sample size are properties of the data; the lean is a
    property of the thresholds. Keeping the first two means the second can be
    recomputed for any candidate setting without touching the network again.
    """
    h2h = row.get("head_to_head")
    platoon = row.get("platoon_split")

    return {
        "game_id": game_id,
        "side": side,
        "batter": row.get("name"),
        "confidence": row.get("confidence"),
        "has_expected": row.get("expected") is not None,
        "h2h": (
            {
                "delta": h2h["delta"],
                "sample": h2h["at_bats"],
                "reliability": h2h["reliability"],
                "lean": h2h["lean"],
                "borderline": h2h["borderline"],
            }
            if h2h
            else None
        ),
        "platoon": (
            {
                "delta": platoon["delta"],
                "sample": platoon["plate_appearances"],
                "reliability": platoon["reliability"],
                "lean": platoon["lean"],
                "borderline": platoon["borderline"],
            }
            if platoon
            else None
        ),
    }


async def collect(
    client: MLBStatsClient,
    days: Iterable[str],
    season: int,
    max_games: Optional[int],
) -> list[dict[str, Any]]:
    """Walk the slate and reduce every lineup row to an observation."""
    observations: list[dict[str, Any]] = []
    seen_games = 0

    for day in days:
        try:
            games = await _games_on(client, day)
        except Exception as exc:
            logger.warning("  %s: schedule unavailable (%s)", day, exc)
            continue

        logger.info("%s — %d games", day, len(games))

        for game in games:
            if max_games is not None and seen_games >= max_games:
                logger.info("Reached --max-games cap.")
                return observations

            game_id = game.get("gamePk")
            if game_id is None:
                continue

            try:
                preview = await get_game_preview(
                    game_id, season=season, mlb_client=client
                )
            except Exception as exc:
                logger.warning("  game %s skipped (%s)", game_id, exc)
                continue

            rows = 0
            for side in ("away", "home"):
                for row in preview.get(side, {}).get("lineup", []):
                    observations.append(_observe(row, side, game_id))
                    rows += 1

            seen_games += 1
            logger.info("  game %s — %d rows", game_id, rows)
            await asyncio.sleep(PAUSE_BETWEEN_GAMES)

    return observations


def _pct(count: int, total: int) -> str:
    return f"{count / total:6.1%}" if total else "     --"


def _distribution(title: str, counts: Counter, total: int) -> None:
    logger.info("\n%s", title)
    for key, count in counts.most_common():
        logger.info("  %-14s %5d  %s", key, count, _pct(count, total))


def _percentiles(samples: list[int]) -> str:
    """
    Where the sample sizes actually land.

    This is the number that decides whether the noise thresholds are even
    the right dial. A threshold only matters for rows that clear the sample
    tiers first — if the 90th percentile sits below the trustworthy cutoff,
    no threshold value will ever produce a lean and the tiers are what need
    revisiting.
    """
    if not samples:
        return "no data"
    ordered = sorted(samples)

    def at(fraction: float) -> int:
        index = min(int(fraction * len(ordered)), len(ordered) - 1)
        return ordered[index]

    return (
        f"min {ordered[0]}  p25 {at(0.25)}  median {at(0.50)}  "
        f"p75 {at(0.75)}  p90 {at(0.90)}  max {ordered[-1]}"
    )


def report(observations: list[dict[str, Any]]) -> None:
    """Coverage of the thresholds as they are configured right now."""
    total = len(observations)
    if not total:
        logger.warning("No observations collected.")
        return

    logger.info("\n%s", "=" * 58)
    logger.info("COVERAGE AT CURRENT THRESHOLDS  (%d lineup rows)", total)
    logger.info("%s", "=" * 58)
    logger.info(
        "  NOISE_THRESHOLD_AVG=%.3f  NOISE_THRESHOLD_OPS=%.3f",
        NOISE_THRESHOLD_AVG,
        NOISE_THRESHOLD_OPS,
    )
    logger.info("  trustworthy tiers: %s", ", ".join(TRUSTWORTHY_TIERS))

    for key, label in (("h2h", "HEAD-TO-HEAD"), ("platoon", "PLATOON")):
        present = [obs for obs in observations if obs[key]]
        missing = total - len(present)

        logger.info("\n%s", "-" * 58)
        logger.info("%s", label)
        logger.info("  absent entirely   %5d  %s", missing, _pct(missing, total))

        if present:
            _distribution(
                "  lean", Counter(obs[key]["lean"] for obs in present), total
            )
            _distribution(
                "  sample tier",
                Counter(obs[key]["reliability"] for obs in present),
                total,
            )
            logger.info(
                "\n  sample size    %s",
                _percentiles([obs[key]["sample"] for obs in present]),
            )
            borderline = sum(1 for obs in present if obs[key]["borderline"])
            logger.info(
                "\n  borderline     %5d  %s of present",
                borderline,
                _pct(borderline, len(present)),
            )

    # The headline number. A row where neither signal commits is a row the
    # panel renders with nothing to say.
    silent = sum(
        1
        for obs in observations
        if not (obs["h2h"] and obs["h2h"]["lean"] in DECISIVE)
        and not (obs["platoon"] and obs["platoon"]["lean"] in DECISIVE)
    )
    both = sum(
        1
        for obs in observations
        if (obs["h2h"] and obs["h2h"]["lean"] in DECISIVE)
        and (obs["platoon"] and obs["platoon"]["lean"] in DECISIVE)
    )

    logger.info("\n%s", "=" * 58)
    logger.info("SILENT ROWS       %5d  %s", silent, _pct(silent, total))
    logger.info("BOTH SIGNALS      %5d  %s", both, _pct(both, total))

    confidences = [
        obs["confidence"] for obs in observations if obs["confidence"] is not None
    ]
    if confidences:
        logger.info(
            "CONFIDENCE        min %.2f  mean %.2f  max %.2f",
            min(confidences),
            sum(confidences) / len(confidences),
            max(confidences),
        )

    expected = sum(1 for obs in observations if obs["has_expected"])
    logger.info("LOG5 PRESENT      %5d  %s", expected, _pct(expected, total))


def _reclassify(
    signal: dict[str, Any],
    threshold: float,
    trustworthy: tuple[str, ...],
) -> str:
    """Re-run the live classifier against one observation under a new threshold."""
    rate = RegressedRate(
        observed=0.0,
        regressed=0.0,
        prior=0.0,
        sample=signal["sample"],
        sample_weight=0.0,
        delta=signal["delta"],
        signal_strength=0.0,
    )
    lean, _ = _classify(
        rate,
        signal["reliability"],
        threshold,
        favors_when_high="batter",
        favors_when_low="pitcher",
    )
    # _classify reads TRUSTWORTHY_TIERS from the module, so the widened-tier
    # case is applied here rather than by monkeypatching the module.
    if lean == "inconclusive" and signal["reliability"] in trustworthy:
        return "batter" if signal["delta"] > 0 else "pitcher"
    return lean


def sweep(observations: list[dict[str, Any]]) -> None:
    """
    What the same rows would look like under other thresholds.

    Reported as counts, not as a recommendation. Picking a value from this
    table alone optimises for loudness, which is exactly the failure mode
    worth avoiding — pair it with a backtest before moving anything.
    """
    total = len(observations)
    if not total:
        return

    widened = tuple(sorted(set(TRUSTWORTHY_TIERS) | {"limited"}))

    for key, label, candidates, tiers in (
        ("h2h", "HEAD-TO-HEAD", AVG_CANDIDATES, H2H_TIERS),
        ("platoon", "PLATOON", OPS_CANDIDATES, PLATOON_TIERS),
    ):
        present = [obs for obs in observations if obs[key]]
        if not present:
            continue

        logger.info("\n%s", "=" * 58)
        logger.info("THRESHOLD SWEEP — %s  (%d rows with data)", label, len(present))
        logger.info("  sample tiers: %s", tiers)
        logger.info("%s", "=" * 58)
        logger.info(
            "  %-10s %-8s %8s %8s %8s", "threshold", "tiers", "decisive", "neutral", "inconcl."
        )

        for trustworthy, tier_label in ((TRUSTWORTHY_TIERS, "current"), (widened, "+limited")):
            for threshold in candidates:
                leans = Counter(
                    _reclassify(obs[key], threshold, trustworthy) for obs in present
                )
                decisive = sum(leans[lean] for lean in DECISIVE)
                marker = (
                    " <- live"
                    if trustworthy == TRUSTWORTHY_TIERS
                    and abs(
                        threshold
                        - (
                            NOISE_THRESHOLD_AVG
                            if key == "h2h"
                            else NOISE_THRESHOLD_OPS
                        )
                    )
                    < 1e-9
                    else ""
                )
                logger.info(
                    "  %-10.3f %-8s %8s %8s %8s%s",
                    threshold,
                    tier_label,
                    _pct(decisive, len(present)),
                    _pct(leans["neutral"], len(present)),
                    _pct(leans["inconclusive"], len(present)),
                    marker,
                )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure how often the matchup signals say something."
    )
    parser.add_argument(
        "--date",
        type=lambda raw: datetime.strptime(raw, "%Y-%m-%d").date(),
        default=date.today(),
        help="First date of the slate (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--days", type=int, default=1, help="Consecutive dates to walk."
    )
    parser.add_argument(
        "--season",
        type=int,
        default=None,
        help="Season for rate lookups. Defaults to the year of --date.",
    )
    parser.add_argument(
        "--max-games",
        type=int,
        default=None,
        help="Stop after this many games. Each one costs ~70 upstream calls.",
    )
    args = parser.parse_args()

    season = args.season or args.date.year
    client = get_mlb_client()

    try:
        observations = await collect(
            client, _dates(args.date, args.days), season, args.max_games
        )
        report(observations)
        sweep(observations)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
