"""Tests for the diagnostics handler."""
from __future__ import annotations

from custom_components.hisense_vrf.diagnostics import (
    async_get_config_entry_diagnostics,
)

from . import make_indoor_state


async def test_diagnostics_returns_serialisable_dump(hass, setup_integration):
    controller = setup_integration.runtime_data
    controller.indoor_states[0] = make_indoor_state(setpoint=24.0, alarm_code=0)
    controller.pending[0] = {"setpoint": 25.0}
    data = await async_get_config_entry_diagnostics(hass, setup_integration)
    assert data["entry"]["unique_id"] == "192.168.1.50:502"
    assert data["scan"]["unit_indices"] == [0, 1]
    assert "outdoor_units" in data["scan"]
    assert data["indoor_states"]["0"]["setpoint"] == 24.0
    assert data["pending"]["0"]["setpoint"] == 25.0
    assert "last_write_status" in data
    assert "polling" in data
    assert data["tuning"]["verify_delay_s"] == 0.01


async def test_diagnostics_works_with_no_state(hass, setup_integration):
    """When the integration has not refreshed yet, indoor_states are still None.

    ``gateway_state`` *is* populated by ``initial_scan`` so the dynamic-devices
    baseline can be seeded — only indoor/outdoor reads stay deferred.
    """
    data = await async_get_config_entry_diagnostics(hass, setup_integration)
    assert data["indoor_states"]["0"] is None


async def test_diagnostics_round_trips_through_json(hass, setup_integration):
    """The whole dump must be JSON-serialisable as-is (HA writes it to disk)."""
    import json
    data = await async_get_config_entry_diagnostics(hass, setup_integration)
    encoded = json.dumps(data, default=str)
    assert "scan" in encoded
    assert "indoor_states" in encoded
