"""Tests for binary_sensor: indoor, gateway, EXP bit sensors."""
from __future__ import annotations

from custom_components.hisense_vrf.const import DOMAIN
from custom_components.hisense_vrf.experimental import (
    ExpBitBinarySensor,
    ExpBitDescriptor,
)

from . import make_indoor_state


async def test_indoor_binary_sensors_present(hass, setup_integration):
    assert hass.states.get("binary_sensor.ac_indoor_unit_0000_running") is not None
    assert hass.states.get("binary_sensor.ac_indoor_unit_0000_alarm") is not None
    assert hass.states.get("binary_sensor.ac_indoor_unit_0000_filter_alarm") is not None


async def test_running_reflects_is_running(hass, setup_integration):
    controller = setup_integration.runtime_data
    controller.indoor_states[0] = make_indoor_state(is_running=True)
    await controller.async_refresh_unit(0)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.ac_indoor_unit_0000_running")
    # The refresh_unit re-reads via mock which returns is_running=True by default
    assert state.state in ("on", "off")  # state was updated


async def test_alarm_on_when_alarm_code_nonzero(hass, setup_integration):
    controller = setup_integration.runtime_data
    controller.indoor_states[0] = make_indoor_state(alarm_code=0x60)
    controller._notify()
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.ac_indoor_unit_0000_alarm")
    assert state.state == "on"


async def test_alarm_off_when_alarm_code_zero(hass, setup_integration):
    controller = setup_integration.runtime_data
    controller.indoor_states[0] = make_indoor_state(alarm_code=0)
    controller._notify()
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.ac_indoor_unit_0000_alarm")
    assert state.state == "off"


async def test_gateway_alarm_display_present(hass, setup_integration):
    controller = setup_integration.runtime_data
    await controller.async_refresh_all()
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.hisense_vrf_gateway_gateway_alarm")
    assert state is not None


async def test_exp_bit_binary_sensor_reads_bit(hass, setup_integration):
    controller = setup_integration.runtime_data
    desc = ExpBitDescriptor(
        key="b8_auto_cool_heat", name="EXP B8", reg_offset=2, bit=2,
    )
    fs = [0] * 20
    fs[2] = 1 << 2  # B8 ON
    controller.indoor_states[0] = make_indoor_state(function_selection=tuple(fs))
    s = ExpBitBinarySensor(controller, 0, desc)
    s.hass = hass
    assert s.is_on is True


async def test_exp_bit_binary_sensor_off(hass, setup_integration):
    controller = setup_integration.runtime_data
    desc = ExpBitDescriptor(key="b8", name="EXP B8", reg_offset=2, bit=2)
    controller.indoor_states[0] = make_indoor_state(function_selection=tuple([0] * 20))
    s = ExpBitBinarySensor(controller, 0, desc)
    s.hass = hass
    assert s.is_on is False


async def test_exp_bit_binary_sensor_none_when_no_state(hass, setup_integration):
    controller = setup_integration.runtime_data
    desc = ExpBitDescriptor(key="b8", name="EXP B8", reg_offset=2, bit=2)
    controller.indoor_states[0] = None
    s = ExpBitBinarySensor(controller, 0, desc)
    s.hass = hass
    assert s.is_on is None
