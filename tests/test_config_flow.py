"""Tests for the Hisense VRF config and options flows."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from custom_components.hisense_vrf.const import (
    CONF_OFF_PENDING_TTL,
    CONF_POLL_GATEWAY_EVERY_N,
    CONF_POLL_INTERVAL,
    CONF_POLL_SPACING,
    CONF_POLLING_ENABLED,
    CONF_VERIFY_DELAY,
    CONF_VERIFY_RETRIES,
    DOMAIN,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResultType
from pyacmodbus import CannotConnect


# ── User step ───────────────────────────────────────────────────────────────


async def test_user_step_success(hass, mock_client):
    """Successful create: validation passes and entry is loaded with the mocked client."""
    with (
        patch(
            "custom_components.hisense_vrf.config_flow._async_validate_connection",
            return_value=None,
        ),
        patch(
            "custom_components.hisense_vrf.ACModbusClient",
            return_value=mock_client,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "10.0.0.10",
                CONF_PORT: 502,
                CONF_VERIFY_DELAY: 2.0,
                CONF_VERIFY_RETRIES: 3,
                CONF_OFF_PENDING_TTL: 30.0,
                CONF_POLLING_ENABLED: False,  # avoid lingering polling task in test
                CONF_POLL_INTERVAL: 5.0,
                CONF_POLL_SPACING: 0.0,
                CONF_POLL_GATEWAY_EVERY_N: 10,
            },
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: "10.0.0.10", CONF_PORT: 502}
    assert result["options"][CONF_VERIFY_DELAY] == 2.0
    assert result["options"][CONF_OFF_PENDING_TTL] == 30.0
    assert result["options"][CONF_POLLING_ENABLED] is False


async def test_user_step_cannot_connect(hass):
    with patch(
        "custom_components.hisense_vrf.config_flow._async_validate_connection",
        side_effect=CannotConnect("nope"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "10.0.0.10",
                CONF_PORT: 502,
                CONF_VERIFY_DELAY: 2.0,
                CONF_VERIFY_RETRIES: 3,
                CONF_OFF_PENDING_TTL: 30.0,
                CONF_POLLING_ENABLED: False,
                CONF_POLL_INTERVAL: 5.0,
                CONF_POLL_SPACING: 0.0,
                CONF_POLL_GATEWAY_EVERY_N: 10,
            },
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_step_unexpected_error(hass):
    with patch(
        "custom_components.hisense_vrf.config_flow._async_validate_connection",
        side_effect=RuntimeError("boom"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "10.0.0.10",
                CONF_PORT: 502,
                CONF_VERIFY_DELAY: 2.0,
                CONF_VERIFY_RETRIES: 3,
                CONF_OFF_PENDING_TTL: 30.0,
                CONF_POLLING_ENABLED: False,
                CONF_POLL_INTERVAL: 5.0,
                CONF_POLL_SPACING: 0.0,
                CONF_POLL_GATEWAY_EVERY_N: 10,
            },
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


async def test_user_step_already_configured(hass, config_entry):
    config_entry.add_to_hass(hass)
    with patch(
        "custom_components.hisense_vrf.config_flow._async_validate_connection",
        return_value=None,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "192.168.1.50",
                CONF_PORT: 502,
                CONF_VERIFY_DELAY: 2.0,
                CONF_VERIFY_RETRIES: 3,
                CONF_OFF_PENDING_TTL: 30.0,
                CONF_POLLING_ENABLED: False,
                CONF_POLL_INTERVAL: 5.0,
                CONF_POLL_SPACING: 0.0,
                CONF_POLL_GATEWAY_EVERY_N: 10,
            },
        )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_step_validates_ranges(hass):
    """Verify range validation rejects out-of-bounds values."""
    with patch(
        "custom_components.hisense_vrf.config_flow._async_validate_connection",
        return_value=None,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        with pytest.raises(Exception):  # vol.MultipleInvalid or similar
            await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_HOST: "10.0.0.10",
                    CONF_PORT: 502,
                    CONF_VERIFY_DELAY: 9999.0,  # way over max
                    CONF_VERIFY_RETRIES: 3,
                    CONF_OFF_PENDING_TTL: 30.0,
                    CONF_POLLING_ENABLED: False,
                    CONF_POLL_INTERVAL: 5.0,
                    CONF_POLL_SPACING: 0.0,
                    CONF_POLL_GATEWAY_EVERY_N: 10,
                },
            )


# ── Options step ────────────────────────────────────────────────────────────


async def test_validate_connection_calls_disconnect(hass):
    """The validator must always disconnect (even on success)."""
    from custom_components.hisense_vrf.config_flow import _async_validate_connection
    from unittest.mock import AsyncMock, MagicMock

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    with patch(
        "custom_components.hisense_vrf.config_flow.ACModbusClient",
        return_value=mock_client,
    ):
        await _async_validate_connection("1.2.3.4", 502)
    mock_client.connect.assert_awaited_once()
    mock_client.disconnect.assert_awaited_once()


async def test_validate_connection_swallows_disconnect_error(hass):
    """If disconnect raises after a successful connect, the validator stays silent."""
    from custom_components.hisense_vrf.config_flow import _async_validate_connection
    from unittest.mock import AsyncMock, MagicMock

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock(side_effect=RuntimeError("noisy disconnect"))
    with patch(
        "custom_components.hisense_vrf.config_flow.ACModbusClient",
        return_value=mock_client,
    ):
        await _async_validate_connection("1.2.3.4", 502)  # should not raise


async def test_reconfigure_changes_host(hass, setup_integration, mock_client):
    """Reconfigure flow updates the entry's host/port and reloads it."""
    entry = setup_integration
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    with (
        patch(
            "custom_components.hisense_vrf.config_flow._async_validate_connection",
            return_value=None,
        ),
        patch(
            "custom_components.hisense_vrf.ACModbusClient",
            return_value=mock_client,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "10.99.99.99", CONF_PORT: 502},
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    # Entry's data was updated; options preserved.
    assert entry.data[CONF_HOST] == "10.99.99.99"
    assert entry.unique_id == "10.99.99.99:502"
    assert entry.options[CONF_VERIFY_DELAY] == 0.01  # untouched


async def test_reconfigure_cannot_connect(hass, setup_integration):
    entry = setup_integration
    result = await entry.start_reconfigure_flow(hass)
    with patch(
        "custom_components.hisense_vrf.config_flow._async_validate_connection",
        side_effect=CannotConnect("nope"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "10.99.99.99", CONF_PORT: 502},
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    # Original data preserved
    assert entry.data[CONF_HOST] == "192.168.1.50"


async def test_reconfigure_aborts_if_other_entry_owns_unique_id(hass, setup_integration, mock_client):
    """If a second entry already uses target host/port, reconfigure aborts."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    entry = setup_integration
    # Create a second entry with the unique_id we'll try to move to
    other = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "10.99.99.99", CONF_PORT: 502},
        unique_id="10.99.99.99:502", entry_id="other",
    )
    other.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    with patch(
        "custom_components.hisense_vrf.config_flow._async_validate_connection",
        return_value=None,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "10.99.99.99", CONF_PORT: 502},
        )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_step_updates_values(hass, setup_integration):
    entry = setup_integration
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_VERIFY_DELAY: 5.0,
            CONF_VERIFY_RETRIES: 4,
            CONF_OFF_PENDING_TTL: 60.0,
            CONF_POLLING_ENABLED: True,
            CONF_POLL_INTERVAL: 10.0,
            CONF_POLL_SPACING: 0.1,
            CONF_POLL_GATEWAY_EVERY_N: 5,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_VERIFY_DELAY] == 5.0
    assert entry.options[CONF_OFF_PENDING_TTL] == 60.0
    assert entry.options[CONF_POLL_GATEWAY_EVERY_N] == 5
