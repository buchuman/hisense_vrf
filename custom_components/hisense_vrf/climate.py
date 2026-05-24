"""Climate platform for the Hisense VRF integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyacmodbus import (
    ACDeviceState,
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MED,
    MODE_AUTO,
    MODE_COOL,
    MODE_DRY,
    MODE_FAN,
    MODE_HEAT,
)

from . import HisenseVRFConfigEntry
from .const import signal_new_indoor
from .controller import HisenseVRFController
from .entity import HisenseVRFIndoorEntity

_LOGGER = logging.getLogger(__name__)

# Single Modbus TCP socket per gateway → serialize service actions.
PARALLEL_UPDATES = 1

MODE_BITS = {
    HVACMode.HEAT_COOL: MODE_AUTO,
    HVACMode.COOL: MODE_COOL,
    HVACMode.DRY: MODE_DRY,
    HVACMode.FAN_ONLY: MODE_FAN,
    HVACMode.HEAT: MODE_HEAT,
}
BITS_TO_HVAC = {v: k for k, v in MODE_BITS.items()}

FAN_BITS = {
    "auto": FAN_AUTO,
    "high": FAN_HIGH,
    "medium": FAN_MED,
    "low": FAN_LOW,
}
BITS_TO_FAN = {v: k for k, v in FAN_BITS.items()}

SWING_OFF = "off"
SWING_ON = "on"

# Function selection bits used as dynamic capabilities. Each entry is the
# (reg_offset_within_function_selection, bit) pair in the unit's function
# selection table (regs 40048..40067 → reg_offset 0..19).
CAP_C1_COOL_ONLY = (0, 6)
CAP_B5_FIX_MODE = (1, 4)
CAP_B6_FIX_TEMP = (2, 5)
CAP_B7_FIX_COOLING = (2, 3)
CAP_B8_AUTO = (2, 2)
CAP_B9_FIX_FAN = (2, 7)
# C5 is multi-bit: bits 3-4 of reg_offset 4 → 0=standard, 1=hi_1, 2=hi_2
CAP_C5_HI_SPEED_OFFSET = 4
CAP_C5_HI_SPEED_SHIFT = 3
CAP_C5_HI_SPEED_MASK = 0b11

BASE_HVAC_MODES: list[HVACMode] = [
    HVACMode.OFF,
    HVACMode.COOL,
    HVACMode.DRY,
    HVACMode.FAN_ONLY,
    HVACMode.HEAT,
]
BASE_FAN_MODES: list[str] = ["auto", "high", "medium", "low"]
BASE_FEATURES = (
    ClimateEntityFeature.TARGET_TEMPERATURE
    | ClimateEntityFeature.FAN_MODE
    | ClimateEntityFeature.SWING_MODE
    | ClimateEntityFeature.TURN_ON
    | ClimateEntityFeature.TURN_OFF
)


def _bit(fs: tuple[int, ...], offset: int, bit: int) -> bool:
    if offset >= len(fs):
        return False
    return bool((fs[offset] >> bit) & 0x01)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HisenseVRFConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    controller = entry.runtime_data
    async_add_entities(
        HisenseVRFClimate(controller, idx) for idx in controller.unit_indices
    )

    @callback
    def _on_new_indoor(unit_index: int) -> None:
        async_add_entities([HisenseVRFClimate(controller, unit_index)])

    entry.async_on_unload(
        async_dispatcher_connect(hass, signal_new_indoor(entry.entry_id), _on_new_indoor)
    )


class HisenseVRFClimate(HisenseVRFIndoorEntity, ClimateEntity):
    """One Hisense indoor unit exposed as a HA climate entity."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1.0
    _attr_min_temp = 17
    _attr_max_temp = 30
    _attr_swing_modes = [SWING_OFF, SWING_ON]
    _attr_translation_key = "ac_unit"
    _attr_name = None

    def __init__(self, controller: HisenseVRFController, unit_index: int) -> None:
        super().__init__(controller, unit_index)
        self._attr_unique_id = f"{controller.entry_id}_indoor_{unit_index}_climate"

    # ── Dynamic capabilities (derived from function_selection) ──────────────

    def _fs(self) -> tuple[int, ...]:
        state = self.unit_state
        if state is None:
            return ()
        return state.function_selection or ()

    @property
    def hvac_modes(self) -> list[HVACMode]:
        fs = self._fs()
        if not fs:
            # No function selection read yet → conservative default.
            return list(BASE_HVAC_MODES)

        # B5 Fixing of operating mode: only OFF + current mode allowed.
        if _bit(fs, *CAP_B5_FIX_MODE):
            modes: list[HVACMode] = [HVACMode.OFF]
            state = self.unit_state
            current = BITS_TO_HVAC.get(state.current_mode) if state else None
            if current is not None and current != HVACMode.OFF:
                modes.append(current)
            return modes

        modes = list(BASE_HVAC_MODES)
        # B7 Fixing of cooling: only COOL is allowed beyond OFF.
        if _bit(fs, *CAP_B7_FIX_COOLING):
            return [HVACMode.OFF, HVACMode.COOL]
        # C1 Cool only: drop HEAT.
        if _bit(fs, *CAP_C1_COOL_ONLY) and HVACMode.HEAT in modes:
            modes.remove(HVACMode.HEAT)
        # B8 Auto cool/heat: enable HEAT_COOL.
        if _bit(fs, *CAP_B8_AUTO):
            # Insert AUTO right after OFF for natural ordering.
            modes.insert(1, HVACMode.HEAT_COOL)
        return modes

    @property
    def fan_modes(self) -> list[str]:
        fs = self._fs()
        if not fs:
            return list(BASE_FAN_MODES)
        # B9 Fixing of fan speed: only current mode allowed.
        if _bit(fs, *CAP_B9_FIX_FAN):
            state = self.unit_state
            current = BITS_TO_FAN.get(state.fan_speed) if state else None
            return [current] if current else list(BASE_FAN_MODES)
        modes = list(BASE_FAN_MODES)
        # C5 Indoor fan Hi speed: enables additional Hi 1 / Hi 2.
        hi = (fs[CAP_C5_HI_SPEED_OFFSET] >> CAP_C5_HI_SPEED_SHIFT) & CAP_C5_HI_SPEED_MASK
        if hi == 1:
            modes.append("high_high_1")
        elif hi == 2:
            modes.append("high_high_2")
        return modes

    @property
    def supported_features(self) -> ClimateEntityFeature:
        fs = self._fs()
        features = BASE_FEATURES
        if not fs:
            return features
        if _bit(fs, *CAP_B6_FIX_TEMP):
            features &= ~ClimateEntityFeature.TARGET_TEMPERATURE
        if _bit(fs, *CAP_B9_FIX_FAN):
            features &= ~ClimateEntityFeature.FAN_MODE
        return features

    # ── State view ───────────────────────────────────────────────────────────

    @property
    def assumed_state(self) -> bool:
        return self.controller.is_pending(self.unit_index)

    @property
    def current_temperature(self) -> float | None:
        state = self.unit_state
        return state.inlet_temp if state else None

    @property
    def target_temperature(self) -> float | None:
        state = self.display_state
        return state.setpoint if state else None

    @property
    def hvac_mode(self) -> HVACMode | None:
        state = self.display_state
        if state is None:
            return None
        if not state.is_running:
            return HVACMode.OFF
        return BITS_TO_HVAC.get(state.current_mode)

    @property
    def fan_mode(self) -> str | None:
        state = self.display_state
        if state is None:
            return None
        return BITS_TO_FAN.get(state.fan_speed)

    @property
    def swing_mode(self) -> str | None:
        state = self.display_state
        if state is None:
            return None
        return SWING_ON if state.auto_swing else SWING_OFF

    @property
    def min_temp(self) -> float:
        state = self.unit_state
        if state is None:
            return self._attr_min_temp
        if state.current_mode == MODE_COOL:
            return state.cooling_lower_limit
        if state.current_mode == MODE_HEAT:
            return state.heating_lower_limit
        return self._attr_min_temp

    @property
    def max_temp(self) -> float:
        state = self.unit_state
        if state is None:
            return self._attr_max_temp
        if state.current_mode == MODE_COOL:
            return state.cooling_upper_limit
        if state.current_mode == MODE_HEAT:
            return state.heating_upper_limit
        return self._attr_max_temp

    # ── Commands ─────────────────────────────────────────────────────────────

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        target = float(temp)
        if self.controller.is_powered_on(self.unit_index):
            await self.controller.async_write_and_verify(
                self.unit_index,
                pending_attrs={"setpoint": target},
                verify_fn=lambda s: s.setpoint is not None and abs(s.setpoint - target) < 0.5,
                write_fn=lambda: self.controller.client.set_setpoint(self.unit_index, target),
                context=self._context,
            )
        else:
            user = await self.controller._resolve_user(self._context)  # noqa: SLF001
            self.controller.accumulate_off_pending(
                self.unit_index, {"setpoint": target}, user=user
            )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        bits = FAN_BITS.get(fan_mode)
        if bits is None:
            return
        if self.controller.is_powered_on(self.unit_index):
            await self.controller.async_write_and_verify(
                self.unit_index,
                pending_attrs={"fan_speed": bits},
                verify_fn=lambda s: s.fan_speed == bits,
                write_fn=lambda: self.controller.client.set_fan_speed(self.unit_index, bits),
                context=self._context,
            )
        else:
            user = await self.controller._resolve_user(self._context)  # noqa: SLF001
            self.controller.accumulate_off_pending(
                self.unit_index, {"fan_speed": bits}, user=user
            )

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        auto = swing_mode == SWING_ON
        if self.controller.is_powered_on(self.unit_index):
            await self.controller.async_write_and_verify(
                self.unit_index,
                pending_attrs={"auto_swing": auto},
                verify_fn=lambda s: s.auto_swing == auto,
                write_fn=lambda: self.controller.client.set_swing(self.unit_index, auto, 0),
                context=self._context,
            )
        else:
            user = await self.controller._resolve_user(self._context)  # noqa: SLF001
            self.controller.accumulate_off_pending(
                self.unit_index,
                {"auto_swing": auto, "louver_position": 0},
                user=user,
            )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            if not self.controller.is_powered_on(self.unit_index):
                # Already off — nothing to do, don't send a Modbus write.
                return
            await self.controller.async_write_and_verify(
                self.unit_index,
                pending_attrs={"is_running": False},
                verify_fn=lambda s: not s.is_running,
                write_fn=lambda: self.controller.client.turn_off(self.unit_index),
                context=self._context,
            )
            return

        mode_bits = MODE_BITS.get(hvac_mode)
        if mode_bits is None:
            return

        if self.controller.is_powered_on(self.unit_index):
            # Unit already running: only the mode register needs writing.
            await self.controller.async_write_and_verify(
                self.unit_index,
                pending_attrs={"current_mode": mode_bits},
                verify_fn=lambda s: _mode_matches(s, mode_bits),
                write_fn=lambda: self.controller.client.set_mode(self.unit_index, mode_bits),
                context=self._context,
            )
            return

        # Unit off → this is an ON event. Flush off_pending as a bundled write.
        await self.controller.async_send_on_with_pending(
            self.unit_index, mode_override=mode_bits, context=self._context
        )

    async def async_turn_on(self) -> None:
        if self.controller.is_powered_on(self.unit_index):
            return
        await self.controller.async_send_on_with_pending(
            self.unit_index, context=self._context
        )

    async def async_turn_off(self) -> None:
        if not self.controller.is_powered_on(self.unit_index):
            return
        await self.controller.async_write_and_verify(
            self.unit_index,
            pending_attrs={"is_running": False},
            verify_fn=lambda s: not s.is_running,
            write_fn=lambda: self.controller.client.turn_off(self.unit_index),
            context=self._context,
        )


def _mode_matches(state: ACDeviceState, expected_bits: int) -> bool:
    """Accept either current_mode (set value) or mode_jump (actually running)."""
    return state.current_mode == expected_bits or state.mode_jump == expected_bits
