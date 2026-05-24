> 🌐 **English** · [Español](SDD.es.md)

# Hisense VRF — Software Design Document

**Version:** 1.0.0
**Last updated:** 2026-05-24
**Quality scale:** Platinum

---

## 1. Overview

`hisense_vrf` is a **Home Assistant custom integration** that controls Hisense VRF indoor units through an **i-Modkit Modbus TCP gateway** (model `HCPC-H2M1C`). It replaces the previous `acmodbus` (v1) integration, rewritten from scratch to fix write-and-verify bugs, adapt to the actual capabilities of each unit, and reach the Platinum tier of the [HA Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale).

### Goals

- **Correctness:** every user-initiated change is confirmed by reading the hardware back before being declared applied.
- **Observability:** every operation is logged with user attribution; a `last_write_status` sensor per unit exposes the most recent outcome.
- **Per-unit adaptability:** each climate entity's `hvac_modes`, `fan_modes`, and `supported_features` are derived dynamically from the 20 function-selection registers (40048-40067).
- **Operational resilience:** tolerant to transient gateway failures (timeouts, TID jitter, disconnects), with automatic recovery after restart.
- **Maintainability:** 193 tests at 96% coverage, mypy strict with 0 errors, 100% of user-facing strings translatable via `translation_key`.

### Non-goals

- No support for multiple gateways in a single config entry (one gateway = one config entry).
- No persistence of integration data outside HA's standard entity registry / recorder.
- `pyacmodbus` is not published to PyPI — it is bundled into the integration for HA OS deployments.

---

## 2. Architecture

### 2.1 Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                  Home Assistant frontend (Lovelace)              │
└─────────────────────────────────────────────────────────────────┘
                                ▲
                                │ entity state, services
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  hisense_vrf integration (custom_components/hisense_vrf/)        │
│                                                                  │
│  ┌──────────────┐  ┌────────────┐  ┌─────────────┐  ┌────────┐ │
│  │ climate.py   │  │ sensor.py  │  │ switch.py   │  │ ...    │ │
│  │ binary_*.py  │  │ select.py  │  │ button.py   │  │        │ │
│  └──────┬───────┘  └─────┬──────┘  └──────┬──────┘  └────────┘ │
│         └─────────┬──────┴────────────────┘                     │
│                   ▼                                              │
│           ┌─────────────────────┐                                │
│           │ HisenseVRFController │   (controller.py)             │
│           └──────────┬──────────┘                                │
│                      │                                            │
│                      ▼                                            │
│             ┌──────────────┐                                     │
│             │ ACModbusClient│   (bundled pyacmodbus/)            │
│             └──────────────┘                                     │
└──────────────────────│──────────────────────────────────────────┘
                       │ Modbus TCP (FC 0x03 / 0x10)
                       ▼
              ┌─────────────────┐
              │ i-Modkit gateway │ @ <gateway-ip>:502
              └────────┬────────┘
                       │ Hisense H-LINK proprietary bus
                       ▼
       ┌─────────────────────────────────┐
       │ N indoor units + M outdoor      │
       └─────────────────────────────────┘
```

### 2.2 Repository layout

```
homeassistant-v2/
├── custom_components/hisense_vrf/
│   ├── __init__.py              ← setup/unload, pyacmodbus bundle loader
│   ├── config_flow.py           ← ConfigFlow + OptionsFlow + reconfigure step
│   ├── controller.py            ← central logic (~750 lines)
│   ├── entity.py                ← base classes Indoor/Gateway/Outdoor entity
│   ├── climate.py               ← one climate entity per indoor unit
│   ├── sensor.py                ← ~50 sensors per indoor + outdoor + gateway
│   ├── binary_sensor.py         ← running / alarm / filter + EXP bits
│   ├── switch.py                ← power + 5 prohibit locks + gateway alarm
│   ├── select.py                ← louver + dry mode
│   ├── button.py                ← refresh/reset/lock/discover/eeprom buttons
│   ├── experimental.py          ← ExpBitBinarySensor / ExpEnumSensor (EXP)
│   ├── exp_descriptors.py       ← (auto-generated) 100 BIT + 3 ENUM EXP descriptors
│   ├── diagnostics.py           ← async_get_config_entry_diagnostics
│   ├── const.py                 ← DOMAIN, CONF_*, signals, thresholds
│   ├── manifest.json            ← version 1.0.0, quality_scale platinum
│   ├── quality_scale.yaml       ← per-rule status (Bronze + Silver + Gold + Platinum)
│   ├── strings.json             ← source of truth for translations
│   ├── icons.json               ← mdi:* mappings per translation_key
│   ├── translations/en.json     ← sync of strings.json
│   └── brand/                   ← icon.png + logo.png served locally
├── pyacmodbus-stub/
│   └── pyacmodbus/__init__.py   ← Modbus client + data model (~750 lines)
├── tests/                       ← 193 tests, 96% coverage
├── mypy.ini                     ← strict (with 3 suppressions for HA framework)
├── pyproject.toml
└── README.md
```

### 2.3 Exposed HA platforms

| Platform | Entities per unit | Notes |
|----------|-------------------|-------|
| `climate` | 1 (`ac_unit`) | the primary control entity |
| `sensor` | ~50 normal + ~3 EXP enum + 2 diagnostic | inlet/outlet temp, setpoint, op_state, alarm, fan_actual, expansion_valve, etc. |
| `binary_sensor` | 7 normal + 100 EXP bit | running, alarm, filter, swing_active, oil_return, test_run, remote_control_active |
| `switch` | 6 | power + 5 prohibit (on_off, mode, fan, swing, temp) |
| `select` | 2 | louver (auto + 0..7), dry_mode (dry1/dry2/dry3) |
| `button` | 4 | refresh_unit, reset_filter, lock_all, unlock_all |
| Gateway-level | 5 entities | unit_count, eeprom, alarm_display switch, refresh_all + eeprom_clear + discover buttons |
| Outdoor-level | ~25 sensors | temperatures, pressures, current, frequency, runtime, valve openings |

**Entity count scales per unit**: roughly **~170 per indoor** (of which ~103 are EXP, `disabled_by_default=True`) + **~25 per outdoor module** + 5 gateway-level. As a reference, a 7-indoor / 1-outdoor deployment registers ~1100 entities total, ~700 of them EXP and hidden by default.

---

## 3. Core modules

### 3.1 `controller.py` — `HisenseVRFController`

The central class that owns state and mediates between entities and the Modbus client. It **replaces the standard `DataUpdateCoordinator`** because the write-and-verify + off-pending + dynamic-devices logic doesn't fit the coordinator's polling-only pattern.

**State held:**

- `unit_indices: list[int]` — discovered units (offsets 0, 1, 2, 10, ...).
- `unit_identifiers: dict[int, (host_sys, host_addr)]` — used for `ac_indoor_unit_NNNN` naming.
- `unit_capacities: dict[int, int]` — used in device_info's `model` field.
- `outdoor_units: list[(sys, mod)]` — detected outdoor modules.
- `indoor_states: dict[int, ACDeviceState | None]` — last good read per unit (None while unavailable).
- `gateway_state: GatewayState | None`.
- `outdoor_states: dict[(sys, mod), OutdoorUnitState | None]`.
- `pending: dict[int, dict[str, Any]]` — active write-verify overlay.
- `_off_pending: dict[int, dict[str, Any]]` — accumulated changes while unit is OFF.
- `_off_timers: dict[int, TimerHandle]` — TTL timers.
- `last_write_status: dict[int, dict[str, Any]]` — backs the diagnostic sensor.
- `_unit_locks: dict[int, asyncio.Lock]` — serializes writes per unit (the global lock lives inside `ACModbusClient`).
- Counters: `_unit_read_failures`, `_gateway_read_failures`, `_unit_write_failures`, `_last_known_unit_count`.

**Public API:**

| Method | Purpose |
|--------|---------|
| `async_initial_scan()` | connect + scan + identifiers + capacity + outdoor + seed gateway baseline |
| `async_start_polling()` / `async_stop_polling()` | controls the background task |
| `async_refresh_all()` / `async_refresh_unit(idx)` | on-demand reads (buttons) |
| `async_write_and_verify(idx, pending, verify_fn, write_fn)` | core write with verify+retry |
| `async_send_on_with_pending(idx, mode_override)` | bundled write for ON event with off-pending |
| `accumulate_off_pending(idx, attrs, user)` | append to off-pending queue + (re)start TTL |
| `get_display_state(idx)` | state with pending + off_pending overlays applied (for UI) |
| `is_field_pending(idx, field)` | for `assumed_state` on entities |
| `unit_name(idx)` / `unit_model(idx)` / `unit_register_base(idx)` | naming helpers |
| `async_shutdown()` | cancel timers + disconnect (called on unload) |

**Dispatcher signals emitted:**

- `SIGNAL_UPDATE` (`hisense_vrf_update_{entry_id}`): any state change → all entities refresh.
- `signal_new_indoor(entry_id)`: a new unit was detected (gw_unit_count changed) → platforms create entities hot.
- `signal_new_outdoor(entry_id)`: a new outdoor module appeared.

### 3.2 `pyacmodbus/__init__.py` — Modbus client + data model

Stub library bundled with the integration (not on PyPI). It encapsulates:

- **`ACModbusClient`**: wrapper around `AsyncModbusTcpClient` with a global `asyncio.Lock` (the gateway accepts only one TCP connection at a time). Methods:
  - `connect()`, `disconnect()`, `_require_client()`.
  - `scan_devices()` → list[int] of unit indices with `unit_code != 0`.
  - `read_device(idx)` → `ACDeviceState`.
  - `read_unit_identifiers(idx)`, `read_unit_capacity(idx)`.
  - `read_gateway()`, `read_outdoor_connections()`, `read_outdoor_unit(sys, mod)`.
  - `turn_on(idx)`, `turn_off(idx)`, `set_setpoint(idx, t)`, `set_mode(idx, m)`, `set_fan_speed(idx, f)`, `set_swing(idx, auto, pos)`, `set_dry_mode(idx, v)`, `set_prohibition(idx, reg, on)`, `lock_all(idx)`, `unlock_all(idx)`, `reset_filter(idx)`.
  - `write_control_block(idx, run, mode, fan, swing, temp)` — Modbus 0x10 frame writing 5 registers for the bundled ON event.
  - `set_alarm_display(on)`, `clear_eeprom()` (gateway-level).
- **Data model** (`@dataclass`): `ACDeviceState`, `GatewayState`, `OutdoorUnitState`.
- **Constants**: register offsets, mode bitmasks, fan bitmasks, alarm codes, base address (`BASE_ADDR=40000`, `UNIT_STRIDE=91`).
- **Exceptions**: `CannotConnect`, `ModbusReadError`.

**Configurable timeout** `_MODBUS_TIMEOUT_S = 1.5` (vs pymodbus default 3 s) — reduces the worst-case initial scan time from ~54 s to ~27 s.

### 3.3 `experimental.py` + `exp_descriptors.py` — EXP entities

103 **diagnostic** entities per unit exposing individual bits and enums of the function-selection registers (40048-40067). Descriptors are auto-generated from a mapping table; a single generic Python class (`ExpBitBinarySensor` / `ExpEnumSensor`) instantiates them with `translation_key` and `disabled_default=True`.

**Rationale:** EXP entities are useful for diagnostics but rarely change. Since they add ~700 entities and bloat the recorder DB, they default to *disabled*. The user enables them individually from Settings → Devices when needed.

### 3.4 `config_flow.py`

- **User step:** host + port + parameters (`verify_delay_s`, `verify_retries`, `off_pending_ttl_s`, `polling_enabled`, `poll_interval_s`, `poll_spacing_s`, `poll_gateway_every_n_cycles`).
- **Reconfigure step:** lets the user change host/port without recreating the entry (Gold rule).
- **Options flow:** runtime tweak of parameters (without host/port — those go through reconfigure).
- **Unique ID:** `{host}:{port}` (Bronze rule).

---

## 4. Key flows

### 4.1 Initial setup

```
async_setup_entry()
  ├─ ACModbusClient(host, port, timeout=1.5)
  ├─ controller = HisenseVRFController(...)
  ├─ controller.async_initial_scan()
  │   ├─ client.connect()
  │   ├─ scan_devices() → [0, 1, 2, 3, 4, 5, 6]
  │   ├─ for idx: read_unit_identifiers(idx) → (host_sys, host_addr)
  │   ├─ for idx: read_unit_capacity(idx) → kBTU
  │   ├─ read_outdoor_connections() → [(0,0)]
  │   ├─ read_gateway() → seed _last_known_unit_count
  │   └─ _prune_stale_devices()
  ├─ entry.runtime_data = controller
  ├─ async_forward_entry_setups(PLATFORMS) → 6 platforms create entities
  └─ controller.async_start_polling() (if polling_enabled)
```

Failure modes:
- `CannotConnect` → `ConfigEntryNotReady("Cannot connect to host:port")` → HA retries with backoff.
- `ModbusReadError` at any step → idem.

### 4.2 Polling loop

```python
while True:
    cycle += 1
    for idx in unit_indices:
        if pending[idx]: skip   # verify in progress, don't trample
        await _read_and_track_unit(idx)
        if poll_spacing_s > 0: await sleep(poll_spacing_s)
    if cycle % poll_gateway_every_n_cycles == 0:
        await _read_and_track_gateway()
        for ou in outdoor_units:
            await read_outdoor_unit(ou)
    _notify()
    await sleep(poll_interval_s)
```

`_read_and_track_unit` and `_read_and_track_gateway` increment consecutive-failure counters; on hitting `UNAVAILABLE_THRESHOLD=3`, the entity becomes `unavailable` and a WARNING `UNAVAILABLE` is logged. On the first successful read after that, an `AVAILABLE recovered` line is logged.

`_read_and_track_gateway` also triggers `_rescan_for_dynamic_devices` when `gw_unit_count` differs from `_last_known_unit_count`.

### 4.3 Write-and-verify (unit ON)

```
async_write_and_verify(idx, pending_attrs, verify_fn, write_fn)
  ├─ acquire unit_lock
  ├─ pending[idx].update(pending_attrs)          # active UI overlay
  ├─ last_write_status[idx] = PENDING + notify()
  ├─ try:
  │     await write_fn()                          # Modbus write
  │   except ModbusReadError:
  │     _clear_pending; last_write_status=FAILED
  │     _on_write_failed(idx)                     # counter for repair issue
  │     return False
  ├─ for attempt in 1..verify_retries+1:
  │     await sleep(verify_delay_s)
  │     state = await read_device(idx)
  │     if verify_fn(state):
  │         _clear_pending; last_write_status=CONFIRMED
  │         _on_write_confirmed(idx)              # reset counter, delete repair
  │         return True
  └─ _clear_pending; last_write_status=FAILED
     _on_write_failed(idx)                         # 3 consecutive failures → repair issue
     return False
```

### 4.4 Off-pending + bundled ON

When the user changes something while the unit is OFF, `accumulate_off_pending(idx, attrs)` stores it in `_off_pending[idx]` and arms a `loop.call_later(off_pending_ttl_s, expire)`.

On an ON request (climate.set_hvac_mode != OFF, or switch.power.on, or turn_on):

```
async_send_on_with_pending(idx, mode_override?)
  ├─ pick mode, fan, setpoint, swing from off_pending or state
  ├─ if any missing → persistent_notification + return False
  ├─ swing_reg = encode(auto, position)
  ├─ flush off_pending (cancel timer)
  └─ async_write_and_verify(idx, ..., write_fn=client.write_control_block(idx, 1, mode, fan, swing_reg, temp))
```

`write_control_block` issues a single Modbus 0x10 frame with the 5 control registers (40078-40082). This is what the i-Modkit manual recommends to power on a unit with a target configuration: one atomic operation instead of 5 individual writes.

### 4.5 Dynamic devices

On every gateway poll, `_read_and_track_gateway` compares `new_state.unit_count` with `_last_known_unit_count`. On mismatch → `_rescan_for_dynamic_devices()`:

```
_rescan_for_dynamic_devices()
  ├─ current_indoor = await client.scan_devices()
  ├─ current_outdoor = await client.read_outdoor_connections()
  ├─ for new_idx in current_indoor - known_indoor:
  │     read_unit_identifiers + read_unit_capacity
  │     unit_indices.append(new_idx)
  │     dispatcher.async_dispatcher_send(signal_new_indoor(entry_id), new_idx)
  └─ same for outdoor
```

Each platform's `async_setup_entry` subscribes to the signal and creates entities hot via `async_add_entities(...)`. Removals are NOT handled here — they go unavailable and `_prune_stale_devices` cleans them on the next reload.

### 4.6 Repair issues

`_on_write_failed(idx)` increments `_unit_write_failures[idx]`. Once it reaches `WRITE_FAILED_ISSUE_THRESHOLD=3` consecutive failures:

```python
ir.async_create_issue(
    hass, DOMAIN, f"write_failed_{entry_id}_{idx}",
    is_fixable=False, severity=ir.IssueSeverity.WARNING,
    translation_key="write_failed_repeated",
    translation_placeholders={"unit_name": ..., "threshold": "3"},
)
```

The next `_on_write_confirmed(idx)` clears the issue with `ir.async_delete_issue`. The translated description lists the 5 typical causes (locks, alarm, cooldown, outdoor capacity, wire controller).

---

## 5. Design decisions and trade-offs

### 5.1 Why not use the standard `DataUpdateCoordinator`

The standard HA coordinator assumes a poll-only pattern with `_async_update_data() → dict`. Our flow requires:
- Polls + on-demand reads + writes + verify cycles **under the same lock**.
- Intermediate state (`pending`, `_off_pending`) consumed by entities via `assumed_state`.
- Optimistic UI overlay (`get_display_state`) merging hardware + pending.
- Per-entry-scoped dispatcher signals for new-device hot-add.

Implementing all that on top of the coordinator added complexity. The `HisenseVRFController` class is ~750 lines and encapsulates everything in a self-explanatory way.

### 5.2 Write-and-verify vs optimistic

Popular alternative: mark the command as successful as soon as it's sent and let normal polling correct any drift. We dropped it because:

- VRF units sometimes **silently reject commands** (locks, alarm, outdoor capacity) without the Modbus frame failing. Without verify, the user never finds out the change wasn't applied.
- `last_write_status` is valuable for troubleshooting — it would be opaque without a verify cycle.

Trade-off: each user action takes `verify_delay_s × (verify_retries + 1)` to return (default = 2s × 4 = 8s worst case). Acceptable.

### 5.3 Off-pending TTL instead of immediate write

Hisense **doesn't accept** mode/fan/setpoint changes while a unit is OFF. If the user moves the thermostat to 24 °C with the AC off, the setpoint in hardware stays at the last known value. On pressing ON, the unit boots up with a stale setpoint.

The off-pending solution: accumulate changes in memory and ship them in a **bundled atomic write** at ON time. The user adjusts everything while OFF, presses ON once, and the AC starts with the requested config.

TTL=30s avoids forgotten changes being applied hours later.

### 5.4 EXP entities disabled-by-default

The 100 BIT + 3 ENUM EXP entities per unit are diagnostic — they expose bits of the function-selection registers. Useful to understand "why doesn't AUTO work on this unit" or "what does this undocumented bit do". But the average user doesn't inspect them.

Up to v0.x they were enabled. Result on a representative multi-indoor deployment: hundreds of extra entities × silent state changes caused noticeable recorder DB growth and cold startup taking close to two minutes. In v1.0.0 they switched to `disabled_default=True`; users opt into the specific EXP bits they care about. The same deployment saw cold startup drop by roughly an order of magnitude.

### 5.5 Naming `ac_indoor_unit_<host_sys><host_addr>`

The user requested this convention over the more natural `ac_indoor_unit_<index>`. The reason is operational: `host_sys` + `host_addr` are the physical IDs of the unit on the H-LINK bus (visible from the wire controller). Browsing entities, the user sees names that correspond to real devices, not to discovery order (which can change).

Trade-off: if a unit's physical address changes, the entity_id doesn't migrate automatically — it needs a remove + re-add of the config entry.

### 5.6 pymodbus timeout 1.5 s (vs 3 s default)

The gateway typically responds in <500 ms. The default 3 s was overly conservative for our use case, multiplying setup worst-case. 1.5 s leaves ~3× safety margin. If spurious `ModbusReadError` start showing up in normal operation, bumping it back up is the simplest fix.

### 5.7 `pyacmodbus` bundling

`pyacmodbus` is not on PyPI. For HA OS production, the library is copied inside `custom_components/hisense_vrf/pyacmodbus/`. The integration's `__init__.py` uses `importlib.util.spec_from_file_location` to load it by path **without touching `sys.path`** (this avoids shadowing stdlib modules like `select`).

Trade-off: every deploy copies two paths. The manifest **does not** declare `requirements=["pyacmodbus"]` because that triggers a PyPI install that fails. If the library is ever published, the bundle goes away and the requirement becomes standard.

### 5.8 Strict typing with 3 exceptions

`mypy.ini` enables full strict mode except for:
- `disallow_subclassing_any = False`
- `disallow_untyped_decorators = False`
- `disallow_any_generics = False`

HA's base entity classes (`ClimateEntity`, `SensorEntity`, etc.) and decorators like `@callback` appear as `Any` to mypy because it can't fully resolve framework types. HA core itself disables these flags for integrations — we follow the same approach.

---

## 6. Register map (summary)

### 6.1 Indoor units

`base = 40000 + unit_index × 91`

| Offset | Reg | Type | Notes |
|--------|-----|------|-------|
| 0 | UNIT_CODE | uint16 | 0 = empty slot |
| 1 | CAPACITY | uint16 | × 8 = BTU/1000 |
| 2 | STATUS | bitmask | bit0=running, bit1-2=op_state, bit3=oil_return |
| 3 | CURR_MODE | bitmask | MODE_AUTO/COOL/DRY/FAN/HEAT |
| 4 | FAN_SW | bitmask | FAN_AUTO/HIGH/MED/LOW |
| 5 | MODE_JUMP | bitmask | running mode (may differ during transition) |
| 6 | FAN_JUMP | bitmask | running fan |
| 7 | MISC | bitmask | swing_active, full_heat_exchange, auto_swing, louver_pos |
| 8 | SETPOINT_R | uint8 | °C, 0xFF = not set |
| 9-12 | COOL/HEAT MAX/MIN | uint16 | per-mode temperature limits |
| 17 | TEMP_TRMT | int16 | remote control temp (signed) |
| 19 | OUTLET_TEMP | int16 | °C |
| 20 | INLET_TEMP | int16 | °C |
| 22 | ALARM | uint16 | alarm code (decimal of the documented hex value) |
| 28 | FAN_ACTUAL | bitmask | actual speed (HH2, HIGH, MED, MEDLOW, LOW, MUTE, BREEZE) |
| 48-67 | FUNCTION_SELECTION | uint8 × 20 | decoded as EXP entities |
| 78 | RUN_STOP | R/W | 0=stop, 1=run |
| 79-82 | SET_MODE/FAN/SWING/TEMP | R/W | targets |
| 84-89 | PROHIBIT_* | R/W | locks (1=block, 0/2=clear) |

### 6.2 Gateway

| Reg | Constant | R/W |
|-----|----------|-----|
| 4996 | ALARM_DISPLAY | R/W |
| 4997 | UNIT_COUNT | R |
| 4998 | CTRL_MODE | R (1 = Modbus active) |
| 4999 | EEPROM_CLEAR | R/W |

### 6.3 Outdoor

- **Connections:** regs 1000-1005 (one per system 0..5; each bit = one module).
- **Data:** `base = 5000 + system × 490 + module × 98`. Offsets 0-14 ASCII model name, 15 capacity code, 16 ID, 17-53 operational parameters (zero when compressor inactive).

---

## 7. Testing

### 7.1 Structure

```
tests/
├── conftest.py                ← shared fixtures: mock_client, config_entry, setup_integration
├── __init__.py                ← helpers: make_indoor_state, make_gateway_state, make_outdoor_state, indoor_uid, gateway_uid, outdoor_uid
├── test_pyacmodbus.py         ← 24 tests of the Modbus client
├── test_controller.py         ← 51 tests: poll, write-verify, off-pending, debounce, dynamic-devices, repair, stale-devices
├── test_climate.py            ← HVAC actions
├── test_sensor.py             ← indoor + outdoor + gateway sensors + EXP enum
├── test_binary_sensor.py      ← running, alarm, filter, gateway, EXP bit
├── test_switch.py             ← power + prohibits + gateway alarm
├── test_select.py             ← louver + dry mode
├── test_button.py             ← refresh, reset_filter, lock, eeprom, discover
├── test_init.py               ← setup_entry happy/error, dynamic-devices end-to-end
├── test_config_flow.py        ← config + options + reconfigure flows
├── test_diagnostics.py        ← async_get_config_entry_diagnostics
└── test_smoke.py              ← imports + manifest sanity
```

**193 tests, 30 s wall-clock, 96% coverage** (`pytest --cov`).

### 7.2 Key patterns

- **Client mocking**: every pymodbus method is an `AsyncMock` with sensible defaults (in `conftest.py`).
- **`setup_integration` fixture**: `yield` inside `with patch(...)` to keep the mock active during reloads triggered by the options flow.
- **Entity_id discovery**: `er.async_get(hass).async_get_entity_id(platform, DOMAIN, uid)` with `indoor_uid(idx, suffix)` — robust to translation changes.
- **Polling disabled by default in tests**: `CONF_POLLING_ENABLED: False` in the `config_entry` fixture; avoids lingering tasks that would break other tests.

---

## 8. Quality scale — per-level status

### Bronze (18/18)
- ✅ action-setup (exempt), appropriate-polling, brands (exempt — custom_component), common-modules, config-flow, config-flow-test-coverage, dependency-transparency, docs-* (high-level, install, removal), entity-event-setup, entity-unique-id, has-entity-name, runtime-data, test-before-configure, test-before-setup, unique-config-entry, docs-actions (exempt).

### Silver (10/10)
- ✅ action-exceptions (exempt), config-entry-unloading, docs-config-params, docs-install-params, entity-unavailable, integration-owner, log-when-unavailable, parallel-updates, reauthentication-flow (exempt — Modbus TCP has no auth), test-coverage.

### Gold (21/21)
- ✅ devices, diagnostics, discovery (exempt), discovery-update-info (exempt), docs-data-update, docs-examples, docs-known-limitations, docs-supported-devices, docs-supported-functions, docs-troubleshooting, docs-use-cases, dynamic-devices, entity-category, entity-device-class, entity-disabled-by-default, entity-translations, exception-translations (exempt), icon-translations, reconfiguration-flow, repair-issues, stale-devices.

### Platinum (3/3)
- ✅ async-dependency, inject-websession (exempt — no HTTP), strict-typing.

---

## 9. Deploy

### 9.1 Dev → Prod

```bash
# From the project root, with /Volumes/config/ mounted
cp -r custom_components/hisense_vrf/* /Volumes/config/custom_components/hisense_vrf/
cp -r pyacmodbus-stub/pyacmodbus /Volumes/config/custom_components/hisense_vrf/
rm -rf /Volumes/config/custom_components/hisense_vrf/__pycache__ \
       /Volumes/config/custom_components/hisense_vrf/pyacmodbus/__pycache__
```

After: reload the integration from the UI **or** restart HA prod if `pyacmodbus/` was touched (Python module cache).

### 9.2 Automated restart via API

The script in `/tmp/disable_exp.py` shows the pattern:
1. Trigger `POST /api/services/homeassistant/restart`
2. Poll HTTP until shutdown is detected (HTTP 000)
3. Sleep ~8 s for the final registry flush
4. (Edit the registry if needed, while HA is down)
5. Poll until HTTP 200

A long-lived token is required (Authorization Bearer header).

---

## 10. Future work

Known non-urgent items:

- **Publish `pyacmodbus` to PyPI** → drop the bundle + `sys.path` workaround.
- **Recorder excludes** for EXP entities the user reactivates — currently nothing is excluded, so re-enabling an EXP brings its history back to the DB.
- **Recorder DB hygiene** — for deployments where the DB has grown large (typically because EXP entities were enabled), configure `recorder.purge_keep_days` and trigger `recorder.purge` to reclaim space.
- **HACS release** once `pyacmodbus` is on PyPI.
- **Outdoor `compressor_active`**: when all operational registers 17-53 are zero, return `None` instead of raw 0 (cosmetic).
- **Energy sensor**: `current_primary × known_voltage → kW` for HA's energy dashboard.
