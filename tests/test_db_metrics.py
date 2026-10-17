"""
The database usage counters.

Neon bills for awake time, not queries, and nothing in the stack reports it
back on the Free plan. These counters are the only measurement available, so
the arithmetic behind them has to be pinned down - a meter that under-reports
is worse than no meter, because it reads as reassurance.
"""

import time
from unittest.mock import patch

import pytest

from app.services import db_metrics


@pytest.fixture(autouse=True)
def _clean_counters():
    db_metrics.reset()

    yield

    db_metrics.reset()


class FakeClock:
    """A monotonic clock the test drives by hand."""

    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TestQueryCounting:
    def test_counts_every_statement(self):
        for _ in range(5):
            db_metrics.record_query()

        assert db_metrics.snapshot().queries == 5

    def test_counts_connections_separately(self):
        db_metrics.record_query()
        db_metrics.record_connection()
        db_metrics.record_connection()

        usage = db_metrics.snapshot()

        assert usage.queries == 1
        assert usage.connections == 2

    def test_no_queries_means_no_awake_time(self):
        """A process that never touches the database must cost nothing."""
        usage = db_metrics.snapshot()

        assert usage.queries == 0
        assert usage.awake_seconds == 0.0
        assert usage.cu_hours == 0.0


class TestAwakeTime:
    """The whole point: awake time is not proportional to query count."""

    def test_one_query_buys_the_suspend_window(self):
        clock = FakeClock()

        with patch.object(db_metrics.time, "monotonic", clock):
            db_metrics.reset()
            db_metrics.record_query()

            usage = db_metrics.snapshot()

        assert usage.awake_seconds == pytest.approx(
            db_metrics.SUSPEND_AFTER_SECONDS
        )

    def test_a_burst_costs_the_same_as_one_query(self):
        """1000 queries inside a second do not cost 1000 windows.

        This is why query count alone cannot be used as a cost proxy, and why
        the awake figure is the one worth watching.
        """
        clock = FakeClock()

        with patch.object(db_metrics.time, "monotonic", clock):
            db_metrics.reset()

            for _ in range(1000):
                db_metrics.record_query()

            usage = db_metrics.snapshot()

        assert usage.queries == 1000
        assert usage.awake_seconds == pytest.approx(
            db_metrics.SUSPEND_AFTER_SECONDS
        )

    def test_overlapping_windows_merge(self):
        """Polling every 10s holds ONE window open, it does not stack."""
        clock = FakeClock()

        with patch.object(db_metrics.time, "monotonic", clock):
            db_metrics.reset()

            # 60 queries, 10s apart: 590s of elapsed time, all contiguous.
            for _ in range(60):
                db_metrics.record_query()
                clock.advance(10)

            usage = db_metrics.snapshot()

        # Queries land at t=0..590; the last holds the compute to t=890.
        expected = 590.0 + db_metrics.SUSPEND_AFTER_SECONDS

        assert usage.awake_seconds == pytest.approx(expected)

    def test_a_gap_longer_than_the_timeout_starts_a_new_window(self):
        """Two separated queries cost two windows, not one long one."""
        clock = FakeClock()

        with patch.object(db_metrics.time, "monotonic", clock):
            db_metrics.reset()

            db_metrics.record_query()
            clock.advance(db_metrics.SUSPEND_AFTER_SECONDS + 3600)
            db_metrics.record_query()

            usage = db_metrics.snapshot()

        assert usage.awake_seconds == pytest.approx(
            2 * db_metrics.SUSPEND_AFTER_SECONDS
        )

    def test_an_hour_of_silence_is_not_billed(self):
        """The fix being measured: idle time must not accumulate."""
        clock = FakeClock()

        with patch.object(db_metrics.time, "monotonic", clock):
            db_metrics.reset()

            db_metrics.record_query()
            clock.advance(3600)

            usage = db_metrics.snapshot()

        assert usage.awake_seconds == pytest.approx(
            db_metrics.SUSPEND_AFTER_SECONDS
        )
        assert usage.uptime_seconds == pytest.approx(3600)
        # 5 minutes awake out of an hour.
        assert usage.awake_fraction < 0.1


class TestReportedCost:
    def test_cu_hours_use_the_minimum_compute_size(self):
        clock = FakeClock()

        with patch.object(db_metrics.time, "monotonic", clock):
            db_metrics.reset()
            db_metrics.record_query()
            # Keep one window open for exactly four hours.
            for _ in range(4 * 3600 // 60):
                clock.advance(60)
                db_metrics.record_query()

            usage = db_metrics.snapshot()

        # 4 hours awake at 0.25 CU = 1 CU-hour, matching Neon's own example.
        assert usage.awake_hours == pytest.approx(4 + 300 / 3600, abs=0.01)
        assert usage.cu_hours == pytest.approx(usage.awake_hours * 0.25)

    def test_always_on_reads_as_one_hundred_percent(self):
        """The signature of the bug that caused the overage."""
        clock = FakeClock()

        with patch.object(db_metrics.time, "monotonic", clock):
            db_metrics.reset()

            for _ in range(360):
                db_metrics.record_query()
                clock.advance(10)

            usage = db_metrics.snapshot()

        assert usage.awake_fraction > 0.99

    def test_summary_is_human_readable(self):
        db_metrics.record_query()

        summary = db_metrics.snapshot().summary()

        assert "queries=1" in summary
        assert "awake=" in summary
        assert "cu_hours=" in summary


class TestInstrumentation:
    def test_listeners_attach_once(self):
        """Repeated engine access must not double-count every query."""
        from sqlalchemy import create_engine

        engine = create_engine("sqlite://")

        db_metrics.instrument(engine)
        db_metrics.instrument(engine)

        db_metrics.reset()

        with engine.connect() as conn:
            from sqlalchemy import text

            conn.execute(text("SELECT 1"))

        assert db_metrics.snapshot().queries == 1

    def test_real_queries_are_counted(self):
        """End to end through SQLAlchemy, not just the bare counter."""
        from sqlalchemy import create_engine, text

        engine = create_engine("sqlite://")
        db_metrics.instrument(engine)
        db_metrics.reset()

        with engine.connect() as conn:
            for _ in range(3):
                conn.execute(text("SELECT 1"))

        usage = db_metrics.snapshot()

        assert usage.queries == 3
        assert usage.connections >= 1
