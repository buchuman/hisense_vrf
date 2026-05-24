"""Shared fixtures for Hisense VRF tests."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Ensure custom_components.hisense_vrf is importable.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from custom_components.hisense_vrf.const import (  # noqa: E402
    CONF_OFF_PENDING_TTL,
    CONF_POLL_GATEWAY_EVERY_N,
    CONF_POLL_INTERVAL,
    CONF_POLL_SPACING,
    CONF_POLLING_ENABLED,
    CONF_VERIFY_DELAY,
    CONF_VERIFY_RETRIES,
    DOMAIN,
)
from homeassistant.const import CONF_HOST, CONF_PORT  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from . import (  # noqa: E402
    make_gateway_state,
    make_indoor_state,
    make_outdoor_state,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations in every test (HA test helper)."""
    yield


@pytest.fixture
def mock_client():
    """Provide a mocked ACModbusClient with sane default responses."""
    client = AsyncMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.scan_devices = AsyncMock(return_value=[0, 1])
    client.read_unit_identifiers = AsyncMock(side_effect=lambda idx: (0, idx))
    client.read_unit_capacity = AsyncMock(return_value=22)
    client.read_outdoor_connections = AsyncMock(return_value=[(0, 0)])
    client.read_device = AsyncMock(side_effect=lambda idx: make_indoor_state(unit_index=idx))
    client.read_gateway = AsyncMock(return_value=make_gateway_state())
    client.read_outdoor_unit = AsyncMock(side_effect=lambda s, m: make_outdoor_state(s, m))
    client.write_control_block = AsyncMock()
    client.turn_on = AsyncMock()
    client.turn_off = AsyncMock()
    client.set_setpoint = AsyncMock()
    client.set_mode = AsyncMock()
    client.set_fan_speed = AsyncMock()
    client.set_swing = AsyncMock()
    client.set_dry_mode = AsyncMock()
    client.reset_filter = AsyncMock()
    client.set_prohibition = AsyncMock()
    client.lock_all = AsyncMock()
    client.unlock_all = AsyncMock()
    client.set_alarm_display = AsyncMock()
    client.clear_eeprom = AsyncMock()
    return client


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A fully-populated MockConfigEntry with default options."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Hisense VRF Test",
        data={CONF_HOST: "192.168.1.50", CONF_PORT: 502},
        options={
            CONF_VERIFY_DELAY: 0.01,
            CONF_VERIFY_RETRIES: 2,
            CONF_OFF_PENDING_TTL: 30.0,
            CONF_POLLING_ENABLED: False,  # disabled in tests by default
            CONF_POLL_INTERVAL: 5.0,
            CONF_POLL_SPACING: 0.0,
            CONF_POLL_GATEWAY_EVERY_N: 10,
        },
        unique_id="192.168.1.50:502",
        entry_id="test_entry_id",
    )


@pytest.fixture
async def setup_integration(hass, mock_client, config_entry):
    """Add the entry and load the integration with mocked client."""
    config_entry.add_to_hass(hass)
    with patch(
        "custom_components.hisense_vrf.ACModbusClient",
        return_value=mock_client,
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        yield config_entry


@pytest.fixture
def make_state():
    """Helper to construct customized ACDeviceState in tests."""
    return make_indoor_state
