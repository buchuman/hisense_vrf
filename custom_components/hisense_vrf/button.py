"""Button platform for the Hisense VRF integration."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HisenseVRFConfigEntry
from .const import signal_new_indoor
from .controller import HisenseVRFController
from .entity import HisenseVRFGatewayEntity, HisenseVRFIndoorEntity

_LOGGER = logging.getLogger(__name__)

# Single Modbus TCP socket per gateway → serialize service actions.
PARALLEL_UPDATES = 1


def _build_indoor_buttons(
    controller: HisenseVRFController, idx: int
) -> list[ButtonEntity]:
    return [
        RefreshUnitButton(controller, idx),
        ResetFilterButton(controller, idx),
        LockAllButton(controller, idx),
        UnlockAllButton(controller, idx),
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HisenseVRFConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    controller = entry.runtime_data

    entities: list[ButtonEntity] = [
        RefreshAllButton(controller),
        EepromClearButton(controller),
        DiscoverDevicesButton(controller, hass, entry),
    ]
    for idx in controller.unit_indices:
        entities.extend(_build_indoor_buttons(controller, idx))

    async_add_entities(entities)

    @callback
    def _on_new_indoor(unit_index: int) -> None:
        async_add_entities(_build_indoor_buttons(controller, unit_index))

    entry.async_on_unload(
        async_dispatcher_connect(hass, signal_new_indoor(entry.entry_id), _on_new_indoor)
    )


# ── Gateway-level buttons ─────────────────────────────────────────────────────


class RefreshAllButton(HisenseVRFGatewayEntity, ButtonEntity):
    """Read every indoor unit, the gateway, and every outdoor module on demand."""

    _attr_translation_key = "refresh_all"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, controller: HisenseVRFController) -> None:
        super().__init__(controller)
        self._attr_unique_id = f"{controller.entry_id}_refresh_all"

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        await self.controller.async_refresh_all(context=self._context)


class EepromClearButton(HisenseVRFGatewayEntity, ButtonEntity):
    _attr_translation_key = "eeprom_clear"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, controller: HisenseVRFController) -> None:
        super().__init__(controller)
        self._attr_unique_id = f"{controller.entry_id}_eeprom_clear"

    async def async_press(self) -> None:
        await self.controller.client.clear_eeprom()
        try:
            self.controller.gateway_state = await self.controller.client.read_gateway()
        finally:
            self.controller._notify()  # noqa: SLF001


class DiscoverDevicesButton(HisenseVRFGatewayEntity, ButtonEntity):
    """Re-run device discovery (re-scan indoor + outdoor)."""

    _attr_translation_key = "discover_devices"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        controller: HisenseVRFController,
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(controller)
        self._hass = hass
        self._entry = entry
        self._attr_unique_id = f"{controller.entry_id}_discover_devices"

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        await self._hass.config_entries.async_reload(self._entry.entry_id)


# ── Indoor-unit buttons ───────────────────────────────────────────────────────


class RefreshUnitButton(HisenseVRFIndoorEntity, ButtonEntity):
    _attr_translation_key = "refresh_unit"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, controller: HisenseVRFController, unit_index: int) -> None:
        super().__init__(controller, unit_index)
        self._attr_unique_id = f"{controller.entry_id}_indoor_{unit_index}_refresh"

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        await self.controller.async_refresh_unit(self.unit_index, context=self._context)


class ResetFilterButton(HisenseVRFIndoorEntity, ButtonEntity):
    _attr_translation_key = "reset_filter"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, controller: HisenseVRFController, unit_index: int) -> None:
        super().__init__(controller, unit_index)
        self._attr_unique_id = f"{controller.entry_id}_indoor_{unit_index}_reset_filter"

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        await self.controller.client.reset_filter(self.unit_index)
        await self.controller.async_refresh_unit(self.unit_index, context=self._context)


class LockAllButton(HisenseVRFIndoorEntity, ButtonEntity):
    _attr_translation_key = "lock_all"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, controller: HisenseVRFController, unit_index: int) -> None:
        super().__init__(controller, unit_index)
        self._attr_unique_id = f"{controller.entry_id}_indoor_{unit_index}_lock_all"

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        await self.controller.client.lock_all(self.unit_index)
        await self.controller.async_refresh_unit(self.unit_index, context=self._context)


class UnlockAllButton(HisenseVRFIndoorEntity, ButtonEntity):
    _attr_translation_key = "unlock_all"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, controller: HisenseVRFController, unit_index: int) -> None:
        super().__init__(controller, unit_index)
        self._attr_unique_id = f"{controller.entry_id}_indoor_{unit_index}_unlock_all"

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        await self.controller.client.unlock_all(self.unit_index)
        await self.controller.async_refresh_unit(self.unit_index, context=self._context)
