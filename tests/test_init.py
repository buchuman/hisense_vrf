"""Tests for the integration setup/unload (__init__.py)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from pyacmodbus import CannotConnect, ModbusReadError


async def test_setup_entry_loads(hass, setup_integration):
    """Integration loads successfully and stores the controller."""
    entry = setup_integration
    assert entry.state.value == "loaded"
    controller = entry.runtime_data
    assert controller is not None
    # Defaults from the fixture
    assert controller.verify_delay_s == 0.01
    assert controller.verify_retries == 2


async def test_setup_entry_cannot_connect_raises_not_ready(hass, mock_client, config_entry):
    config_entry.add_to_hass(hass)
    mock_client.connect.side_effect = CannotConnect("nope")
    with patch(
        "custom_components.hisense_vrf.ACModbusClient",
        return_value=mock_client,
    ):
        result = await hass.config_entries.async_setup(config_entry.entry_id)
    assert result is False  # setup_retry semantics
    assert config_entry.state.value == "setup_retry"


async def test_setup_entry_initial_scan_failure_raises_not_ready(
    hass, mock_client, config_entry
):
    config_entry.add_to_hass(hass)
    mock_client.scan_devices.side_effect = ModbusReadError("oops")
    with patch(
        "custom_components.hisense_vrf.ACModbusClient",
        return_value=mock_client,
    ):
        result = await hass.config_entries.async_setup(config_entry.entry_id)
    assert result is False
    assert config_entry.state.value == "setup_retry"


async def test_setup_entry_connect_timeout_raises_not_ready(
    hass, mock_client, config_entry
):
    """If the initial scan exceeds connect_timeout_s, setup retries instead of
    blocking the bootstrap forever (the bug previously seen when the gateway
    was unreachable because another client held its single TCP slot).
    """
    import asyncio

    from custom_components.hisense_vrf.const import CONF_CONNECT_TIMEOUT

    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry,
        options={**config_entry.options, CONF_CONNECT_TIMEOUT: 0.05},
    )

    async def _hang() -> None:
        await asyncio.sleep(10)

    mock_client.connect.side_effect = _hang

    with patch(
        "custom_components.hisense_vrf.ACModbusClient",
        return_value=mock_client,
    ):
        result = await hass.config_entries.async_setup(config_entry.entry_id)
    assert result is False
    assert config_entry.state.value == "setup_retry"


async def test_unload_entry_cleans_up(hass, setup_integration):
    entry = setup_integration
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    # After unload, runtime_data is reset by HA
    assert entry.state.value != "loaded"


async def test_unload_then_setup_again(hass, setup_integration, mock_client):
    """Unload then setup again works (idempotent)."""
    entry = setup_integration
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    with patch(
        "custom_components.hisense_vrf.ACModbusClient",
        return_value=mock_client,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state.value == "loaded"


async def test_dynamic_devices_creates_entities_in_all_platforms(
    hass, setup_integration, mock_client
):
    """When a new indoor unit is discovered at runtime, every platform creates
    its entities for that unit without a reload."""
    from homeassistant.helpers import entity_registry as er
    from tests import make_gateway_state, make_indoor_state

    controller = setup_integration.runtime_data
    registry = er.async_get(hass)

    # Sanity: unit 2 does not exist yet.
    assert registry.async_get_entity_id("climate", "hisense_vrf",
                                        "test_entry_id_indoor_2_climate") is None

    # Gateway now reports 3 units; scan returns the extra one.
    mock_client.read_gateway.return_value = make_gateway_state(unit_count=3)
    mock_client.scan_devices.return_value = [0, 1, 2]
    mock_client.read_device.side_effect = lambda idx: make_indoor_state(unit_index=idx)
    mock_client.read_unit_identifiers.side_effect = lambda idx: (0, idx)

    await controller._read_and_track_gateway()
    await hass.async_block_till_done()

    # One entity per platform was created for the new unit.
    for platform, uid in [
        ("climate", "test_entry_id_indoor_2_climate"),
        ("sensor", "test_entry_id_indoor_2_inlet_temp"),
        ("binary_sensor", "test_entry_id_indoor_2_running"),
        ("switch", "test_entry_id_indoor_2_power"),
        ("select", "test_entry_id_indoor_2_louver"),
        ("button", "test_entry_id_indoor_2_refresh"),
    ]:
        assert registry.async_get_entity_id(platform, "hisense_vrf", uid) is not None, (
            f"missing {platform}.{uid}"
        )
