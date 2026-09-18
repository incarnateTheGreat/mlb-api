#!/usr/bin/env python
"""Quick eval runner script for Week 4b testing."""

import asyncio
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.services.eval_runner import EvalRunner


async def main():
    runner = EvalRunner()
    print("Running all eval cases (15 total)...")
    results = await runner.run_all()

    report = runner.generate_report()
    print(f"\n✓ Pass rate: {report['summary']['pass_rate']*100:.1f}%")
    print(f"✓ Avg score: {report['summary']['avg_score']:.2f}")
    print(f"✓ Avg latency: {report['summary']['avg_latency_ms']:.0f}ms")

    print("\nBy mode:")
    for mode, stats in report.get("by_mode", {}).items():
        print(f"  {mode}: {stats['passed']}/{stats['count']} passed ({stats['pass_rate']*100:.0f}%)")

    # Save detailed report
    runner.save_report("eval_results.json")


if __name__ == "__main__":
    asyncio.run(main())
