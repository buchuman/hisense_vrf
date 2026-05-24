"""Tests for HisenseVRFController.

Cover write-and-verify, off-pending TTL, external power-on discard, polling
loop, and pending overlay logic. All Modbus calls are mocked.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from custom_components.hisense_vrf.const import (
    WRITE_STATUS_CONFIRMED,
    WRITE_STATUS_FAILED,
    WRITE_STATUS_OFF_PENDING,
    WRITE_STATUS_PENDING,
)
from custom_components.hisense_vrf.controller import HisenseVRFController
from pyacmodbus import (
    FAN_AUTO,
    FAN_HIGH,
    MODE_COOL,
    MODE_HEAT,
    ModbusReadError,
)

from . import make_gateway_state, make_indoor_state, make_outdoor_state


@pytest.fixture
async def controller(hass, mock_client):
    """A fresh controller wired to the mock client; not yet scanned."""
    c = HisenseVRFController(
        hass=hass,
        entry_id="test_entry_id",
        client=mock_client,
        verify_delay_s=0.01,
        verify_retries=2,
        off_pending_ttl_s=0.2,  # short for tests
        polling_enabled=False,
        poll_interval_s=0.05,
        poll_spacing_s=0.0,
        poll_gateway_every_n=2,
    )
    yield c
    # Teardown: cancel any leftover timers/tasks so HA's lingering-resource
    # check doesn't fail unrelated tests.
    await c.async_shutdown()


@pytest.fixture
async def scanned_controller(controller, mock_client):
    """Controller that already finished the initial scan."""
    await controller.async_initial_scan()
    return controller


# ── async_initial_scan ──────────────────────────────────────────────────────


async def test_initial_scan_populates_state(scanned_controller, mock_client):
    assert scanned_controller.unit_indices == [0, 1]
    assert scanned_controller.unit_identifiers == {0: (0, 0), 1: (0, 1)}
    assert scanned_controller.unit_capacities == {0: 22, 1: 22}
    assert scanned_controller.outdoor_units == [(0, 0)]
    assert scanned_controller.indoor_states == {0: None, 1: None}
    mock_client.connect.assert_awaited_once()


async def test_initial_scan_identifier_failure_uses_fallback(controller, mock_client):
    mock_client.read_unit_identifiers.side_effect = ModbusReadError("boom")
    await controller.async_initial_scan()
    assert controller.unit_identifiers == {0: (0, 0), 1: (0, 1)}


async def test_initial_scan_outdoor_failure_keeps_empty(controller, mock_client):
    mock_client.read_outdoor_connections.side_effect = ModbusReadError("boom")
    await controller.async_initial_scan()
    assert controller.outdoor_units == []


# ── unit_name / unit_model / unit_register_base ──────────────────────────────


async def test_unit_name_format(scanned_controller):
    assert scanned_controller.unit_name(0) == "ac_indoor_unit_0000"
    assert scanned_controller.unit_name(1) == "ac_indoor_unit_0001"


async def test_unit_model_with_capacity(scanned_controller):
    assert scanned_controller.unit_model(0) == "VRF Indoor 22 kBTU"


async def test_unit_model_without_capacity(controller):
    await controller.async_initial_scan()
    controller.unit_capacities[0] = 0
    assert controller.unit_model(0) == "VRF Indoor"


async def test_register_base_formula(scanned_controller):
    assert scanned_controller.unit_register_base(0) == 40000
    assert scanned_controller.unit_register_base(1) == 40091


# ── is_powered_on / get_display_state ────────────────────────────────────────


async def test_is_powered_on_no_state(scanned_controller):
    assert scanned_controller.is_powered_on(0) is False


async def test_is_powered_on_with_state(scanned_controller):
    scanned_controller.indoor_states[0] = make_indoor_state(is_running=True)
    assert scanned_controller.is_powered_on(0) is True
    scanned_controller.indoor_states[0] = make_indoor_state(is_running=False)
    assert scanned_controller.is_powered_on(0) is False


async def test_display_state_applies_pending_overlay(scanned_controller):
    scanned_controller.indoor_states[0] = make_indoor_state(setpoint=22.0)
    scanned_controller.pending[0] = {"setpoint": 24.0}
    display = scanned_controller.get_display_state(0)
    assert display.setpoint == 24.0
    # Underlying state is unchanged
    assert scanned_controller.indoor_states[0].setpoint == 22.0


async def test_display_state_applies_off_pending_overlay(scanned_controller):
    scanned_controller.indoor_states[0] = make_indoor_state(setpoint=22.0, is_running=False)
    scanned_controller._off_pending[0] = {"setpoint": 25.0, "fan_speed": FAN_HIGH}
    display = scanned_controller.get_display_state(0)
    assert display.setpoint == 25.0
    assert display.fan_speed == FAN_HIGH


# ── async_refresh_unit / async_refresh_all ───────────────────────────────────


async def test_refresh_unit_populates_state(scanned_controller, mock_client):
    state = await scanned_controller.async_refresh_unit(0)
    assert state is not None
    assert scanned_controller.indoor_states[0] is state
    mock_client.read_device.assert_awaited()


async def test_refresh_unit_failure_returns_none(scanned_controller, mock_client):
    mock_client.read_device.side_effect = ModbusReadError("nope")
    result = await scanned_controller.async_refresh_unit(0)
    assert result is None


async def test_refresh_unit_detects_external_power_on(scanned_controller, mock_client):
    # Off_pending accumulated while unit is off
    scanned_controller.indoor_states[0] = make_indoor_state(is_running=False)
    scanned_controller._off_pending[0] = {"setpoint": 26.0}
    # New read returns is_running=True
    mock_client.read_device.side_effect = lambda idx: make_indoor_state(
        unit_index=idx, is_running=True
    )
    await scanned_controller.async_refresh_unit(0)
    assert scanned_controller._off_pending.get(0, {}) == {}


async def test_refresh_all_reads_everything(scanned_controller, mock_client):
    # initial_scan also reads the gateway once to seed the dynamic-devices
    # baseline; reset the counter so we only count refresh_all's read.
    mock_client.read_gateway.reset_mock()
    await scanned_controller.async_refresh_all()
    assert mock_client.read_gateway.await_count == 1
    assert mock_client.read_device.await_count == 2
    assert mock_client.read_outdoor_unit.await_count == 1


# ── async_write_and_verify ───────────────────────────────────────────────────


async def test_write_and_verify_confirmed_on_first_read(scanned_controller, mock_client):
    scanned_controller.indoor_states[0] = make_indoor_state(setpoint=22.0)
    mock_client.read_device.side_effect = lambda idx: make_indoor_state(
        unit_index=idx, setpoint=24.0
    )
    write_fn = AsyncMock()
    result = await scanned_controller.async_write_and_verify(
        0,
        pending_attrs={"setpoint": 24.0},
        verify_fn=lambda s: s.setpoint == 24.0,
        write_fn=write_fn,
    )
    assert result is True
    assert scanned_controller.last_write_status[0]["status"] == WRITE_STATUS_CONFIRMED
    write_fn.assert_awaited_once()


async def test_write_and_verify_failed_after_retries(scanned_controller, mock_client):
    scanned_controller.indoor_states[0] = make_indoor_state(setpoint=22.0)
    # State never changes; verify_fn always returns False.
    write_fn = AsyncMock()
    result = await scanned_controller.async_write_and_verify(
        0,
        pending_attrs={"setpoint": 24.0},
        verify_fn=lambda s: False,
        write_fn=write_fn,
    )
    assert result is False
    assert scanned_controller.last_write_status[0]["status"] == WRITE_STATUS_FAILED
    # 3 reads: 1 initial + 2 retries (verify_retries=2)
    assert mock_client.read_device.await_count == 3


async def test_write_and_verify_write_failure_records_status(scanned_controller, mock_client):
    scanned_controller.indoor_states[0] = make_indoor_state()
    write_fn = AsyncMock(side_effect=ModbusReadError("send failed"))
    result = await scanned_controller.async_write_and_verify(
        0,
        pending_attrs={"setpoint": 24.0},
        verify_fn=lambda s: True,
        write_fn=write_fn,
    )
    assert result is False
    status = scanned_controller.last_write_status[0]
    assert status["status"] == WRITE_STATUS_FAILED
    assert "error" in status


async def test_write_and_verify_pending_overlay_during_verify(scanned_controller, mock_client):
    scanned_controller.indoor_states[0] = make_indoor_state(setpoint=22.0)
    # While the write is running, pending overlay should be applied.
    saw_pending = []

    async def slow_read(idx):
        saw_pending.append(scanned_controller.get_display_state(idx).setpoint)
        return make_indoor_state(unit_index=idx, setpoint=24.0)

    mock_client.read_device.side_effect = slow_read
    await scanned_controller.async_write_and_verify(
        0,
        pending_attrs={"setpoint": 24.0},
        verify_fn=lambda s: s.setpoint == 24.0,
        write_fn=AsyncMock(),
    )
    # Display state during verify saw the pending value
    assert saw_pending and saw_pending[0] == 24.0


# ── Off-pending and TTL ─────────────────────────────────────────────────────


async def test_accumulate_off_pending_starts_timer(scanned_controller):
    scanned_controller.accumulate_off_pending(0, {"setpoint": 24.0}, user="test")
    assert scanned_controller._off_pending[0] == {"setpoint": 24.0}
    assert 0 in scanned_controller._off_timers
    assert (
        scanned_controller.last_write_status[0]["status"] == WRITE_STATUS_OFF_PENDING
    )


async def test_accumulate_off_pending_merges_multiple_changes(scanned_controller):
    scanned_controller.accumulate_off_pending(0, {"setpoint": 24.0}, user="u")
    scanned_controller.accumulate_off_pending(0, {"fan_speed": FAN_HIGH}, user="u")
    assert scanned_controller._off_pending[0] == {
        "setpoint": 24.0,
        "fan_speed": FAN_HIGH,
    }


async def test_off_pending_ttl_discards(scanned_controller):
    scanned_controller.accumulate_off_pending(0, {"setpoint": 24.0}, user="u")
    # TTL configured at 0.2s
    await asyncio.sleep(0.35)
    assert scanned_controller._off_pending.get(0, {}) == {}


async def test_external_power_on_discards_off_pending(scanned_controller, mock_client):
    scanned_controller.indoor_states[0] = make_indoor_state(is_running=False)
    scanned_controller.accumulate_off_pending(0, {"setpoint": 24.0}, user="u")
    assert 0 in scanned_controller._off_timers

    mock_client.read_device.side_effect = lambda idx: make_indoor_state(
        unit_index=idx, is_running=True
    )
    await scanned_controller.async_refresh_unit(0)
    assert scanned_controller._off_pending.get(0, {}) == {}
    assert 0 not in scanned_controller._off_timers


# ── async_send_on_with_pending ───────────────────────────────────────────────


async def test_send_on_with_pending_fires_bundled_write(scanned_controller, mock_client):
    scanned_controller.indoor_states[0] = make_indoor_state(
        is_running=False,
        current_mode=MODE_COOL,
        fan_speed=FAN_AUTO,
        setpoint=22.0,
        auto_swing=True,
        louver_position=0,
    )
    mock_client.read_device.side_effect = lambda idx: make_indoor_state(
        unit_index=idx,
        is_running=True,
        current_mode=MODE_HEAT,
        fan_speed=FAN_AUTO,
        setpoint=24.0,
        auto_swing=True,
        louver_position=0,
    )

    result = await scanned_controller.async_send_on_with_pending(
        0, mode_override=MODE_HEAT
    )
    assert result is True
    mock_client.write_control_block.assert_awaited_once()
    call = mock_client.write_control_block.await_args
    # write_control_block(unit_index, 1, mode, fan, swing, temp)
    assert call.args[0] == 0
    assert call.args[1] == 1  # run
    assert call.args[2] == MODE_HEAT  # mode override
    assert call.args[3] == FAN_AUTO  # from state
    # swing reg = (1 if auto else 0) | (pos << 1) = 1
    assert call.args[4] == 1
    assert call.args[5] == 22  # int(setpoint)


async def test_send_on_with_pending_missing_fields_blocked(scanned_controller, mock_client):
    # State exists but has no setpoint, and off_pending doesn't fill it either.
    scanned_controller.indoor_states[0] = make_indoor_state(
        is_running=False,
        setpoint=None,
        current_mode=MODE_COOL,
        fan_speed=FAN_AUTO,
    )
    result = await scanned_controller.async_send_on_with_pending(0)
    assert result is False
    mock_client.write_control_block.assert_not_awaited()
    status = scanned_controller.last_write_status[0]
    assert status["status"] == WRITE_STATUS_FAILED
    assert "missing" in status.get("error", "").lower()


async def test_send_on_with_pending_uses_off_pending_overlay(scanned_controller, mock_client):
    scanned_controller.indoor_states[0] = make_indoor_state(
        is_running=False, setpoint=22.0, current_mode=MODE_COOL,
        fan_speed=FAN_AUTO, auto_swing=True, louver_position=0,
    )
    # User accumulated a setpoint change
    scanned_controller._off_pending[0] = {"setpoint": 26.0}
    mock_client.read_device.side_effect = lambda idx: make_indoor_state(
        unit_index=idx, is_running=True, current_mode=MODE_COOL,
        fan_speed=FAN_AUTO, setpoint=26.0, auto_swing=True, louver_position=0,
    )
    await scanned_controller.async_send_on_with_pending(0)
    call = mock_client.write_control_block.await_args
    assert call.args[5] == 26  # setpoint from off_pending, not state


# ── Polling loop ─────────────────────────────────────────────────────────────


async def test_polling_loop_skips_units_with_active_verify(scanned_controller, mock_client):
    # Mark unit 0 as having an active write
    scanned_controller.pending[0] = {"setpoint": 24.0}
    scanned_controller.polling_enabled = True

    mock_client.read_device.reset_mock()
    await scanned_controller.async_start_polling()
    await asyncio.sleep(0.1)
    await scanned_controller.async_stop_polling()

    # Verify unit 0 was NOT read but unit 1 was
    read_indices = [c.args[0] for c in mock_client.read_device.await_args_list]
    assert 0 not in read_indices
    assert 1 in read_indices


async def test_polling_loop_reads_gateway_every_n_cycles(scanned_controller, mock_client):
    scanned_controller.polling_enabled = True
    scanned_controller.poll_gateway_every_n = 1  # every cycle
    scanned_controller.poll_interval_s = 0.05

    mock_client.read_gateway.reset_mock()
    await scanned_controller.async_start_polling()
    await asyncio.sleep(0.15)  # at least 2 cycles
    await scanned_controller.async_stop_polling()
    assert mock_client.read_gateway.await_count >= 2


async def test_polling_disabled_does_not_start_loop(controller):
    await controller.async_initial_scan()
    controller.polling_enabled = False
    await controller.async_start_polling()
    assert controller._polling_task is None


async def test_polling_stop_cancels_task(scanned_controller):
    scanned_controller.polling_enabled = True
    await scanned_controller.async_start_polling()
    assert scanned_controller._polling_task is not None
    await scanned_controller.async_stop_polling()
    assert scanned_controller._polling_task is None


# ── Repair issues (write failures) ──────────────────────────────────────────


async def test_repair_issue_created_after_threshold_writes_failed(
    scanned_controller, mock_client
):
    from homeassistant.helpers import issue_registry as ir
    from custom_components.hisense_vrf.const import DOMAIN

    scanned_controller.indoor_states[0] = make_indoor_state(setpoint=22.0)
    write_fn = AsyncMock()
    # Three failed writes in a row
    for _ in range(3):
        await scanned_controller.async_write_and_verify(
            0,
            pending_attrs={"setpoint": 24.0},
            verify_fn=lambda s: False,  # always mismatch
            write_fn=write_fn,
        )

    registry = ir.async_get(scanned_controller.hass)
    issue_id = scanned_controller._write_failure_issue_id(0)
    assert registry.async_get_issue(DOMAIN, issue_id) is not None


async def test_repair_issue_dismissed_on_next_success(
    scanned_controller, mock_client
):
    from homeassistant.helpers import issue_registry as ir
    from custom_components.hisense_vrf.const import DOMAIN

    # Trigger the issue first
    scanned_controller.indoor_states[0] = make_indoor_state(setpoint=22.0)
    for _ in range(3):
        await scanned_controller.async_write_and_verify(
            0,
            pending_attrs={"setpoint": 24.0},
            verify_fn=lambda s: False,
            write_fn=AsyncMock(),
        )
    issue_id = scanned_controller._write_failure_issue_id(0)
    registry = ir.async_get(scanned_controller.hass)
    assert registry.async_get_issue(DOMAIN, issue_id) is not None

    # Now a successful write should clear it
    mock_client.read_device.side_effect = lambda idx: make_indoor_state(
        unit_index=idx, setpoint=24.0,
    )
    await scanned_controller.async_write_and_verify(
        0,
        pending_attrs={"setpoint": 24.0},
        verify_fn=lambda s: s.setpoint == 24.0,
        write_fn=AsyncMock(),
    )
    assert registry.async_get_issue(DOMAIN, issue_id) is None


async def test_repair_issue_not_created_under_threshold(
    scanned_controller, mock_client
):
    from homeassistant.helpers import issue_registry as ir
    from custom_components.hisense_vrf.const import DOMAIN

    scanned_controller.indoor_states[0] = make_indoor_state(setpoint=22.0)
    # Only 2 failures — under threshold (3)
    for _ in range(2):
        await scanned_controller.async_write_and_verify(
            0,
            pending_attrs={"setpoint": 24.0},
            verify_fn=lambda s: False,
            write_fn=AsyncMock(),
        )
    registry = ir.async_get(scanned_controller.hass)
    issue_id = scanned_controller._write_failure_issue_id(0)
    assert registry.async_get_issue(DOMAIN, issue_id) is None


# ── Stale device pruning ────────────────────────────────────────────────────


async def test_initial_scan_removes_stale_devices(hass, mock_client):
    """Devices in the registry that aren't in the new scan are removed."""
    from homeassistant.helpers import device_registry as dr
    from custom_components.hisense_vrf.const import DOMAIN

    # First scan finds 3 units
    mock_client.scan_devices.return_value = [0, 1, 2]
    c = HisenseVRFController(
        hass=hass, entry_id="stale_test", client=mock_client,
        verify_delay_s=0.01, verify_retries=0, off_pending_ttl_s=30.0,
        polling_enabled=False, poll_interval_s=5.0, poll_spacing_s=0.0,
        poll_gateway_every_n=10,
    )
    await c.async_initial_scan()

    # Manually register a "ghost" device for a unit that no longer exists
    registry = dr.async_get(hass)
    fake_entry = pytest.importorskip(
        "pytest_homeassistant_custom_component"
    ).common.MockConfigEntry(
        domain=DOMAIN, entry_id="stale_test"
    )
    fake_entry.add_to_hass(hass)
    registry.async_get_or_create(
        config_entry_id="stale_test",
        identifiers={(DOMAIN, "stale_test_indoor_99")},
        name="Phantom",
    )
    assert any(
        (DOMAIN, "stale_test_indoor_99") in d.identifiers
        for d in dr.async_entries_for_config_entry(registry, "stale_test")
    )

    # Second scan still finds 3 units; ghost should be cleaned
    await c.async_initial_scan()
    await c.async_shutdown()
    remaining = dr.async_entries_for_config_entry(registry, "stale_test")
    assert not any(
        (DOMAIN, "stale_test_indoor_99") in d.identifiers for d in remaining
    )


# ── Additional controller paths ─────────────────────────────────────────────


async def test_verify_read_failure_continues_loop(scanned_controller, mock_client):
    """A failed read during verify shouldn't abort the cycle; it should be retried."""
    scanned_controller.indoor_states[0] = make_indoor_state(setpoint=22.0)
    call_count = {"n": 0}

    def side_effect(idx):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ModbusReadError("transient")
        return make_indoor_state(unit_index=idx, setpoint=24.0)

    mock_client.read_device.side_effect = side_effect
    result = await scanned_controller.async_write_and_verify(
        0,
        pending_attrs={"setpoint": 24.0},
        verify_fn=lambda s: s.setpoint == 24.0,
        write_fn=AsyncMock(),
    )
    assert result is True
    assert mock_client.read_device.await_count >= 2  # 1 fail + 1 success


async def test_refresh_all_with_outdoor_failure(scanned_controller, mock_client):
    mock_client.read_outdoor_unit.side_effect = ModbusReadError("outdoor down")
    await scanned_controller.async_refresh_all()
    # No crash; outdoor entry remains as it was (None)
    assert scanned_controller.outdoor_states[(0, 0)] is None


async def test_send_on_with_pending_write_failure(scanned_controller, mock_client):
    """Write failure during ON event is recorded as failed status."""
    scanned_controller.indoor_states[0] = make_indoor_state(
        is_running=False, current_mode=MODE_COOL, fan_speed=FAN_AUTO,
        setpoint=22.0, auto_swing=True, louver_position=0,
    )
    mock_client.write_control_block.side_effect = ModbusReadError("send failed")
    result = await scanned_controller.async_send_on_with_pending(0)
    assert result is False
    status = scanned_controller.last_write_status[0]
    assert status["status"] in ("failed",)


async def test_capability_pending_fields_returns_merged(scanned_controller):
    scanned_controller.pending[0] = {"setpoint": 24.0}
    scanned_controller._off_pending[0] = {"fan_speed": 2}
    fields = set(scanned_controller.pending_fields(0))
    assert "setpoint" in fields
    assert "fan_speed" in fields


async def test_is_field_pending_works_for_either_overlay(scanned_controller):
    scanned_controller.pending[0] = {"setpoint": 24.0}
    scanned_controller._off_pending[0] = {"fan_speed": 2}
    assert scanned_controller.is_field_pending(0, "setpoint") is True
    assert scanned_controller.is_field_pending(0, "fan_speed") is True
    assert scanned_controller.is_field_pending(0, "current_mode") is False


# ── Availability tracking (log-when-unavailable) ────────────────────────────


async def test_unit_marked_unavailable_after_n_failures(scanned_controller, mock_client, caplog):
    import logging
    caplog.set_level(logging.WARNING)
    mock_client.read_device.side_effect = ModbusReadError("comms down")
    # 3 consecutive failures (UNAVAILABLE_THRESHOLD)
    for _ in range(3):
        await scanned_controller.async_refresh_unit(0)
    assert scanned_controller.indoor_states[0] is None
    assert any("UNAVAILABLE unit=ac_indoor_unit_0000" in r.message for r in caplog.records)


async def test_unit_recovers_after_success(scanned_controller, mock_client, caplog):
    import logging
    # Trigger the unavailable transition first
    mock_client.read_device.side_effect = ModbusReadError("comms down")
    for _ in range(3):
        await scanned_controller.async_refresh_unit(0)
    assert scanned_controller.indoor_states[0] is None
    caplog.clear()
    caplog.set_level(logging.INFO)
    # Now succeed
    mock_client.read_device.side_effect = lambda idx: make_indoor_state(unit_index=idx)
    await scanned_controller.async_refresh_unit(0)
    assert scanned_controller.indoor_states[0] is not None
    assert scanned_controller._unit_read_failures[0] == 0
    assert any("AVAILABLE unit=ac_indoor_unit_0000" in r.message for r in caplog.records)


async def test_unit_transient_failure_does_not_mark_unavailable(scanned_controller, mock_client):
    # Seed a last-known state first
    scanned_controller.indoor_states[0] = make_indoor_state(unit_index=0)
    # 2 failures (under threshold) → state stays, no unavailable
    call_count = {"n": 0}

    def side_effect(idx):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            raise ModbusReadError("blip")
        return make_indoor_state(unit_index=idx)

    mock_client.read_device.side_effect = side_effect
    await scanned_controller.async_refresh_unit(0)  # fail 1
    await scanned_controller.async_refresh_unit(0)  # fail 2
    assert scanned_controller.indoor_states[0] is not None  # still last known
    assert scanned_controller._unit_read_failures[0] == 2
    await scanned_controller.async_refresh_unit(0)  # success
    assert scanned_controller._unit_read_failures[0] == 0


async def test_gateway_marked_unavailable_after_n_failures(scanned_controller, mock_client, caplog):
    import logging
    caplog.set_level(logging.WARNING)
    mock_client.read_gateway.side_effect = ModbusReadError("comms down")
    for _ in range(3):
        await scanned_controller._read_and_track_gateway()
    assert scanned_controller.gateway_state is None
    assert any("UNAVAILABLE gateway" in r.message for r in caplog.records)


async def test_gateway_recovers(scanned_controller, mock_client, caplog):
    import logging
    mock_client.read_gateway.side_effect = ModbusReadError("comms down")
    for _ in range(3):
        await scanned_controller._read_and_track_gateway()
    caplog.clear()
    caplog.set_level(logging.INFO)
    mock_client.read_gateway.side_effect = None
    mock_client.read_gateway.return_value = make_gateway_state()
    await scanned_controller._read_and_track_gateway()
    assert scanned_controller.gateway_state is not None
    assert scanned_controller._gateway_read_failures == 0
    assert any("AVAILABLE gateway" in r.message for r in caplog.records)


# ── _resolve_user ────────────────────────────────────────────────────────────


async def test_resolve_user_no_context(scanned_controller):
    assert await scanned_controller._resolve_user(None) == "system"


async def test_resolve_user_with_known_user(scanned_controller, hass):
    user = await hass.auth.async_create_user(name="Alice")
    from homeassistant.core import Context
    ctx = Context(user_id=user.id)
    assert await scanned_controller._resolve_user(ctx) == "Alice"


async def test_resolve_user_unknown_user_id(scanned_controller):
    from homeassistant.core import Context
    ctx = Context(user_id="0123456789abcdef" * 2)
    name = await scanned_controller._resolve_user(ctx)
    assert name.startswith("user:")


# ── Dynamic devices (gateway unit_count delta) ──────────────────────────────


async def test_dynamic_rescan_adds_new_indoor_unit(scanned_controller, mock_client):
    """When unit_count changes, a rescan picks up the new unit and dispatches a signal."""
    from custom_components.hisense_vrf.const import signal_new_indoor
    from homeassistant.core import callback
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    assert scanned_controller.unit_indices == [0, 1]
    assert scanned_controller._last_known_unit_count == 2

    received: list[int] = []

    @callback
    def _capture(idx: int) -> None:
        received.append(idx)

    async_dispatcher_connect(
        scanned_controller.hass,
        signal_new_indoor("test_entry_id"),
        _capture,
    )

    # Gateway now reports 3 units; scan returns [0, 1, 2].
    mock_client.read_gateway.return_value = make_gateway_state(unit_count=3)
    mock_client.scan_devices.return_value = [0, 1, 2]
    mock_client.read_unit_identifiers.side_effect = lambda idx: (0, idx)
    mock_client.read_unit_capacity.return_value = 22

    await scanned_controller._read_and_track_gateway()
    await scanned_controller.hass.async_block_till_done()

    assert 2 in scanned_controller.unit_indices
    assert scanned_controller.unit_identifiers[2] == (0, 2)
    assert scanned_controller.unit_capacities[2] == 22
    assert scanned_controller._last_known_unit_count == 3
    assert received == [2]


async def test_dynamic_rescan_no_op_when_count_unchanged(scanned_controller, mock_client):
    """If unit_count stays the same, scan_devices is not invoked again."""
    mock_client.scan_devices.reset_mock()
    await scanned_controller._read_and_track_gateway()
    mock_client.scan_devices.assert_not_awaited()


async def test_dynamic_rescan_adds_new_outdoor_module(scanned_controller, mock_client):
    """A second outdoor module appearing in read_outdoor_connections is picked up."""
    from custom_components.hisense_vrf.const import signal_new_outdoor
    from homeassistant.core import callback
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    received: list[tuple[int, int]] = []

    @callback
    def _capture(ou: tuple[int, int]) -> None:
        received.append(ou)

    async_dispatcher_connect(
        scanned_controller.hass,
        signal_new_outdoor("test_entry_id"),
        _capture,
    )

    # Indoor count changes (forces rescan) and outdoor now has 2 modules.
    mock_client.read_gateway.return_value = make_gateway_state(unit_count=3)
    mock_client.scan_devices.return_value = [0, 1]
    mock_client.read_outdoor_connections.return_value = [(0, 0), (1, 0)]

    await scanned_controller._read_and_track_gateway()
    await scanned_controller.hass.async_block_till_done()

    assert (1, 0) in scanned_controller.outdoor_units
    assert received == [(1, 0)]


async def test_dynamic_rescan_scan_failure_does_not_crash(scanned_controller, mock_client):
    mock_client.read_gateway.return_value = make_gateway_state(unit_count=3)
    mock_client.scan_devices.side_effect = ModbusReadError("scan down")
    # Must not raise.
    await scanned_controller._read_and_track_gateway()
    assert scanned_controller.unit_indices == [0, 1]  # unchanged
    # Baseline still updated so we don't keep retrying on every poll.
    assert scanned_controller._last_known_unit_count == 3
