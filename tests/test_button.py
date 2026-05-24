"""Tests for button platform: refresh, reset_filter, lock/unlock, eeprom, discover."""
from __future__ import annotations

from custom_components.hisense_vrf.const import DOMAIN


async def test_refresh_all_button_present(hass, setup_integration):
    assert (
        hass.states.get("button.hisense_vrf_gateway_refresh_all_units")
        is not None
    )


async def test_refresh_all_button_press_triggers_reads(
    hass, setup_integration, mock_client
):
    mock_client.read_device.reset_mock()
    mock_client.read_gateway.reset_mock()
    await hass.services.async_call(
        "button", "press",
        {"entity_id": "button.hisense_vrf_gateway_refresh_all_units"},
        blocking=True,
    )
    assert mock_client.read_device.await_count >= 2
    assert mock_client.read_gateway.await_count >= 1


async def test_refresh_unit_button_present(hass, setup_integration):
    assert (
        hass.states.get("button.ac_indoor_unit_0000_refresh_this_unit")
        is not None
    )


async def test_refresh_unit_button_reads_only_that_unit(
    hass, setup_integration, mock_client
):
    mock_client.read_device.reset_mock()
    await hass.services.async_call(
        "button", "press",
        {"entity_id": "button.ac_indoor_unit_0000_refresh_this_unit"},
        blocking=True,
    )
    # exactly one read, for unit_index 0
    assert mock_client.read_device.await_count == 1
    args = mock_client.read_device.await_args
    assert args.args[0] == 0


async def test_reset_filter_button(hass, setup_integration, mock_client):
    await hass.services.async_call(
        "button", "press",
        {"entity_id": "button.ac_indoor_unit_0000_reset_filter_alarm"},
        blocking=True,
    )
    mock_client.reset_filter.assert_awaited_once_with(0)


async def test_lock_all_button(hass, setup_integration, mock_client):
    await hass.services.async_call(
        "button", "press",
        {"entity_id": "button.ac_indoor_unit_0000_lock_all_controls"},
        blocking=True,
    )
    mock_client.lock_all.assert_awaited_once_with(0)


async def test_unlock_all_button(hass, setup_integration, mock_client):
    await hass.services.async_call(
        "button", "press",
        {"entity_id": "button.ac_indoor_unit_0000_unlock_all_controls"},
        blocking=True,
    )
    mock_client.unlock_all.assert_awaited_once_with(0)


async def test_eeprom_clear_button(hass, setup_integration, mock_client):
    # Populate gateway state first so the entity is available
    controller = setup_integration.runtime_data
    await controller.async_refresh_all()
    await hass.async_block_till_done()
    await hass.services.async_call(
        "button", "press",
        {"entity_id": "button.hisense_vrf_gateway_clear_eeprom"},
        blocking=True,
    )
    mock_client.clear_eeprom.assert_awaited_once()


async def test_discover_devices_button_reloads(hass, setup_integration):
    entry_id = setup_integration.entry_id
    await hass.services.async_call(
        "button", "press",
        {"entity_id": "button.hisense_vrf_gateway_discover_devices_reload"},
        blocking=True,
    )
    # After reload, the entry should still be loaded
    entry = hass.config_entries.async_get_entry(entry_id)
    assert entry.state.value == "loaded"
