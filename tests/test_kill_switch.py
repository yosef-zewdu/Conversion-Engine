"""
Property tests for kill-switch routing.

# Feature: conversion-engine, Property 1: Kill-Switch Routing
# For any KILL_SWITCH value, destination is STAFF_SINK unless value is exactly "enabled".
# Validates: Requirements 1.1, 1.2, 1.3, 1.5
"""
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from config.models import Destination


def _get_destination_for_value(value: str) -> Destination:
    """Helper: set KILL_SWITCH env var and call get_outbound_destination()."""
    import os
    import importlib
    import config.kill_switch as ks_module

    os.environ["KILL_SWITCH"] = value
    # Re-read env var by calling the function directly (it reads os.environ each call)
    return ks_module.get_outbound_destination()


# ---------------------------------------------------------------------------
# Property 1: Kill-Switch Routing
# For ANY text value other than exactly "enabled", destination must be STAFF_SINK.
# ---------------------------------------------------------------------------

@given(st.text().filter(lambda x: "\0" not in x))
@settings(max_examples=100)
def test_kill_switch_non_enabled_routes_to_staff_sink(value: str):
    """
    # Feature: conversion-engine, Property 1: Kill-Switch Routing
    For any KILL_SWITCH value that is NOT exactly "enabled",
    get_outbound_destination() must return STAFF_SINK.
    """
    if value == "enabled":
        return  # skip the one valid value; tested separately below

    destination = _get_destination_for_value(value)
    assert destination == Destination.STAFF_SINK, (
        f"Expected STAFF_SINK for KILL_SWITCH={value!r}, got {destination}"
    )


def test_kill_switch_enabled_routes_to_prospect():
    """
    When KILL_SWITCH is exactly "enabled", destination must be PROSPECT.
    """
    destination = _get_destination_for_value("enabled")
    assert destination == Destination.PROSPECT


def test_kill_switch_empty_routes_to_staff_sink():
    """
    When KILL_SWITCH is empty string (unset default), destination must be STAFF_SINK.
    """
    destination = _get_destination_for_value("")
    assert destination == Destination.STAFF_SINK


def test_kill_switch_unset_routes_to_staff_sink(monkeypatch):
    """
    When KILL_SWITCH env var is absent entirely, destination must be STAFF_SINK.
    """
    import config.kill_switch as ks_module

    monkeypatch.delenv("KILL_SWITCH", raising=False)
    destination = ks_module.get_outbound_destination()
    assert destination == Destination.STAFF_SINK


@pytest.mark.parametrize("value", [
    "Enabled", "ENABLED", "enable", "enabled ", " enabled", "true", "1", "yes", "on",
])
def test_kill_switch_near_miss_values_route_to_staff_sink(value: str):
    """
    Near-miss values (wrong case, extra whitespace, synonyms) must still route to STAFF_SINK.
    Only the exact string "enabled" is accepted.
    """
    destination = _get_destination_for_value(value)
    assert destination == Destination.STAFF_SINK, (
        f"Expected STAFF_SINK for near-miss value {value!r}, got {destination}"
    )


def test_kill_switch_logs_warning_on_unrecognised_value(caplog):
    """
    An unrecognised (non-empty, non-"enabled") value must emit a warning log.
    """
    import logging
    import config.kill_switch as ks_module

    with caplog.at_level(logging.WARNING, logger="config.kill_switch"):
        _get_destination_for_value("not-enabled")

    assert any("unrecognised" in record.message.lower() for record in caplog.records), (
        "Expected a warning log containing 'unrecognised' for an unrecognised KILL_SWITCH value"
    )


def test_kill_switch_logs_warning_on_absent_value(caplog):
    """
    An absent (empty) KILL_SWITCH must emit a warning log.
    """
    import logging
    import config.kill_switch as ks_module

    with caplog.at_level(logging.WARNING, logger="config.kill_switch"):
        _get_destination_for_value("")

    assert any(record.levelno == logging.WARNING for record in caplog.records), (
        "Expected a warning log when KILL_SWITCH is not set"
    )
