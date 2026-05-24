"""Tests for sensor platform: indoor, outdoor, gateway, EXP enum sensors."""
from __future__ import annotations

import pytest

from custom_components.hisense_vrf.const import DOMAIN
from custom_components.hisense_vrf.sensor import (
    ALARM_CODES,
    LastWriteStatusSensor,
    RegisterBaseAddressSensor,
)
from custom_components.hisense_vrf.experimental import (
    ExpEnumDescriptor,
    ExpEnumSensor,
)


async def test_indoor_sensors_present(hass, setup_integration):
    """Each indoor unit exposes the expected indoor sensors."""
    assert hass.states.get("sensor.ac_indoor_unit_0000_inlet_temperature") is not None
    assert hass.states.get("sensor.ac_indoor_unit_0000_outlet_temperature") is not None
    assert hass.states.get("sensor.ac_indoor_unit_0000_setpoint") is not None
    assert hass.states.get("sensor.ac_indoor_unit_0000_alarm_code") is not None


async def test_indoor_sensor_reflects_state(hass, setup_integration):
    """A sensor's value mirrors the cached ACDeviceState."""
    controller = setup_integration.runtime_data
    await controller.async_refresh_unit(0)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.ac_indoor_unit_0000_inlet_temperature")
    assert state is not None
    assert state.state == "22.0"


async def test_alarm_codes_contains_known_codes():
    assert ALARM_CODES[0x00] == "No alarm"
    assert "protection" in ALARM_CODES[0x01].lower()
    assert 0x60 in ALARM_CODES  # gateway-specific


async def test_register_base_address(hass, setup_integration):
    controller = setup_integration.runtime_data
    s = RegisterBaseAddressSensor(controller, 1)
    s.hass = hass
    assert s.native_value == 40091  # 40000 + 1 * 91


async def test_last_write_status_default_idle(hass, setup_integration):
    controller = setup_integration.runtime_data
    s = LastWriteStatusSensor(controller, 0)
    s.hass = hass
    assert s.native_value == "idle"
    assert s.available is True


async def test_outdoor_sensors_present(hass, setup_integration):
    """Outdoor module exposes sensors and they pick up state after refresh."""
    controller = setup_integration.runtime_data
    await controller.async_refresh_all()
    await hass.async_block_till_done()
    state = hass.states.get("sensor.outdoor_unit_0_0_outdoor_ambient_temperature")
    assert state is not None


async def test_gateway_sensors_present(hass, setup_integration):
    controller = setup_integration.runtime_data
    await controller.async_refresh_all()
    await hass.async_block_till_done()
    state = hass.states.get("sensor.hisense_vrf_gateway_connected_indoor_units")
    assert state is not None


async def test_exp_enum_sensor_decodes_value(hass, setup_integration):
    """ExpEnumSensor returns the configured option name based on the bits."""
    from tests import make_indoor_state

    controller = setup_integration.runtime_data
    desc = ExpEnumDescriptor(
        key="b4_filter_cleaning_time",
        name="EXP B4 Filter",
        reg_offset=1,
        shift=0,
        mask=0b1111,
        options=["indoor_std", "100h", "1200h", "2500h", "no_indication"],
    )
    # reg[1] = 2 → option 2 = "1200h"
    fs = list((0,) * 20)
    fs[1] = 2
    controller.indoor_states[0] = make_indoor_state(function_selection=tuple(fs))
    s = ExpEnumSensor(controller, 0, desc)
    s.hass = hass
    assert s.native_value == "1200h"


async def test_exp_enum_sensor_unknown_for_out_of_range(hass, setup_integration):
    from tests import make_indoor_state

    controller = setup_integration.runtime_data
    desc = ExpEnumDescriptor(
        key="x",
        name="EXP X",
        reg_offset=1,
        shift=0,
        mask=0b1111,
        options=["a", "b"],
    )
    fs = list((0,) * 20)
    fs[1] = 5  # not in options
    controller.indoor_states[0] = make_indoor_state(function_selection=tuple(fs))
    s = ExpEnumSensor(controller, 0, desc)
    s.hass = hass
    assert s.native_value == "unknown_5"


async def test_exp_enum_sensor_none_when_no_state(hass, setup_integration):
    controller = setup_integration.runtime_data
    desc = ExpEnumDescriptor(
        key="x", name="EXP X", reg_offset=0, shift=0, mask=0b11, options=["a", "b"],
    )
    controller.indoor_states[0] = None
    s = ExpEnumSensor(controller, 0, desc)
    s.hass = hass
    assert s.native_value is None
