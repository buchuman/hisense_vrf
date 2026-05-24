"""Switch platform for the Hisense VRF integration."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyacmodbus import (
    ACDeviceState,
    REG_PROHIBIT_FAN,
    REG_PROHIBIT_MODE,
    REG_PROHIBIT_SW,
    REG_PROHIBIT_SWING,
    REG_PROHIBIT_TEMP,
)

from . import HisenseVRFConfigEntry
from .const import signal_new_indoor
from .controller import HisenseVRFController
from .entity import HisenseVRFGatewayEntity, HisenseVRFIndoorEntity

# Single Modbus TCP socket per gateway → serialize service actions.
PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class ProhibitDescription(SwitchEntityDescription):
    state_attr: str
    register: int


PROHIBIT_SWITCHES: tuple[ProhibitDescription, ...] = (
    ProhibitDescription(
        key="prohibit_on_off",
        translation_key="prohibit_on_off",
        entity_category=EntityCategory.CONFIG,
        state_attr="prohibit_on_off",
        register=REG_PROHIBIT_SW,
    ),
    ProhibitDescription(
        key="prohibit_mode",
        translation_key="prohibit_mode",
        entity_category=EntityCategory.CONFIG,
        state_attr="prohibit_mode",
        register=REG_PROHIBIT_MODE,
    ),
    ProhibitDescription(
        key="prohibit_fan",
        translation_key="prohibit_fan",
        entity_category=EntityCategory.CONFIG,
        state_attr="prohibit_fan",
        register=REG_PROHIBIT_FAN,
    ),
    ProhibitDescription(
        key="prohibit_swing",
        translation_key="prohibit_swing",
        entity_category=EntityCategory.CONFIG,
        state_attr="prohibit_swing",
        register=REG_PROHIBIT_SWING,
    ),
    ProhibitDescription(
        key="prohibit_temp",
        translation_key="prohibit_temp",
        entity_category=EntityCategory.CONFIG,
        state_attr="prohibit_temp",
        register=REG_PROHIBIT_TEMP,
    ),
)


def _build_indoor_switches(
    controller: HisenseVRFController, idx: int
) -> list[SwitchEntity]:
    entities: list[SwitchEntity] = [PowerSwitch(controller, idx)]
    for desc in PROHIBIT_SWITCHES:
        entities.append(ProhibitSwitch(controller, idx, desc))
    return entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HisenseVRFConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    controller = entry.runtime_data

    entities: list[SwitchEntity] = []
    for idx in controller.unit_indices:
        entities.extend(_build_indoor_switches(controller, idx))
    entities.append(GatewayAlarmDisplaySwitch(controller))

    async_add_entities(entities)

    @callback
    def _on_new_indoor(unit_index: int) -> None:
        async_add_entities(_build_indoor_switches(controller, unit_index))

    entry.async_on_unload(
        async_dispatcher_connect(hass, signal_new_indoor(entry.entry_id), _on_new_indoor)
    )


class PowerSwitch(HisenseVRFIndoorEntity, SwitchEntity):
    """Direct on/off switch (mirrors climate.turn_on/off)."""

    _attr_translation_key = "power"
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(self, controller: HisenseVRFController, unit_index: int) -> None:
        super().__init__(controller, unit_index)
        self._attr_unique_id = f"{controller.entry_id}_indoor_{unit_index}_power"

    @property
    def assumed_state(self) -> bool:
        return self.controller.is_field_pending(self.unit_index, "is_running")

    @property
    def is_on(self) -> bool | None:
        state = self.display_state
        return state.is_running if state else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.controller.is_powered_on(self.unit_index):
            return
        await self.controller.async_send_on_with_pending(
            self.unit_index, context=self._context
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        if not self.controller.is_powered_on(self.unit_index):
            return
        await self.controller.async_write_and_verify(
            self.unit_index,
            pending_attrs={"is_running": False},
            verify_fn=lambda s: not s.is_running,
            write_fn=lambda: self.controller.client.turn_off(self.unit_index),
            context=self._context,
        )


class ProhibitSwitch(HisenseVRFIndoorEntity, SwitchEntity):
    entity_description: ProhibitDescription

    def __init__(
        self,
        controller: HisenseVRFController,
        unit_index: int,
        description: ProhibitDescription,
    ) -> None:
        super().__init__(controller, unit_index)
        self.entity_description = description
        self._attr_unique_id = f"{controller.entry_id}_indoor_{unit_index}_{description.key}"

    @property
    def assumed_state(self) -> bool:
        return self.controller.is_field_pending(self.unit_index, self.entity_description.state_attr)

    @property
    def is_on(self) -> bool | None:
        state = self.display_state
        return getattr(state, self.entity_description.state_attr, None) if state else None

    async def _async_set(self, value: bool) -> None:
        attr = self.entity_description.state_attr
        register = self.entity_description.register
        await self.controller.async_write_and_verify(
            self.unit_index,
            pending_attrs={attr: value},
            verify_fn=lambda s: getattr(s, attr) == value,
            write_fn=lambda: self.controller.client.set_prohibition(
                self.unit_index, register, value
            ),
            context=self._context,
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)


class GatewayAlarmDisplaySwitch(HisenseVRFGatewayEntity, SwitchEntity):
    """Toggle the gateway alarm display digit tube."""

    _attr_translation_key = "gw_alarm_display"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, controller: HisenseVRFController) -> None:
        super().__init__(controller)
        self._attr_unique_id = f"{controller.entry_id}_gateway_alarm_display"

    @property
    def is_on(self) -> bool | None:
        if self.gateway_state is None:
            return None
        return bool(self.gateway_state.alarm_display)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.controller.client.set_alarm_display(True)
        # Refresh gateway to reflect the new value.
        try:
            self.controller.gateway_state = await self.controller.client.read_gateway()
        finally:
            self.controller._notify()  # noqa: SLF001

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.controller.client.set_alarm_display(False)
        try:
            self.controller.gateway_state = await self.controller.client.read_gateway()
        finally:
            self.controller._notify()  # noqa: SLF001
