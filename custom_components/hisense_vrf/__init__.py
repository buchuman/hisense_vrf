"""Hisense VRF — Home Assistant integration for i-Modkit Modbus TCP gateways."""
from __future__ import annotations

import importlib.util
import logging
import os
import sys


def _ensure_pyacmodbus_loaded() -> None:
    """Load bundled pyacmodbus if it isn't already importable from site-packages.

    The library is loaded by file path so we don't touch ``sys.path`` —
    prepending the custom-component directory there would shadow stdlib
    modules like ``select`` with our own platform files.
    """
    try:
        import pyacmodbus  # noqa: F401
        return
    except ImportError:
        pass
    bundle = os.path.join(os.path.dirname(__file__), "pyacmodbus", "__init__.py")
    if not os.path.isfile(bundle):
        raise
    spec = importlib.util.spec_from_file_location("pyacmodbus", bundle)
    if spec is None or spec.loader is None:
        raise ImportError("Failed to load bundled pyacmodbus")
    module = importlib.util.module_from_spec(spec)
    sys.modules["pyacmodbus"] = module
    spec.loader.exec_module(module)


_ensure_pyacmodbus_loaded()

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from pyacmodbus import ACModbusClient, CannotConnect, ModbusReadError

from .const import (
    CONF_OFF_PENDING_TTL,
    CONF_POLL_GATEWAY_EVERY_N,
    CONF_POLL_INTERVAL,
    CONF_POLL_SPACING,
    CONF_POLLING_ENABLED,
    CONF_VERIFY_DELAY,
    CONF_VERIFY_RETRIES,
    DEFAULT_OFF_PENDING_TTL,
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
    )

    try:
        await controller.async_initial_scan()
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
