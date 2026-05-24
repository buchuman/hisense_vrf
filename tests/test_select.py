"""Tests for select platform: louver and dry_mode."""
from __future__ import annotations

from custom_components.hisense_vrf.const import DOMAIN

from . import make_indoor_state


async def test_louver_select_exists(hass, setup_integration):
    assert hass.states.get("select.ac_indoor_unit_0000_louver") is not None


async def test_louver_select_auto_state(hass, setup_integration):
    controller = setup_integration.runtime_data
    controller.indoor_states[0] = make_indoor_state(auto_swing=True, louver_position=0)
    controller._notify()
    await hass.async_block_till_done()
    state = hass.states.get("select.ac_indoor_unit_0000_louver")
    assert state.state == "auto"


async def test_louver_select_position_state(hass, setup_integration):
    controller = setup_integration.runtime_data
    controller.indoor_states[0] = make_indoor_state(auto_swing=False, louver_position=3)
    controller._notify()
    await hass.async_block_till_done()
    state = hass.states.get("select.ac_indoor_unit_0000_louver")
    assert state.state == "3"


async def test_louver_select_auto_writes(hass, setup_integration, mock_client):
    controller = setup_integration.runtime_data
    controller.indoor_states[0] = make_indoor_state(is_running=True, auto_swing=False)
    mock_client.read_device.side_effect = lambda idx: make_indoor_state(
        unit_index=idx, is_running=True, auto_swing=True,
    )
    await hass.services.async_call(
        "select", "select_option",
        {"entity_id": "select.ac_indoor_unit_0000_louver", "option": "auto"},
        blocking=True,
    )
    mock_client.set_swing.assert_awaited()


async def test_dry_mode_select_exists(hass, setup_integration):
    assert hass.states.get("select.ac_indoor_unit_0000_dry_mode") is not None


async def test_louver_current_option_none_when_no_state(hass, setup_integration):
    controller = setup_integration.runtime_data
    controller.indoor_states[0] = None
    controller._notify()
    await hass.async_block_till_done()
    state = hass.states.get("select.ac_indoor_unit_0000_louver")
    assert state.state == "unavailable"


async def test_louver_select_position_writes(hass, setup_integration, mock_client):
    controller = setup_integration.runtime_data
    controller.indoor_states[0] = make_indoor_state(is_running=True, auto_swing=True, louver_position=0)
    mock_client.read_device.side_effect = lambda idx: make_indoor_state(
        unit_index=idx, is_running=True, auto_swing=False, louver_position=3,
    )
    await hass.services.async_call(
        "select", "select_option",
        {"entity_id": "select.ac_indoor_unit_0000_louver", "option": "3"},
        blocking=True,
    )
    mock_client.set_swing.assert_awaited()


async def test_louver_select_invalid_option_noop(hass, setup_integration, mock_client):
    controller = setup_integration.runtime_data
    controller.indoor_states[0] = make_indoor_state(is_running=True)
    # Out-of-range "8" goes through int() but range check rejects it.
    from custom_components.hisense_vrf.select import LouverSelect
    entity = LouverSelect(controller, 0)
    entity.hass = hass
    await entity.async_select_option("8")
    mock_client.set_swing.assert_not_awaited()


async def test_louver_select_non_numeric_noop(hass, setup_integration, mock_client):
    from custom_components.hisense_vrf.select import LouverSelect
    controller = setup_integration.runtime_data
    entity = LouverSelect(controller, 0)
    entity.hass = hass
    await entity.async_select_option("garbage")
    mock_client.set_swing.assert_not_awaited()


async def test_dry_mode_current_option_none_when_no_state(hass, setup_integration):
    controller = setup_integration.runtime_data
    controller.indoor_states[0] = None
    controller._notify()
    await hass.async_block_till_done()
    state = hass.states.get("select.ac_indoor_unit_0000_dry_mode")
    assert state.state == "unavailable"


async def test_dry_mode_select_writes(hass, setup_integration, mock_client):
    controller = setup_integration.runtime_data
    controller.indoor_states[0] = make_indoor_state(is_running=True, dry_mode=0)
    mock_client.read_device.side_effect = lambda idx: make_indoor_state(
        unit_index=idx, is_running=True, dry_mode=1,
    )
    await hass.services.async_call(
        "select", "select_option",
        {"entity_id": "select.ac_indoor_unit_0000_dry_mode", "option": "dry2"},
        blocking=True,
    )
    mock_client.set_dry_mode.assert_awaited()


async def test_dry_mode_invalid_option_noop(hass, setup_integration, mock_client):
    from custom_components.hisense_vrf.select import DryModeSelect
    controller = setup_integration.runtime_data
    entity = DryModeSelect(controller, 0)
    entity.hass = hass
    await entity.async_select_option("nonexistent")
    mock_client.set_dry_mode.assert_not_awaited()
