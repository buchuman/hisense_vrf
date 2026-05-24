# Hisense VRF — Architecture & Design Decisions

This document records the **why** behind the non-obvious design choices in the integration. The goal is that someone (probably future-you) reading the code six months from now doesn't have to revive the investigation that led to each decision.

The integration consists of two layers:

```
custom_components/hisense_vrf/   ←  HA-aware layer (controller, entities, config flow)
pyacmodbus-stub/pyacmodbus/      ←  Pure-Python Modbus client (no HA dependencies)
```

`pyacmodbus` knows about i-Modkit registers and Modbus framing.
The controller (in `controller.py`) owns the per-entry state, the write-verify cycle, the off-pending TTL machinery, and the polling loop. Entities (`climate.py`, `sensor.py`, etc.) are thin views over the controller's state with no Modbus knowledge of their own.

---

## 1. No TID patch

**Decision**: the integration **does not patch** pymodbus's transaction-id (TID) validation.

### Background

An early version of the integration patched pymodbus's framer to accept any TID, because the i-Modkit gateway was assumed to send non-compliant TIDs. The patch overrode `FramerSocket.handleFrame` so that a response always looked like it matched the request.

### What we found

A traced capture of raw frames showed that **the gateway does echo the TID correctly**. The MBAP header bytes 0–1 of the response always match the request's `transaction_id`.

What the gateway **also** does, however, is occasionally emit spurious frames with unrelated TIDs (we observed TIDs in the 2000-5000 range showing up between our request and its real response). These are most likely the gateway re-broadcasting responses meant for another Modbus client that had a recent session.

Pymodbus's stock `handleFrame` skips frames whose TID doesn't match the expected one. Without the patch, those spurious frames are correctly discarded and the actual response is picked up. With the patch, the framer accepted the first frame it saw — sometimes a stale frame from a previous request to a different unit — which caused short reads to return data from another register block.

### The fix

Removed the patch entirely. `pyacmodbus.ACModbusClient.connect` just opens the connection and lets pymodbus do its TID validation.

If you ever see logs like:

```
ERROR: request ask for transaction_id=1 but got id=920, Skipping.
```

That's pymodbus doing its job — a stale frame was filtered out and the next legitimate one was used.

---

## 2. Modbus 0x10 instead of 0x06

**Decision**: every write — even a single-register write like `set_setpoint` — uses function code `0x10` (Write Multiple Registers) with a single-element list.

### Background

The i-Modkit manual (Table 4.1) documents exactly two function codes: `0x03` (Read holding) and `0x10` (Write holding). `0x06` (Write Single Register) is not mentioned.

### Why it matters

Older versions used pymodbus's `write_register()` which sends `0x06`. The gateway appears to accept it most of the time but the behaviour is undocumented and could change with firmware updates. Switching to `write_registers(addr, [value])` is semantically equivalent (one register modified) but emits a documented frame.

### Rollback

In `pyacmodbus.ACModbusClient._write()`, replace:

```python
result = await self._client.write_registers(address, [value], device_id=self._slave_id)
```

with:

```python
result = await self._client.write_register(address, value, device_id=self._slave_id)
```

---

## 3. Write-then-verify cycle

**Decision**: every write to an indoor unit is followed by a delayed read (`verify_delay_s`) of the same unit, then up to `verify_retries` extra reads if the value hasn't propagated yet.

### Why

The gateway acknowledges writes immediately (`0x10` response), but the indoor unit's read-side registers update asynchronously — depending on the parameter it can take 100 ms to a few seconds. Without verifying, the UI could permanently lag the hardware after a write, or worse, the user could think the value was applied when in fact the hardware ignored it (e.g. trying to set `mode=AUTO` on a unit with `B8=0`).

### State machine

```
[idle] → user changes a value
          ↓
   [pending] (last_write_status=pending)
       │  write attempted, awaiting verify_delay_s
       ↓
   read state ──── matches expected? ───→ [confirmed]
       │                  │
       │                  ↓ (if no, retry)
       │            verify_delay_s
       │                  ↓
       └─────── retried verify_retries times
                          │
                          ↓
                     [failed]
```

The `pending` overlay (a dict of `{field: expected_value}` per unit) is applied on top of the cached `ACDeviceState` whenever a property reads `get_display_state`. So during the verify window the UI shows the user's requested value, not the hardware's old one. Once verification succeeds or fails, the overlay is cleared and the UI reflects the actual hardware state.

### Tunables

Both `verify_delay_s` (default 2 s) and `verify_retries` (default 3) are exposed in the options flow. Slow units may need a longer delay; fast LAN setups can drop the delay to 0.5 s.

---

## 4. Off-pending TTL & bundled ON event

**Decision**: when a unit is OFF and the user makes UI changes (setpoint, fan, swing, mode), the integration **does not send anything to the gateway** immediately. Instead it accumulates the changes in an `_off_pending` dict for that unit and starts a TTL timer. When the user presses ON within the TTL, a **single 5-register write (40078..40082)** flushes all the accumulated changes together.

### Why off changes are local

The Hisense firmware ignores most parameter writes when the unit is off — sending `set_temperature(24)` to an off unit might or might not be reflected in the read-side register, and even if it is, it doesn't survive the next power cycle reliably. The wire-controller user model (which the integration mimics) is: configure everything you want, then press ON. The TTL exists so that an asynchronous polling read doesn't overwrite the user's accumulated changes back to the hardware values.

### Why a bundled write on ON

The manual section 4.2 explicitly recommends:

> "In code sending stop, operation mode setting, fan speed setting, swing louver position setting, temperature setting, it is recommended to use continuation write command 0x10 to complete all the setting as above, do not recommend to send one single setting mode as a command."

Empirically: writing the registers one by one was unreliable on our test unit. A single 5-register write was always honoured.

### ON-event preconditions

For the bundled write to be valid, **every one of the 5 fields** (`run`, `mode`, `fan`, `swing`, `setpoint`) must have a value somewhere — either from the cached `ACDeviceState` of that unit (populated by polling/refresh/previous verify reads) or from the off_pending accumulator. If any is missing the controller emits a persistent notification ("Set X first") and the write is skipped.

### State machine

```
unit off, user changes setpoint → off_pending = {setpoint:N}, timer t=30s
                                ↓
                          user changes fan → off_pending = {setpoint:N, fan:F}, timer reset
                                ↓
                  ┌─────────── user presses ON within TTL ───────────┐
                  ↓                                                   │
            bundled write 40078..40082 with {1, mode, fan, swing, temp}
                  ↓
            standard write-then-verify cycle on the 5-field expected state
                  │
                  └─────── if 30s elapses without ON: off_pending dropped, UI back to hardware values
```

There's also an **external-power-on detector**: if a polling read during the TTL window shows `is_running` flipped from false to true (someone else turned on the unit via the wire controller), the off_pending is discarded with reason `external_power_on_detected`. The UI snaps to whatever the hardware reports.

---

## 5. Polling vs. verify coordination

**Decision**: the polling loop **skips any unit that has an in-flight verify cycle** for that polling tick.

### Why

`async_write_and_verify` takes a per-unit asyncio.Lock and does its own reads during the verify window. If the polling loop also reads the same unit concurrently, both reads are serialised by the Modbus client lock anyway, but the polling read can race against the verify read and update `indoor_states` with the same (or differently-paced) data the verifier just read. The cleaner approach is: while a unit has `pending` non-empty, polling doesn't touch it. The verify is already reading frequently enough during the window.

For units **without** pending, polling reads them. Even if they have `off_pending`, polling still reads because the read serves the external-power-on detector (see §4).

### Implementation

```python
async def _polling_loop():
    cycle = 0
    while True:
        cycle += 1
        for idx in self.unit_indices:
            if self.pending.get(idx):       # verify in progress → skip
                continue
            await self._read_and_track_unit(idx)
            if self.poll_spacing_s > 0:
                await asyncio.sleep(self.poll_spacing_s)
        if cycle % self.poll_gateway_every_n == 0:
            ... # gateway + outdoor reads
        await asyncio.sleep(self.poll_interval_s)
```

---

## 6. Dynamic capabilities from function selection

**Decision**: `hvac_modes`, `fan_modes` and `supported_features` of each climate entity are computed **dynamically** from the function-selection register bits (40048..40067) that are part of `ACDeviceState`. They are not hard-coded as class attributes.

### Why

Each indoor unit can be configured at the firmware level to have different capabilities — some units have AUTO mode enabled, others don't; some are cooling-only; some have the fan locked. Exposing capabilities that the firmware silently ignores leads to bad UX (the user changes a value, the verify fails, they don't understand why).

### Mapping

| Function-selection bit (register, bit) | Effect when ON |
|----------------------------------------|----------------|
| `B5` (40049, bit 4) — Fixing of operating mode | `hvac_modes = [OFF, current_mode]` (mode is locked) |
| `B6` (40050, bit 5) — Fixing of setting temperature | Remove `TARGET_TEMPERATURE` from `supported_features` |
| `B7` (40050, bit 3) — Fixing of operation as cooling | `hvac_modes = [OFF, COOL]` |
| `B8` (40050, bit 2) — Automatic COOL/HEAT operation | Add `HEAT_COOL` to `hvac_modes` (otherwise hidden) |
| `B9` (40050, bit 7) — Fixing of fan speed | Remove `FAN_MODE`; `fan_modes = [current]` |
| `C1` (40048, bit 6) — Cool only | Remove `HEAT` from `hvac_modes` |
| `C5` (40052, bits 3–4) — Indoor fan Hi speed (multi-bit) | If 1 → add `high_high_1`; if 2 → add `high_high_2` |

The properties read `state.function_selection` every time HA queries them, so the capabilities update automatically on the next polling cycle after the function-selection bits change (typically after a wire-controller config + EEPROM clear).

### Bits explicitly NOT used as capabilities

- **EE** (Auto fan speed mode) and **CE** (Swing louver individual) at first looked like good candidates, but empirical testing showed they don't behave as on/off capability flags in our firmware. Some units have these bits at 0 but the function works fine via Modbus.
- The 16 input/output configuration bits (40063..40067) describe physical wiring, not Modbus behaviour.
- Lock-function bits (`Fb`, `FA`, `F9`, `F8`) are wire-controller locks; they're exposed as EXP diagnostic sensors but not consumed as capabilities by the climate entity.

---

## 7. Entity naming & identity

**Decision**: indoor units are named `ac_indoor_unit_<sys><addr>` with both fields zero-padded to two digits (e.g. `ac_indoor_unit_0013`).

### Why this format

The `host_system_number` (register 29) and `host_address_number` (register 30) are stable identifiers wire-encoded by the installation. Using them in the device name means the same physical unit always has the same HA device id across reinstalls, regardless of the `unit_index` (which depends on the order in which the gateway scanned the bus).

`unit_index` is still used internally for register-base arithmetic and is exposed as the `serial_number` field of `DeviceInfo` (so it shows up in the Device Info section as `40000`, `40091`, etc.).

---

## 8. Single TCP connection limitation

The i-Modkit firmware accepts **only one Modbus TCP client at a time**. This has two consequences:

1. **You cannot run two HA instances against the same gateway**. The integration assumes it owns the connection. If a second client (legacy v1 of this integration, a Python script, etc.) is also connected, the gateway forwards responses to all open sockets, and pymodbus discards them as TID mismatches — adding ~200 ms per round-trip in our measurements.
2. **There is no point parallelising reads**. The `asyncio.Lock` in `ACModbusClient` serialises all reads/writes. The polling loop reads units sequentially, one at a time, on purpose.

The "delay between pressing a button and the AC responding" issue mentioned in the README is entirely explained by point 1: the legacy v1 polling every 10 s adds spurious frames the new integration has to skip.

---

## 9. Logging & observability

Every write is logged at INFO level with the **user who triggered it** (resolved from `Context.user_id` via `hass.auth.async_get_user`). The format is:

```
WRITE unit=ac_indoor_unit_0000 user=alice expected={'setpoint': 24.0} delay=2.0s retries=3
WRITE unit=ac_indoor_unit_0000 user=alice sent, verifying...
VERIFY unit=ac_indoor_unit_0000 attempt=1/4 read={'setpoint': 24.0} match=True
WRITE_CONFIRMED unit=ac_indoor_unit_0000 user=alice attempts=1 expected={'setpoint': 24.0} actual={'setpoint': 24.0}
```

Polling reads:

```
READ_ALL user=system units=[0,1,2,3,4,5,6]
READ unit=ac_indoor_unit_0000 ok is_running=True mode=0x02 fan=0x01 setpoint=24.0 inlet=22.5
```

The `last_write_status` diagnostic sensor mirrors the above as a structured attribute dict, so automations can react to failed writes.

---

## 10. Test strategy

The integration is tested at three levels (see `tests/`):

- **`test_pyacmodbus.py`** (24 tests) — register-level parsing/encoding with a mocked pymodbus client. Covers each `read_*` decoding (signed temps, 0xFF setpoint, filter alarm bit), each write function, and the bit-shifting in `set_swing`.
- **`test_controller.py`** (33 tests) — the core lifecycle: scan, refresh, write-verify (confirm/fail/retries), off-pending TTL, external power-on discard, polling loop (skip during verify, gateway cadence, start/stop), user resolution.
- **`test_climate.py` / `test_sensor.py` / `test_binary_sensor.py` / `test_switch.py` / `test_select.py` / `test_button.py`** (~75 tests) — entity-level integration tests using HA's `pytest_homeassistant_custom_component` fixtures. Cover dynamic capabilities (B5/B6/B7/B8/B9/C1/C5), powered-on vs off routing, EXP bit & enum decoding, and service calls.
- **`test_config_flow.py` + `test_init.py`** (~10 tests) — config/options flow, setup/unload, retry on connect failure.

Run with `pytest tests/` — 137 tests, ~25 s.
