"""Config flow for the Hisense VRF integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_PORT

from pyacmodbus import ACModbusClient, CannotConnect

from .const import (
    CONF_CONNECT_TIMEOUT,
    CONF_OFF_PENDING_TTL,
    CONF_POLL_GATEWAY_EVERY_N,
    CONF_POLL_INTERVAL,
    CONF_POLL_SPACING,
    CONF_POLLING_ENABLED,
    CONF_VERIFY_DELAY,
    CONF_VERIFY_RETRIES,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_OFF_PENDING_TTL,
    DEFAULT_POLL_GATEWAY_EVERY_N,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_POLL_SPACING,
    DEFAULT_POLLING_ENABLED,
    DEFAULT_PORT,
    DEFAULT_VERIFY_DELAY,
    DEFAULT_VERIFY_RETRIES,
    DOMAIN,
    MAX_CONNECT_TIMEOUT,
    MAX_OFF_PENDING_TTL,
    MAX_POLL_GATEWAY_EVERY_N,
    MAX_POLL_INTERVAL,
    MAX_POLL_SPACING,
    MAX_VERIFY_DELAY,
    MAX_VERIFY_RETRIES,
    MIN_CONNECT_TIMEOUT,
    MIN_OFF_PENDING_TTL,
    MIN_POLL_GATEWAY_EVERY_N,
    MIN_POLL_INTERVAL,
    MIN_POLL_SPACING,
    MIN_VERIFY_DELAY,
    MIN_VERIFY_RETRIES,
)

_LOGGER = logging.getLogger(__name__)


async def _async_validate_connection(host: str, port: int) -> None:
    client = ACModbusClient(host=host, port=port)
    try:
        await client.connect()
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass


class HisenseVRFConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for Hisense VRF."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            unique_id = f"{host}:{port}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()
            try:
                await _async_validate_connection(host, port)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating gateway")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"Hisense VRF ({host})",
                    data={CONF_HOST: host, CONF_PORT: port},
                    options={
                        CONF_VERIFY_DELAY: float(user_input[CONF_VERIFY_DELAY]),
                        CONF_VERIFY_RETRIES: int(user_input[CONF_VERIFY_RETRIES]),
                        CONF_OFF_PENDING_TTL: float(user_input[CONF_OFF_PENDING_TTL]),
                        CONF_POLLING_ENABLED: bool(user_input[CONF_POLLING_ENABLED]),
                        CONF_POLL_INTERVAL: float(user_input[CONF_POLL_INTERVAL]),
                        CONF_POLL_SPACING: float(user_input[CONF_POLL_SPACING]),
                        CONF_POLL_GATEWAY_EVERY_N: int(
                            user_input[CONF_POLL_GATEWAY_EVERY_N]
                        ),
                        CONF_CONNECT_TIMEOUT: float(user_input[CONF_CONNECT_TIMEOUT]),
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
                vol.Required(CONF_VERIFY_DELAY, default=DEFAULT_VERIFY_DELAY): vol.All(
                    vol.Coerce(float), vol.Range(min=MIN_VERIFY_DELAY, max=MAX_VERIFY_DELAY)
                ),
                vol.Required(CONF_VERIFY_RETRIES, default=DEFAULT_VERIFY_RETRIES): vol.All(
                    vol.Coerce(int), vol.Range(min=MIN_VERIFY_RETRIES, max=MAX_VERIFY_RETRIES)
                ),
                vol.Required(
                    CONF_OFF_PENDING_TTL, default=DEFAULT_OFF_PENDING_TTL
                ): vol.All(
                    vol.Coerce(float), vol.Range(min=MIN_OFF_PENDING_TTL, max=MAX_OFF_PENDING_TTL)
                ),
                vol.Required(
                    CONF_POLLING_ENABLED, default=DEFAULT_POLLING_ENABLED
                ): bool,
                vol.Required(
                    CONF_POLL_INTERVAL, default=DEFAULT_POLL_INTERVAL
                ): vol.All(
                    vol.Coerce(float), vol.Range(min=MIN_POLL_INTERVAL, max=MAX_POLL_INTERVAL)
                ),
                vol.Required(
                    CONF_POLL_SPACING, default=DEFAULT_POLL_SPACING
                ): vol.All(
                    vol.Coerce(float), vol.Range(min=MIN_POLL_SPACING, max=MAX_POLL_SPACING)
                ),
                vol.Required(
                    CONF_POLL_GATEWAY_EVERY_N, default=DEFAULT_POLL_GATEWAY_EVERY_N
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_POLL_GATEWAY_EVERY_N, max=MAX_POLL_GATEWAY_EVERY_N),
                ),
                vol.Required(
                    CONF_CONNECT_TIMEOUT, default=DEFAULT_CONNECT_TIMEOUT
                ): vol.All(
                    vol.Coerce(float),
                    vol.Range(min=MIN_CONNECT_TIMEOUT, max=MAX_CONNECT_TIMEOUT),
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow the user to change host/port without re-creating the entry.

        Keeps all options (verify_delay, polling settings, etc.) untouched.
        Validates the new connection before persisting; if it fails the form
        is shown again with an error.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            new_unique_id = f"{host}:{port}"
            # If the user is keeping the same host/port, no uniqueness check
            # is needed. Otherwise, make sure no other entry already owns the
            # new unique_id.
            if new_unique_id != entry.unique_id:
                for other in self.hass.config_entries.async_entries(DOMAIN):
                    if other.entry_id != entry.entry_id and other.unique_id == new_unique_id:
                        return self.async_abort(reason="already_configured")
            try:
                await _async_validate_connection(host, port)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating gateway")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_HOST: host, CONF_PORT: port},
                    title=f"Hisense VRF ({host})",
                    unique_id=new_unique_id,
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=entry.data.get(CONF_HOST)): str,
                vol.Required(
                    CONF_PORT, default=entry.data.get(CONF_PORT, DEFAULT_PORT)
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            }
        )
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HisenseVRFOptionsFlow()


class HisenseVRFOptionsFlow(OptionsFlowWithReload):
    """Edit verify delay / retries without reconfiguring the host."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_VERIFY_DELAY: float(user_input[CONF_VERIFY_DELAY]),
                    CONF_VERIFY_RETRIES: int(user_input[CONF_VERIFY_RETRIES]),
                    CONF_OFF_PENDING_TTL: float(user_input[CONF_OFF_PENDING_TTL]),
                    CONF_POLLING_ENABLED: bool(user_input[CONF_POLLING_ENABLED]),
                    CONF_POLL_INTERVAL: float(user_input[CONF_POLL_INTERVAL]),
                    CONF_POLL_SPACING: float(user_input[CONF_POLL_SPACING]),
                    CONF_POLL_GATEWAY_EVERY_N: int(
                        user_input[CONF_POLL_GATEWAY_EVERY_N]
                    ),
                    CONF_CONNECT_TIMEOUT: float(user_input[CONF_CONNECT_TIMEOUT]),
                },
            )

        current = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_VERIFY_DELAY,
                    default=current.get(CONF_VERIFY_DELAY, DEFAULT_VERIFY_DELAY),
                ): vol.All(vol.Coerce(float), vol.Range(min=MIN_VERIFY_DELAY, max=MAX_VERIFY_DELAY)),
                vol.Required(
                    CONF_VERIFY_RETRIES,
                    default=current.get(CONF_VERIFY_RETRIES, DEFAULT_VERIFY_RETRIES),
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_VERIFY_RETRIES, max=MAX_VERIFY_RETRIES)),
                vol.Required(
                    CONF_OFF_PENDING_TTL,
                    default=current.get(CONF_OFF_PENDING_TTL, DEFAULT_OFF_PENDING_TTL),
                ): vol.All(
                    vol.Coerce(float), vol.Range(min=MIN_OFF_PENDING_TTL, max=MAX_OFF_PENDING_TTL)
                ),
                vol.Required(
                    CONF_POLLING_ENABLED,
                    default=current.get(CONF_POLLING_ENABLED, DEFAULT_POLLING_ENABLED),
                ): bool,
                vol.Required(
                    CONF_POLL_INTERVAL,
                    default=current.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                ): vol.All(
                    vol.Coerce(float), vol.Range(min=MIN_POLL_INTERVAL, max=MAX_POLL_INTERVAL)
                ),
                vol.Required(
                    CONF_POLL_SPACING,
                    default=current.get(CONF_POLL_SPACING, DEFAULT_POLL_SPACING),
                ): vol.All(
                    vol.Coerce(float), vol.Range(min=MIN_POLL_SPACING, max=MAX_POLL_SPACING)
                ),
                vol.Required(
                    CONF_POLL_GATEWAY_EVERY_N,
                    default=current.get(
                        CONF_POLL_GATEWAY_EVERY_N, DEFAULT_POLL_GATEWAY_EVERY_N
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_POLL_GATEWAY_EVERY_N, max=MAX_POLL_GATEWAY_EVERY_N),
                ),
                vol.Required(
                    CONF_CONNECT_TIMEOUT,
                    default=current.get(CONF_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT),
                ): vol.All(
                    vol.Coerce(float),
                    vol.Range(min=MIN_CONNECT_TIMEOUT, max=MAX_CONNECT_TIMEOUT),
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
