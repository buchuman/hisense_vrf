"""Sensor platform for the Hisense VRF integration."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfFrequency,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from pyacmodbus import (
    BASE_ADDR,
    UNIT_STRIDE,
    ACDeviceState,
    OutdoorUnitState,
    fan_actual_name,
)

from . import HisenseVRFConfigEntry
from .const import signal_new_indoor, signal_new_outdoor
from .controller import HisenseVRFController
from .entity import (
    HisenseVRFGatewayEntity,
    HisenseVRFIndoorEntity,
    HisenseVRFOutdoorEntity,
)

# Single Modbus TCP socket per gateway → serialize service actions.
PARALLEL_UPDATES = 1
from .exp_descriptors import ENUM_DESCRIPTORS
from .experimental import ExpEnumSensor

ALARM_CODES: dict[int, str] = {
    0x00: "No alarm",
    0x01: "Device protection: float switch",
    0x02: "Device protection: high pressure cut",
    0x03: "Abnormality between indoor and outdoor",
    0x04: "Inverter PCB / outdoor PCB transmission failure",
    0x05: "Abnormal power supply phases",
    0x06: "Abnormal voltage",
    0x07: "Decrease in discharge gas superheat",
    0x08: "Increase in discharge gas temperature",
    0x0A: "Transmission error between indoor and outdoor",
    0x0B: "Wrong outdoor unit address setting",
    0x0C: "Wrong outdoor unit main unit setting",
    0x11: "Inlet air/water thermistor fault",
    0x12: "Outlet air/water thermistor fault",
    0x13: "Freeze protection thermistor fault",
    0x14: "Gas piping thermistor fault",
    0x16: "Remote control thermistor fault",
    0x17: "Wire controller thermistor fault",
    0x19: "Indoor fan protection activated",
    0x21: "High pressure sensor fault",
    0x22: "Outdoor air thermistor fault",
    0x23: "Discharge gas thermistor fault",
    0x24: "Heat exchanger liquid pipe thermistor fault",
    0x25: "Heat exchanger gas pipe thermistor fault",
    0x29: "Low pressure sensor fault",
    0x31: "Incorrect capacity setting (outdoor/indoor)",
    0x35: "Abnormal transmitting between indoor units",
    0x36: "Incorrect indoor unit number setting",
    0x38: "Abnormality of protection circuit in outdoor unit",
    0x39: "Abnormal running current (constant speed compressor)",
    0x3A: "Abnormality of outdoor unit capacity",
    0x3B: "Incorrect outdoor unit models combination or voltage",
    0x3D: "Abnormality transmission between main and sub units",
    0x43: "Low compression ratio protection activated",
    0x44: "Low pressure increase protection activated",
    0x45: "High pressure increase protection activated",
    0x47: "Low pressure decrease protection (vacuum)",
    0x48: "Inverter overcurrent protection activated",
    0x51: "Abnormal inverter current sensor",
    0x53: "Inverter error signal detection",
    0x54: "Abnormality of inverter fin temperature",
    0x55: "Inverter failure",
    0x57: "Fan controller protection activated",
    0x5A: "Fan controller fin temperature abnormality",
    0x5B: "Fan controller overcurrent protection",
    0x5C: "Fan controller sensor abnormality",
    0x60: "Gateway: abnormal outdoor unit communication (units running)",
    0x61: "Gateway: abnormal indoor unit communication (units running)",
    0x64: "Gateway: abnormal outdoor unit communication (units stopped)",
    0x65: "Gateway: abnormal indoor unit communication (units stopped)",
    0xB1: "Incorrect unit and refrigerant cycle number setting",
    0xB5: "Incorrect indoor unit connection number setting",
    0xC1: "Incorrect indoor unit connection (switch box)",
    0xC2: "Incorrect indoor unit connection number (switch box)",
    0xC3: "Incorrect indoor unit connection (refrigerant cycle)",
    0xEE: "Compressor protection alarm (cannot reset from remote)",
}


@dataclass(frozen=True, kw_only=True)
class IndoorSensorDescription(SensorEntityDescription):
    value_fn: Callable[[ACDeviceState], StateType]


@dataclass(frozen=True, kw_only=True)
class OutdoorSensorDescription(SensorEntityDescription):
    value_fn: Callable[[OutdoorUnitState], StateType]


# dict[str, Any] so it can be **-unpacked into SensorEntityDescription
# constructors with mixed-type fields under mypy strict.
_TEMP: dict[str, Any] = {
    "device_class": SensorDeviceClass.TEMPERATURE,
    "state_class": SensorStateClass.MEASUREMENT,
    "native_unit_of_measurement": UnitOfTemperature.CELSIUS,
}

INDOOR_SENSORS: tuple[IndoorSensorDescription, ...] = (
    IndoorSensorDescription(key="inlet_temp", translation_key="inlet_temp", value_fn=lambda d: d.inlet_temp, **_TEMP),
    IndoorSensorDescription(key="outlet_temp", translation_key="outlet_temp", value_fn=lambda d: d.outlet_temp, **_TEMP),
    IndoorSensorDescription(key="setpoint", translation_key="setpoint", value_fn=lambda d: d.setpoint, **_TEMP),
    IndoorSensorDescription(key="temp_tg", translation_key="temp_tg", value_fn=lambda d: d.temp_tg, **_TEMP),
    IndoorSensorDescription(key="temp_tl", translation_key="temp_tl", value_fn=lambda d: d.temp_tl, **_TEMP),
    IndoorSensorDescription(key="temp_remote", translation_key="temp_remote",
                             value_fn=lambda d: d.temp_remote if d.temp_remote != 0.0 else None, **_TEMP),
    IndoorSensorDescription(key="cooling_upper_limit", translation_key="cooling_upper_limit",
                             value_fn=lambda d: d.cooling_upper_limit, **_TEMP),
    IndoorSensorDescription(key="cooling_lower_limit", translation_key="cooling_lower_limit",
                             value_fn=lambda d: d.cooling_lower_limit, **_TEMP),
    IndoorSensorDescription(key="heating_upper_limit", translation_key="heating_upper_limit",
                             value_fn=lambda d: d.heating_upper_limit, **_TEMP),
    IndoorSensorDescription(key="heating_lower_limit", translation_key="heating_lower_limit",
                             value_fn=lambda d: d.heating_lower_limit, **_TEMP),
    IndoorSensorDescription(
        key="expansion_valve_pct", translation_key="expansion_valve_pct",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda d: d.expansion_valve_pct,
    ),
    IndoorSensorDescription(
        key="required_frequency", translation_key="required_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        value_fn=lambda d: d.required_frequency,
    ),
    IndoorSensorDescription(
        key="fan_actual", translation_key="fan_actual",
        device_class=SensorDeviceClass.ENUM,
        options=["high_high_2", "high", "medium", "medium_low", "low", "mute", "breeze"],
        value_fn=lambda d: (
            fan_actual_name(d.fan_actual)
            if d.fan_actual and not fan_actual_name(d.fan_actual).startswith("unknown")
            else None
        ),
    ),
    IndoorSensorDescription(
        key="op_state", translation_key="op_state",
        device_class=SensorDeviceClass.ENUM,
        options=["stop", "th_off", "th_on", "alarm"],
        value_fn=lambda d: ("stop", "th_off", "th_on", "alarm")[d.op_state]
            if d.op_state in range(4) else None,
    ),
    IndoorSensorDescription(
        key="louver_position", translation_key="louver_position",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.louver_position,
    ),
    IndoorSensorDescription(
        key="alarm_code", translation_key="alarm_code",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.alarm_code,
    ),
    IndoorSensorDescription(
        key="alarm_description", translation_key="alarm_description",
        value_fn=lambda d: ALARM_CODES.get(d.alarm_code, f"Unknown (0x{d.alarm_code:02X})"),
    ),
    IndoorSensorDescription(
        key="shutdown_reason", translation_key="shutdown_reason",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.shutdown_reason,
    ),
    IndoorSensorDescription(key="capacity_code", translation_key="capacity_code", value_fn=lambda d: d.capacity_code),
    IndoorSensorDescription(key="remote_control_group", translation_key="remote_control_group", value_fn=lambda d: d.remote_control_group),
    IndoorSensorDescription(key="temp_correction", translation_key="temp_correction", value_fn=lambda d: d.temp_correction),
    IndoorSensorDescription(key="unit_system_number", translation_key="unit_system_number", value_fn=lambda d: d.unit_system_number),
    IndoorSensorDescription(key="unit_address_number", translation_key="unit_address_number", value_fn=lambda d: d.unit_address_number),
    IndoorSensorDescription(key="host_system_number", translation_key="host_system_number", value_fn=lambda d: d.host_system_number),
    IndoorSensorDescription(key="host_address_number", translation_key="host_address_number", value_fn=lambda d: d.host_address_number),
    IndoorSensorDescription(
        key="humidity", translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda d: d.humidity,
    ),
    IndoorSensorDescription(key="mode_jump", translation_key="mode_jump",
                             state_class=SensorStateClass.MEASUREMENT, value_fn=lambda d: d.mode_jump),
    IndoorSensorDescription(key="fan_jump", translation_key="fan_jump",
                             state_class=SensorStateClass.MEASUREMENT, value_fn=lambda d: d.fan_jump),
    IndoorSensorDescription(key="func_display", translation_key="func_display", value_fn=lambda d: d.func_display),
    IndoorSensorDescription(
        key="dry_mode", translation_key="dry_mode",
        device_class=SensorDeviceClass.ENUM,
        options=["dry1", "dry2", "dry3"],
        value_fn=lambda d: ("dry1", "dry2", "dry3")[d.dry_mode]
            if d.dry_mode in (0, 1, 2) else None,
    ),
)

OUTDOOR_SENSORS: tuple[OutdoorSensorDescription, ...] = (
    OutdoorSensorDescription(key="ou_model_name", translation_key="ou_model_name", value_fn=lambda d: d.model_name or None),
    OutdoorSensorDescription(key="ou_run_status", translation_key="ou_run_status", value_fn=lambda d: d.run_status),
    OutdoorSensorDescription(key="ou_protection_info", translation_key="ou_protection_info", value_fn=lambda d: d.protection_info),
    OutdoorSensorDescription(key="ou_fan_stage", translation_key="ou_fan_stage",
                              state_class=SensorStateClass.MEASUREMENT, value_fn=lambda d: d.fan_stage),
    OutdoorSensorDescription(key="ou_temp_tdo", translation_key="ou_temp_tdo", value_fn=lambda d: d.temp_tdo, **_TEMP),
    OutdoorSensorDescription(key="ou_temp_ambient", translation_key="ou_temp_ambient", value_fn=lambda d: d.temp_ambient, **_TEMP),
    OutdoorSensorDescription(key="ou_pressure_discharge", translation_key="ou_pressure_discharge",
                              state_class=SensorStateClass.MEASUREMENT, value_fn=lambda d: d.pressure_discharge),
    OutdoorSensorDescription(key="ou_pressure_suction", translation_key="ou_pressure_suction",
                              state_class=SensorStateClass.MEASUREMENT, value_fn=lambda d: d.pressure_suction),
    OutdoorSensorDescription(key="ou_ev_b", translation_key="ou_ev_b",
                              state_class=SensorStateClass.MEASUREMENT, native_unit_of_measurement=PERCENTAGE,
                              value_fn=lambda d: d.ev_b_pct),
    OutdoorSensorDescription(key="ou_ev_j", translation_key="ou_ev_j",
                              state_class=SensorStateClass.MEASUREMENT, native_unit_of_measurement=PERCENTAGE,
                              value_fn=lambda d: d.ev_j_pct),
    OutdoorSensorDescription(key="ou_temp_tsc", translation_key="ou_temp_tsc", value_fn=lambda d: d.temp_tsc, **_TEMP),
    OutdoorSensorDescription(key="ou_temp_tchg", translation_key="ou_temp_tchg", value_fn=lambda d: d.temp_tchg, **_TEMP),
    OutdoorSensorDescription(key="ou_temp_td_avg", translation_key="ou_temp_td_avg", value_fn=lambda d: d.temp_td_avg, **_TEMP),
    OutdoorSensorDescription(key="ou_temp_td", translation_key="ou_temp_td", value_fn=lambda d: d.temp_td, **_TEMP),
    OutdoorSensorDescription(
        key="ou_cumulative_runtime", translation_key="ou_cumulative_runtime",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.HOURS,
        value_fn=lambda d: d.cumulative_runtime,
    ),
    OutdoorSensorDescription(
        key="ou_current_primary", translation_key="ou_current_primary",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        value_fn=lambda d: d.current_primary,
    ),
    OutdoorSensorDescription(
        key="ou_current_secondary", translation_key="ou_current_secondary",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        value_fn=lambda d: d.current_secondary,
    ),
    OutdoorSensorDescription(key="ou_inverter_status", translation_key="ou_inverter_status", value_fn=lambda d: d.inverter_status),
    OutdoorSensorDescription(key="ou_temp_fin", translation_key="ou_temp_fin", value_fn=lambda d: d.temp_fin, **_TEMP),
    OutdoorSensorDescription(
        key="ou_freq_actual", translation_key="ou_freq_actual",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        value_fn=lambda d: d.freq_actual,
    ),
    OutdoorSensorDescription(
        key="ou_ev_o", translation_key="ou_ev_o",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda d: d.ev_o_pct,
    ),
    OutdoorSensorDescription(key="ou_temp_te", translation_key="ou_temp_te", value_fn=lambda d: d.temp_te, **_TEMP),
    OutdoorSensorDescription(key="ou_temp_tg", translation_key="ou_temp_tg", value_fn=lambda d: d.temp_tg, **_TEMP),
    OutdoorSensorDescription(key="ou_fan_status", translation_key="ou_fan_status", value_fn=lambda d: d.fan_status),
)


def _build_indoor_sensors(
    controller: HisenseVRFController, idx: int
) -> list[SensorEntity]:
    entities: list[SensorEntity] = [
        IndoorSensor(controller, idx, desc) for desc in INDOOR_SENSORS
    ]
    entities.append(LastWriteStatusSensor(controller, idx))
    entities.append(RegisterBaseAddressSensor(controller, idx))
    for exp in ENUM_DESCRIPTORS:
        entities.append(ExpEnumSensor(controller, idx, exp))
    return entities


def _build_outdoor_sensors(
    controller: HisenseVRFController, system_idx: int, module_idx: int
) -> list[SensorEntity]:
    return [
        OutdoorSensor(controller, system_idx, module_idx, desc)
        for desc in OUTDOOR_SENSORS
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HisenseVRFConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    controller = entry.runtime_data

    entities: list[SensorEntity] = []
    for idx in controller.unit_indices:
        entities.extend(_build_indoor_sensors(controller, idx))

    entities.append(GatewayUnitCountSensor(controller))
    entities.append(GatewayEepromSensor(controller))

    for sys_idx, mod_idx in controller.outdoor_units:
        entities.extend(_build_outdoor_sensors(controller, sys_idx, mod_idx))

    async_add_entities(entities)

    @callback
    def _on_new_indoor(unit_index: int) -> None:
        async_add_entities(_build_indoor_sensors(controller, unit_index))

    @callback
    def _on_new_outdoor(module: tuple[int, int]) -> None:
        async_add_entities(_build_outdoor_sensors(controller, *module))

    entry.async_on_unload(
        async_dispatcher_connect(hass, signal_new_indoor(entry.entry_id), _on_new_indoor)
    )
    entry.async_on_unload(
        async_dispatcher_connect(hass, signal_new_outdoor(entry.entry_id), _on_new_outdoor)
    )


class IndoorSensor(HisenseVRFIndoorEntity, SensorEntity):
    entity_description: IndoorSensorDescription

    def __init__(
        self,
        controller: HisenseVRFController,
        unit_index: int,
        description: IndoorSensorDescription,
    ) -> None:
        super().__init__(controller, unit_index)
        self.entity_description = description
        self._attr_unique_id = f"{controller.entry_id}_indoor_{unit_index}_{description.key}"

    @property
    def native_value(self) -> StateType:
        state = self.unit_state
        if state is None:
            return None
        return self.entity_description.value_fn(state)


class RegisterBaseAddressSensor(HisenseVRFIndoorEntity, SensorEntity):
    """Diagnostic sensor exposing the Modbus base register address of the unit."""

    _attr_translation_key = "register_base_address"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, controller: HisenseVRFController, unit_index: int) -> None:
        super().__init__(controller, unit_index)
        self._attr_unique_id = (
            f"{controller.entry_id}_indoor_{unit_index}_register_base_address"
        )

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> StateType:
        return BASE_ADDR + self.unit_index * UNIT_STRIDE


class LastWriteStatusSensor(HisenseVRFIndoorEntity, SensorEntity):
    """Diagnostic sensor exposing the last write/verify outcome for the unit."""

    _attr_translation_key = "last_write_status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["idle", "pending", "off_pending", "confirmed", "failed"]

    def __init__(self, controller: HisenseVRFController, unit_index: int) -> None:
        super().__init__(controller, unit_index)
        self._attr_unique_id = f"{controller.entry_id}_indoor_{unit_index}_last_write_status"

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> StateType:
        status = self.controller.last_write_status.get(self.unit_index, {}).get(
            "status", "idle"
        )
        return str(status)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        info = self.controller.last_write_status.get(self.unit_index, {})
        return {k: v for k, v in info.items() if k != "status"}


class GatewayUnitCountSensor(HisenseVRFGatewayEntity, SensorEntity):
    _attr_translation_key = "gw_unit_count"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, controller: HisenseVRFController) -> None:
        super().__init__(controller)
        self._attr_unique_id = f"{controller.entry_id}_gateway_unit_count"

    @property
    def native_value(self) -> StateType:
        return self.gateway_state.unit_count if self.gateway_state else None


class GatewayEepromSensor(HisenseVRFGatewayEntity, SensorEntity):
    _attr_translation_key = "gw_eeprom_status"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, controller: HisenseVRFController) -> None:
        super().__init__(controller)
        self._attr_unique_id = f"{controller.entry_id}_gateway_eeprom"

    @property
    def native_value(self) -> StateType:
        return self.gateway_state.eeprom_clear if self.gateway_state else None


class OutdoorSensor(HisenseVRFOutdoorEntity, SensorEntity):
    entity_description: OutdoorSensorDescription

    def __init__(
        self,
        controller: HisenseVRFController,
        system_idx: int,
        module_idx: int,
        description: OutdoorSensorDescription,
    ) -> None:
        super().__init__(controller, system_idx, module_idx)
        self.entity_description = description
        self._attr_unique_id = (
            f"{controller.entry_id}_outdoor_{system_idx}_{module_idx}_{description.key}"
        )

    @property
    def native_value(self) -> StateType:
        state = self.outdoor_state
        if state is None:
            return None
        return self.entity_description.value_fn(state)
