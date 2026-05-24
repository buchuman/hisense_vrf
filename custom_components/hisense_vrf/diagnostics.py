"""Diagnostics support for the Hisense VRF integration.

Exposes a JSON dump of the integration's current state from the HA UI
(Settings → Devices → Hisense VRF → ⋮ → Download diagnostics). Useful for
issue reports without screenshots.

Nothing here is sensitive — the integration uses no credentials, no tokens.
Host/port are included because they're needed to interpret a diagnostic dump.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

import pyacmodbus
from homeassistant.core import HomeAssistant

from . import HisenseVRFConfigEntry


def _as_dict(value: Any) -> Any:
    """Convert dataclasses (ACDeviceState, GatewayState, OutdoorUnitState) to
    plain dicts so they survive JSON serialization. Recurse into containers
    so nested structures are also serialisable."""
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, dict):
        return {str(k): _as_dict(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_dict(v) for v in value]
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HisenseVRFConfigEntry
) -> dict[str, Any]:
    """Return a JSON-serialisable snapshot of the integration state."""
    controller = entry.runtime_data
    return {
        "pyacmodbus": {
            "file": pyacmodbus.__file__,
            "version": getattr(pyacmodbus, "__version__", "(no __version__ attr)"),
        },
        "entry": {
            "data": dict(entry.data),
            "options": dict(entry.options),
            "unique_id": entry.unique_id,
            "version": entry.version,
        },
        "scan": {
            "unit_indices": list(controller.unit_indices),
            "unit_identifiers": {
                str(k): list(v) for k, v in controller.unit_identifiers.items()
            },
            "unit_capacities": {
                str(k): v for k, v in controller.unit_capacities.items()
            },
            "outdoor_units": [list(t) for t in controller.outdoor_units],
        },
        "indoor_states": {
            str(k): _as_dict(v) for k, v in controller.indoor_states.items()
        },
        "gateway_state": _as_dict(controller.gateway_state),
        "outdoor_states": {
            f"{s}.{m}": _as_dict(v) for (s, m), v in controller.outdoor_states.items()
        },
        "pending": {str(k): _as_dict(v) for k, v in controller.pending.items()},
        "off_pending": {
            str(k): _as_dict(v) for k, v in controller._off_pending.items()  # noqa: SLF001
        },
        "last_write_status": {
            str(k): _as_dict(v) for k, v in controller.last_write_status.items()
        },
        "availability": {
            "unit_consecutive_read_failures": {
                str(k): v
                for k, v in controller._unit_read_failures.items()  # noqa: SLF001
            },
            "gateway_consecutive_read_failures": (
                controller._gateway_read_failures  # noqa: SLF001
            ),
        },
        "polling": {
            "polling_enabled": controller.polling_enabled,
            "poll_interval_s": controller.poll_interval_s,
            "poll_spacing_s": controller.poll_spacing_s,
            "poll_gateway_every_n": controller.poll_gateway_every_n,
            "is_running": (
                controller._polling_task is not None  # noqa: SLF001
                and not controller._polling_task.done()  # noqa: SLF001
            ),
        },
        "tuning": {
            "verify_delay_s": controller.verify_delay_s,
            "verify_retries": controller.verify_retries,
            "off_pending_ttl_s": controller.off_pending_ttl_s,
        },
    }
