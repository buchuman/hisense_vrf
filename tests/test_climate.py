"""Tests for the climate platform: dynamic capabilities and on/off routing."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.hisense_vrf.climate import HisenseVRFClimate
from custom_components.hisense_vrf.const import DOMAIN
from homeassistant.components.climate import (
    ClimateEntityFeature,
    HVACMode,
)
from pyacmodbus import (
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    MODE_COOL,
    MODE_HEAT,
)

from . import make_indoor_state


def _fs_with_bit(reg_offset: int, bit: int, *, value: int = 1) -> tuple[int, ...]:
    """Build a 20-element function_selection tuple with one bit set."""
    fs = [0] * 20
    fs[reg_offset] = value << bit if value else 0
    return tuple(fs)


def _fs_multi(*pairs: tuple[int, int]) -> tuple[int, ...]:
    """Build a 20-element function_selection tuple with several bits set."""
    fs = [0] * 20
    for reg_offset, bit in pairs:
        fs[reg_offset] |= 1 << bit
    return tuple(fs)


@pytest.fixture
def climate_entity(scanned_climate):
    """Convenience alias."""
    return scanned_climate


@pytest.fixture
def scanned_climate(hass, mock_client):
    """A HisenseVRFClimate wired to a controller with a fake state."""
    from custom_components.hisense_vrf.controller import HisenseVRFController

    c = HisenseVRFController(
        hass=hass,
        entry_id="test_entry_id",
        client=mock_client,
        verify_delay_s=0.01,
        verify_retries=0,
        off_pending_ttl_s=0.2,
        polling_enabled=False,
        poll_interval_s=5.0,
        poll_spacing_s=0.0,
        poll_gateway_every_n=10,
    )
    c.unit_indices = [0]
    c.unit_identifiers = {0: (0, 0)}
    c.unit_capacities = {0: 22}
    c.indoor_states = {0: make_indoor_state()}
    c.pending = {0: {}}
    c._off_pending = {0: {}}
    c.last_write_status = {0: {"status": "idle"}}
    entity = HisenseVRFClimate(c, 0)
    entity.hass = hass
    return entity


@pytest.fixture(autouse=True)
async def _cleanup_climate_fixture(scanned_climate):
    """Ensure off_pending timers and polling tasks are cancelled after each test."""
    yield
    await scanned_climate.controller.async_shutdown()


# ── Capabilities dinámicas ──────────────────────────────────────────────────


def test_hvac_modes_default_no_bits(scanned_climate):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(
        function_selection=tuple([0] * 20),
    )
    modes = scanned_climate.hvac_modes
    assert HVACMode.OFF in modes
    assert HVACMode.COOL in modes
    assert HVACMode.HEAT in modes
    assert HVACMode.DRY in modes
    assert HVACMode.FAN_ONLY in modes
    assert HVACMode.HEAT_COOL not in modes


def test_hvac_modes_b8_enables_heat_cool(scanned_climate):
    # B8 = reg_offset 2, bit 2
    scanned_climate.controller.indoor_states[0] = make_indoor_state(
        function_selection=_fs_with_bit(2, 2),
    )
    assert HVACMode.HEAT_COOL in scanned_climate.hvac_modes


def test_hvac_modes_c1_removes_heat(scanned_climate):
    # C1 = reg_offset 0, bit 6
    scanned_climate.controller.indoor_states[0] = make_indoor_state(
        function_selection=_fs_with_bit(0, 6),
    )
    assert HVACMode.HEAT not in scanned_climate.hvac_modes


def test_hvac_modes_b7_locks_cooling(scanned_climate):
    # B7 = reg_offset 2, bit 3
    scanned_climate.controller.indoor_states[0] = make_indoor_state(
        function_selection=_fs_with_bit(2, 3),
    )
    assert scanned_climate.hvac_modes == [HVACMode.OFF, HVACMode.COOL]


def test_hvac_modes_b5_locks_current_mode(scanned_climate):
    # B5 = reg_offset 1, bit 4. With current_mode = MODE_HEAT.
    scanned_climate.controller.indoor_states[0] = make_indoor_state(
        current_mode=MODE_HEAT,
        function_selection=_fs_with_bit(1, 4),
    )
    modes = scanned_climate.hvac_modes
    assert HVACMode.OFF in modes
    assert HVACMode.HEAT in modes
    assert HVACMode.COOL not in modes


def test_fan_modes_default(scanned_climate):
    scanned_climate.controller.indoor_states[0] = make_indoor_state()
    assert scanned_climate.fan_modes == ["auto", "high", "medium", "low"]


def test_fan_modes_b9_locks_to_current(scanned_climate):
    # B9 = reg_offset 2, bit 7
    scanned_climate.controller.indoor_states[0] = make_indoor_state(
        fan_speed=FAN_HIGH,
        function_selection=_fs_with_bit(2, 7),
    )
    assert scanned_climate.fan_modes == ["high"]


def test_fan_modes_c5_adds_hi1(scanned_climate):
    # C5 = reg_offset 4, shift 3, mask 0b11. value 1 → hi_1
    fs = [0] * 20
    fs[4] = 1 << 3
    scanned_climate.controller.indoor_states[0] = make_indoor_state(
        function_selection=tuple(fs),
    )
    assert "high_high_1" in scanned_climate.fan_modes


def test_fan_modes_c5_adds_hi2(scanned_climate):
    fs = [0] * 20
    fs[4] = 2 << 3  # 2 → hi_2
    scanned_climate.controller.indoor_states[0] = make_indoor_state(
        function_selection=tuple(fs),
    )
    assert "high_high_2" in scanned_climate.fan_modes


def test_supported_features_b6_removes_target_temperature(scanned_climate):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(
        function_selection=_fs_with_bit(2, 5),  # B6
    )
    feat = scanned_climate.supported_features
    assert not (feat & ClimateEntityFeature.TARGET_TEMPERATURE)
    assert feat & ClimateEntityFeature.FAN_MODE


def test_supported_features_b9_removes_fan_mode(scanned_climate):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(
        function_selection=_fs_with_bit(2, 7),  # B9
    )
    feat = scanned_climate.supported_features
    assert not (feat & ClimateEntityFeature.FAN_MODE)
    assert feat & ClimateEntityFeature.TARGET_TEMPERATURE


def test_capabilities_default_when_no_state(scanned_climate):
    scanned_climate.controller.indoor_states[0] = None
    # No state → conservative defaults
    assert HVACMode.OFF in scanned_climate.hvac_modes
    assert scanned_climate.fan_modes == ["auto", "high", "medium", "low"]


# ── Basic properties ────────────────────────────────────────────────────────


def test_current_temperature_from_inlet_temp(scanned_climate):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(inlet_temp=22.5)
    assert scanned_climate.current_temperature == 22.5


def test_target_temperature_from_display_state(scanned_climate):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(setpoint=24.0)
    assert scanned_climate.target_temperature == 24.0


def test_target_temperature_uses_pending_overlay(scanned_climate):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(setpoint=22.0)
    scanned_climate.controller.pending[0] = {"setpoint": 24.0}
    assert scanned_climate.target_temperature == 24.0


def test_hvac_mode_off_when_not_running(scanned_climate):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(is_running=False)
    assert scanned_climate.hvac_mode == HVACMode.OFF


def test_hvac_mode_reports_current_mode(scanned_climate):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(
        is_running=True, current_mode=MODE_HEAT,
    )
    assert scanned_climate.hvac_mode == HVACMode.HEAT


# ── Methods — powered-on routing ────────────────────────────────────────────


async def test_set_temperature_powered_on_writes_immediately(scanned_climate, mock_client):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(is_running=True)
    mock_client.read_device.side_effect = lambda idx: make_indoor_state(
        unit_index=idx, is_running=True, setpoint=25.0,
    )
    await scanned_climate.async_set_temperature(temperature=25.0)
    mock_client.set_setpoint.assert_awaited_once()


async def test_set_hvac_mode_off_writes_turn_off(scanned_climate, mock_client):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(is_running=True)
    mock_client.read_device.side_effect = lambda idx: make_indoor_state(
        unit_index=idx, is_running=False,
    )
    await scanned_climate.async_set_hvac_mode(HVACMode.OFF)
    mock_client.turn_off.assert_awaited_once()


async def test_set_hvac_mode_when_already_off_no_op(scanned_climate, mock_client):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(is_running=False)
    await scanned_climate.async_set_hvac_mode(HVACMode.OFF)
    mock_client.turn_off.assert_not_awaited()


async def test_set_hvac_mode_on_powered_writes_set_mode(scanned_climate, mock_client):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(
        is_running=True, current_mode=MODE_COOL,
    )
    mock_client.read_device.side_effect = lambda idx: make_indoor_state(
        unit_index=idx, is_running=True, current_mode=MODE_HEAT,
    )
    await scanned_climate.async_set_hvac_mode(HVACMode.HEAT)
    mock_client.set_mode.assert_awaited_once_with(0, MODE_HEAT)


async def test_set_fan_mode_powered_on(scanned_climate, mock_client):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(is_running=True)
    mock_client.read_device.side_effect = lambda idx: make_indoor_state(
        unit_index=idx, is_running=True, fan_speed=FAN_HIGH,
    )
    await scanned_climate.async_set_fan_mode("high")
    mock_client.set_fan_speed.assert_awaited_once_with(0, FAN_HIGH)


# ── Methods — powered-off routing (off_pending) ─────────────────────────────


async def test_set_temperature_powered_off_accumulates(scanned_climate, mock_client):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(is_running=False)
    await scanned_climate.async_set_temperature(temperature=25.0)
    mock_client.set_setpoint.assert_not_awaited()
    assert scanned_climate.controller._off_pending[0]["setpoint"] == 25.0


async def test_set_fan_mode_powered_off_accumulates(scanned_climate, mock_client):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(is_running=False)
    await scanned_climate.async_set_fan_mode("low")
    mock_client.set_fan_speed.assert_not_awaited()
    assert scanned_climate.controller._off_pending[0]["fan_speed"] == FAN_LOW


async def test_set_hvac_mode_on_when_off_uses_bundled_write(scanned_climate, mock_client):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(
        is_running=False, current_mode=MODE_COOL,
        fan_speed=FAN_AUTO, setpoint=23.0, auto_swing=True, louver_position=0,
    )
    mock_client.read_device.side_effect = lambda idx: make_indoor_state(
        unit_index=idx, is_running=True, current_mode=MODE_HEAT,
        fan_speed=FAN_AUTO, setpoint=23.0, auto_swing=True, louver_position=0,
    )
    await scanned_climate.async_set_hvac_mode(HVACMode.HEAT)
    mock_client.write_control_block.assert_awaited_once()
    mock_client.set_mode.assert_not_awaited()


async def test_turn_on_when_off_with_missing_fields_blocks(scanned_climate, mock_client):
    """If state has no setpoint and no pending fills it, ON should not write."""
    scanned_climate.controller.indoor_states[0] = make_indoor_state(
        is_running=False, setpoint=None,
    )
    await scanned_climate.async_turn_on()
    mock_client.write_control_block.assert_not_awaited()


async def test_turn_on_when_already_on_no_op(scanned_climate, mock_client):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(is_running=True)
    await scanned_climate.async_turn_on()
    mock_client.write_control_block.assert_not_awaited()
    mock_client.turn_on.assert_not_awaited()


# ── Additional state/property tests ─────────────────────────────────────────


def test_hvac_mode_none_when_no_state(scanned_climate):
    scanned_climate.controller.indoor_states[0] = None
    assert scanned_climate.hvac_mode is None


def test_fan_mode_none_when_no_state(scanned_climate):
    scanned_climate.controller.indoor_states[0] = None
    assert scanned_climate.fan_mode is None


def test_swing_mode_none_when_no_state(scanned_climate):
    scanned_climate.controller.indoor_states[0] = None
    assert scanned_climate.swing_mode is None


def test_target_temperature_none_when_no_state(scanned_climate):
    scanned_climate.controller.indoor_states[0] = None
    assert scanned_climate.target_temperature is None


def test_current_temperature_none_when_no_state(scanned_climate):
    scanned_climate.controller.indoor_states[0] = None
    assert scanned_climate.current_temperature is None


def test_min_max_temp_use_cool_limits_in_cool_mode(scanned_climate):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(
        current_mode=MODE_COOL, cooling_lower_limit=18.0, cooling_upper_limit=29.0,
    )
    assert scanned_climate.min_temp == 18.0
    assert scanned_climate.max_temp == 29.0


def test_min_max_temp_use_heat_limits_in_heat_mode(scanned_climate):
    from pyacmodbus import MODE_HEAT
    scanned_climate.controller.indoor_states[0] = make_indoor_state(
        current_mode=MODE_HEAT, heating_lower_limit=16.0, heating_upper_limit=28.0,
    )
    assert scanned_climate.min_temp == 16.0
    assert scanned_climate.max_temp == 28.0


def test_min_max_temp_fallback_when_no_state(scanned_climate):
    scanned_climate.controller.indoor_states[0] = None
    assert scanned_climate.min_temp == scanned_climate._attr_min_temp
    assert scanned_climate.max_temp == scanned_climate._attr_max_temp


async def test_set_temperature_with_no_temp_no_op(scanned_climate, mock_client):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(is_running=True)
    await scanned_climate.async_set_temperature()  # no temperature kwarg
    mock_client.set_setpoint.assert_not_awaited()


async def test_set_fan_mode_invalid_no_op(scanned_climate, mock_client):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(is_running=True)
    await scanned_climate.async_set_fan_mode("nonexistent")
    mock_client.set_fan_speed.assert_not_awaited()


async def test_set_hvac_mode_invalid_no_op(scanned_climate, mock_client):
    """A mode not in MODE_BITS just returns silently."""
    from homeassistant.components.climate import HVACMode
    scanned_climate.controller.indoor_states[0] = make_indoor_state(is_running=True)
    # Force-pass an unmapped value by going around the typed signature.
    await scanned_climate.async_set_hvac_mode("nonexistent")  # type: ignore[arg-type]
    mock_client.set_mode.assert_not_awaited()


async def test_set_swing_mode_accumulates_when_off(scanned_climate, mock_client):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(is_running=False)
    await scanned_climate.async_set_swing_mode("on")
    mock_client.set_swing.assert_not_awaited()
    assert scanned_climate.controller._off_pending[0].get("auto_swing") is True


async def test_set_swing_mode_powered_on(scanned_climate, mock_client):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(is_running=True)
    mock_client.read_device.side_effect = lambda idx: make_indoor_state(
        unit_index=idx, is_running=True, auto_swing=True,
    )
    await scanned_climate.async_set_swing_mode("on")
    mock_client.set_swing.assert_awaited()


async def test_turn_off_when_off_no_op(scanned_climate, mock_client):
    scanned_climate.controller.indoor_states[0] = make_indoor_state(is_running=False)
    await scanned_climate.async_turn_off()
    mock_client.turn_off.assert_not_awaited()
