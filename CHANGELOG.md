# Changelog

All notable changes to the Hisense VRF integration are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] — 2026-05-24

### Changed

- **`pyacmodbus` is now a PyPI dependency** instead of a bundled library. `manifest.json` declares `"requirements": ["pyacmodbus>=1.0.0"]`; Home Assistant installs the library automatically on first load.
- `__init__.py` no longer ships the `_ensure_pyacmodbus_loaded()` helper nor any `importlib.util.spec_from_file_location` machinery — it's a plain `from pyacmodbus import ...` now.
- Install instructions in the README are simplified: only the integration directory needs to be copied to `custom_components/`. No more secondary `cp` for the library.

### Removed

- The bundled `pyacmodbus/` subdirectory inside `custom_components/hisense_vrf/`. The library code still lives in `pyacmodbus-stub/` in the repo (used for local dev and packaging) but is no longer copied into the integration at deploy time.

## [1.0.0] — 2026-05-24

First public release. Quality scale: **Platinum** (highest tier).

### Added

- **Climate control per indoor unit** — HVAC mode, target temperature, fan speed, swing mode, on/off, with `assumed_state` while a write is in flight.
- **Write-then-verify cycle** — every write is followed by N reads with configurable delay; status surfaces on the `last_write_status` diagnostic sensor.
- **Off-pending accumulator** — UI changes made while the unit is off are buffered (configurable TTL, default 30 s) and flushed in a single bundled Modbus 0x10 write at ON time (the manual's recommended approach).
- **Dynamic capabilities** — `hvac_modes`, `fan_modes`, and `supported_features` adapt to each unit's function-selection bits (`B5`, `B6`, `B7`, `B8`, `B9`, `C1`, `C5`).
- **Background polling** — configurable interval; skips units mid-verify so it never trampers in-flight writes.
- **EXP diagnostic entities** — 100 binary + 3 enum entities per indoor unit exposing every documented bit of the function-selection registers (40048-40067). Disabled by default to keep the recorder DB lean.
- **Outdoor unit sensors** — temperatures, pressures, runtime, currents, valve openings.
- **Gateway entities** — alarm-display switch, unit count, ctrl-mode binary sensor, EEPROM clear button.
- **Per-user attribution** in logs and on the `last_write_status` sensor.
- **Dynamic devices** — new indoor units appearing on the bus are picked up at runtime by watching the gateway's `unit_count` register (no reload required).
- **Reconfiguration flow** — change the gateway host/port without recreating the config entry.
- **Repair issues** — 3 consecutive failed writes to a unit raise a translated, actionable repair notification.
- **Strict typing** — `mypy --strict` passes with 0 errors across the integration and the bundled `pyacmodbus` library.
- **Full translations** — all entity names, states, options, errors and repair issues use `translation_key`. English bundled; the system supports more.
- **Per-entity icons** — `icons.json` maps every `translation_key` to an appropriate `mdi:*` glyph.
- **Diagnostics handler** — `async_get_config_entry_diagnostics` exposes the full controller state for issue reports.
- **193 tests** with **96% line coverage** across 11 test files.

### Design notes

- Replaces the legacy `acmodbus` integration (v0.x). Entity_ids follow the `ac_indoor_unit_<host_sys><host_addr>` convention (physical IDs from the H-LINK bus, not discovery order).
- The `pymodbus` per-operation timeout is set to **1.5 s** (vs the library default of 3 s) to bound worst-case setup time.
- The `pyacmodbus` library is bundled inside `custom_components/hisense_vrf/` because it is not on PyPI yet. The integration loads it via `importlib.util.spec_from_file_location` without touching `sys.path` (avoids shadowing stdlib modules like `select`).
- `EXP*` entities default to `disabled_by_default=True`. They're available but hidden until the user opts in from Settings → Devices → entity.

[1.1.0]: https://github.com/buchuman/hisense_vrf/releases/tag/v1.1.0
[1.0.0]: https://github.com/buchuman/hisense_vrf/releases/tag/v1.0.0
