"""Experimental diagnostic entities — function selection bits.

Exposes one entity per documented bit of the 20 function-selection registers
(40048..40067). Read-only, derived from the polling-refreshed state, no Modbus
writes are performed here. All entities are marked ``entity_category=diagnostic``
and named with the ``EXP`` prefix so they are easy to spot.

Descriptors live in ``exp_descriptors.py`` (auto-generated from the mapping
table spreadsheet).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory

from .controller import HisenseVRFController
from .entity import HisenseVRFIndoorEntity


@dataclass(frozen=True, kw_only=True)
class ExpBitDescriptor:
    """Single-bit experimental descriptor.

    ``reg_offset`` is the index inside the 20-element ``function_selection``
    tuple (0 → register 40048, 19 → register 40067). ``bit`` is 0..7.
    ``disabled_default=True`` (the default) hides EXP entities from the
    registry until the user explicitly enables them. EXP entities are
    diagnostic-only, rarely change, and add ~100 entities per indoor unit —
    persisting their history bloats the recorder DB and slows HA startup.
    """

    key: str
    name: str
    reg_offset: int
    bit: int
    disabled_default: bool = True


@dataclass(frozen=True, kw_only=True)
class ExpEnumDescriptor:
    """Multi-bit enum descriptor (e.g. filter cleaning time).

    ``disabled_default=True`` mirrors ``ExpBitDescriptor`` — EXP entities are
    hidden until the user opts in.
    """

    key: str
    name: str
    reg_offset: int
    shift: int
    mask: int
    options: Sequence[str]
    disabled_default: bool = True


class ExpBitBinarySensor(HisenseVRFIndoorEntity, BinarySensorEntity):
    """Read-only binary sensor reflecting one bit of a function-selection register."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        controller: HisenseVRFController,
        unit_index: int,
        descriptor: ExpBitDescriptor,
    ) -> None:
        super().__init__(controller, unit_index)
        self._desc = descriptor
        self._attr_translation_key = descriptor.key
        self._attr_unique_id = (
            f"{controller.entry_id}_indoor_{unit_index}_exp_{descriptor.key}"
        )
        self._attr_entity_registry_enabled_default = not descriptor.disabled_default
        self.entity_description = BinarySensorEntityDescription(
            key=descriptor.key,
            translation_key=descriptor.key,
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=not descriptor.disabled_default,
        )

    @property
    def is_on(self) -> bool | None:
        state = self.unit_state
        if state is None or not state.function_selection:
            return None
        if self._desc.reg_offset >= len(state.function_selection):
            return None
        reg = state.function_selection[self._desc.reg_offset]
        return bool((reg >> self._desc.bit) & 0x01)


class ExpEnumSensor(HisenseVRFIndoorEntity, SensorEntity):
    """Read-only multi-bit enum sensor (function-selection group of bits)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(
        self,
        controller: HisenseVRFController,
        unit_index: int,
        descriptor: ExpEnumDescriptor,
    ) -> None:
        super().__init__(controller, unit_index)
        self._desc = descriptor
        self._attr_translation_key = descriptor.key
        self._attr_unique_id = (
            f"{controller.entry_id}_indoor_{unit_index}_exp_{descriptor.key}"
        )
        self._attr_entity_registry_enabled_default = not descriptor.disabled_default
        # Append 'unknown_<n>' overflow option so HA's enum validation accepts
        # out-of-range raw values without throwing.
        self._attr_options = list(descriptor.options) + [
            f"unknown_{i}" for i in range(len(descriptor.options), descriptor.mask + 1)
        ]
        self.entity_description = SensorEntityDescription(
            key=descriptor.key,
            translation_key=descriptor.key,
            entity_registry_enabled_default=not descriptor.disabled_default,
            entity_category=EntityCategory.DIAGNOSTIC,
            device_class=SensorDeviceClass.ENUM,
        )

    @property
    def native_value(self) -> str | None:
        state = self.unit_state
        if state is None or not state.function_selection:
            return None
        if self._desc.reg_offset >= len(state.function_selection):
            return None
        reg = state.function_selection[self._desc.reg_offset]
        raw = (reg >> self._desc.shift) & self._desc.mask
        if raw < len(self._desc.options):
            return self._desc.options[raw]
        return f"unknown_{raw}"
