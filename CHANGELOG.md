# Changelog

All notable changes to the Hisense VRF integration are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.6.4] — 2026-06-21

### Changed

- **The `Power-on retry` sensor now reads as time *remaining*** (was elapsed): 100% at the start of the retry window, draining toward 0% as it approaches giving up — so a gauge/bar reads as a countdown.

## [1.6.3] — 2026-06-21

### Added

- **`Power-on retry` progress sensor (per indoor unit, diagnostic).** Reports the percentage of the retry window remaining (100→0%) while a long-window power-on retry is in flight, with `attempt`, `remaining_s` and `total_s` as attributes; reads `unknown` when no retry is running. Put it on a gauge/bar card to watch a retry progress (and how long until it gives up). Tied to the live retry task, so it clears the moment the retry ends.

## [1.6.2] — 2026-06-21

### Changed

- **The climate card now shows a distinct "powering on" state during a retry** — neither a false ON nor a misleading plain OFF. While a long-window power-on retry is in flight, the climate entity reports the chosen mode (e.g. Heat) with `hvac_action = preheating`, so the thermostat card reads "Heat / Preheating…". On confirmation it becomes the real running state (`Heat / Heating`); on failure/timeout it returns to `Off`. The raw power switch and `is_running` stay truthfully off until the unit actually confirms. (Resolves the 1.6.0/1.6.1 back-and-forth on how the retry should surface in the UI.)

## [1.6.1] — 2026-06-21

### Fixed

- **Power-on retry no longer shows a unit as ON while it is physically OFF.** v1.6.0 kept an optimistic `is_running=True` overlay for the whole retry window, so a unit could be marked ON in HA for up to 6 minutes while actually off — misleading, and it invited conflicting commands (observed in prod 2026-06-21: a unit shown ON was off; a user then issued an OFF that "failed"). The background retry now keeps **no** optimistic overlay: the card reflects the real (off) state for the whole window and only flips ON once the unit actually confirms running. The `retrying` write-status remains the in-progress signal.

## [1.6.0] — 2026-06-20

### Added

- **Long-window background power-on resend — the real fix for the intermittent on-failure.** Diagnosis (see CHANGELOG 1.4.0/1.5.0 and prod capture 2026-06-19): only the **ON** command ever fails (OFF/temp/mode/fan never do), because ON is the only command issued against a unit the gateway is desynced from / whose H-NET path is dormant after an off-bus IR power-off. During the stall the gateway does not deliver the command for minutes; recovery only comes from resending a fresh ON until the path wakes (the user's "resend after several minutes works"). So when the synchronous ON attempt fails, a per-unit background task now keeps resending the ON bundle every ~25 s (±5 s jitter) for up to 6 minutes, watching actual state every ~5 s.
  - **Stops as soon as the goal is met or the situation changes**: the unit reports running by *any* path (our resend, the IR remote, another user) → confirmed; an externally-originated state change while still off → yields control (idle); a new HA command for the unit supersedes it; an OFF cancels it; the gateway dropping to handshake aborts it; otherwise it gives up at the timeout (failed).
  - **Scoped to the ON path only** — OFF/temp/mode/fan are untouched. Per-unit locking means every other unit stays fully controllable; the newest command always wins (it cancels any in-flight retry first); the retry recomposes the bundle each round so a setpoint/mode/fan change made while it runs is picked up.
  - **UI**: new `retrying` state on the `Last write status` sensor (with `attempt` / `remaining_s` attributes); the climate card stays optimistically ON for the whole window. New `on_retry` option (default on) as a kill-switch.
  - **Logged at WARNING**: `ON_RETRY_START`, `ON_RETRY_RESEND`, `ON_RETRY_SUCCESS`, `ON_RETRY_ABANDON`, `ON_RETRY_TIMEOUT`, `ON_RETRY_ABORT`.

### Changed

- **Edge-force is now off by default** (`on_edge_force`, was on in 1.4.0/1.5.0). Prod evidence (2026-06-19) showed it cannot fix the on-failure: during the stall the gateway never consumes the command slot, so writing OFF before the ON is a no-op. Kept as an opt-in kill-switch; the long-window resend above is the durable fix.

## [1.5.0] — 2026-06-19

### Fixed

- **Edge-force now waits for the OFF to be *consumed* before re-sending the ON.** The v1.4.0 edge-force slept a fixed `1.0s` after writing `REG_RUN_STOP=0`, then immediately re-sent the ON bundle. In prod (unit `ac_indoor_unit_0013`, 2026-06-19) that 1s was too short: the gateway had not yet drained the OFF from its per-unit command slot (`ON_EDGE_FORCE_SETTLED … consumed=False`), so the re-sent ON overwrote the slot before the OFF ever reached the H-NET bus — the edge was lost and the unit stayed off (`WRITE_FAILED`). The edge-force now **polls the command slot until it drains back to `[0xFF]*5`** (the gateway consumed the OFF), up to `ON_EDGE_DRAIN_TIMEOUT_S` (8s), before re-sending the ON. Only then is the ON a genuine `0→1` transition on the bus.
  - New WARNING markers: `ON_EDGE_FORCE_SETTLED … consumed=True waited=…s polls=…` (OFF drained, ON re-sent as an edge) vs `ON_EDGE_FORCE_NOT_DRAINED` (gateway never consumed the OFF within the timeout; ON re-sent anyway — edge may still be lost, escalate).
  - New tunables `ON_EDGE_DRAIN_TIMEOUT_S` and `ON_EDGE_POLL_INTERVAL_S`.

## [1.4.0] — 2026-06-19

### Added

- **Edge-forced power-on retry (intermittent on-failure workaround).** When a power-on bundle is accepted by the gateway but the unit never reflects `is_running=True` on the first round, the retry round now writes `REG_RUN_STOP=0` first, waits ~1s for it to propagate, then re-sends the ON bundle — turning a useless identical resend into a real `0→1` edge. This targets the failure mode where an IR remote turned the unit off directly (bypassing the H-NET bus), leaving the gateway's run baseline stuck at `1` so a fresh `1` is a no-op until the gateway's slow poll re-syncs (the "wait several minutes and retry" symptom). Units that power on cleanly on the first round are unaffected.
  - New `on_edge_force` option (default on, toggle in the config/options flow) as a kill-switch.
  - Logged at WARNING for post-hoc analysis from the Logs panel without enabling DEBUG: `ON_EDGE_FORCE` (slot before + settle), `ON_EDGE_FORCE_OFF_SENT`, `ON_EDGE_FORCE_SETTLED` (slot after + `consumed`). A subsequent `WRITE_CONFIRMED round=2/2` means the forced edge powered the unit; a `WRITE_FAILED` means it did not.

## [1.3.0] — 2026-05-31

### Added

- **Diagnostic buttons for the intermittent on-failure investigation.** One per indoor unit, all under `Diagnostic`:
  - `Power on (run_stop only)` — writes only `REG_RUN_STOP=1` instead of the 5-register bundle, to isolate whether the bug is bundle-specific (FC 0x10 count=5) vs. single-register (count=1).
  - `Reset F14 lock bits` — writes 0 to Function setting 14 (reg base+61), used to test whether wire-controller lock bits also block H-NET commands from the gateway.
  - `Clear command slot 78-82` — writes `[0xFF]×5` to the gateway's pending command slot for the unit, clearing any stale pending command.
- **Command slot 78-82 instrumentation.** The five registers act as the gateway's per-unit pending-command buffer — they are set to `[0xFF]×5` once the indoor unit consumes the command via H-NET. When they stay non-empty for more than a few seconds, the indoor unit failed to ACK (the wire-level fingerprint of the intermittent on-failure bug):
  - `Command slot 78-82` sensor (Diagnostic) — CSV of the five values plus per-register attributes (`run_stop`, `set_mode`, `set_fan`, `set_swing`, `set_temp`) and a `consumed` boolean.
  - `Command slot stuck` binary sensor (Problem class, visible) — fires when the slot ≠ `[0xFF]×5`.
  - Controller-side cache (`indoor_command_slots`) refreshed on every poll, plus `ON_PRE_WRITE_SLOT` / `ON_VERIFY_SLOT` / `ON_FAILED_DIAG_SLOT` debug logging at every step of the power-on path.
- **Remote-controller request sensors.** Expose what the user asked the wire/IR controller for, complementing the existing `mode_jump` / `fan_jump` (current execution) and `fan_actual` (physical):
  - `Mode (remote request)` ENUM sensor — values `auto` / `cool` / `dry` / `fan_only` / `heat` (from `REG_CURR_MODE` bits 0-4).
  - `Fan speed (remote request)` ENUM sensor — values `auto` / `high` / `medium` / `low` (from `REG_FAN_SW`).

### Fixed

- **Bootstrap hang when the gateway is unreachable.** The i-Modkit accepts only one TCP client at a time; if another client (another HA instance, a Modbus proxy/sniffer, a forgotten session) is holding it, `await client.connect()` could block `async_setup_entry` indefinitely instead of raising `CannotConnect`. The initial scan is now wrapped in `asyncio.timeout(connect_timeout_s)` (configurable in the options flow, default 5s, range 1-60s); on timeout `ConfigEntryNotReady` is raised so Home Assistant retries with backoff instead of stalling the bootstrap.

## [1.2.1] — 2026-05-28

### Fixed

- Polling task no longer blocks Home Assistant bootstrap completion. The `_polling_loop` is now scheduled with `hass.async_create_background_task` instead of `hass.async_create_task`, so the bootstrap stage doesn't wait ~5 minutes for the never-ending poll loop to "complete". Eliminates the `Setup timed out for bootstrap waiting on hisense_vrf_polling` warning and the corresponding "Something is blocking Home Assistant from wrapping up the start up phase" warning. No change to runtime behavior — the task still runs, is still cancelled cleanly on shutdown and reload.

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
