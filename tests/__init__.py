"""Tests for the Hisense VRF integration."""
from __future__ import annotations

from dataclasses import replace

from pyacmodbus import (
    ACDeviceState,
    FAN_AUTO,
    GatewayState,
    MODE_COOL,
    OutdoorUnitState,
)


def make_indoor_state(unit_index: int = 0, **overrides) -> ACDeviceState:
    """Build a minimal but fully-populated ACDeviceState for tests."""
    base = ACDeviceState(
        unit_index=unit_index,
        is_running=True,
        op_state=2,
        oil_return=False,
        current_mode=MODE_COOL,
        mode_jump=MODE_COOL,
        fan_speed=FAN_AUTO,
        fan_jump=FAN_AUTO,
        fan_actual=0x02,
        setpoint=24.0,
        inlet_temp=22.0,
        outlet_temp=18.0,
        temp_tg=10.0,
        temp_tl=12.0,
        temp_remote=22.0,
        cooling_upper_limit=30.0,
        cooling_lower_limit=19.0,
        heating_upper_limit=30.0,
        heating_lower_limit=17.0,
        expansion_valve_pct=50,
        alarm_code=0,
        shutdown_reason=0,
        required_frequency=50,
        filter_alarm=False,
        auto_swing=False,
        swing_active=False,
        louver_position=0,
        test_run=False,
        remote_control_active=True,
        full_heat_exchange=False,
        humidity=0,
        dry_mode=0,
        func_display=0,
        capacity_code=22,
        unit_system_number=0,
        unit_address_number=unit_index,
        host_system_number=0,
        host_address_number=unit_index,
        remote_control_group=0,
        temp_correction=0,
        prohibit_on_off=False,
        prohibit_mode=False,
        prohibit_fan=False,
        prohibit_swing=False,
        prohibit_temp=False,
        function_selection=(0,) * 20,
    )
    return replace(base, **overrides) if overrides else base


def make_gateway_state(**overrides) -> GatewayState:
    base = GatewayState(alarm_display=0, unit_count=2, ctrl_mode=1, eeprom_clear=0)
    return replace(base, **overrides) if overrides else base


def make_outdoor_state(system_idx: int = 0, module_idx: int = 0, **overrides) -> OutdoorUnitState:
    base = OutdoorUnitState(
        system_idx=system_idx,
        module_idx=module_idx,
        model_name="TESTOUTDOOR",
        run_status=0,
        protection_info=0,
        fan_stage=0,
        temp_tdo=None,
        temp_ambient=20,
        pressure_discharge=0,
        pressure_suction=0,
        ev_b_pct=0,
        ev_j_pct=0,
        temp_tsc=None,
        temp_tchg=None,
        temp_td_avg=None,
        temp_td=None,
        cumulative_runtime=100,
        current_primary=0,
        current_secondary=0.0,
        inverter_status=0,
        temp_fin=None,
        freq_actual=0,
        ev_o_pct=0,
        temp_te=None,
        temp_tg=None,
        fan_status=0,
    )
    return replace(base, **overrides) if overrides else base


def indoor_uid(unit_index: int, suffix: str, entry_id: str = "test_entry_id") -> str:
    return f"{entry_id}_indoor_{unit_index}_{suffix}"


def gateway_uid(suffix: str, entry_id: str = "test_entry_id") -> str:
    return f"{entry_id}_gateway_{suffix}"


def outdoor_uid(system_idx: int, module_idx: int, suffix: str, entry_id: str = "test_entry_id") -> str:
    return f"{entry_id}_outdoor_{system_idx}_{module_idx}_{suffix}"
