"""
Track what the application actually costs on a serverless database.

Neon bills for the time the compute stays AWAKE, not for queries, and it
suspends after a few minutes without one. That makes the expensive thing
invisible from inside the app: a single query every few minutes costs the same
as a continuous workload, while a burst of thousands inside one game costs
almost nothing extra.

Nothing in the stack reports this. The consumption API that would is limited
to paid plans, and the Console keeps only a day of history on Free - which is
how a month's allowance was spent on empty polling before anyone noticed. So
the app counts it itself.

The awake figure is a MODEL of Neon's behaviour, not a reading from it: every
query is assumed to hold the compute awake for `SUSPEND_AFTER_SECONDS`, and
overlapping windows are merged. It tracks the real bill closely for a workload
like this one, but treat it as an estimate and reconcile against the Console
before trusting it for anything that matters.
"""

import threading
import time
from dataclasses import dataclass

# Neon suspends an idle compute after 5 minutes on every plan; Free cannot
# change it. Any query therefore buys at least this much awake time.
SUSPEND_AFTER_SECONDS = 300.0

# Smallest compute Neon will run, and what this workload sits at. CU-hours are
# billed as compute size x hours, so this converts awake time into allowance.
MIN_COMPUTE_UNITS = 0.25

_lock = threading.Lock()

_queries = 0
_connections = 0

# Closed awake windows, in seconds.
_closed_awake = 0.0

# The window currently open, as monotonic timestamps. `_awake_until` is in the
# future: the compute stays up that long unless another query extends it.
_window_start: float | None = None
_awake_until = 0.0

_process_started = time.monotonic()
_first_query_wall: float | None = None


@dataclass(frozen=True)
class DbUsage:
    """A point-in-time reading of what the database has cost."""

    queries: int
    connections: int
    awake_seconds: float
    uptime_seconds: float
    first_query_wall: float | None

    @property
    def awake_hours(self) -> float:
        return self.awake_seconds / 3600

    @property
    def cu_hours(self) -> float:
        return self.awake_hours * MIN_COMPUTE_UNITS

    @property
    def awake_fraction(self) -> float:
        """Share of the process's life the database was billable.

        The number that matters: near 1.0 means it never suspended once.
        Capped, because the open window is counted through to its suspend
        point and that can reach slightly past now.
        """
        if self.uptime_seconds <= 0:
            return 0.0

        return min(1.0, self.awake_seconds / self.uptime_seconds)

    def summary(self) -> str:
        return (
            f"queries={self.queries} connections={self.connections} "
            f"awake={self.awake_hours:.2f}h of {self.uptime_seconds / 3600:.2f}h "
            f"({self.awake_fraction * 100:.1f}%) "
            f"cu_hours={self.cu_hours:.3f}"
        )


def record_query() -> None:
    """Count one statement and extend the awake window."""
    global _queries, _closed_awake, _window_start, _awake_until
    global _first_query_wall

    now = time.monotonic()

    with _lock:
        _queries += 1

        if _first_query_wall is None:
            _first_query_wall = time.time()

        if _window_start is None:
            _window_start = now
        elif now > _awake_until:
            # The compute suspended before this query, so the previous window
            # is finished and this one starts a new (billable) resume.
            _closed_awake += _awake_until - _window_start
            _window_start = now

        _awake_until = now + SUSPEND_AFTER_SECONDS


def record_connection() -> None:
    """Count one new pooled connection."""
    global _connections

    with _lock:
        _connections += 1


def snapshot() -> DbUsage:
    """Read the counters without disturbing them."""
    now = time.monotonic()

    with _lock:
        awake = _closed_awake

        if _window_start is not None:
            # The window ends where the compute suspends, whether or not that
            # has happened yet. Measuring to `now` instead would count every
            # idle second as billable and report an always-on database even
            # after the polling that caused one was removed.
            awake += _awake_until - _window_start

        return DbUsage(
            queries=_queries,
            connections=_connections,
            awake_seconds=awake,
            uptime_seconds=now - _process_started,
            first_query_wall=_first_query_wall,
        )


def reset() -> None:
    """Clear every counter. For tests."""
    global _queries, _connections, _closed_awake, _window_start
    global _awake_until, _process_started, _first_query_wall

    with _lock:
        _queries = 0
        _connections = 0
        _closed_awake = 0.0
        _window_start = None
        _awake_until = 0.0
        _process_started = time.monotonic()
        _first_query_wall = None


def instrument(engine) -> None:
    """Attach the counters to a SQLAlchemy engine.

    Listens on the sync engine behind the async one, which is where SQLAlchemy
    emits these events. Safe to call more than once.
    """
    from sqlalchemy import event

    target = getattr(engine, "sync_engine", engine)

    if getattr(target, "_mlb_usage_instrumented", False):
        return

    @event.listens_for(target, "before_cursor_execute")
    def _count_query(conn, cursor, statement, parameters, context, executemany):
        record_query()

    @event.listens_for(target, "connect")
    def _count_connection(dbapi_connection, connection_record):
        record_connection()

    target._mlb_usage_instrumented = True
