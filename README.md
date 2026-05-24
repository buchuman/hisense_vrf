# Hisense VRF — Home Assistant Integration

[![quality-scale](https://img.shields.io/badge/quality__scale-platinum-9c27b0)](https://developers.home-assistant.io/docs/core/integration-quality-scale)
[![version](https://img.shields.io/badge/version-1.0.0-blue)](custom_components/hisense_vrf/manifest.json)

Home Assistant custom integration to control Hisense VRF indoor units through an **i-Modkit Modbus TCP gateway** (model `HCPC-H2M1C`).

This is the **v2 rewrite** of the original `acmodbus` integration, focused on correctness, observability and adapting to the firmware-level capabilities of each unit.

## Quality scale: Platinum

The integration meets every applicable rule of the Home Assistant [Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale) up to and including **Platinum** (the highest tier). See [`custom_components/hisense_vrf/quality_scale.yaml`](custom_components/hisense_vrf/quality_scale.yaml) for the rule-by-rule status. Notable concrete consequences:

- **Strict typing**: passes `mypy --strict` (15 source files, 0 errors).
- **Translations**: all entity names, states, options, error messages and repair issues are translation-key based — no hardcoded English in entity code.
- **Icons**: assigned per `translation_key` via [`icons.json`](custom_components/hisense_vrf/icons.json) for ~60 entities.
- **Dynamic devices**: new indoor units appearing on the bus are discovered at runtime (no reload) by watching the gateway's `unit_count` register.
- **Reconfiguration flow**: host/port can be changed in-place without removing the entry.
- **Repair issues**: 3 consecutive failed writes to the same unit raise a translated, actionable repair notification.
- **Diagnostics + 193 tests** across 11 test files with 96% line coverage.

## Features

- **Climate control per indoor unit** — temperature, fan speed, swing mode, on/off, HVAC mode.
- **Write-then-verify** for every change — the integration confirms each write by re-reading the unit after a configurable delay and retries the read up to N times if the value hasn't propagated yet. Status is exposed as a diagnostic sensor.
- **Off-pending accumulator** — when the unit is off, UI changes are kept locally for a configurable TTL (default 30 s). Pressing ON within that window flushes them all in a single Modbus 0x10 frame (the bundled write recommended by the manual).
- **Background polling** — configurable interval; reads each unit on a fixed cadence so external changes (wire controller, other apps) are reflected in HA. Polling skips units that have an in-flight write so it never competes with the verify cycle.
- **Dynamic capabilities** — `hvac_modes`, `fan_modes` and `supported_features` of each climate entity adapt to the function-selection bits read from the unit's firmware:
  - `B8` enables `HVACMode.HEAT_COOL` (Auto).
  - `C1` removes `HVACMode.HEAT` (cooling-only unit).
  - `B7` locks the unit to cooling only.
  - `B5` locks the operating mode (only the current mode remains selectable).
  - `B6` removes `TARGET_TEMPERATURE` (temp fixed).
  - `B9` removes `FAN_MODE` (fan locked).
  - `C5` adds `high_high_1` / `high_high_2` to `fan_modes`.
- **EXP diagnostic entities** — 103 read-only sensors per indoor unit exposing every documented bit of the 20 function-selection registers (40048-40067). Useful for debugging "why doesn't AUTO work" or "why is fan locked" without inspecting raw Modbus.
- **Outdoor unit sensors** — temperatures, pressures, runtime, currents, valve openings.
- **Gateway entities** — alarm display switch, unit count, ctrl-mode indicator, EEPROM clear button.
- **Per-user attribution** in logs and in the `last_write_status` diagnostic sensor.
- **Discovery on demand** — the integration only fetches metadata on setup; full state is loaded by the polling loop or by the `Refresh all units` button.

## Installation

### Quick install via custom_components symlink (dev)

If you have the Home Assistant `config/` directory directly accessible (e.g. local dev setup), symlink the integration:

```bash
ln -sfn /path/to/homeassistant-v2/custom_components/hisense_vrf \
        /path/to/ha_config/custom_components/hisense_vrf
```

And install the `pyacmodbus` library in your HA Python environment:

```bash
pip install -e /path/to/homeassistant-v2/pyacmodbus-stub/
```

Restart Home Assistant. The integration appears under **Settings → Devices & Services → Add Integration → Hisense VRF**.

### HA OS production deploy (with bundling)

Since `pyacmodbus` isn't on PyPI yet, it has to be bundled into the custom component for HA OS installs:

```bash
# Copy the integration files
cp -r homeassistant-v2/custom_components/hisense_vrf/* /Volumes/config/custom_components/hisense_vrf/

# Bundle the library inside the integration directory
cp -r homeassistant-v2/pyacmodbus-stub/pyacmodbus /Volumes/config/custom_components/hisense_vrf/
```

The integration's `__init__.py` uses `importlib.util.spec_from_file_location` to load the bundled `pyacmodbus` if it isn't pip-installed in the runtime. No `sys.path` manipulation is needed (this avoids shadowing stdlib modules like `select`).

Restart HA after the first deploy. On subsequent updates, you can reload from **Settings → Devices & Services → Hisense VRF → ⋮ → Reload**. If the reload doesn't pick up code changes (Python cache), delete `__pycache__` under the integration dir before reloading.

## Use cases

This integration turns an HVAC system that historically lived inside a wired wall controller into a first-class HA citizen. Typical things you can do once it's installed:

- **Dashboard control**: a `climate` card per indoor unit with full setpoint / mode / fan / swing control, just like any other HA climate.
- **Area-level automations**: `climate.turn_off` targeted to an `area:` or `floor:` to switch off every Hisense unit in a room or zone in one call.
- **External temperature sensors**: feed an external thermometer (Zigbee, BLE, etc.) into a temperature template or generic_thermostat that drives the Hisense climate — useful when the indoor unit's own sensor is poorly placed.
- **Schedules**: shut all units off automatically at bedtime or when nobody is home, pre-cool the bedroom 30 min before sleep.
- **Alarm visibility**: the `Alarm` binary sensor and `Alarm description` sensor expose firmware-level faults (e.g. communication errors, sensor faults, compressor protection) so a notification can fire when the AC has trouble — long before someone walks into a warm room.
- **Energy/runtime tracking**: the outdoor unit's `Cumulative runtime` sensor lets you build a long-term graph of how many hours the compressor actually runs each day, which is more useful than raw electricity if you don't have per-circuit metering.
- **Diagnostic visibility**: the EXP function-selection sensors show **why** the unit behaves the way it does (which features are firmware-enabled), which is invaluable when an "AUTO" mode silently doesn't work.

## Supported devices

The integration talks to **any indoor unit reachable through a Hisense i-Modkit HCPC-H2M1C gateway**. The Modbus register layout is per-unit, so the indoor model is transparent to HA — what matters is the firmware version of the i-Modkit and the wire-controller configuration.

Tested in production:

| Component | Model | Notes |
|-----------|-------|-------|
| Gateway | i-Modkit HCPC-H2M1C | Tested on a multi-indoor / single-outdoor deployment running the firmware that ships pre-installed; capability bit B8 (Auto cool/heat) was OFF on all units. |
| Indoor units | Mixed Hisense VRF cassettes / wall-mounted | Capacities seen in testing: 8, 10, 16, 22 kBTU/h. Other capacities supported by the firmware should work without changes. |
| Outdoor unit | HiSmart H5 family | Operational registers (offsets 17–53) reported as zero — see "Limitations". |

Other Hisense VRF setups using the same gateway model should work without changes. Different gateway models (e.g. KNX, BACnet) are **not** supported.

## Examples

Below are short YAML snippets to drop into `configuration.yaml` or the automations editor. Adjust the entity IDs to match your install (`ac_indoor_unit_<sys><addr>`).

### Turn everything off when nobody is home

```yaml
automation:
  - alias: "AC off when away"
    triggers:
      - trigger: state
        entity_id: zone.home
        to: "0"   # last person leaves
    actions:
      - action: climate.turn_off
        target:
          entity_id:
            - climate.ac_indoor_unit_0000
            - climate.ac_indoor_unit_0001
            - climate.ac_indoor_unit_0002
            - climate.ac_indoor_unit_0010
            - climate.ac_indoor_unit_0011
            - climate.ac_indoor_unit_0012
            - climate.ac_indoor_unit_0013
```

### Pre-cool the bedroom 30 min before sleep

```yaml
automation:
  - alias: "Bedroom pre-cool"
    triggers:
      - trigger: time
        at: "22:30:00"
    actions:
      - action: climate.set_temperature
        target:
          entity_id: climate.ac_indoor_unit_0001
        data:
          hvac_mode: cool
          temperature: 22
```

### Notify when any indoor unit reports an alarm

```yaml
automation:
  - alias: "Notify on Hisense alarm"
    triggers:
      - trigger: state
        entity_id:
          - binary_sensor.ac_indoor_unit_0000_alarm
          - binary_sensor.ac_indoor_unit_0001_alarm
        to: "on"
    actions:
      - action: notify.mobile_app_my_phone
        data:
          title: "AC alarm"
          message: >-
            {{ trigger.entity_id }} reported:
            {{ states('sensor.' + trigger.entity_id.split('.')[1].replace('_alarm', '_alarm_description')) }}
```

### Daily runtime summary card (lovelace)

```yaml
type: entities
title: AC runtime
entities:
  - entity: sensor.outdoor_unit_0_0_cumulative_runtime
    name: Compressor hours
```

### Force a fresh read of every unit (e.g. after manual changes via wire controller)

```yaml
script:
  refresh_all_acs:
    sequence:
      - action: button.press
        target:
          entity_id: button.hisense_vrf_gateway_refresh_all_units
```

## Configuration

When you add the integration the form asks for these values; all of them are editable later via **Configure**:

| Parameter                    | Default | Range          | Meaning                                                                 |
|------------------------------|---------|----------------|-------------------------------------------------------------------------|
| Host                         | —       | —              | IP or hostname of the i-Modkit gateway.                                |
| Port                         | 502     | 1–65535        | Modbus TCP port.                                                       |
| Verification delay (s)       | 2.0     | 0.2–30         | Seconds to wait after a write before re-reading the unit.              |
| Verification retries         | 3       | 0–20           | Extra reads attempted if the value doesn't match yet.                  |
| Off-pending TTL (s)          | 30      | 5–600          | How long off-pending UI changes are kept before being discarded.       |
| Enable polling               | true    | —              | Toggle the background polling without uninstalling.                    |
| Polling interval (s)         | 5       | 0–3600         | Pause between polling cycles. 0 means cycles run back-to-back.         |
| Polling spacing (s)          | 0       | 0–10           | Pause between consecutive unit reads inside one cycle.                 |
| Poll gateway every N cycles  | 10      | 1–1000         | The gateway and outdoor module are slower-moving data; default is read every 10 polling cycles (~50 s with the default interval). |

## Usage

After adding the integration each indoor unit appears as a device named `ac_indoor_unit_<sys><addr>` (two-digit zero-padded). For example `ac_indoor_unit_0000`, `ac_indoor_unit_0013`. The device contains:

- **Controls section**: the `climate` entity (main control surface).
- **Sensors section**: temperatures (inlet, outlet, setpoint), expansion valve %, required frequency, fan actual, op_state, alarm code + description, etc.
- **Configuration section**: power switch, lock-all/lock-* prohibitions, dry mode select, louver select, refresh-this-unit button, reset filter alarm, lock/unlock all, etc.
- **Diagnostic section**: register base address, last write status, EXP bits (around 100 entries reflecting the function-selection bits — collapsed by default).

The gateway is a separate device named **Hisense VRF Gateway** with: `Refresh all units` button (force a full read on-demand), `Clear EEPROM` (used after writing function-selection registers), `Discover devices` (re-runs the initial scan), `Modbus control mode` binary sensor, `Connected indoor units` count, `EEPROM status`.

## Troubleshooting

### "Cannot connect to gateway"

- Verify the host/port and that you can reach the gateway from the HA host: `nc -zv <host> 502`.
- The i-Modkit firmware allows only one Modbus TCP client at a time. If another integration (e.g. v1 of this project) is already connected, your second client will be rejected or fight for the socket. Disable the other integration first.

### Writes "confirmed" in HA but the AC doesn't physically change

- Check the `Last write status` diagnostic sensor — the registers may be accepting the value but the unit itself ignores it because of a function-selection capability (e.g. `B5 Fixing of operating mode` blocks mode changes).
- Look at the EXP diagnostic entities for that unit; the relevant `Fixing of *` bit being ON is the most common cause.

### `HVACMode.HEAT_COOL` (Auto) doesn't appear in the climate selector

- Auto mode requires `B8 Automatic COOL/HEAT operation` to be enabled in the function selection (register 40050 bit 2). The integration removes `HEAT_COOL` from `hvac_modes` until that bit is set, because writing `mode=0x01` on a unit with `B8=0` is silently ignored by the firmware.
- To enable it: stop the unit, write the 20 function-selection registers (40048-40067) preserving everything except bit 2 of 40050, then press the `Clear EEPROM` button on the gateway device and reload the integration.

### Delay between pressing a button in HA and the AC responding

- ~50 ms per Modbus round-trip in a clean LAN.
- If you have a second Modbus client (e.g. legacy `acmodbus` v1) polling the gateway in parallel, expect ~250 ms per round-trip because pymodbus is skipping spurious frames from the other client. The integration logs a `Skipping` warning when this happens.

### Stale frames in the log (`ERROR: request ask for transaction_id=X but got id=Y, Skipping.`)

- These are responses to **another** client's request that the gateway echoed to all open sockets. Pymodbus drops them. Harmless but they signal that there's another consumer of the gateway, which adds latency.

## Architecture & Internals

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the design decisions: why the TID patch was removed, why we use Modbus 0x10 instead of 0x06, the write-then-verify cycle, the off-pending TTL state machine, polling vs. verify coordination, and the dynamic-capabilities mechanism.

The register map is documented in [`REGISTERS.md`](REGISTERS.md).

## Brand assets (logo/icon)

The integration declares the domain `hisense_vrf`. For HA to show a brand logo/icon in the UI (config flow, device cards, etc.) the same name must exist under `core_integrations/hisense_vrf/` in the [home-assistant/brands](https://github.com/home-assistant/brands) repository.

To add it (one-time step, once the integration is publicly released):

```
core_integrations/hisense_vrf/
├── icon.png    # 256×256, transparent background
└── logo.png    # 256×256 or wider, the official Hisense / project logo
```

Submit a PR to that repo following its README (the bot validates dimensions, filename, transparent background). Until that PR is merged, HA shows a generic placeholder icon — functionality is unaffected.

## Quality scale

The integration self-declares **Platinum** (the highest tier) in its manifest. The full per-rule status is in [`custom_components/hisense_vrf/quality_scale.yaml`](custom_components/hisense_vrf/quality_scale.yaml); a deeper walk-through of how each rule is met (strict typing, dynamic devices, repair issues, reconfiguration flow, translations, icons, etc.) lives in [`SDD.md`](SDD.md).

## Further documentation

- [`SDD.md`](SDD.md) — full Software Design Document: architecture, flows, design decisions, register map, testing, deploy. ([Español](SDD.es.md))
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — narrower deep-dive on specific design decisions (TID patch removal, write-then-verify, off-pending state machine, dynamic capabilities).
- [`REGISTERS.md`](REGISTERS.md) — Modbus register map reference.

## Uninstalling

To completely remove the integration:

1. **Settings → Devices & Services**, find **Hisense VRF**, click the three-dot menu → **Delete**. This removes the config entry, every device and every entity (climate, sensors, switches, EXP diagnostics, etc.) from the registries.
2. Remove the custom component files. For a dev install with the symlink described above:
   ```bash
   rm /path/to/ha_config/custom_components/hisense_vrf
   ```
   For an HA OS install with bundled files:
   ```bash
   rm -rf /Volumes/config/custom_components/hisense_vrf
   ```
3. If you installed `pyacmodbus` via `pip install -e`, uninstall it from the HA Python environment:
   ```bash
   pip uninstall pyacmodbus
   ```
4. Restart Home Assistant.

The integration does **not** create any database tables, blueprints or third-party files outside its `custom_components/hisense_vrf/` directory and the standard HA registries — deleting the entry plus the directory is enough to leave HA in a clean state.

## Running tests

```bash
cd /path/to/homeassistant-v2
pip install -e ./pyacmodbus-stub
pip install pytest pytest-asyncio pytest-cov pytest-timeout pytest-homeassistant-custom-component
pytest tests/
```

The test suite covers `pyacmodbus`, the controller (write-verify, off-pending, polling, dynamic-devices, repair issues), the config/options/reconfigure flow, setup/unload, diagnostics, and the climate / sensor / binary_sensor / switch / select / button platforms — 193 tests at 96% line coverage, runs in ~30 s.

### Type checking

```bash
pip install homeassistant mypy
mypy custom_components/hisense_vrf/ pyacmodbus-stub/pyacmodbus/
```

If you develop against a Home Assistant core fork checkout instead of the pip package, point mypy at it via `MYPYPATH`:

```bash
MYPYPATH=/path/to/homeassistant/core mypy custom_components/hisense_vrf/ pyacmodbus-stub/pyacmodbus/
```

## Limitations

- `pyacmodbus` is not yet on PyPI — for HA OS deploys the library has to be bundled.
- The integration accepts a single config entry per gateway (one TCP connection limit imposed by the firmware).
- Function-selection writes (e.g. enabling `B8` to use AUTO) are **not** exposed from the UI yet. Today you can read every bit through the EXP diagnostic entities, but writing them needs to be done manually or from a future "Apply function selection" service.
- Currents reported by the outdoor unit may be 0 — the i-Modkit firmware in some installs doesn't populate operational outdoor registers (offsets 17-53).
