"""Select platform for the Hisense VRF integration."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HisenseVRFConfigEntry
from .const import signal_new_indoor
from .controller import HisenseVRFController
from .entity import HisenseVRFIndoorEntity

# Single Modbus TCP socket per gateway → serialize service actions.
PARALLEL_UPDATES = 1

DRY_MODES = {"dry1": 0, "dry2": 1, "dry3": 2}
DRY_VALUE_TO_OPTION = {v: k for k, v in DRY_MODES.items()}

LOUVER_AUTO = "auto"
LOUVER_OPTIONS = [LOUVER_AUTO] + [str(i) for i in range(0, 8)]


def _build_indoor_selects(
    controller: HisenseVRFController, idx: int
) -> list[SelectEntity]:
    return [LouverSelect(controller, idx), DryModeSelect(controller, idx)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HisenseVRFConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    controller = entry.runtime_data
    entities: list[SelectEntity] = []
    for idx in controller.unit_indices:
        entities.extend(_build_indoor_selects(controller, idx))
    async_add_entities(entities)

    @callback
    def _on_new_indoor(unit_index: int) -> None:
        async_add_entities(_build_indoor_selects(controller, unit_index))

    entry.async_on_unload(
        async_dispatcher_connect(hass, signal_new_indoor(entry.entry_id), _on_new_indoor)
    )


class LouverSelect(HisenseVRFIndoorEntity, SelectEntity):
    """Combined auto/position selector for the louver."""

    _attr_translation_key = "louver"
    _attr_options = LOUVER_OPTIONS

    def __init__(self, controller: HisenseVRFController, unit_index: int) -> None:
        super().__init__(controller, unit_index)
        self._attr_unique_id = f"{controller.entry_id}_indoor_{unit_index}_louver"

    @property
    def assumed_state(self) -> bool:
        return (
            self.controller.is_field_pending(self.unit_index, "auto_swing")
            or self.controller.is_field_pending(self.unit_index, "louver_position")
        )

    @property
    def current_option(self) -> str | None:
        state = self.display_state
        if state is None:
            return None
        if state.auto_swing:
            return LOUVER_AUTO
        pos = state.louver_position
        if 0 <= pos <= 7:
            return str(pos)
        return None

    async def async_select_option(self, option: str) -> None:
        if option == LOUVER_AUTO:
            pending = {"auto_swing": True, "louver_position": 0}
            await self.controller.async_write_and_verify(
                self.unit_index,
                pending_attrs=pending,
                verify_fn=lambda s: s.auto_swing,
                write_fn=lambda: self.controller.client.set_swing(self.unit_index, True, 0),
            context=self._context,
            )
            return
        try:
            position = int(option)
        except ValueError:
            return
        if not 0 <= position <= 7:
            return
        pending = {"auto_swing": False, "louver_position": position}
        await self.controller.async_write_and_verify(
            self.unit_index,
            pending_attrs=pending,
            verify_fn=lambda s: not s.auto_swing and s.louver_position == position,
            write_fn=lambda: self.controller.client.set_swing(self.unit_index, False, position),
            context=self._context,
        )


class DryModeSelect(HisenseVRFIndoorEntity, SelectEntity):
    """Dehumidification mode (Dry1 / Dry2 / Dry3)."""

    _attr_translation_key = "dry_mode_select"
    _attr_options = list(DRY_MODES.keys())
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, controller: HisenseVRFController, unit_index: int) -> None:
        super().__init__(controller, unit_index)
        self._attr_unique_id = f"{controller.entry_id}_indoor_{unit_index}_dry_mode"

    @property
    def assumed_state(self) -> bool:
        return self.controller.is_field_pending(self.unit_index, "dry_mode")

    @property
    def current_option(self) -> str | None:
        state = self.display_state
        if state is None:
            return None
        return DRY_VALUE_TO_OPTION.get(state.dry_mode)

    async def async_select_option(self, option: str) -> None:
        value = DRY_MODES.get(option)
        if value is None:
            return
        await self.controller.async_write_and_verify(
            self.unit_index,
            pending_attrs={"dry_mode": value},
            verify_fn=lambda s: s.dry_mode == value,
            write_fn=lambda: self.controller.client.set_dry_mode(self.unit_index, value),
            context=self._context,
        )
