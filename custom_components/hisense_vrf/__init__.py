"""Hisense VRF — Home Assistant integration for i-Modkit Modbus TCP gateways."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from pyacmodbus import ACModbusClient, CannotConnect, ModbusReadError

from .const import (
    CONF_CONNECT_TIMEOUT,
    CONF_OFF_PENDING_TTL,
    CONF_ON_EDGE_FORCE,
    CONF_ON_RETRY,
    CONF_POLL_GATEWAY_EVERY_N,
    CONF_POLL_INTERVAL,
    CONF_POLL_SPACING,
    CONF_POLLING_ENABLED,
    CONF_VERIFY_DELAY,
    CONF_VERIFY_RETRIES,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_OFF_PENDING_TTL,
    DEFAULT_ON_EDGE_FORCE,
    DEFAULT_ON_RETRY,
    DEFAULT_POLL_GATEWAY_EVERY_N,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_POLL_SPACING,
    DEFAULT_POLLING_ENABLED,
    DEFAULT_VERIFY_DELAY,
    DEFAULT_VERIFY_RETRIES,
    PLATFORMS,
)
from .controller import HisenseVRFController

_LOGGER = logging.getLogger(__name__)

type HisenseVRFConfigEntry = ConfigEntry[HisenseVRFController]


async def async_setup_entry(hass: HomeAssistant, entry: HisenseVRFConfigEntry) -> bool:
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    verify_delay = float(entry.options.get(CONF_VERIFY_DELAY, DEFAULT_VERIFY_DELAY))
    verify_retries = int(entry.options.get(CONF_VERIFY_RETRIES, DEFAULT_VERIFY_RETRIES))
    off_pending_ttl = float(entry.options.get(CONF_OFF_PENDING_TTL, DEFAULT_OFF_PENDING_TTL))
    polling_enabled = bool(entry.options.get(CONF_POLLING_ENABLED, DEFAULT_POLLING_ENABLED))
    poll_interval = float(entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))
    poll_spacing = float(entry.options.get(CONF_POLL_SPACING, DEFAULT_POLL_SPACING))
    poll_gw_every = int(entry.options.get(CONF_POLL_GATEWAY_EVERY_N, DEFAULT_POLL_GATEWAY_EVERY_N))
    connect_timeout = float(entry.options.get(CONF_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT))
    on_edge_force = bool(entry.options.get(CONF_ON_EDGE_FORCE, DEFAULT_ON_EDGE_FORCE))
    on_retry = bool(entry.options.get(CONF_ON_RETRY, DEFAULT_ON_RETRY))

    client = ACModbusClient(host=host, port=port)
    controller = HisenseVRFController(
        hass=hass,
        entry_id=entry.entry_id,
        client=client,
        verify_delay_s=verify_delay,
        verify_retries=verify_retries,
        off_pending_ttl_s=off_pending_ttl,
        polling_enabled=polling_enabled,
        poll_interval_s=poll_interval,
        poll_spacing_s=poll_spacing,
        poll_gateway_every_n=poll_gw_every,
        on_edge_force=on_edge_force,
        on_retry=on_retry,
    )

    try:
        async with asyncio.timeout(connect_timeout):
            await controller.async_initial_scan()
    except TimeoutError as err:
        raise ConfigEntryNotReady(
            f"Initial scan timed out after {connect_timeout}s on {host}:{port}"
        ) from err
    except CannotConnect as err:
        raise ConfigEntryNotReady(f"Cannot connect to {host}:{port}") from err
    except ModbusReadError as err:
        raise ConfigEntryNotReady(f"Initial scan failed: {err}") from err

    entry.runtime_data = controller

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await controller.async_start_polling()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HisenseVRFConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_shutdown()
    return unload_ok
