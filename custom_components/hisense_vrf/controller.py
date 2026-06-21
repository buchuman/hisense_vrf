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
import random
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
    BASE_ADDR,
    GatewayState,
    ModbusReadError,
    OutdoorUnitState,
    REG_RUN_STOP,
    UNIT_STRIDE,
)

from .const import (
    DOMAIN,
    ON_EDGE_DRAIN_TIMEOUT_S,
    ON_EDGE_POLL_INTERVAL_S,
    ON_EDGE_SETTLE_S,
    ON_RETRY_INTERVAL_S,
    ON_RETRY_JITTER_S,
    ON_RETRY_TIMEOUT_S,
    ON_RETRY_WATCH_INTERVAL_S,
    UNAVAILABLE_THRESHOLD,
    WRITE_FAILED_ISSUE_THRESHOLD,
    WRITE_STATUS_CONFIRMED,
    WRITE_STATUS_FAILED,
    WRITE_STATUS_IDLE,
    WRITE_STATUS_OFF_PENDING,
    WRITE_STATUS_PENDING,
    WRITE_STATUS_RETRYING,
    signal_new_indoor,
    signal_new_outdoor,
)

_LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _swing_to_register(auto: bool, position: int) -> int:
    return (0x01 if auto else 0x00) | ((position & 0x07) << 1)


_ON_DEBUG_FIELDS = (
    "is_running", "op_state",
    "current_mode", "mode_jump", "fan_speed", "fan_jump", "setpoint",
    "prohibit_on_off", "prohibit_mode", "prohibit_fan",
    "prohibit_swing", "prohibit_temp",
    "alarm_code",
)


def _on_debug_snapshot(state: ACDeviceState | None) -> dict[str, Any]:
    if state is None:
        return {}
    return {f: getattr(state, f, None) for f in _ON_DEBUG_FIELDS}


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
        on_edge_force: bool = False,
        on_retry: bool = True,
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
        # When True, the power-on retry round forces a 0->1 edge (write
        # REG_RUN_STOP=0, settle, then re-send the ON bundle) instead of a
        # plain identical resend. Targets the intermittent on-failure where an
        # IR-driven OFF desyncs the gateway's run baseline. Kill-switch only;
        # has no effect on units that power on cleanly on the first round.
        self.on_edge_force = on_edge_force
        # When True, a failed synchronous power-on starts a per-unit background
        # task that keeps resending the ON bundle (long-window resend) until the
        # unit reports running, an external change is seen, a new command
        # supersedes it, or ON_RETRY_TIMEOUT_S elapses. Scoped to the ON path.
        self.on_retry = on_retry
        self._polling_task: asyncio.Task | None = None
        # Per-unit background power-on retry tasks + a monotonically increasing
        # epoch used to detect when a retry has been superseded.
        self._on_retry_tasks: dict[int, asyncio.Task] = {}
        self._on_retry_epoch: dict[int, int] = {}

        self.unit_indices: list[int] = []
        self.unit_identifiers: dict[int, tuple[int, int]] = {}
        self.unit_capacities: dict[int, int] = {}
        self.outdoor_units: list[tuple[int, int]] = []

        self.indoor_states: dict[int, ACDeviceState | None] = {}
        self.gateway_state: GatewayState | None = None
        self.outdoor_states: dict[tuple[int, int], OutdoorUnitState | None] = {}

        # Per-unit cache of registers 78..82 (gateway's pending command slot).
        # [0xFF]*5 means the slot is empty (the unit consumed the last command);
        # any other value means a command is pending. Refreshed on every poll.
        self.indoor_command_slots: dict[int, list[int] | None] = {}

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

        # Tracks whether we already notified the user about the gate state so
        # we don't spam notifications every poll cycle.
        self._gate_notified: bool = False

    @property
    def gateway_controllable(self) -> bool:
        """True iff the gateway reported ctrl_mode=1 in its last read."""
        return self.gateway_state is not None and self.gateway_state.ctrl_mode == 1

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
        for unit_index in list(self._on_retry_tasks):
            self._cancel_on_retry(unit_index, reason="shutdown")
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
        self._polling_task = self.hass.async_create_background_task(
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
        """Read every unit on a fixed cadence; honour verify-window skips.

        Each cycle starts with a gateway read. If ``ctrl_mode != 1`` (gate
        state, e.g. EEPROM handshake in progress), the unit reads are skipped
        and entities reflect that via ``gateway_controllable``.
        """
        cycle = 0
        try:
            while True:
                if not self.polling_enabled:
                    await asyncio.sleep(self.poll_interval_s or 1.0)
                    continue
                cycle += 1

                await self._read_and_track_gateway()

                if not self.gateway_controllable:
                    if not self._gate_notified:
                        self._notify_gate_state()
                        self._gate_notified = True
                    self._notify()
                    if self.poll_interval_s > 0:
                        await asyncio.sleep(self.poll_interval_s)
                    continue

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

    def _notify_gate_state(self) -> None:
        """Persistent notification: the gateway is in handshake / not controllable."""
        persistent_notification.async_create(
            self.hass,
            "The Modbus gateway is in handshake state (ctrl_mode=0). "
            "Reads and writes are paused until it recovers (RUN light flashes again). "
            "If this persists, check the gateway hardware.",
            title="Hisense VRF — gateway not controllable",
            notification_id=f"{DOMAIN}_{self.entry_id}_gate",
        )

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

        # A fresh ON supersedes any in-flight long-window retry for this unit.
        self._cancel_on_retry(unit_index, reason="new ON command")

        if not self.gateway_controllable:
            _LOGGER.warning(
                "ON_BLOCKED unit=%s user=%s — gateway not controllable (ctrl_mode!=1)",
                name, user,
            )
            persistent_notification.async_create(
                self.hass,
                f"Cannot power on {name}: the Modbus gateway is in handshake state "
                "and not accepting commands. Wait until the RUN light flashes again, then retry.",
                title=f"Hisense VRF — {name}",
                notification_id=f"{DOMAIN}_{self.entry_id}_{unit_index}_gate",
            )
            self.last_write_status[unit_index] = {
                "status": WRITE_STATUS_FAILED,
                "error": "gateway not controllable (ctrl_mode!=1)",
                "user": user,
                "timestamp": _now_iso(),
            }
            self._notify()
            return False

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

        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(
                "ON_PRE_WRITE_STATE unit=%s snapshot=%s",
                name, _on_debug_snapshot(state),
            )
            try:
                gw_state = await self.client.read_gateway()
            except ModbusReadError as err:
                _LOGGER.debug("ON_PRE_WRITE_GATEWAY unit=%s read failed: %s", name, err)
            else:
                _LOGGER.debug(
                    "ON_PRE_WRITE_GATEWAY unit=%s gateway={alarm_display=%d unit_count=%d ctrl_mode=%d eeprom_clear=%d}",
                    name,
                    gw_state.alarm_display,
                    gw_state.unit_count,
                    gw_state.ctrl_mode,
                    gw_state.eeprom_clear,
                )
            base_addr = BASE_ADDR + unit_index * UNIT_STRIDE + REG_RUN_STOP
            payload = [1, mode, fan, swing_reg, temp_int]
            _LOGGER.debug(
                "ON_MODBUS_PAYLOAD unit=%s fc=0x10 base_addr=%d count=%d values=%s",
                name, base_addr, len(payload), payload,
            )
            slot_before = await self._read_command_slot(unit_index)
            stale = slot_before is not None and slot_before != [0xFF] * 5
            _LOGGER.debug(
                "ON_PRE_WRITE_SLOT unit=%s slot=%s stale=%s",
                name, slot_before, stale,
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

        pre_retry_fn: Callable[[], Awaitable[None]] | None = None
        if self.on_edge_force:
            async def _edge_force() -> None:
                await self._on_edge_force_prewrite(unit_index, name, user)

            pre_retry_fn = _edge_force

        ok = await self.async_write_and_verify(
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
            verbose_on_debug=True,
            retry_on_no_response=True,
            pre_retry_fn=pre_retry_fn,
        )

        # The synchronous attempt failed to confirm. Only the ON command suffers
        # the intermittent on-failure, so hand off to the long-window background
        # resend (if enabled and the gateway is still controllable) rather than
        # leaving the unit off. The retry recomposes the bundle each round, so it
        # picks up any setpoint/mode/fan the user changes while it runs.
        if not ok and self.on_retry and self.gateway_controllable:
            self._start_on_retry(unit_index, user=user)

        return ok

    # ── Long-window power-on resend (only the ON command fails) ──────────────

    _ON_SNAPSHOT_FIELDS = (
        "current_mode",
        "fan_speed",
        "setpoint",
        "auto_swing",
        "louver_position",
    )

    def _compose_on_payload(
        self, unit_index: int
    ) -> tuple[dict[str, Any], int, int, int, int] | None:
        """Recompose the ON bundle from current state + accumulated off_pending.

        Returns ``(pending_attrs, mode, fan, swing_reg, temp)`` or ``None`` if a
        required field is unknown. Unlike ``async_send_on_with_pending`` this does
        NOT pop off_pending, so the background retry can call it every round and
        pick up any setpoint/mode/fan the user changes mid-retry.
        """
        state = self.indoor_states.get(unit_index)
        off_p = self._off_pending.get(unit_index, {})

        def pick(field: str) -> Any:
            if field in off_p:
                return off_p[field]
            if state is not None:
                return getattr(state, field, None)
            return None

        mode = pick("current_mode")
        fan = pick("fan_speed")
        setpoint = pick("setpoint")
        auto_swing = pick("auto_swing")
        louver_position = pick("louver_position")
        if None in (mode, fan, setpoint, auto_swing, louver_position):
            return None

        swing_reg = _swing_to_register(bool(auto_swing), int(louver_position))
        temp_int = int(setpoint)
        pending_attrs = {
            "is_running": True,
            "current_mode": mode,
            "fan_speed": fan,
            "auto_swing": bool(auto_swing),
            "louver_position": int(louver_position),
            "setpoint": float(temp_int),
        }
        return pending_attrs, mode, fan, swing_reg, temp_int

    def _on_state_snapshot(self, unit_index: int) -> dict[str, Any]:
        state = self.indoor_states.get(unit_index)
        if state is None:
            return {}
        return {f: getattr(state, f, None) for f in self._ON_SNAPSHOT_FIELDS}

    def _external_change(
        self, snapshot: dict[str, Any], state: ACDeviceState
    ) -> str | None:
        """Detect a state change we did not cause (IR remote, another actor).

        During the stall the unit does not consume our writes, so its reported
        settings stay frozen; any change to mode/fan/setpoint/swing means someone
        else is now driving the unit and we must yield control.
        """
        for field in self._ON_SNAPSHOT_FIELDS:
            old = snapshot.get(field)
            new = getattr(state, field, None)
            if old is not None and new is not None and old != new:
                return f"{field}:{old}->{new}"
        return None

    def _cancel_on_retry(self, unit_index: int, *, reason: str) -> None:
        """Stop an in-flight power-on retry (new command / OFF / shutdown)."""
        # Bump the epoch so any iteration already past its cancel checkpoint bails.
        if unit_index in self._on_retry_epoch:
            self._on_retry_epoch[unit_index] += 1
        task = self._on_retry_tasks.pop(unit_index, None)
        if task is not None and not task.done():
            task.cancel()
            _LOGGER.info(
                "ON_RETRY_CANCELLED unit=%s — %s",
                self.unit_name(unit_index), reason,
            )

    def _start_on_retry(self, unit_index: int, *, user: str) -> None:
        name = self.unit_name(unit_index)
        epoch = self._on_retry_epoch.get(unit_index, 0) + 1
        self._on_retry_epoch[unit_index] = epoch
        snapshot = self._on_state_snapshot(unit_index)

        # Do NOT assert an optimistic is_running overlay while retrying: the unit
        # is physically OFF for the whole window and showing it ON for up to
        # ON_RETRY_TIMEOUT_S misleads the user (and invites conflicting commands).
        # The card reflects the real (off) state; the `retrying` write-status is
        # the only signal that a resend is in progress. It flips ON only once the
        # unit actually confirms running.

        self.last_write_status[unit_index] = {
            "status": WRITE_STATUS_RETRYING,
            "fields": ["is_running"],
            "attempt": 0,
            "remaining_s": ON_RETRY_TIMEOUT_S,
            "user": user,
            "timestamp": _now_iso(),
        }
        self._notify()
        _LOGGER.warning(
            "ON_RETRY_START unit=%s user=%s — long-window resend every ~%.0fs up to %.0fs",
            name, user, ON_RETRY_INTERVAL_S, ON_RETRY_TIMEOUT_S,
        )
        self._on_retry_tasks[unit_index] = self.hass.async_create_background_task(
            self._on_retry_loop(unit_index, epoch, user, snapshot),
            name=f"{DOMAIN}_on_retry_{self.entry_id}_{unit_index}",
        )

    def _touch_on_retry_status(
        self, unit_index: int, user: str, attempt: int, remaining_s: float
    ) -> None:
        self.last_write_status[unit_index] = {
            "status": WRITE_STATUS_RETRYING,
            "fields": ["is_running"],
            "attempt": attempt,
            "remaining_s": round(max(0.0, remaining_s), 1),
            "user": user,
            "timestamp": _now_iso(),
        }
        self._notify()

    def _finish_on_retry(
        self, unit_index: int, *, status: str, user: str, reason: str, attempt: int
    ) -> None:
        self._on_retry_tasks.pop(unit_index, None)
        bucket = self.pending.get(unit_index)
        if bucket:
            bucket.clear()
        self.last_write_status[unit_index] = {
            "status": status,
            "reason": reason,
            "attempts": attempt,
            "user": user,
            "timestamp": _now_iso(),
        }
        if status == WRITE_STATUS_CONFIRMED:
            self._on_write_confirmed(unit_index)
        elif status == WRITE_STATUS_FAILED:
            self._on_write_failed(unit_index)
        self._notify()

    async def _on_retry_resend(self, unit_index: int, attempt: int, user: str) -> None:
        name = self.unit_name(unit_index)
        payload = self._compose_on_payload(unit_index)
        if payload is None:
            _LOGGER.warning(
                "ON_RETRY_RESEND_SKIP unit=%s attempt=%d — missing control fields",
                name, attempt,
            )
            return
        _pending_attrs, mode, fan, swing_reg, temp_int = payload
        # No optimistic overlay: the card keeps showing the real (off) state
        # until the unit actually confirms running (see _start_on_retry).
        _LOGGER.warning(
            "ON_RETRY_RESEND unit=%s user=%s attempt=%d run=1 mode=0x%02x fan=0x%02x temp=%d",
            name, user, attempt, mode, fan, temp_int,
        )
        try:
            async with self._lock_for(unit_index):
                await self.client.write_control_block(
                    unit_index, 1, mode, fan, swing_reg, temp_int
                )
        except ModbusReadError as err:
            _LOGGER.warning(
                "ON_RETRY_RESEND_FAILED unit=%s attempt=%d — %s", name, attempt, err,
            )

    async def _on_retry_loop(
        self, unit_index: int, epoch: int, user: str, snapshot: dict[str, Any]
    ) -> None:
        name = self.unit_name(unit_index)
        loop = self.hass.loop
        deadline = loop.time() + ON_RETRY_TIMEOUT_S
        attempt = 0
        try:
            while loop.time() < deadline:
                if self._on_retry_epoch.get(unit_index) != epoch:
                    return
                if not self.gateway_controllable:
                    _LOGGER.warning(
                        "ON_RETRY_ABORT unit=%s user=%s — gateway not controllable",
                        name, user,
                    )
                    self._finish_on_retry(
                        unit_index, status=WRITE_STATUS_FAILED, user=user,
                        reason="gateway not controllable", attempt=attempt,
                    )
                    return

                # Watch window: re-read actual state often so we react to an
                # external power-on / change within ON_RETRY_WATCH_INTERVAL_S.
                interval = max(
                    ON_RETRY_WATCH_INTERVAL_S,
                    ON_RETRY_INTERVAL_S
                    + random.uniform(-ON_RETRY_JITTER_S, ON_RETRY_JITTER_S),
                )
                waited = 0.0
                while waited < interval and loop.time() < deadline:
                    await asyncio.sleep(
                        min(ON_RETRY_WATCH_INTERVAL_S, interval - waited)
                    )
                    waited += ON_RETRY_WATCH_INTERVAL_S
                    if self._on_retry_epoch.get(unit_index) != epoch:
                        return
                    try:
                        state = await self.client.read_device(unit_index)
                    except ModbusReadError:
                        continue
                    self.indoor_states[unit_index] = state

                    if state.is_running:
                        _LOGGER.warning(
                            "ON_RETRY_SUCCESS unit=%s user=%s attempt=%d — unit reports running",
                            name, user, attempt,
                        )
                        self._finish_on_retry(
                            unit_index, status=WRITE_STATUS_CONFIRMED, user=user,
                            reason="unit running", attempt=attempt,
                        )
                        return

                    changed = self._external_change(snapshot, state)
                    if changed:
                        _LOGGER.warning(
                            "ON_RETRY_ABANDON unit=%s user=%s — external change (%s); yielding control",
                            name, user, changed,
                        )
                        self._finish_on_retry(
                            unit_index, status=WRITE_STATUS_IDLE, user=user,
                            reason=f"external change: {changed}", attempt=attempt,
                        )
                        return

                    self._touch_on_retry_status(
                        unit_index, user, attempt, deadline - loop.time()
                    )

                if loop.time() >= deadline:
                    break
                attempt += 1
                await self._on_retry_resend(unit_index, attempt, user)

            _LOGGER.warning(
                "ON_RETRY_TIMEOUT unit=%s user=%s attempts=%d — giving up after %.0fs",
                name, user, attempt, ON_RETRY_TIMEOUT_S,
            )
            self._finish_on_retry(
                unit_index, status=WRITE_STATUS_FAILED, user=user,
                reason="timeout", attempt=attempt,
            )
        except asyncio.CancelledError:
            _LOGGER.info("ON_RETRY_STOP unit=%s — cancelled", name)
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.exception("ON_RETRY_ERROR unit=%s — unexpected error", name)
            self._finish_on_retry(
                unit_index, status=WRITE_STATUS_FAILED, user=user,
                reason="error", attempt=attempt,
            )

    async def _on_edge_force_prewrite(
        self, unit_index: int, name: str, user: str
    ) -> None:
        """Force a 0->1 edge before the power-on retry re-sends the ON bundle.

        The intermittent on-failure looks like this: an IR remote turned the
        unit off directly (bypassing the bus), so the gateway still believes
        the commanded run state is 1. Re-sending run_stop=1 is therefore not a
        fresh 0->1 edge and the gateway never relays it via H-NET — the unit
        stays off until the gateway's slow poll re-syncs its baseline (the
        "several minutes" before a manual resend works).

        This writes REG_RUN_STOP=0 (resyncing the baseline to off), then *waits
        for the gateway to actually consume that OFF* — the command slot must
        drain back to ``[0xFF]*5`` — before returning; the caller then re-sends
        the ON bundle, which is now an unambiguous 0->1 transition on the bus.

        Why the wait must be a drain-poll, not a fixed sleep (v1.5.0): the
        gateway drains its per-unit command buffer on its own slow H-NET cycle.
        If we re-send the ON before the OFF drains, the ON simply overwrites the
        buffer slot and the gateway never transmits the OFF — so there is no real
        edge on the bus and the unit stays off. This was observed in prod on
        2026-06-19 (unit ac_indoor_unit_0013): a fixed 1.0s settle left the slot
        ``consumed=False`` and the resend produced WRITE_FAILED. Logged at
        WARNING so the on-failure path is analysable from the Logs panel.
        """
        slot_before = await self._read_command_slot(unit_index)
        _LOGGER.warning(
            "ON_EDGE_FORCE unit=%s user=%s — writing REG_RUN_STOP=0 to force a "
            "0->1 edge; slot_before=%s settle=%.1fs drain_timeout=%.1fs",
            name, user, slot_before, ON_EDGE_SETTLE_S, ON_EDGE_DRAIN_TIMEOUT_S,
        )
        try:
            await self.client.turn_off(unit_index)
        except ModbusReadError as err:
            _LOGGER.warning(
                "ON_EDGE_FORCE_OFF_FAILED unit=%s user=%s — turn_off raised: %s "
                "(falling back to a plain resend)",
                name, user, err,
            )
            return
        _LOGGER.warning("ON_EDGE_FORCE_OFF_SENT unit=%s user=%s", name, user)

        # Wait for the gateway to consume the OFF (slot -> [0xFF]*5) so it is
        # actually transmitted onto the H-NET bus before we re-send the ON.
        await asyncio.sleep(ON_EDGE_SETTLE_S)
        waited = ON_EDGE_SETTLE_S
        slot_after = await self._read_command_slot(unit_index)
        consumed = slot_after == [0xFF] * 5
        polls = 0
        while not consumed and waited < ON_EDGE_DRAIN_TIMEOUT_S:
            await asyncio.sleep(ON_EDGE_POLL_INTERVAL_S)
            waited += ON_EDGE_POLL_INTERVAL_S
            polls += 1
            slot_after = await self._read_command_slot(unit_index)
            consumed = slot_after == [0xFF] * 5

        if consumed:
            _LOGGER.warning(
                "ON_EDGE_FORCE_SETTLED unit=%s user=%s slot_after=%s consumed=True "
                "waited=%.1fs polls=%d — OFF drained, re-sending ON as a 0->1 edge",
                name, user, slot_after, waited, polls,
            )
        else:
            _LOGGER.warning(
                "ON_EDGE_FORCE_NOT_DRAINED unit=%s user=%s slot_after=%s "
                "waited=%.1fs polls=%d — gateway did not consume the OFF within "
                "the timeout; re-sending ON anyway (edge may still be lost)",
                name, user, slot_after, waited, polls,
            )

    # ── Diagnostic: read/clear the gateway's pending command slot (regs 78-82) ─

    async def _read_command_slot(self, unit_index: int) -> list[int] | None:
        """Read regs 78..82 (the gateway's pending command slot for this unit).

        When the gateway has forwarded the command to the indoor unit via H-NET
        and the indoor unit acknowledged it, the gateway resets these registers
        to ``[0xFF]*5``. If they show other values, the gateway is still holding
        a pending command — typically the symptom of the intermittent on-failure
        where the indoor unit never confirmed reception.
        """
        base = BASE_ADDR + unit_index * UNIT_STRIDE + REG_RUN_STOP
        try:
            async with self.client._lock:  # noqa: SLF001
                client = self.client._require_client()  # noqa: SLF001
                result = await client.read_holding_registers(
                    base, count=5, device_id=self.client._slave_id  # noqa: SLF001
                )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("command slot read failed for unit %d: %s", unit_index, err)
            return None
        if (
            result.isError()
            or not getattr(result, "registers", None)
            or len(result.registers) < 5
        ):
            return None
        return list(result.registers)

    async def async_clear_command_slot(
        self,
        unit_index: int,
        *,
        context: Context | None = None,
    ) -> bool:
        """Write ``[0xFF]*5`` to regs 78..82 — clear any stale pending command.

        Diagnostic: if a previous bundled write got stuck (gateway holds the
        command pending forever because the indoor unit never ACKed via H-NET),
        explicitly resetting the slot to the sentinel may allow the next write
        to land on a clean slot.
        """
        user = await self._resolve_user(context)
        name = self.unit_name(unit_index)

        if not self.gateway_controllable:
            _LOGGER.warning(
                "CLEAR_SLOT_BLOCKED unit=%s user=%s — gateway not controllable",
                name, user,
            )
            return False

        before = await self._read_command_slot(unit_index)
        _LOGGER.warning(
            "CLEAR_SLOT unit=%s user=%s — slot_before=%s, writing [0xFF]*5",
            name, user, before,
        )
        try:
            await self.client.write_control_block(
                unit_index, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF
            )
        except ModbusReadError as err:
            _LOGGER.error("CLEAR_SLOT unit=%s failed: %s", name, err)
            return False
        after = await self._read_command_slot(unit_index)
        _LOGGER.warning(
            "CLEAR_SLOT_DONE unit=%s slot_after=%s",
            name, after,
        )
        self._notify()
        return True

    # ── Diagnostic: reset Function setting 14 (wire controller lock) ─────────

    async def async_reset_function_14(
        self,
        unit_index: int,
        *,
        context: Context | None = None,
    ) -> None:
        """Write 0 to Function setting 14 (reg base+61) — clears all wire
        controller lock bits (F8/F9/FA/FB/FF). Used to test the hypothesis
        that those lock bits also block H-NET commands from the gateway.
        """
        user = await self._resolve_user(context)
        name = self.unit_name(unit_index)

        if not self.gateway_controllable:
            _LOGGER.warning(
                "RESET_F14_BLOCKED unit=%s user=%s — gateway not controllable", name, user,
            )
            return

        offset = 61  # Function setting 14
        addr = BASE_ADDR + unit_index * UNIT_STRIDE + offset
        _LOGGER.warning(
            "RESET_F14 unit=%s user=%s — writing 0 to reg %d (Function setting 14)",
            name, user, addr,
        )
        # _write_unit is the low-level API in the pyacmodbus client. Used here
        # for a one-off diagnostic experiment; no public client method exists
        # for arbitrary function-setting writes today.
        await self.client._write_unit(unit_index, offset, 0)  # noqa: SLF001
        # Read back to confirm
        try:
            state = await self.client.read_device(unit_index)
            self.indoor_states[unit_index] = state
            _LOGGER.warning(
                "RESET_F14_DONE unit=%s — read back confirmed (state updated)",
                name,
            )
        except ModbusReadError as err:
            _LOGGER.warning("RESET_F14_READBACK_FAILED unit=%s: %s", name, err)
        self._notify()

    # ── Diagnostic: power on writing only REG_RUN_STOP=1 ─────────────────────

    async def async_power_on_runstop_only(
        self,
        unit_index: int,
        *,
        context: Context | None = None,
    ) -> bool:
        """Power on the unit by writing ONLY REG_RUN_STOP=1, no bundled write.

        Used to diagnose whether the intermittent on-failure is specific to
        the 5-register bundle (FC 0x10 with count=5) vs. a single-register
        write (FC 0x10 with count=1). Mode/fan/setpoint are not touched —
        the unit keeps whatever was already configured.
        """
        user = await self._resolve_user(context)
        name = self.unit_name(unit_index)

        if not self.gateway_controllable:
            _LOGGER.warning(
                "RUNSTOP_ONLY_BLOCKED unit=%s user=%s — gateway not controllable",
                name, user,
            )
            persistent_notification.async_create(
                self.hass,
                f"Cannot power on {name} (run_stop only): the Modbus gateway is in handshake state.",
                title=f"Hisense VRF — {name}",
                notification_id=f"{DOMAIN}_{self.entry_id}_{unit_index}_gate",
            )
            return False

        _LOGGER.info("RUNSTOP_ONLY unit=%s user=%s — writing only REG_RUN_STOP=1", name, user)
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(
                "RUNSTOP_ONLY_PRE_STATE unit=%s snapshot=%s",
                name, _on_debug_snapshot(self.indoor_states.get(unit_index)),
            )
            base_addr = BASE_ADDR + unit_index * UNIT_STRIDE + REG_RUN_STOP
            _LOGGER.debug(
                "RUNSTOP_ONLY_PAYLOAD unit=%s fc=0x10 base_addr=%d count=1 value=1",
                name, base_addr,
            )

        return await self.async_write_and_verify(
            unit_index,
            pending_attrs={"is_running": True},
            verify_fn=lambda s: s.is_running,
            write_fn=lambda: self.client.turn_on(unit_index),
            context=context,
            verbose_on_debug=True,
            retry_on_no_response=False,
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

        was_controllable = self.gateway_controllable
        self.gateway_state = new_state
        is_controllable = self.gateway_controllable
        if was_controllable and not is_controllable:
            _LOGGER.warning(
                "Gateway ctrl_mode=%s — entering gate state, blocking unit reads/writes",
                new_state.ctrl_mode,
            )
        elif not was_controllable and is_controllable:
            _LOGGER.info("Gateway ctrl_mode=1 — gate state cleared, resuming normal polling")
            self._gate_notified = False

        new_count = new_state.unit_count
        prior_count = self._last_known_unit_count
        if prior_count is not None and new_count != prior_count and is_controllable:
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
                self.indoor_command_slots[unit_index] = None
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
        # Refresh the command slot cache (regs 78..82).
        self.indoor_command_slots[unit_index] = await self._read_command_slot(unit_index)
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
        verbose_on_debug: bool = False,
        retry_on_no_response: bool = False,
        pre_retry_fn: Callable[[], Awaitable[None]] | None = None,
    ) -> bool:
        """Run the write-then-verify cycle for one indoor unit.

        ``verbose_on_debug`` enables extra _LOGGER.debug output (extended
        snapshot per VERIFY and a diagnostic re-read after WRITE_FAILED).
        ``retry_on_no_response`` re-sends the same write_fn one extra time
        if the unit never reflected the change after the first round.
        ``pre_retry_fn``, when set, is awaited before each retry round's
        write_fn — the power-on path uses it to force a 0->1 edge.
        All three are used by the power-on path today.
        """
        user = await self._resolve_user(context)
        name = self.unit_name(unit_index)
        verbose = verbose_on_debug and _LOGGER.isEnabledFor(logging.DEBUG)
        total_rounds = 2 if retry_on_no_response else 1
        total_reads = self.verify_retries + 1

        # An explicit OFF supersedes any in-flight power-on retry: the user no
        # longer wants the unit on, so stop resending immediately.
        if pending_attrs.get("is_running") is False:
            self._cancel_on_retry(unit_index, reason="OFF command")

        if not self.gateway_controllable:
            _LOGGER.warning(
                "WRITE_BLOCKED unit=%s user=%s — gateway not controllable (ctrl_mode!=1)",
                name, user,
            )
            persistent_notification.async_create(
                self.hass,
                f"Cannot send command to {name}: the Modbus gateway is in handshake state "
                "and not accepting commands. Wait until the RUN light flashes again, then retry.",
                title=f"Hisense VRF — {name}",
                notification_id=f"{DOMAIN}_{self.entry_id}_{unit_index}_gate",
            )
            self.last_write_status[unit_index] = {
                "status": WRITE_STATUS_FAILED,
                "fields": list(pending_attrs.keys()),
                "expected": dict(pending_attrs),
                "error": "gateway not controllable (ctrl_mode!=1)",
                "user": user,
                "timestamp": _now_iso(),
            }
            self._notify()
            return False

        async with self._lock_for(unit_index):
            _LOGGER.info(
                "WRITE unit=%s user=%s expected=%s delay=%.1fs retries=%d rounds=%d",
                name, user, pending_attrs, self.verify_delay_s, self.verify_retries, total_rounds,
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

            last_state: ACDeviceState | None = None
            actual: dict[str, Any] = {}

            for round_idx in range(total_rounds):
                if round_idx > 0:
                    _LOGGER.warning(
                        "ON_RETRY_AFTER_NO_RESPONSE unit=%s round=%d/%d — re-sending FC 0x10",
                        name, round_idx + 1, total_rounds,
                    )
                    if pre_retry_fn is not None:
                        await pre_retry_fn()

                try:
                    await write_fn()
                except ModbusReadError as err:
                    _LOGGER.error(
                        "WRITE unit=%s user=%s round=%d/%d send-failed: %s",
                        name, user, round_idx + 1, total_rounds, err,
                    )
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

                _LOGGER.info(
                    "WRITE unit=%s user=%s round=%d/%d sent, verifying...",
                    name, user, round_idx + 1, total_rounds,
                )
                if verbose:
                    _LOGGER.debug(
                        "ON_WRITE_RETURNED unit=%s round=%d/%d (no exception, FC 0x10 accepted by gateway)",
                        name, round_idx + 1, total_rounds,
                    )

                for attempt in range(total_reads):
                    await asyncio.sleep(self.verify_delay_s)
                    try:
                        state = await self.client.read_device(unit_index)
                    except ModbusReadError as err:
                        _LOGGER.warning(
                            "VERIFY unit=%s round=%d/%d attempt=%d/%d read-failed: %s",
                            name, round_idx + 1, total_rounds, attempt + 1, total_reads, err,
                        )
                        continue

                    last_state = state
                    self.indoor_states[unit_index] = state

                    actual = {k: getattr(state, k, None) for k in pending_attrs}
                    match = verify_fn(state)
                    _LOGGER.info(
                        "VERIFY unit=%s round=%d/%d attempt=%d/%d read=%s match=%s",
                        name, round_idx + 1, total_rounds, attempt + 1, total_reads, actual, match,
                    )
                    if verbose:
                        _LOGGER.debug(
                            "ON_VERIFY_EXTRA unit=%s round=%d/%d attempt=%d/%d snapshot=%s",
                            name, round_idx + 1, total_rounds, attempt + 1, total_reads,
                            _on_debug_snapshot(state),
                        )
                        slot_now = await self._read_command_slot(unit_index)
                        slot_consumed = slot_now == [0xFF] * 5
                        _LOGGER.debug(
                            "ON_VERIFY_SLOT unit=%s round=%d/%d attempt=%d/%d slot=%s consumed=%s",
                            name, round_idx + 1, total_rounds, attempt + 1, total_reads,
                            slot_now, slot_consumed,
                        )

                    if match:
                        self._clear_pending(unit_index, pending_attrs)
                        self.last_write_status[unit_index] = {
                            "status": WRITE_STATUS_CONFIRMED,
                            "fields": list(pending_attrs.keys()),
                            "expected": dict(pending_attrs),
                            "actual": actual,
                            "attempts": attempt + 1,
                            "round": round_idx + 1,
                            "user": user,
                            "timestamp": _now_iso(),
                        }
                        _LOGGER.info(
                            "WRITE_CONFIRMED unit=%s user=%s round=%d/%d attempts=%d expected=%s actual=%s",
                            name, user, round_idx + 1, total_rounds, attempt + 1, pending_attrs, actual,
                        )
                        self._on_write_confirmed(unit_index)
                        self._notify()
                        return True

            # all rounds exhausted without match
            self._clear_pending(unit_index, pending_attrs)
            final_actual: dict[str, Any] = {}
            if last_state is not None:
                for k in pending_attrs:
                    if hasattr(last_state, k):
                        final_actual[k] = getattr(last_state, k)
            self.last_write_status[unit_index] = {
                "status": WRITE_STATUS_FAILED,
                "fields": list(pending_attrs.keys()),
                "expected": dict(pending_attrs),
                "actual": final_actual,
                "attempts": total_reads,
                "rounds": total_rounds,
                "user": user,
                "timestamp": _now_iso(),
            }
            _LOGGER.warning(
                "WRITE_FAILED unit=%s user=%s rounds=%d attempts=%d expected=%s actual=%s",
                name, user, total_rounds, total_reads, pending_attrs, final_actual,
            )
            if verbose:
                try:
                    diag_state = await self.client.read_device(unit_index)
                except ModbusReadError as err:
                    _LOGGER.debug(
                        "ON_FAILED_DIAG unit=%s re-read failed: %s", name, err,
                    )
                else:
                    self.indoor_states[unit_index] = diag_state
                    _LOGGER.debug(
                        "ON_FAILED_DIAG unit=%s post-fail snapshot=%s",
                        name, _on_debug_snapshot(diag_state),
                    )
                slot_post = await self._read_command_slot(unit_index)
                _LOGGER.debug(
                    "ON_FAILED_DIAG_SLOT unit=%s slot=%s stuck=%s",
                    name, slot_post, slot_post is not None and slot_post != [0xFF] * 5,
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
