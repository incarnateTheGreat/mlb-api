"""
Pytest configuration and shared fixtures.
"""

import pytest


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "integration: marks tests that hit real external APIs (deselect with '-m \"not integration\"')"
    )
