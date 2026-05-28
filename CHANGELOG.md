# Changelog

All notable changes to the Hisense VRF integration are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] — 2026-05-28

### Added

- **Gate state handling.** When the gateway reports `ctrl_mode=0` (e.g. during an EEPROM handshake), the polling loop skips indoor/outdoor reads, indoor/outdoor entities go `unavailable`, and any control write is rejected with a persistent notification. Gateway entities (sensors, buttons) stay available so the user can monitor recovery and trigger another EEPROM clear if needed. Recovery is automatic on the next poll cycle where `ctrl_mode=1` is observed.
- **Retry round on no-response in the power-on path.** When the bundled FC 0x10 returns OK but the unit never reflects `is_running=True` after the verify window, the same frame is re-sent once before declaring `WRITE_FAILED`. Logged as `ON_RETRY_AFTER_NO_RESPONSE`.
- **Power-on path debug logging.** New `_LOGGER.debug` entries gated behind the `custom_components.hisense_vrf.controller: debug` logger level and only triggered on power-on bundles (not on every write):
  - `ON_PRE_WRITE_STATE` — full snapshot of the device state right before the write (`is_running`, `op_state`, modes, fans, prohibitions, `alarm_code`).
  - `ON_PRE_WRITE_GATEWAY` — gateway state (`alarm_display`, `unit_count`, `ctrl_mode`, `eeprom_clear`) at the moment of the write.
  - `ON_MODBUS_PAYLOAD` — the exact FC 0x10 frame parameters (base address + values).
  - `ON_WRITE_RETURNED` — confirmation that the gateway acknowledged the FC 0x10 frame.
  - `ON_VERIFY_EXTRA` — extended snapshot of the device on each verify attempt.
  - `ON_FAILED_DIAG` — fresh re-read of the device after a final `WRITE_FAILED`, useful to capture state after the pending overlay is cleared.

### Changed

- Polling loop now reads the gateway first on every cycle (not every N cycles). The gateway read is the gate for proceeding with indoor/outdoor reads.
- `WRITE_FAILED` log line now includes `rounds=N` so multi-round failures are distinguishable from single-round ones.

## [1.1.2] — 2026-05-25

### Changed

- `sensor.unit_system_number`, `sensor.unit_address_number`, `sensor.host_system_number`, and `sensor.host_address_number` now use `entity_category=DIAGNOSTIC`. They move from the device card's main Sensors section to the Diagnostic section, where identification metadata fits better. The values are unchanged.
- `select.dry_mode` no longer sets `entity_category=CONFIG`. It now appears in the Controls section of the device card alongside the louver select, matching what users expect for a runtime control.

## [1.1.1] — 2026-05-24

### Added

- `hacs.json` declaring the integration to the HACS default repository.
- `issue_tracker` URL in the manifest (required by HACS validation).

### Changed

- Manifest keys reordered alphabetically after `domain` + `name` (required by `hassfest`).

This is a packaging-only release: no runtime behaviour change vs 1.1.0. The version bump is needed because the HACS bot validates the latest release tag, not `main` — and the HACS-required fixes landed on `main` after 1.1.0 was tagged.

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

[1.1.2]: https://github.com/buchuman/hisense_vrf/releases/tag/v1.1.2
[1.1.1]: https://github.com/buchuman/hisense_vrf/releases/tag/v1.1.1
[1.1.0]: https://github.com/buchuman/hisense_vrf/releases/tag/v1.1.0
[1.0.0]: https://github.com/buchuman/hisense_vrf/releases/tag/v1.0.0
