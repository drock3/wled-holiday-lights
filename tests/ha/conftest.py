"""Fixtures for tests against real Home Assistant APIs."""

import pytest


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load the integration from this repository."""
