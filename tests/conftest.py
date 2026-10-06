"""
Pytest configuration and shared fixtures.
"""

import pytest

import app.services.mlb_client as mlb_client_module


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "integration: marks tests that hit real external APIs (deselect with '-m \"not integration\"')"
    )


@pytest.fixture(autouse=True)
def reset_mlb_client():
    """
    Drop the cached MLB client between tests.

    MLBStatsClient keeps httpx.AsyncClient instances on a module-level
    singleton so connection pools are reused across requests. TestClient runs
    each request in its own event loop, so a pool created by one test is bound
    to a loop that is closed by the time the next test runs — the reused client
    then fails with "Event loop is closed" and the endpoint returns 500.

    The client only rebuilds a pool when it is missing or explicitly closed,
    and a pool orphaned by a dead loop is neither, so clearing the singleton is
    what forces a usable one. Production is unaffected: there the app runs on a
    single long-lived loop, which is exactly what the singleton is designed for.
    """
    mlb_client_module._mlb_client = None

    yield

    mlb_client_module._mlb_client = None
