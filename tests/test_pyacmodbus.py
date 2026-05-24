"""Tests for pyacmodbus — the low-level Modbus client.

We mock pymodbus.AsyncModbusTcpClient and verify that pyacmodbus produces the
right Modbus calls and parses the responses correctly.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyacmodbus import (
    ACModbusClient,
    BASE_ADDR,
    CannotConnect,
    FAN_HIGH,
    MODE_COOL,
    MODE_HEAT,
    ModbusReadError,
    UNIT_STRIDE,
    fan_actual_name,
    FAN_ACTUAL_HIGH,
    FAN_ACTUAL_LOW,
)


def _mock_response(registers: list[int]):
    """Return a pymodbus-style response object."""
    r = MagicMock()
    r.isError.return_value = False
    r.registers = registers
    return r


def _err_response():
    r = MagicMock()
    r.isError.return_value = True
    r.registers = []
    return r


def _unit_registers(
    *,
    unit_code: int = 10,
    capacity: int = 22,
    status: int = 0x01,
    curr_mode: int = MODE_COOL,
    fan_sw: int = 0,
    mode_jump: int = MODE_COOL,
    fan_jump: int = 0,
    misc: int = 0,
    setpoint: int = 24,
    cool_max: int = 30,
    cool_min: int = 19,
    heat_max: int = 30,
    heat_min: int = 17,
    rc_group: int = 0,
    temp_corr: int = 0,
    humidity: int = 0,
    temp_tg: int = 10,
    temp_trmt: int = 22,
    temp_tl: int = 12,
    outlet: int = 18,
    inlet: int = 22,
    freq_req: int = 50,
    alarm: int = 0,
    shutdown: int = 0,
    exp_high: int = 0,
    exp_low: int = 0,
    func_disp: int = 0,
    exp_valve: int = 50,
    fan_actual: int = FAN_ACTUAL_HIGH,
    host_sys: int = 0,
    host_addr: int = 0,
    unit_sys: int = 0,
    unit_addr: int = 0,
    dry_mode: int = 0,
    function_selection: list[int] | None = None,
    prohibits: tuple[int, int, int, int, int] = (0, 0, 0, 0, 0),
) -> list[int]:
    """Build the 91-register list returned by read_device for an indoor unit."""
    regs = [0] * UNIT_STRIDE
    regs[0] = unit_code
    regs[1] = capacity
    regs[2] = status
    regs[3] = curr_mode
    regs[4] = fan_sw
    regs[5] = mode_jump
    regs[6] = fan_jump
    regs[7] = misc
    regs[8] = setpoint
    regs[9] = cool_max
    regs[10] = cool_min
    regs[11] = heat_max
    regs[12] = heat_min
    regs[13] = rc_group
    regs[14] = temp_corr
    regs[15] = humidity
    regs[16] = temp_tg
    regs[17] = temp_trmt
    regs[18] = temp_tl
    regs[19] = outlet
    regs[20] = inlet
    regs[21] = freq_req
    regs[22] = alarm
    regs[23] = shutdown
    regs[24] = exp_high
    regs[25] = exp_low
    regs[26] = func_disp
    regs[27] = exp_valve
    regs[28] = fan_actual
    regs[29] = host_sys
    regs[30] = host_addr
    regs[31] = unit_sys
    regs[32] = unit_addr
    if function_selection is not None:
        regs[48:68] = function_selection
    regs[77] = dry_mode
    regs[85], regs[86], regs[87], regs[88], regs[89] = prohibits
    return regs


@pytest.fixture
def client():
    """An ACModbusClient with a mocked underlying pymodbus client."""
    c = ACModbusClient("1.2.3.4", 502)
    inner = MagicMock()
    inner.connect = AsyncMock(return_value=True)
    inner.read_holding_registers = AsyncMock()
    inner.write_registers = AsyncMock()
    inner.close = MagicMock()
    c._client = inner
    return c


# ── connect / disconnect ─────────────────────────────────────────────────────


async def test_connect_success():
    c = ACModbusClient("1.2.3.4", 502)
    inner = MagicMock()
    inner.connect = AsyncMock(return_value=True)
    with patch("pyacmodbus.AsyncModbusTcpClient", return_value=inner):
        await c.connect()
    assert c._client is inner


async def test_connect_failure_raises():
    c = ACModbusClient("1.2.3.4", 502)
    inner = MagicMock()
    inner.connect = AsyncMock(return_value=False)
    with patch("pyacmodbus.AsyncModbusTcpClient", return_value=inner):
        with pytest.raises(CannotConnect):
            await c.connect()


async def test_disconnect_closes_inner_client(client):
    await client.disconnect()
    client._client.close.assert_called_once()


# ── scan_devices ─────────────────────────────────────────────────────────────


async def test_scan_devices_returns_range(client):
    client._client.read_holding_registers.return_value = _mock_response([7])
    units = await client.scan_devices()
    assert units == [0, 1, 2, 3, 4, 5, 6]
    client._client.read_holding_registers.assert_awaited_once()
    call = client._client.read_holding_registers.await_args
    assert call.args[0] == 4997  # GW_UNIT_COUNT
    assert call.kwargs["count"] == 1


async def test_scan_devices_error_raises(client):
    client._client.read_holding_registers.return_value = _err_response()
    with pytest.raises(ModbusReadError):
        await client.scan_devices()


# ── read_unit_identifiers / read_unit_capacity ───────────────────────────────


async def test_read_unit_identifiers_decodes_two_values(client):
    client._client.read_holding_registers.return_value = _mock_response([0, 3, 0, 3])
    ids = await client.read_unit_identifiers(1)
    assert ids == (0, 3)
    call = client._client.read_holding_registers.await_args
    # Address should be base of unit 1 + REG_HOST_SYS(29).
    assert call.args[0] == BASE_ADDR + UNIT_STRIDE + 29
    assert call.kwargs["count"] == 4


async def test_read_unit_capacity(client):
    client._client.read_holding_registers.return_value = _mock_response([22])
    capacity = await client.read_unit_capacity(0)
    assert capacity == 22


async def test_read_unit_capacity_error_raises(client):
    client._client.read_holding_registers.return_value = _err_response()
    with pytest.raises(ModbusReadError):
        await client.read_unit_capacity(0)


# ── read_device ──────────────────────────────────────────────────────────────


async def test_read_device_decodes_basic_fields(client):
    regs = _unit_registers(
        status=0b00001,  # running
        curr_mode=MODE_COOL,
        setpoint=23,
        inlet=22,
        host_sys=0,
        host_addr=5,
        capacity=18,
    )
    client._client.read_holding_registers.return_value = _mock_response(regs)
    s = await client.read_device(5)
    assert s.unit_index == 5
    assert s.is_running is True
    assert s.current_mode == MODE_COOL
    assert s.setpoint == 23.0
    assert s.inlet_temp == 22.0
    assert s.host_system_number == 0
    assert s.host_address_number == 5
    assert s.capacity_code == 18


async def test_read_device_setpoint_0xff_becomes_none(client):
    regs = _unit_registers(setpoint=0xFF)
    client._client.read_holding_registers.return_value = _mock_response(regs)
    s = await client.read_device(0)
    assert s.setpoint is None


async def test_read_device_signed_temps(client):
    # negative temps stored as two's complement 16-bit
    regs = _unit_registers(inlet=65530, outlet=65500)  # -6 and -36 as int16
    client._client.read_holding_registers.return_value = _mock_response(regs)
    s = await client.read_device(0)
    assert s.inlet_temp == -6.0
    assert s.outlet_temp == -36.0


async def test_read_device_filter_alarm_decoded(client):
    # filter_alarm is the inverted bit 5 of REG_CURR_MODE
    regs_alarm = _unit_registers(curr_mode=MODE_COOL)  # bit5=0 → alarm
    regs_clean = _unit_registers(curr_mode=MODE_COOL | 0x20)  # bit5=1 → no alarm
    client._client.read_holding_registers.return_value = _mock_response(regs_alarm)
    s1 = await client.read_device(0)
    assert s1.filter_alarm is True
    client._client.read_holding_registers.return_value = _mock_response(regs_clean)
    s2 = await client.read_device(0)
    assert s2.filter_alarm is False


async def test_read_device_function_selection_populated(client):
    fs = list(range(20))  # easy to verify
    regs = _unit_registers(function_selection=fs)
    client._client.read_holding_registers.return_value = _mock_response(regs)
    s = await client.read_device(0)
    assert s.function_selection == tuple(range(20))


async def test_read_device_short_response_raises(client):
    client._client.read_holding_registers.return_value = _mock_response([0] * 10)
    with pytest.raises(ModbusReadError):
        await client.read_device(0)


# ── read_gateway / read_outdoor_connections / read_outdoor_unit ──────────────


async def test_read_gateway(client):
    client._client.read_holding_registers.return_value = _mock_response([0, 7, 1, 0])
    g = await client.read_gateway()
    assert g.alarm_display == 0
    assert g.unit_count == 7
    assert g.ctrl_mode == 1
    assert g.eeprom_clear == 0


async def test_read_outdoor_connections_bit_decoding(client):
    # System 0 has modules 0 and 2 connected (0b00101 = 5).
    # System 1 has module 3 (0b01000 = 8).
    client._client.read_holding_registers.return_value = _mock_response([5, 8, 0, 0, 0, 0])
    out = await client.read_outdoor_connections()
    assert (0, 0) in out
    assert (0, 2) in out
    assert (1, 3) in out
    assert len(out) == 3


async def test_read_outdoor_unit(client):
    # Build a 54-register response (offsets 0..53)
    regs = [0] * 54
    for i, ch in enumerate(b"MODEL-X"):
        regs[i] = ch
    regs[15] = 5
    regs[17] = 1
    regs[30] = 25  # ambient
    regs[41] = 0  # runtime high
    regs[42] = 1234  # runtime low
    regs[43] = 7
    regs[44] = 2  # ×0.5
    client._client.read_holding_registers.return_value = _mock_response(regs)
    o = await client.read_outdoor_unit(0, 0)
    assert o.model_name == "MODEL-X"
    assert o.run_status == 1
    assert o.temp_ambient == 25
    assert o.cumulative_runtime == 1234
    assert o.current_primary == 7
    assert o.current_secondary == 1.0


# ── writes (verify 0x10 framing) ─────────────────────────────────────────────


async def test_write_control_block_sends_5_registers(client):
    wr = MagicMock()
    wr.isError.return_value = False
    client._client.write_registers.return_value = wr
    await client.write_control_block(1, run=1, mode=MODE_HEAT, fan=FAN_HIGH, swing=0, temp=24)
    client._client.write_registers.assert_awaited_once()
    call = client._client.write_registers.await_args
    # Address: BASE_ADDR + 1*UNIT_STRIDE + REG_RUN_STOP(78)
    assert call.args[0] == BASE_ADDR + UNIT_STRIDE + 78
    assert call.args[1] == [1, MODE_HEAT, FAN_HIGH, 0, 24]


async def test_write_control_block_error_raises(client):
    wr = MagicMock()
    wr.isError.return_value = True
    client._client.write_registers.return_value = wr
    with pytest.raises(ModbusReadError):
        await client.write_control_block(0, run=1, mode=MODE_COOL, fan=FAN_HIGH, swing=0, temp=24)


async def test_set_setpoint_uses_write_registers(client):
    wr = MagicMock()
    wr.isError.return_value = False
    client._client.write_registers.return_value = wr
    await client.set_setpoint(0, 25.0)
    call = client._client.write_registers.await_args
    # Address = BASE_ADDR + 82 (REG_SET_TEMP)
    assert call.args[0] == BASE_ADDR + 82
    assert call.args[1] == [25]


async def test_turn_on_and_turn_off_addresses(client):
    wr = MagicMock()
    wr.isError.return_value = False
    client._client.write_registers.return_value = wr
    await client.turn_on(3)
    on_call = client._client.write_registers.await_args
    assert on_call.args[0] == BASE_ADDR + 3 * UNIT_STRIDE + 78
    assert on_call.args[1] == [1]

    await client.turn_off(3)
    off_call = client._client.write_registers.await_args
    assert off_call.args[0] == BASE_ADDR + 3 * UNIT_STRIDE + 78
    assert off_call.args[1] == [0]


async def test_set_swing_encodes_auto_and_position(client):
    wr = MagicMock()
    wr.isError.return_value = False
    client._client.write_registers.return_value = wr
    # auto=True, position ignored → value = 0x01
    await client.set_swing(0, True, 0)
    assert client._client.write_registers.await_args.args[1] == [0x01]
    # auto=False, position=3 → value = (0 | (3 << 1)) = 6
    await client.set_swing(0, False, 3)
    assert client._client.write_registers.await_args.args[1] == [6]


# ── helpers ──────────────────────────────────────────────────────────────────


def test_fan_actual_name_maps_known_bits():
    assert fan_actual_name(FAN_ACTUAL_HIGH) == "high"
    assert fan_actual_name(FAN_ACTUAL_LOW) == "low"


def test_fan_actual_name_unknown_returns_hex():
    assert "unknown" in fan_actual_name(0xAA)
