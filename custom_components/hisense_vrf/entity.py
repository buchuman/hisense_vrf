"""Entity base classes for the Hisense VRF integration."""
from __future__ import annotations

from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from pyacmodbus import ACDeviceState, GatewayState, OutdoorUnitState

from .const import DOMAIN
from .controller import HisenseVRFController


class HisenseVRFBaseEntity(Entity):
    """Common behavior: subscribe to controller dispatcher signal."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, controller: HisenseVRFController) -> None:
        self.controller = controller

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.controller.signal, self._handle_controller_update
            )
        )

    @callback
    def _handle_controller_update(self) -> None:
        self.async_write_ha_state()


class HisenseVRFIndoorEntity(HisenseVRFBaseEntity):
    """Base for entities tied to an indoor unit (unit_index)."""

    def __init__(self, controller: HisenseVRFController, unit_index: int) -> None:
        super().__init__(controller)
        self.unit_index = unit_index

    @property
    def unit_state(self) -> ACDeviceState | None:
        """Hardware state (raw, without pending overlay)."""
        return self.controller.indoor_states.get(self.unit_index)

    @property
    def display_state(self) -> ACDeviceState | None:
        """State with pending values overlaid — what the UI should show."""
        return self.controller.get_display_state(self.unit_index)

    @property
    def available(self) -> bool:
        return self.unit_state is not None or self.controller.is_pending(self.unit_index)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.controller.entry_id}_indoor_{self.unit_index}")},
            name=self.controller.unit_name(self.unit_index),
            manufacturer="Hisense",
            model=self.controller.unit_model(self.unit_index),
            serial_number=str(self.controller.unit_register_base(self.unit_index)),
            via_device=(DOMAIN, f"{self.controller.entry_id}_gateway"),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        if self.controller.is_pending(self.unit_index):
            attrs["pending_write"] = True
            attrs["pending_fields"] = self.controller.pending_fields(self.unit_index)
        return attrs


class HisenseVRFGatewayEntity(HisenseVRFBaseEntity):
    """Base for gateway-level entities."""

    @property
    def gateway_state(self) -> GatewayState | None:
        return self.controller.gateway_state

    @property
    def available(self) -> bool:
        return self.gateway_state is not None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.controller.entry_id}_gateway")},
            name="Hisense VRF Gateway",
            manufacturer="Hisense",
            model="i-Modkit",
        )


class HisenseVRFOutdoorEntity(HisenseVRFBaseEntity):
    """Base for entities tied to an outdoor module."""

    def __init__(
        self, controller: HisenseVRFController, system_idx: int, module_idx: int
    ) -> None:
        super().__init__(controller)
        self.system_idx = system_idx
        self.module_idx = module_idx

    @property
    def outdoor_state(self) -> OutdoorUnitState | None:
        return self.controller.outdoor_states.get((self.system_idx, self.module_idx))

    @property
    def available(self) -> bool:
        return self.outdoor_state is not None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    f"{self.controller.entry_id}_outdoor_{self.system_idx}_{self.module_idx}",
                )
            },
            name=f"Outdoor Unit {self.system_idx}.{self.module_idx}",
            manufacturer="Hisense",
            model="VRF Outdoor",
            via_device=(DOMAIN, f"{self.controller.entry_id}_gateway"),
        )
