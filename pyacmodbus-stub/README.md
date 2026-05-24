# pyacmodbus

[![PyPI version](https://img.shields.io/pypi/v/pyacmodbus.svg)](https://pypi.org/project/pyacmodbus/)
[![Python versions](https://img.shields.io/pypi/pyversions/pyacmodbus.svg)](https://pypi.org/project/pyacmodbus/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Async Modbus TCP client for **Hisense i-Modkit** VRF gateways (model `HCPC-H2M1C`). Built on top of [`pymodbus`](https://pypi.org/project/pymodbus/), with a typed data model and a register map tuned for the i-Modkit firmware quirks.

Originally extracted from the [`hisense_vrf`](https://github.com/buchuman/hisense_vrf) Home Assistant custom integration; published standalone so other projects can drive the same gateway hardware.

## Install

```bash
pip install pyacmodbus
```

## Example

```python
import asyncio
from pyacmodbus import ACModbusClient

async def main() -> None:
    client = ACModbusClient(host="10.0.0.5", port=502)
    await client.connect()
    try:
        units = await client.scan_devices()
        print(f"Discovered indoor units: {units}")
        for idx in units:
            state = await client.read_device(idx)
            print(f"  unit {idx}: running={state.is_running} "
                  f"setpoint={state.setpoint}°C inlet={state.inlet_temp}°C "
                  f"mode=0x{state.current_mode:02x}")
    finally:
        await client.disconnect()

asyncio.run(main())
```

## Features

- **Async client** (`ACModbusClient`) with a global `asyncio.Lock` — the i-Modkit accepts only one TCP connection at a time.
- **Typed data model** (`ACDeviceState`, `GatewayState`, `OutdoorUnitState`) — `dataclass`-based, fully annotated, `py.typed` marker for downstream mypy.
- **Indoor read/write API** — `read_device`, `set_setpoint`, `set_mode`, `set_fan_speed`, `set_swing`, `set_dry_mode`, `turn_on`/`turn_off`, prohibition locks, filter reset.
- **Bundled control write** — `write_control_block(idx, run, mode, fan, swing, temp)` issues a single Modbus 0x10 frame against the 5 control registers (40078-40082); the firmware's recommended atomic ON-with-config flow.
- **Outdoor + gateway discovery** — `read_outdoor_connections`, `read_outdoor_unit`, `read_gateway`, `set_alarm_display`, `clear_eeprom`.
- **Configurable timeout** — `_MODBUS_TIMEOUT_S = 1.5` (vs pymodbus's default 3 s) keeps the worst-case discovery time bounded.

## Register map

See [REGISTERS.md](https://github.com/buchuman/hisense_vrf/blob/main/REGISTERS.md) in the parent repo for the full register reference, cross-referenced against the i-Modkit Modbus Adapter manual.

## Supported gateways

- i-Modkit **HCPC-H2M1C** (Modbus TCP). Other Hisense gateways (KNX, BACnet) are **not** supported.

## Related projects

- [`hisense_vrf`](https://github.com/buchuman/hisense_vrf) — Home Assistant integration that uses this library. Platinum quality scale.

## License

MIT — see [LICENSE](LICENSE).
