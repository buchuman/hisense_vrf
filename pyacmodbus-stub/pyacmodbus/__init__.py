"""pyacmodbus — Async Modbus TCP client for Hisense i-Modkit VRF gateways.

Register map source: i-Modkit-Mapping-Table (Parameter mapping table sheet).
Protocol: Modbus TCP, holding registers (FC3 read / FC6 write).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

_LOGGER = logging.getLogger(__name__)


class CannotConnect(Exception):
    """Raised when the TCP connection to the gateway cannot be established."""


class ModbusReadError(Exception):
    """Raised when a Modbus register read or write fails."""


# ── Register map ──────────────────────────────────────────────────────────────

BASE_ADDR = 40000
UNIT_STRIDE = 91

REG_UNIT_CODE = 0
REG_CAPACITY = 1
REG_STATUS = 2
REG_CURR_MODE = 3
REG_FAN_SW = 4
REG_MODE_JUMP = 5
REG_FAN_JUMP = 6
REG_MISC = 7
REG_SETPOINT_R = 8
REG_COOL_MAX = 9
REG_COOL_MIN = 10
REG_HEAT_MAX = 11
REG_HEAT_MIN = 12
REG_RC_GROUP = 13
REG_TEMP_CORR = 14
REG_HUMIDITY = 15
REG_TEMP_TG = 16
REG_TEMP_TRMT = 17
REG_TEMP_TL = 18
REG_OUTLET_TEMP = 19
REG_INLET_TEMP = 20
REG_FREQ_REQ = 21
REG_ALARM = 22
REG_SHUTDOWN = 23
REG_EXP_HIGH = 24
REG_EXP_LOW = 25
REG_FUNC_DISP = 26
REG_EXP_VALVE = 27
REG_FAN_ACTUAL = 28
REG_HOST_SYS = 29
REG_HOST_ADDR = 30
REG_UNIT_SYS = 31
REG_UNIT_ADDR = 32
REG_DRY_MODE = 77

REG_RUN_STOP = 78
REG_SET_MODE = 79
REG_SET_FAN = 80
REG_SET_SWING = 81
REG_SET_TEMP = 82
REG_FILTER_RST = 83
REG_PROHIBIT_ALL = 84
REG_PROHIBIT_SW = 85
REG_PROHIBIT_MODE = 86
REG_PROHIBIT_FAN = 87
REG_PROHIBIT_SWING = 88
REG_PROHIBIT_TEMP = 89

GW_ALARM_DISPLAY = 4996
GW_UNIT_COUNT = 4997
GW_CTRL_MODE = 4998
GW_EEPROM_CLEAR = 4999

OUTDOOR_CONNECT_BASE = 1000
OUTDOOR_MAX_SYSTEMS = 6
OUTDOOR_MAX_MODULES = 5
OUTDOOR_BASE = 5000
OUTDOOR_MODULE_STRIDE = 98
OUTDOOR_SYSTEM_STRIDE = 490

OU_RUN_STATUS = 17
OU_PROTECTION_INFO = 18
OU_FAN_STAGE = 20
OU_TDO = 29
OU_TEMP_AMBIENT = 30
OU_PRESSURE_PD = 31
OU_PRESSURE_PS = 32
OU_EV_B = 33
OU_EV_J = 34
OU_TEMP_TSC = 35
OU_TEMP_TCHG = 36
OU_TEMP_TD_AVG = 37
OU_TEMP_TD = 40
OU_RUNTIME_HI = 41
OU_RUNTIME_LO = 42
OU_CURRENT_PRI = 43
OU_CURRENT_SEC = 44
OU_INVERTER_STATUS = 45
OU_TEMP_FIN = 47
OU_FREQ_ACTUAL = 48
OU_EV_O = 50
OU_TEMP_TE = 51
OU_TEMP_TG = 52
OU_FAN_STATUS = 53

# ── Mode bitmasks ─────────────────────────────────────────────────────────────

MODE_AUTO = 0x01
MODE_COOL = 0x02
MODE_DRY = 0x04
MODE_FAN = 0x08
MODE_HEAT = 0x10

# ── Fan speed bitmasks — setpoint ─────────────────────────────────────────────

FAN_AUTO = 0x01
FAN_HIGH = 0x02
FAN_MED = 0x04
FAN_LOW = 0x08

# ── Fan speed bitmasks — actual (REG_FAN_ACTUAL) ──────────────────────────────

FAN_ACTUAL_HH2 = 0x01
FAN_ACTUAL_HIGH = 0x02
FAN_ACTUAL_MED = 0x04
FAN_ACTUAL_MEDLOW = 0x08
FAN_ACTUAL_LOW = 0x10
FAN_ACTUAL_MUTE = 0x20
FAN_ACTUAL_BREEZE = 0x40


def fan_actual_name(bitmask: int) -> str:
    names = {
        FAN_ACTUAL_HH2: "high_high_2",
        FAN_ACTUAL_HIGH: "high",
        FAN_ACTUAL_MED: "medium",
        FAN_ACTUAL_MEDLOW: "medium_low",
        FAN_ACTUAL_LOW: "low",
        FAN_ACTUAL_MUTE: "mute",
        FAN_ACTUAL_BREEZE: "breeze",
    }
    return names.get(bitmask, f"unknown(0x{bitmask:02x})")


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class ACDeviceState:
    """Full state snapshot of one indoor Hisense VRF unit."""

    unit_index: int

    is_running: bool
    op_state: int
    oil_return: bool

    current_mode: int
    mode_jump: int
    fan_speed: int
    fan_jump: int
    fan_actual: int

    setpoint: float | None
    inlet_temp: float
    outlet_temp: float
    temp_tg: float
    temp_tl: float
    temp_remote: float

    cooling_upper_limit: float
    cooling_lower_limit: float
    heating_upper_limit: float
    heating_lower_limit: float

    expansion_valve_pct: int

    alarm_code: int
    shutdown_reason: int
    required_frequency: int

    filter_alarm: bool

    auto_swing: bool
    swing_active: bool
    louver_position: int

    test_run: bool
    remote_control_active: bool
    full_heat_exchange: bool

    humidity: int
    dry_mode: int
    func_display: int

    capacity_code: int
    unit_system_number: int
    unit_address_number: int
    host_system_number: int
    host_address_number: int
    remote_control_group: int
    temp_correction: int

    prohibit_on_off: bool
    prohibit_mode: bool
    prohibit_fan: bool
    prohibit_swing: bool
    prohibit_temp: bool

    # Function selection (offsets 48-67, registers 40048..40067).
    # Tuple of 20 raw register values; the EXP_* entities on the HA side
    # decode individual bits.
    function_selection: tuple[int, ...] = ()


@dataclass
class GatewayState:
    """State snapshot of the i-Modkit VRF gateway device."""

    alarm_display: int
    unit_count: int
    ctrl_mode: int
    eeprom_clear: int


@dataclass
class OutdoorUnitState:
    """Operating state snapshot of one outdoor VRF module."""

    system_idx: int
    module_idx: int
    model_name: str
    run_status: int
    protection_info: int
    fan_stage: int
    temp_tdo: int | None
    temp_ambient: int | None
    pressure_discharge: int
    pressure_suction: int
    ev_b_pct: int
    ev_j_pct: int
    temp_tsc: int | None
    temp_tchg: int | None
    temp_td_avg: int | None
    temp_td: int | None
    cumulative_runtime: int
    current_primary: int
    current_secondary: float
    inverter_status: int
    temp_fin: int | None
    freq_actual: int
    ev_o_pct: int
    temp_te: int | None
    temp_tg: int | None
    fan_status: int


def _to_signed(raw: int) -> float:
    return float(raw - 65536 if raw > 32767 else raw)


def _to_signed8(raw: int) -> int:
    v = raw & 0xFF
    return v if v < 128 else v - 256


# ── Field-to-register-offset map (used by verification & write API) ──────────

WRITABLE_OFFSETS: dict[str, int] = {
    "run_stop": REG_RUN_STOP,
    "mode": REG_SET_MODE,
    "fan": REG_SET_FAN,
    "swing": REG_SET_SWING,
    "setpoint": REG_SET_TEMP,
    "filter_reset": REG_FILTER_RST,
    "prohibit_all": REG_PROHIBIT_ALL,
    "prohibit_on_off": REG_PROHIBIT_SW,
    "prohibit_mode": REG_PROHIBIT_MODE,
    "prohibit_fan": REG_PROHIBIT_FAN,
    "prohibit_swing": REG_PROHIBIT_SWING,
    "prohibit_temp": REG_PROHIBIT_TEMP,
    "dry_mode": REG_DRY_MODE,
}


def state_field_for_write(field: str) -> str | None:
    """Map a write field name to the ACDeviceState attribute used to verify it.

    Returns None when no read-side field exists for the write (e.g. one-shot
    commands like filter_reset / prohibit_all that don't have a status mirror).
    """
    mapping = {
        "run_stop": "is_running",
        "mode": "current_mode",
        "fan": "fan_speed",
        "swing": "_swing",
        "setpoint": "setpoint",
        "prohibit_on_off": "prohibit_on_off",
        "prohibit_mode": "prohibit_mode",
        "prohibit_fan": "prohibit_fan",
        "prohibit_swing": "prohibit_swing",
        "prohibit_temp": "prohibit_temp",
        "dry_mode": "dry_mode",
    }
    return mapping.get(field)


# ── Client ────────────────────────────────────────────────────────────────────


class ACModbusClient:
    """Async Modbus TCP client for i-Modkit VRF gateways (Hisense)."""

    def __init__(self, host: str, port: int, slave_id: int = 1) -> None:
        self._host = host
        self._port = port
        self._slave_id = slave_id
        self._client: AsyncModbusTcpClient | None = None
        self._lock = asyncio.Lock()

    # Per-operation timeout. The gateway typically responds in <500 ms;
    # the default pymodbus value (3 s) makes a degraded gateway stall HA
    # setup for ~54 s when scanning 7 units. 1.5 s leaves a 3× safety
    # margin over normal latency and bounds the worst case to ~27 s.
    _MODBUS_TIMEOUT_S = 1.5

    async def connect(self) -> None:
        """Open the TCP connection. Raises CannotConnect on failure.

        We rely on pymodbus's stock TID validation: the i-Modkit echoes our
        request TID correctly, and the occasional spurious frames it emits with
        unrelated TIDs are skipped automatically by pymodbus.
        """
        self._client = AsyncModbusTcpClient(
            self._host, port=self._port, timeout=self._MODBUS_TIMEOUT_S
        )
        connected = await self._client.connect()
        if not connected:
            raise CannotConnect(f"Cannot connect to {self._host}:{self._port}")

    async def disconnect(self) -> None:
        if self._client is not None:
            self._client.close()

    def _require_client(self) -> AsyncModbusTcpClient:
        """Return the connected pymodbus client or raise if connect() wasn't called."""
        if self._client is None:
            raise CannotConnect("Client not connected")
        return self._client

    # ── Discovery ─────────────────────────────────────────────────────────────

    async def scan_devices(self) -> list[int]:
        """Return a list of unit indices read from GW_UNIT_COUNT."""
        try:
            async with self._lock:
                result = await self._require_client().read_holding_registers(
                    GW_UNIT_COUNT, count=1, device_id=self._slave_id
                )
        except ModbusException as err:
            raise ModbusReadError(f"Communication error during scan: {err}") from err
        if result.isError() or not getattr(result, "registers", None):
            raise ModbusReadError("Invalid response from gateway during scan")
        return list(range(result.registers[0]))

    async def read_unit_capacity(self, unit_index: int) -> int:
        """Return ``capacity_code`` (offset 1) for one indoor unit."""
        addr = BASE_ADDR + unit_index * UNIT_STRIDE + REG_CAPACITY
        try:
            async with self._lock:
                result = await self._require_client().read_holding_registers(
                    addr, count=1, device_id=self._slave_id
                )
        except ModbusException as err:
            raise ModbusReadError(
                f"Communication error reading capacity for unit {unit_index}: {err}"
            ) from err
        if result.isError() or not getattr(result, "registers", None):
            raise ModbusReadError(f"Invalid capacity response for unit {unit_index}")
        return int(result.registers[0])

    async def read_unit_identifiers(self, unit_index: int) -> tuple[int, int]:
        """Return ``(host_system_number, host_address_number)`` for one indoor unit."""
        base = BASE_ADDR + unit_index * UNIT_STRIDE + REG_HOST_SYS
        try:
            async with self._lock:
                result = await self._require_client().read_holding_registers(
                    base, count=4, device_id=self._slave_id
                )
        except ModbusException as err:
            raise ModbusReadError(
                f"Communication error reading identifiers for unit {unit_index}: {err}"
            ) from err
        if result.isError() or not getattr(result, "registers", None) or len(result.registers) < 2:
            raise ModbusReadError(f"Invalid identifier response for unit {unit_index}")
        return int(result.registers[0]), int(result.registers[1])

    async def read_outdoor_connections(self) -> list[tuple[int, int]]:
        """Return (system_idx, module_idx) pairs for connected outdoor modules."""
        try:
            async with self._lock:
                result = await self._require_client().read_holding_registers(
                    OUTDOOR_CONNECT_BASE,
                    count=OUTDOOR_MAX_SYSTEMS,
                    device_id=self._slave_id,
                )
        except ModbusException as err:
            raise ModbusReadError(f"Communication error reading outdoor connections: {err}") from err
        if (
            result.isError()
            or not getattr(result, "registers", None)
            or len(result.registers) < OUTDOOR_MAX_SYSTEMS
        ):
            raise ModbusReadError("Invalid response reading outdoor connections")
        connected: list[tuple[int, int]] = []
        for sys_idx in range(OUTDOOR_MAX_SYSTEMS):
            reg = result.registers[sys_idx]
            for mod_idx in range(OUTDOOR_MAX_MODULES):
                if reg & (1 << mod_idx):
                    connected.append((sys_idx, mod_idx))
        return connected

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def read_device(self, unit_index: int) -> ACDeviceState:
        base = BASE_ADDR + unit_index * UNIT_STRIDE
        try:
            async with self._lock:
                result = await self._require_client().read_holding_registers(
                    base, count=UNIT_STRIDE, device_id=self._slave_id
                )
        except ModbusException as err:
            raise ModbusReadError(f"Communication error reading unit {unit_index}: {err}") from err
        if (
            result.isError()
            or not getattr(result, "registers", None)
            or len(result.registers) < UNIT_STRIDE
        ):
            raise ModbusReadError(
                f"Invalid response for unit {unit_index}: expected {UNIT_STRIDE} registers"
            )

        regs = result.registers
        status = regs[REG_STATUS]
        curr_mode_r = regs[REG_CURR_MODE]
        misc = regs[REG_MISC]

        setpoint_raw = regs[REG_SETPOINT_R]
        setpoint = None if setpoint_raw == 0xFF else float(setpoint_raw)

        return ACDeviceState(
            unit_index=unit_index,
            is_running=bool(status & 0x01),
            op_state=(status >> 1) & 0x03,
            oil_return=bool((status >> 3) & 0x01),
            current_mode=curr_mode_r & 0x1F,
            filter_alarm=not bool((curr_mode_r >> 5) & 0x01),
            mode_jump=regs[REG_MODE_JUMP],
            fan_speed=regs[REG_FAN_SW],
            fan_jump=regs[REG_FAN_JUMP],
            fan_actual=regs[REG_FAN_ACTUAL],
            setpoint=setpoint,
            inlet_temp=_to_signed(regs[REG_INLET_TEMP]),
            outlet_temp=_to_signed(regs[REG_OUTLET_TEMP]),
            temp_tg=_to_signed(regs[REG_TEMP_TG]),
            temp_tl=_to_signed(regs[REG_TEMP_TL]),
            temp_remote=_to_signed(regs[REG_TEMP_TRMT]),
            cooling_upper_limit=float(regs[REG_COOL_MAX]),
            cooling_lower_limit=float(regs[REG_COOL_MIN]),
            heating_upper_limit=float(regs[REG_HEAT_MAX]),
            heating_lower_limit=float(regs[REG_HEAT_MIN]),
            expansion_valve_pct=regs[REG_EXP_VALVE],
            alarm_code=regs[REG_ALARM],
            shutdown_reason=regs[REG_SHUTDOWN],
            required_frequency=regs[REG_FREQ_REQ],
            func_display=regs[REG_FUNC_DISP],
            auto_swing=bool((misc >> 4) & 0x01),
            swing_active=bool((misc >> 2) & 0x01),
            louver_position=(misc >> 5) & 0x07,
            test_run=bool(misc & 0x01),
            remote_control_active=bool((misc >> 1) & 0x01),
            full_heat_exchange=bool((misc >> 3) & 0x01),
            humidity=regs[REG_HUMIDITY],
            dry_mode=regs[REG_DRY_MODE],
            capacity_code=regs[REG_CAPACITY],
            unit_system_number=regs[REG_UNIT_SYS],
            unit_address_number=regs[REG_UNIT_ADDR],
            host_system_number=regs[REG_HOST_SYS],
            host_address_number=regs[REG_HOST_ADDR],
            remote_control_group=regs[REG_RC_GROUP],
            temp_correction=regs[REG_TEMP_CORR] & 0x07,
            prohibit_on_off=bool(regs[REG_PROHIBIT_SW]),
            prohibit_mode=bool(regs[REG_PROHIBIT_MODE]),
            prohibit_fan=bool(regs[REG_PROHIBIT_FAN]),
            prohibit_swing=bool(regs[REG_PROHIBIT_SWING]),
            prohibit_temp=bool(regs[REG_PROHIBIT_TEMP]),
            function_selection=tuple(regs[48:68]),
        )

    async def read_gateway(self) -> GatewayState:
        try:
            async with self._lock:
                result = await self._require_client().read_holding_registers(
                    GW_ALARM_DISPLAY, count=4, device_id=self._slave_id
                )
        except ModbusException as err:
            raise ModbusReadError(f"Communication error reading gateway: {err}") from err
        if (
            result.isError()
            or not getattr(result, "registers", None)
            or len(result.registers) < 4
        ):
            raise ModbusReadError("Invalid response reading gateway registers")
        regs = result.registers
        return GatewayState(
            alarm_display=regs[0],
            unit_count=regs[1],
            ctrl_mode=regs[2],
            eeprom_clear=regs[3],
        )

    async def read_outdoor_unit(self, system_idx: int, module_idx: int) -> OutdoorUnitState:
        base = (
            OUTDOOR_BASE
            + system_idx * OUTDOOR_SYSTEM_STRIDE
            + module_idx * OUTDOOR_MODULE_STRIDE
        )
        reg_count = OU_FAN_STATUS + 1
        try:
            async with self._lock:
                result = await self._require_client().read_holding_registers(
                    base, count=reg_count, device_id=self._slave_id
                )
        except ModbusException as err:
            raise ModbusReadError(
                f"Communication error reading outdoor unit {system_idx}:{module_idx}: {err}"
            ) from err
        if (
            result.isError()
            or not getattr(result, "registers", None)
            or len(result.registers) < reg_count
        ):
            raise ModbusReadError(f"Invalid response for outdoor unit {system_idx}:{module_idx}")
        r = result.registers

        model_name = "".join(chr(r[i]) for i in range(15) if 0x20 <= r[i] <= 0x7E).rstrip()

        return OutdoorUnitState(
            system_idx=system_idx,
            module_idx=module_idx,
            model_name=model_name,
            run_status=r[OU_RUN_STATUS],
            protection_info=r[OU_PROTECTION_INFO],
            fan_stage=r[OU_FAN_STAGE],
            temp_tdo=r[OU_TDO],
            temp_ambient=_to_signed8(r[OU_TEMP_AMBIENT]),
            pressure_discharge=r[OU_PRESSURE_PD],
            pressure_suction=r[OU_PRESSURE_PS],
            ev_b_pct=r[OU_EV_B],
            ev_j_pct=r[OU_EV_J],
            temp_tsc=_to_signed8(r[OU_TEMP_TSC]),
            temp_tchg=_to_signed8(r[OU_TEMP_TCHG]),
            temp_td_avg=r[OU_TEMP_TD_AVG],
            temp_td=r[OU_TEMP_TD],
            cumulative_runtime=(r[OU_RUNTIME_HI] << 16) | r[OU_RUNTIME_LO],
            current_primary=r[OU_CURRENT_PRI],
            current_secondary=r[OU_CURRENT_SEC] * 0.5,
            inverter_status=r[OU_INVERTER_STATUS],
            temp_fin=_to_signed8(r[OU_TEMP_FIN]),
            freq_actual=r[OU_FREQ_ACTUAL],
            ev_o_pct=r[OU_EV_O],
            temp_te=_to_signed8(r[OU_TEMP_TE]),
            temp_tg=_to_signed8(r[OU_TEMP_TG]),
            fan_status=r[OU_FAN_STATUS],
        )

    # ── Writes ────────────────────────────────────────────────────────────────

    async def turn_on(self, unit_index: int) -> None:
        await self._write_unit(unit_index, REG_RUN_STOP, 1)

    async def turn_off(self, unit_index: int) -> None:
        await self._write_unit(unit_index, REG_RUN_STOP, 0)

    async def set_mode(self, unit_index: int, mode_bitmask: int) -> None:
        await self._write_unit(unit_index, REG_SET_MODE, mode_bitmask)

    async def set_setpoint(self, unit_index: int, temp: float) -> None:
        await self._write_unit(unit_index, REG_SET_TEMP, int(temp))

    async def set_fan_speed(self, unit_index: int, fan_bitmask: int) -> None:
        await self._write_unit(unit_index, REG_SET_FAN, fan_bitmask)

    async def set_swing(self, unit_index: int, auto: bool, position: int = 0) -> None:
        value = (0x01 if auto else 0x00) | ((position & 0x07) << 1)
        await self._write_unit(unit_index, REG_SET_SWING, value)

    async def reset_filter(self, unit_index: int) -> None:
        await self._write_unit(unit_index, REG_FILTER_RST, 1)

    async def set_prohibition(self, unit_index: int, reg_offset: int, value: bool) -> None:
        await self._write_unit(unit_index, reg_offset, 1 if value else 0)

    async def lock_all(self, unit_index: int) -> None:
        await self._write_unit(unit_index, REG_PROHIBIT_ALL, 1)

    async def unlock_all(self, unit_index: int) -> None:
        await self._write_unit(unit_index, REG_PROHIBIT_ALL, 2)

    async def set_dry_mode(self, unit_index: int, mode: int) -> None:
        await self._write_unit(unit_index, REG_DRY_MODE, mode)

    async def write_control_block(
        self,
        unit_index: int,
        run: int,
        mode: int,
        fan: int,
        swing: int,
        temp: int,
    ) -> None:
        """Write the five control registers (40078..40082) in one 0x10 frame.

        Per the i-Modkit manual section 4.2, the recommended way to power on
        the unit (or change multiple control parameters at once) is a single
        continuation write spanning run/stop + mode + fan + swing + temp.
        """
        base = BASE_ADDR + unit_index * UNIT_STRIDE + REG_RUN_STOP
        try:
            async with self._lock:
                result = await self._require_client().write_registers(
                    base, [run, mode, fan, swing, temp], device_id=self._slave_id
                )
        except ModbusException as err:
            raise ModbusReadError(
                f"Communication error writing control block for unit {unit_index}: {err}"
            ) from err
        if result.isError():
            raise ModbusReadError(
                f"Control block write failed for unit {unit_index}: {result}"
            )

    async def set_alarm_display(self, value: bool) -> None:
        await self._write(GW_ALARM_DISPLAY, 1 if value else 0)

    async def clear_eeprom(self) -> None:
        await self._write(GW_EEPROM_CLEAR, 1)

    # ── Internal write helpers ────────────────────────────────────────────────

    async def _write_unit(self, unit_index: int, offset: int, value: int) -> None:
        addr = BASE_ADDR + unit_index * UNIT_STRIDE + offset
        await self._write(addr, value)

    async def _write(self, address: int, value: int) -> None:
        # Per the i-Modkit manual (Table 4.1), only function codes 0x03 and 0x10
        # are documented. We use write_registers with a single-element list to
        # send a 0x10 frame instead of pymodbus's write_register (0x06).
        # Rollback: replace `write_registers(address, [value], …)` with
        # `write_register(address, value, …)` to go back to 0x06.
        try:
            async with self._lock:
                result = await self._require_client().write_registers(
                    address, [value], device_id=self._slave_id
                )
        except ModbusException as err:
            raise ModbusReadError(f"Communication error writing register {address}: {err}") from err
        if result.isError():
            raise ModbusReadError(f"Write failed at register {address}: {result}")
