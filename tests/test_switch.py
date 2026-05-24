"""Tests for switch platform: power, prohibits, gateway alarm."""
from __future__ import annotations

from custom_components.hisense_vrf.const import DOMAIN

from . import make_indoor_state


async def test_power_switch_exists(hass, setup_integration):
    assert hass.states.get("switch.ac_indoor_unit_0000_power") is not None


async def test_power_switch_on_state(hass, setup_integration):
    controller = setup_integration.runtime_data
    controller.indoor_states[0] = make_indoor_state(is_running=True)
    controller._notify()
    await hass.async_block_till_done()
    state = hass.states.get("switch.ac_indoor_unit_0000_power")
    assert state.state == "on"


async def test_power_switch_off_when_already_off(hass, setup_integration, mock_client):
    controller = setup_integration.runtime_data
    controller.indoor_states[0] = make_indoor_state(is_running=False)
    controller._notify()
    await hass.async_block_till_done()
    # Call turn_off when already off: should be a no-op
    await hass.services.async_call(
        "switch", "turn_off",
        {"entity_id": "switch.ac_indoor_unit_0000_power"},
        blocking=True,
    )
    mock_client.turn_off.assert_not_awaited()


async def test_prohibit_switches_exist(hass, setup_integration):
    """All five prohibition switches are present."""
    for key in ["on_off", "mode", "fan", "swing", "temperature"]:
        entity_id = f"switch.ac_indoor_unit_0000_lock_{key}_button"
        assert hass.states.get(entity_id) is not None, entity_id


async def test_prohibit_switch_turn_on_calls_client(hass, setup_integration, mock_client):
    controller = setup_integration.runtime_data
    controller.indoor_states[0] = make_indoor_state()  # is_running=True (default)
    # The switch needs the unit "powered_on" to use the write-and-verify path
    mock_client.read_device.side_effect = lambda idx: make_indoor_state(
        unit_index=idx, prohibit_on_off=True,
    )
    await hass.services.async_call(
        "switch", "turn_on",
        {"entity_id": "switch.ac_indoor_unit_0000_lock_on_off_button"},
        blocking=True,
    )
    mock_client.set_prohibition.assert_awaited()


async def test_gateway_alarm_display_switch_exists(hass, setup_integration):
    assert (
        hass.states.get("switch.hisense_vrf_gateway_gateway_alarm_display")
        is not None
    )


async def test_power_switch_is_on_none_when_no_state(hass, setup_integration):
    controller = setup_integration.runtime_data
    controller.indoor_states[0] = None
    controller._notify()
    await hass.async_block_till_done()
    from custom_components.hisense_vrf.switch import PowerSwitch
    s = PowerSwitch(controller, 0)
    s.hass = hass
    assert s.is_on is None


async def test_power_switch_turn_on_when_off_routes_to_send_on(hass, setup_integration, mock_client):
    controller = setup_integration.runtime_data
    controller.indoor_states[0] = make_indoor_state(is_running=False)
    mock_client.read_device.side_effect = lambda idx: make_indoor_state(
        unit_index=idx, is_running=True,
    )
    await hass.services.async_call(
        "switch", "turn_on",
        {"entity_id": "switch.ac_indoor_unit_0000_power"},
        blocking=True,
    )
    mock_client.write_control_block.assert_awaited()


async def test_power_switch_turn_off_when_on(hass, setup_integration, mock_client):
    controller = setup_integration.runtime_data
    controller.indoor_states[0] = make_indoor_state(is_running=True)
    mock_client.read_device.side_effect = lambda idx: make_indoor_state(
        unit_index=idx, is_running=False,
    )
    await hass.services.async_call(
        "switch", "turn_off",
        {"entity_id": "switch.ac_indoor_unit_0000_power"},
        blocking=True,
    )
    mock_client.turn_off.assert_awaited()


async def test_prohibit_switch_turn_off_calls_client(hass, setup_integration, mock_client):
    controller = setup_integration.runtime_data
    controller.indoor_states[0] = make_indoor_state(prohibit_on_off=True)
    mock_client.read_device.side_effect = lambda idx: make_indoor_state(
        unit_index=idx, prohibit_on_off=False,
    )
    await hass.services.async_call(
        "switch", "turn_off",
        {"entity_id": "switch.ac_indoor_unit_0000_lock_on_off_button"},
        blocking=True,
    )
    mock_client.set_prohibition.assert_awaited()


async def test_prohibit_switch_is_on_none_when_no_state(hass, setup_integration):
    from custom_components.hisense_vrf.switch import ProhibitSwitch, PROHIBIT_SWITCHES
    controller = setup_integration.runtime_data
    controller.indoor_states[0] = None
    desc = PROHIBIT_SWITCHES[0]
    entity = ProhibitSwitch(controller, 0, desc)
    entity.hass = hass
    assert entity.is_on is None


async def test_gateway_alarm_display_is_on_none_when_no_state(hass, setup_integration):
    from custom_components.hisense_vrf.switch import GatewayAlarmDisplaySwitch
    controller = setup_integration.runtime_data
    controller.gateway_state = None
    s = GatewayAlarmDisplaySwitch(controller)
    s.hass = hass
    assert s.is_on is None


async def test_gateway_alarm_display_turn_on(hass, setup_integration, mock_client):
    controller = setup_integration.runtime_data
    await controller.async_refresh_all()  # populate gateway_state
    await hass.async_block_till_done()
    await hass.services.async_call(
        "switch", "turn_on",
        {"entity_id": "switch.hisense_vrf_gateway_gateway_alarm_display"},
        blocking=True,
    )
    mock_client.set_alarm_display.assert_awaited_once_with(True)


async def test_gateway_alarm_display_turn_off(hass, setup_integration, mock_client):
    controller = setup_integration.runtime_data
    await controller.async_refresh_all()
    await hass.async_block_till_done()
    await hass.services.async_call(
        "switch", "turn_off",
        {"entity_id": "switch.hisense_vrf_gateway_gateway_alarm_display"},
        blocking=True,
    )
    mock_client.set_alarm_display.assert_awaited_once_with(False)
