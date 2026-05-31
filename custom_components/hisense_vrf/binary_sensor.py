"""Binary sensor platform for the Hisense VRF integration."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyacmodbus import ACDeviceState, GatewayState

from . import HisenseVRFConfigEntry
from .const import signal_new_indoor
from .controller import HisenseVRFController
from .entity import HisenseVRFGatewayEntity, HisenseVRFIndoorEntity

# Single Modbus TCP socket per gateway → serialize service actions.
PARALLEL_UPDATES = 1
from .exp_descriptors import BIT_DESCRIPTORS
from .experimental import ExpBitBinarySensor


@dataclass(frozen=True, kw_only=True)
class IndoorBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[ACDeviceState], bool]


@dataclass(frozen=True, kw_only=True)
class GatewayBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[GatewayState], bool]


INDOOR_BINARY: tuple[IndoorBinaryDescription, ...] = (
    IndoorBinaryDescription(
        key="running",
        translation_key="running",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda d: d.is_running,
    ),
    IndoorBinaryDescription(
        key="alarm",
        translation_key="alarm",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: d.alarm_code != 0,
    ),
    IndoorBinaryDescription(
        key="filter_alarm",
        translation_key="filter_alarm",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: d.filter_alarm,
    ),
    IndoorBinaryDescription(
        key="swing_active",
        translation_key="swing_active",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.swing_active,
    ),
    IndoorBinaryDescription(
        key="oil_return",
        translation_key="oil_return",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.oil_return,
    ),
    IndoorBinaryDescription(
        key="test_run",
        translation_key="test_run",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.test_run,
    ),
    IndoorBinaryDescription(
        key="remote_control_active",
        translation_key="remote_control_active",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.remote_control_active,
    ),
)

GATEWAY_BINARY: tuple[GatewayBinaryDescription, ...] = (
    GatewayBinaryDescription(
        key="ctrl_mode",
        translation_key="gw_ctrl_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda g: g.ctrl_mode == 1,
    ),
    GatewayBinaryDescription(
        key="alarm_display",
        translation_key="gw_alarm_display",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda g: g.alarm_display != 0,
    ),
)


def _build_indoor_binaries(
    controller: HisenseVRFController, idx: int
) -> list[BinarySensorEntity]:
    entities: list[BinarySensorEntity] = [
        IndoorBinary(controller, idx, desc) for desc in INDOOR_BINARY
    ]
    entities.append(CommandSlotStuckBinary(controller, idx))
    for exp in BIT_DESCRIPTORS:
        entities.append(ExpBitBinarySensor(controller, idx, exp))
    return entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HisenseVRFConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    controller = entry.runtime_data

    entities: list[BinarySensorEntity] = []
    for idx in controller.unit_indices:
        entities.extend(_build_indoor_binaries(controller, idx))
    for desc in GATEWAY_BINARY:
        entities.append(GatewayBinary(controller, desc))

    async_add_entities(entities)

    @callback
    def _on_new_indoor(unit_index: int) -> None:
        async_add_entities(_build_indoor_binaries(controller, unit_index))

    entry.async_on_unload(
        async_dispatcher_connect(hass, signal_new_indoor(entry.entry_id), _on_new_indoor)
    )


class IndoorBinary(HisenseVRFIndoorEntity, BinarySensorEntity):
    entity_description: IndoorBinaryDescription

    def __init__(
        self,
        controller: HisenseVRFController,
        unit_index: int,
        description: IndoorBinaryDescription,
    ) -> None:
        super().__init__(controller, unit_index)
        self.entity_description = description
        self._attr_unique_id = f"{controller.entry_id}_indoor_{unit_index}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        state = self.unit_state
        if state is None:
            return None
        return self.entity_description.value_fn(state)


class CommandSlotStuckBinary(HisenseVRFIndoorEntity, BinarySensorEntity):
    """True if regs 78..82 are NOT [0xFF]×5 — a command is pending or stuck.

    Under normal operation the slot is cleared by the indoor unit promptly
    after each write. If it stays non-empty for more than a few seconds the
    indoor unit failed to consume the command (intermittent on-failure bug).
    """

    _attr_translation_key = "command_slot_stuck"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, controller: HisenseVRFController, unit_index: int) -> None:
        super().__init__(controller, unit_index)
        self._attr_unique_id = (
            f"{controller.entry_id}_indoor_{unit_index}_command_slot_stuck"
        )

    @property
    def available(self) -> bool:
        return self.controller.indoor_command_slots.get(self.unit_index) is not None

    @property
    def is_on(self) -> bool | None:
        slot = self.controller.indoor_command_slots.get(self.unit_index)
        if slot is None:
            return None
        return slot != [0xFF] * 5


class GatewayBinary(HisenseVRFGatewayEntity, BinarySensorEntity):
    entity_description: GatewayBinaryDescription

    def __init__(
        self,
        controller: HisenseVRFController,
        description: GatewayBinaryDescription,
    ) -> None:
        super().__init__(controller)
        self.entity_description = description
        self._attr_unique_id = f"{controller.entry_id}_gateway_{description.key}"

    @property
    def is_on(self) -> bool | None:
        if self.gateway_state is None:
            return None
        return self.entity_description.value_fn(self.gateway_state)
