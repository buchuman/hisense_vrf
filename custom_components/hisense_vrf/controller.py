"""Hisense VRF controller — replaces the polling DataUpdateCoordinator.

Provides:
* Discovery (scan_devices, outdoor connections).
* On-demand reads (refresh_all, refresh_unit).
* Write-then-verify cycle with retries (for actions on a powered-on unit).
* Off-pending: accumulate UI changes while the unit is off; fire a single
  bundled write (5 control regs) when the user presses ON within the TTL.
* Display overlay so the UI shows the values the user requested.
* Dispatcher notifications to entities.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send

from pyacmodbus import (
    ACDeviceState,
    ACModbusClient,
    GatewayState,
    ModbusReadError,
    OutdoorUnitState,
)

from .const import (
    DOMAIN,
    UNAVAILABLE_THRESHOLD,
    WRITE_FAILED_ISSUE_THRESHOLD,
    WRITE_STATUS_CONFIRMED,
    WRITE_STATUS_FAILED,
    WRITE_STATUS_IDLE,
    WRITE_STATUS_OFF_PENDING,
    WRITE_STATUS_PENDING,
    signal_new_indoor,
    signal_new_outdoor,
)

_LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _swing_to_register(auto: bool, position: int) -> int:
    return (0x01 if auto else 0x00) | ((position & 0x07) << 1)


class HisenseVRFController:
    """Owns the Modbus client, the cached state, pending writes, and write-verify cycles."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        client: ACModbusClient,
        verify_delay_s: float,
        verify_retries: int,
        off_pending_ttl_s: float,
        polling_enabled: bool,
        poll_interval_s: float,
        poll_spacing_s: float,
        poll_gateway_every_n: int,
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.client = client
        self.verify_delay_s = verify_delay_s
        self.verify_retries = verify_retries
        self.off_pending_ttl_s = off_pending_ttl_s
        self.polling_enabled = polling_enabled
        self.poll_interval_s = poll_interval_s
        self.poll_spacing_s = poll_spacing_s
        self.poll_gateway_every_n = max(1, poll_gateway_every_n)
        self._polling_task: asyncio.Task | None = None

        self.unit_indices: list[int] = []
        self.unit_identifiers: dict[int, tuple[int, int]] = {}
        self.unit_capacities: dict[int, int] = {}
        self.outdoor_units: list[tuple[int, int]] = []

        self.indoor_states: dict[int, ACDeviceState | None] = {}
        self.gateway_state: GatewayState | None = None
        self.outdoor_states: dict[tuple[int, int], OutdoorUnitState | None] = {}

        # Pending overlay during the verify window of an in-flight write.
        self.pending: dict[int, dict[str, Any]] = {}
        # Accumulated changes while the unit is off; flushed on ON event or
        # discarded by TTL/external power-on.
        self._off_pending: dict[int, dict[str, Any]] = {}
        self._off_timers: dict[int, asyncio.TimerHandle] = {}

        self.last_write_status: dict[int, dict[str, Any]] = {}
        self._unit_locks: dict[int, asyncio.Lock] = {}

        # Consecutive-failure counters for availability tracking. A unit (or
        # the gateway) flips to "unavailable" after UNAVAILABLE_THRESHOLD reads
        # in a row fail; the next successful read flips it back to available
        # and logs the recovery.
        self._unit_read_failures: dict[int, int] = {}
        self._gateway_read_failures = 0

        # Consecutive WRITE_FAILED counter per unit. Triggers a repair issue
        # at WRITE_FAILED_ISSUE_THRESHOLD; cleared on first WRITE_CONFIRMED.
        self._unit_write_failures: dict[int, int] = {}

        # Last unit_count value seen on the gateway; a delta triggers a
        # dynamic rescan to pick up newly connected/disconnected units.
        self._last_known_unit_count: int | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def async_initial_scan(self) -> None:
        """Connect, discover units, fetch per-unit identifiers (no full reads)."""
        await self.client.connect()
        self.unit_indices = await self.client.scan_devices()

        for idx in self.unit_indices:
            try:
                self.unit_identifiers[idx] = await self.client.read_unit_identifiers(idx)
            except ModbusReadError as err:
                _LOGGER.warning(
                    "Failed to read identifiers for unit %s, using fallback: %s", idx, err
                )
                self.unit_identifiers[idx] = (0, idx)
            try:
                self.unit_capacities[idx] = await self.client.read_unit_capacity(idx)
            except ModbusReadError as err:
                _LOGGER.warning("Failed to read capacity for unit %s: %s", idx, err)
                self.unit_capacities[idx] = 0

        try:
            self.outdoor_units = await self.client.read_outdoor_connections()
        except ModbusReadError as err:
            _LOGGER.warning("Failed to discover outdoor units: %s", err)
            self.outdoor_units = []

        # Seed the gateway baseline so the next poll can detect a unit_count
        # delta and dynamically pick up newly wired indoor units.
        try:
            self.gateway_state = await self.client.read_gateway()
            self._last_known_unit_count = self.gateway_state.unit_count
        except ModbusReadError as err:
            _LOGGER.warning("Initial gateway read failed: %s", err)

        for idx in self.unit_indices:
            self.indoor_states.setdefault(idx, None)
            self.pending.setdefault(idx, {})
            self._off_pending.setdefault(idx, {})
            self.last_write_status.setdefault(idx, {"status": WRITE_STATUS_IDLE})
        for ou in self.outdoor_units:
            self.outdoor_states.setdefault(ou, None)

        _LOGGER.info(
            "Initial scan: %d indoor unit(s) %s, %d outdoor module(s)",
            len(self.unit_indices),
            self.unit_identifiers,
            len(self.outdoor_units),
        )
        self._prune_stale_devices()

    def _prune_stale_devices(self) -> None:
        """Remove device-registry entries for units no longer reported by the gateway.

        Runs after every initial_scan (which is also what the "Discover devices"
        button triggers via async_reload). If the gateway used to report 7
        units and now reports 5, the two missing devices and all their entities
        get cleaned up from the registry instead of lingering as "unavailable".
        """
        registry = dr.async_get(self.hass)
        valid_identifiers: set[tuple[str, str]] = set()
        for idx in self.unit_indices:
            valid_identifiers.add(
                (DOMAIN, f"{self.entry_id}_indoor_{idx}")
            )
        for sys_idx, mod_idx in self.outdoor_units:
            valid_identifiers.add(
                (DOMAIN, f"{self.entry_id}_outdoor_{sys_idx}_{mod_idx}")
            )
        valid_identifiers.add((DOMAIN, f"{self.entry_id}_gateway"))

        for device in dr.async_entries_for_config_entry(registry, self.entry_id):
            if not any(ident in valid_identifiers for ident in device.identifiers):
                _LOGGER.info(
                    "STALE_DEVICE removed: %s (identifiers=%s)",
                    device.name_by_user or device.name, device.identifiers,
                )
                registry.async_remove_device(device.id)

    def unit_name(self, unit_index: int) -> str:
        """Return ``ac_indoor_unit_<sys><addr>`` (2 digits each) for naming."""
        host_sys, host_addr = self.unit_identifiers.get(unit_index, (0, unit_index))
        return f"ac_indoor_unit_{host_sys:02d}{host_addr:02d}"

    def unit_model(self, unit_index: int) -> str:
        """Return the ``model`` string shown in device info.

        ``capacity_code`` (register offset 1) holds the capacity directly in
        thousands of BTU/h — no scaling.
        """
        capacity = self.unit_capacities.get(unit_index, 0)
        if capacity:
            return f"VRF Indoor {capacity} kBTU"
        return "VRF Indoor"

    def unit_register_base(self, unit_index: int) -> int:
        return 40000 + unit_index * 91

    async def async_shutdown(self) -> None:
        await self.async_stop_polling()
        for timer in self._off_timers.values():
            timer.cancel()
        self._off_timers.clear()
        try:
            await self.client.disconnect()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Disconnect raised", exc_info=True)

    # ── Polling loop ─────────────────────────────────────────────────────────

    async def async_start_polling(self) -> None:
        """Start the background polling task (no-op if already running)."""
        if self._polling_task and not self._polling_task.done():
            return
        if not self.polling_enabled:
            _LOGGER.info("Polling disabled by config; not starting loop")
            return
        self._polling_task = self.hass.async_create_task(
            self._polling_loop(), name=f"{DOMAIN}_polling_{self.entry_id}"
        )
        _LOGGER.info(
            "Polling started: interval=%.1fs spacing=%.1fs gateway_every=%d",
            self.poll_interval_s, self.poll_spacing_s, self.poll_gateway_every_n,
        )

    async def async_stop_polling(self) -> None:
        task = self._polling_task
        if task is None:
            return
        self._polling_task = None
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _LOGGER.info("Polling stopped")

    async def _polling_loop(self) -> None:
        """Read every unit on a fixed cadence; honour verify-window skips."""
        cycle = 0
        try:
            while True:
                if not self.polling_enabled:
                    await asyncio.sleep(self.poll_interval_s or 1.0)
                    continue
                cycle += 1
                for idx in list(self.unit_indices):
                    if self.pending.get(idx):
                        _LOGGER.debug(
                            "POLL skip unit=%s (verify in progress)",
                            self.unit_name(idx),
                        )
                        continue
                    await self._read_and_track_unit(idx)
                    if self.poll_spacing_s > 0:
                        await asyncio.sleep(self.poll_spacing_s)
                if cycle % self.poll_gateway_every_n == 0:
                    await self._read_and_track_gateway()
                    for sys_idx, mod_idx in self.outdoor_units:
                        try:
                            self.outdoor_states[(sys_idx, mod_idx)] = await self.client.read_outdoor_unit(
                                sys_idx, mod_idx
                            )
                        except ModbusReadError as err:
                            _LOGGER.warning(
                                "POLL outdoor=%s:%s failed: %s", sys_idx, mod_idx, err
                            )
                self._notify()
                if self.poll_interval_s > 0:
                    await asyncio.sleep(self.poll_interval_s)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Polling loop crashed; will not restart")
            raise

    # ── Notification ─────────────────────────────────────────────────────────

    @property
    def signal(self) -> str:
        return f"{DOMAIN}_update_{self.entry_id}"

    def _notify(self) -> None:
        async_dispatcher_send(self.hass, self.signal)

    # ── User resolution ──────────────────────────────────────────────────────

    async def _resolve_user(self, context: Context | None) -> str:
        if context is None or context.user_id is None:
            return "system"
        user = await self.hass.auth.async_get_user(context.user_id)
        if user is None:
            return f"user:{context.user_id[:8]}"
        return user.name or f"user:{context.user_id[:8]}"

    # ── Power / state helpers ────────────────────────────────────────────────

    def is_powered_on(self, unit_index: int) -> bool:
        """True if the unit is currently considered on for routing decisions.

        Considers state, in-flight verify pending, and accumulated off_pending
        (in case the user already pressed ON in the UI but the write hasn't
        landed yet — extremely unlikely path but kept for completeness).
        """
        display = self.get_display_state(unit_index)
        if display is None:
            return False
        return bool(display.is_running)

    def is_off_pending(self, unit_index: int) -> bool:
        return bool(self._off_pending.get(unit_index))

    def off_pending_fields(self, unit_index: int) -> list[str]:
        return list(self._off_pending.get(unit_index, {}).keys())

    # ── Off-pending accumulation ─────────────────────────────────────────────

    def accumulate_off_pending(
        self, unit_index: int, attrs: dict[str, Any], *, user: str
    ) -> None:
        """Store changes while the unit is off and (re)start the TTL timer."""
        bucket = self._off_pending.setdefault(unit_index, {})
        bucket.update(attrs)
        name = self.unit_name(unit_index)
        _LOGGER.info(
            "OFF_PENDING unit=%s user=%s added=%s total=%s ttl=%ss",
            name, user, attrs, bucket, self.off_pending_ttl_s,
        )
        self._restart_off_timer(unit_index)
        self.last_write_status[unit_index] = {
            "status": WRITE_STATUS_OFF_PENDING,
            "expected": dict(bucket),
            "user": user,
            "timestamp": _now_iso(),
        }
        self._notify()

    def _restart_off_timer(self, unit_index: int) -> None:
        prev = self._off_timers.pop(unit_index, None)
        if prev is not None:
            prev.cancel()
        loop = asyncio.get_running_loop()
        self._off_timers[unit_index] = loop.call_later(
            self.off_pending_ttl_s, self._on_off_timer_expired, unit_index
        )

    def _on_off_timer_expired(self, unit_index: int) -> None:
        """Synchronous handler called by loop.call_later — schedules async work."""
        self._off_timers.pop(unit_index, None)
        self.hass.async_create_task(
            self._async_discard_off_pending(unit_index, reason="ttl_expired")
        )

    async def _async_discard_off_pending(self, unit_index: int, *, reason: str) -> None:
        if not self._off_pending.get(unit_index):
            return
        bucket = self._off_pending.pop(unit_index, {})
        timer = self._off_timers.pop(unit_index, None)
        if timer is not None:
            timer.cancel()
        _LOGGER.info(
            "OFF_PENDING_DISCARDED unit=%s reason=%s dropped=%s",
            self.unit_name(unit_index), reason, bucket,
        )
        status = self.last_write_status.get(unit_index, {}).get("status")
        if status == WRITE_STATUS_OFF_PENDING:
            self.last_write_status[unit_index] = {"status": WRITE_STATUS_IDLE}
        self._notify()

    # ── ON event — flush off_pending with a bundled write ───────────────────

    async def async_send_on_with_pending(
        self,
        unit_index: int,
        *,
        mode_override: int | None = None,
        context: Context | None = None,
    ) -> bool:
        """Compose the 5 control regs and send a bundled write to power on.

        ``mode_override`` lets ``async_set_hvac_mode`` declare which mode the
        user just chose (otherwise we use whatever is in state/off_pending).
        Returns True if confirmed, False if missing fields or write failed.
        """
        user = await self._resolve_user(context)
        name = self.unit_name(unit_index)
        state = self.indoor_states.get(unit_index)
        off_p = self._off_pending.get(unit_index, {})

        def pick(field: str, default: Any = None) -> Any:
            if field in off_p:
                return off_p[field]
            if state is not None:
                return getattr(state, field, default)
            return default

        mode = mode_override if mode_override is not None else pick("current_mode")
        fan = pick("fan_speed")
        setpoint = pick("setpoint")
        auto_swing = pick("auto_swing")
        louver_position = pick("louver_position")

        missing: list[str] = []
        if mode is None:
            missing.append("mode")
        if fan is None:
            missing.append("fan_speed")
        if setpoint is None:
            missing.append("setpoint")
        if auto_swing is None or louver_position is None:
            missing.append("swing")

        if missing:
            msg = (
                f"Cannot power on {name}: missing values for {', '.join(missing)}. "
                "Set them from the physical controller and refresh, "
                "or configure them in HA before pressing ON."
            )
            _LOGGER.warning("ON_BLOCKED unit=%s user=%s missing=%s", name, user, missing)
            persistent_notification.async_create(
                self.hass, msg, title=f"Hisense VRF — {name}",
                notification_id=f"{DOMAIN}_{self.entry_id}_{unit_index}_missing",
            )
            self.last_write_status[unit_index] = {
                "status": WRITE_STATUS_FAILED,
                "error": f"missing fields: {missing}",
                "user": user,
                "timestamp": _now_iso(),
            }
            self._notify()
            return False

        swing_reg = _swing_to_register(bool(auto_swing), int(louver_position))
        temp_int = int(setpoint)

        _LOGGER.info(
            "ON_BUNDLED unit=%s user=%s run=1 mode=0x%02x fan=0x%02x swing=0x%02x temp=%d",
            name, user, mode, fan, swing_reg, temp_int,
        )

        # Flush the off_pending now; the verify cycle uses the regular pending overlay.
        self._off_pending.pop(unit_index, None)
        timer = self._off_timers.pop(unit_index, None)
        if timer is not None:
            timer.cancel()

        pending_attrs = {
            "is_running": True,
            "current_mode": mode,
            "fan_speed": fan,
            "auto_swing": bool(auto_swing),
            "louver_position": int(louver_position),
            "setpoint": float(temp_int),
        }

        return await self.async_write_and_verify(
            unit_index,
            pending_attrs=pending_attrs,
            verify_fn=lambda s: (
                s.is_running
                and (s.current_mode == mode or s.mode_jump == mode)
                and s.fan_speed == fan
            ),
            write_fn=lambda: self.client.write_control_block(
                unit_index, 1, mode, fan, swing_reg, temp_int
            ),
            context=context,
        )

    # ── On-demand reads ──────────────────────────────────────────────────────

    async def async_refresh_all(self, *, context: Context | None = None) -> None:
        """Read gateway + every indoor unit + every outdoor module."""
        user = await self._resolve_user(context)
        _LOGGER.info("READ_ALL user=%s units=%s", user, self.unit_indices)
        await self._read_and_track_gateway()

        for idx in self.unit_indices:
            await self._read_and_track_unit(idx)

        for sys_idx, mod_idx in self.outdoor_units:
            try:
                self.outdoor_states[(sys_idx, mod_idx)] = await self.client.read_outdoor_unit(
                    sys_idx, mod_idx
                )
            except ModbusReadError as err:
                _LOGGER.warning(
                    "READ_ALL outdoor=%s:%s failed: %s", sys_idx, mod_idx, err
                )

        _LOGGER.info("READ_ALL user=%s done", user)
        self._notify()

    async def async_refresh_unit(
        self, unit_index: int, *, context: Context | None = None
    ) -> ACDeviceState | None:
        user = await self._resolve_user(context)
        name = self.unit_name(unit_index)
        _LOGGER.info("READ unit=%s user=%s", name, user)
        state = await self._read_and_track_unit(unit_index)
        if state is not None:
            _LOGGER.info(
                "READ unit=%s ok is_running=%s mode=0x%02x fan=0x%02x setpoint=%s inlet=%s",
                name, state.is_running, state.current_mode, state.fan_speed,
                state.setpoint, state.inlet_temp,
            )
        self._notify()
        return state

    async def _read_and_track_gateway(self) -> None:
        """Read the gateway state and track availability transitions.

        Also detects a change in ``unit_count`` and triggers a dynamic rescan
        (`dynamic-devices` quality rule) so newly wired indoor units appear
        without a manual reload.
        """
        try:
            new_state = await self.client.read_gateway()
        except ModbusReadError as err:
            self._gateway_read_failures += 1
            failures = self._gateway_read_failures
            _LOGGER.warning(
                "READ gateway failed (%d/%d): %s",
                failures, UNAVAILABLE_THRESHOLD, err,
            )
            if failures == UNAVAILABLE_THRESHOLD:
                _LOGGER.warning(
                    "UNAVAILABLE gateway — %d consecutive read failures, marking unavailable",
                    failures,
                )
                self.gateway_state = None
                self._notify()
            return
        if self._gateway_read_failures >= UNAVAILABLE_THRESHOLD:
            _LOGGER.info(
                "AVAILABLE gateway — recovered after %d failures",
                self._gateway_read_failures,
            )
        self._gateway_read_failures = 0
        self.gateway_state = new_state

        new_count = new_state.unit_count
        prior_count = self._last_known_unit_count
        if prior_count is not None and new_count != prior_count:
            _LOGGER.info(
                "Gateway unit_count changed %s → %s, scanning for new devices",
                prior_count, new_count,
            )
            await self._rescan_for_dynamic_devices()
        self._last_known_unit_count = new_count

    async def _rescan_for_dynamic_devices(self) -> None:
        """Rescan and fire dispatcher signals for newly discovered units.

        Disconnected units are *not* removed here — they go unavailable via
        the existing read-failure flow and stay in the registry until the
        next config-entry reload (handled by `_prune_stale_devices`).
        """
        try:
            current_indoor = await self.client.scan_devices()
        except ModbusReadError as err:
            _LOGGER.warning("Dynamic rescan: scan_devices failed: %s", err)
            return
        try:
            current_outdoor = await self.client.read_outdoor_connections()
        except ModbusReadError as err:
            _LOGGER.warning("Dynamic rescan: outdoor read failed: %s", err)
            current_outdoor = list(self.outdoor_units)

        known_indoor = set(self.unit_indices)
        added_indoor = [idx for idx in current_indoor if idx not in known_indoor]
        for idx in added_indoor:
            try:
                self.unit_identifiers[idx] = await self.client.read_unit_identifiers(idx)
            except ModbusReadError as err:
                _LOGGER.warning(
                    "Dynamic rescan: identifiers for new unit %s failed: %s", idx, err,
                )
                self.unit_identifiers[idx] = (0, idx)
            try:
                self.unit_capacities[idx] = await self.client.read_unit_capacity(idx)
            except ModbusReadError as err:
                _LOGGER.warning("Dynamic rescan: capacity for %s: %s", idx, err)
                self.unit_capacities[idx] = 0
            self.unit_indices.append(idx)
            self.indoor_states.setdefault(idx, None)
            self.pending.setdefault(idx, {})
            self._off_pending.setdefault(idx, {})
            self.last_write_status.setdefault(idx, {"status": WRITE_STATUS_IDLE})
            _LOGGER.info("DYNAMIC_ADD indoor unit=%s", self.unit_name(idx))
            async_dispatcher_send(
                self.hass, signal_new_indoor(self.entry_id), idx
            )

        known_outdoor = set(self.outdoor_units)
        added_outdoor = [ou for ou in current_outdoor if ou not in known_outdoor]
        for ou in added_outdoor:
            self.outdoor_units.append(ou)
            self.outdoor_states.setdefault(ou, None)
            _LOGGER.info("DYNAMIC_ADD outdoor module=%s", ou)
            async_dispatcher_send(
                self.hass, signal_new_outdoor(self.entry_id), ou
            )

    async def _read_and_track_unit(self, unit_index: int) -> ACDeviceState | None:
        """Read one unit; track availability transitions and external power-on."""
        prev = self.indoor_states.get(unit_index)
        name = self.unit_name(unit_index)
        try:
            state = await self.client.read_device(unit_index)
        except ModbusReadError as err:
            self._unit_read_failures[unit_index] = (
                self._unit_read_failures.get(unit_index, 0) + 1
            )
            failures = self._unit_read_failures[unit_index]
            _LOGGER.warning(
                "READ unit=%s failed (%d/%d): %s",
                name, failures, UNAVAILABLE_THRESHOLD, err,
            )
            if failures == UNAVAILABLE_THRESHOLD:
                _LOGGER.warning(
                    "UNAVAILABLE unit=%s — %d consecutive read failures, marking unavailable",
                    name, failures,
                )
                self.indoor_states[unit_index] = None
                self._notify()
            return None

        # Success path
        prior_failures = self._unit_read_failures.get(unit_index, 0)
        if prior_failures >= UNAVAILABLE_THRESHOLD:
            _LOGGER.info(
                "AVAILABLE unit=%s — recovered after %d failures",
                name, prior_failures,
            )
        self._unit_read_failures[unit_index] = 0
        self.indoor_states[unit_index] = state
        if (
            self._off_pending.get(unit_index)
            and state.is_running
            and (prev is None or not prev.is_running)
        ):
            await self._async_discard_off_pending(
                unit_index, reason="external_power_on_detected"
            )
        return state

    # ── Write + verify (powered-on path) ─────────────────────────────────────

    def _lock_for(self, unit_index: int) -> asyncio.Lock:
        if unit_index not in self._unit_locks:
            self._unit_locks[unit_index] = asyncio.Lock()
        return self._unit_locks[unit_index]

    async def async_write_and_verify(
        self,
        unit_index: int,
        pending_attrs: dict[str, Any],
        verify_fn: Callable[[ACDeviceState], bool],
        write_fn: Callable[[], Awaitable[None]],
        *,
        context: Context | None = None,
    ) -> bool:
        """Run the write-then-verify cycle for one indoor unit."""
        user = await self._resolve_user(context)
        name = self.unit_name(unit_index)

        async with self._lock_for(unit_index):
            _LOGGER.info(
                "WRITE unit=%s user=%s expected=%s delay=%.1fs retries=%d",
                name, user, pending_attrs, self.verify_delay_s, self.verify_retries,
            )

            self.pending.setdefault(unit_index, {}).update(pending_attrs)
            self.last_write_status[unit_index] = {
                "status": WRITE_STATUS_PENDING,
                "fields": list(pending_attrs.keys()),
                "expected": dict(pending_attrs),
                "user": user,
                "timestamp": _now_iso(),
            }
            self._notify()

            try:
                await write_fn()
            except ModbusReadError as err:
                _LOGGER.error("WRITE unit=%s user=%s send-failed: %s", name, user, err)
                self._clear_pending(unit_index, pending_attrs)
                self.last_write_status[unit_index] = {
                    "status": WRITE_STATUS_FAILED,
                    "fields": list(pending_attrs.keys()),
                    "expected": dict(pending_attrs),
                    "error": str(err),
                    "user": user,
                    "timestamp": _now_iso(),
                }
                self._on_write_failed(unit_index)
                self._notify()
                return False

            _LOGGER.info("WRITE unit=%s user=%s sent, verifying...", name, user)

            last_state: ACDeviceState | None = None
            total_reads = self.verify_retries + 1
            for attempt in range(total_reads):
                await asyncio.sleep(self.verify_delay_s)
                try:
                    state = await self.client.read_device(unit_index)
                except ModbusReadError as err:
                    _LOGGER.warning(
                        "VERIFY unit=%s attempt=%d/%d read-failed: %s",
                        name, attempt + 1, total_reads, err,
                    )
                    continue

                last_state = state
                self.indoor_states[unit_index] = state

                actual = {k: getattr(state, k, None) for k in pending_attrs}
                match = verify_fn(state)
                _LOGGER.info(
                    "VERIFY unit=%s attempt=%d/%d read=%s match=%s",
                    name, attempt + 1, total_reads, actual, match,
                )

                if match:
                    self._clear_pending(unit_index, pending_attrs)
                    self.last_write_status[unit_index] = {
                        "status": WRITE_STATUS_CONFIRMED,
                        "fields": list(pending_attrs.keys()),
                        "expected": dict(pending_attrs),
                        "actual": actual,
                        "attempts": attempt + 1,
                        "user": user,
                        "timestamp": _now_iso(),
                    }
                    _LOGGER.info(
                        "WRITE_CONFIRMED unit=%s user=%s attempts=%d expected=%s actual=%s",
                        name, user, attempt + 1, pending_attrs, actual,
                    )
                    self._on_write_confirmed(unit_index)
                    self._notify()
                    return True

            self._clear_pending(unit_index, pending_attrs)
            actual = {}
            if last_state is not None:
                for k in pending_attrs:
                    if hasattr(last_state, k):
                        actual[k] = getattr(last_state, k)
            self.last_write_status[unit_index] = {
                "status": WRITE_STATUS_FAILED,
                "fields": list(pending_attrs.keys()),
                "expected": dict(pending_attrs),
                "actual": actual,
                "attempts": total_reads,
                "user": user,
                "timestamp": _now_iso(),
            }
            _LOGGER.warning(
                "WRITE_FAILED unit=%s user=%s attempts=%d expected=%s actual=%s",
                name, user, total_reads, pending_attrs, actual,
            )
            self._on_write_failed(unit_index)
            self._notify()
            return False

    def _clear_pending(self, unit_index: int, attrs: dict[str, Any]) -> None:
        bucket = self.pending.get(unit_index)
        if not bucket:
            return
        for k in attrs:
            bucket.pop(k, None)

    # ── Repair-issue helpers ─────────────────────────────────────────────────

    def _write_failure_issue_id(self, unit_index: int) -> str:
        return f"write_failed_{self.entry_id}_{unit_index}"

    def _on_write_confirmed(self, unit_index: int) -> None:
        """Reset the failure counter and dismiss any active repair issue."""
        prior = self._unit_write_failures.pop(unit_index, 0)
        if prior >= WRITE_FAILED_ISSUE_THRESHOLD:
            ir.async_delete_issue(
                self.hass, DOMAIN, self._write_failure_issue_id(unit_index)
            )

    def _on_write_failed(self, unit_index: int) -> None:
        """Increment the failure counter; raise a repair issue once we hit the threshold."""
        count = self._unit_write_failures.get(unit_index, 0) + 1
        self._unit_write_failures[unit_index] = count
        if count == WRITE_FAILED_ISSUE_THRESHOLD:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._write_failure_issue_id(unit_index),
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="write_failed_repeated",
                translation_placeholders={
                    "unit_name": self.unit_name(unit_index),
                    "threshold": str(WRITE_FAILED_ISSUE_THRESHOLD),
                },
            )

    # ── Display helpers (entities use these) ─────────────────────────────────

    def get_display_state(self, unit_index: int) -> ACDeviceState | None:
        """Return cached state with verify pending + off_pending overlays applied."""
        state = self.indoor_states.get(unit_index)
        pending = self.pending.get(unit_index, {})
        off_p = self._off_pending.get(unit_index, {})
        if state is None and not off_p:
            return state
        if state is None:
            # No hardware state yet; build a synthetic dataclass would be heavy.
            # Entities that need values for off_pending fields read via
            # self._off_pending directly. Returning None keeps strictness.
            return None
        overrides: dict[str, Any] = {}
        for k, v in pending.items():
            if hasattr(state, k):
                overrides[k] = v
        for k, v in off_p.items():
            if hasattr(state, k):
                overrides[k] = v
        return replace(state, **overrides) if overrides else state

    def is_pending(self, unit_index: int) -> bool:
        return bool(self.pending.get(unit_index)) or bool(self._off_pending.get(unit_index))

    def is_field_pending(self, unit_index: int, field: str) -> bool:
        return (
            field in self.pending.get(unit_index, {})
            or field in self._off_pending.get(unit_index, {})
        )

    def pending_fields(self, unit_index: int) -> list[str]:
        merged = set(self.pending.get(unit_index, {}).keys())
        merged.update(self._off_pending.get(unit_index, {}).keys())
        return list(merged)
