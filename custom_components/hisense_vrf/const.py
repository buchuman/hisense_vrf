"""Constants for the Hisense VRF integration."""
from __future__ import annotations

DOMAIN = "hisense_vrf"

CONF_VERIFY_DELAY = "verify_delay_s"
CONF_VERIFY_RETRIES = "verify_retries"
CONF_OFF_PENDING_TTL = "off_pending_ttl_s"
CONF_POLL_INTERVAL = "poll_interval_s"
CONF_POLL_SPACING = "poll_spacing_s"
CONF_POLL_GATEWAY_EVERY_N = "poll_gateway_every_n_cycles"
CONF_POLLING_ENABLED = "polling_enabled"

DEFAULT_PORT = 502
DEFAULT_VERIFY_DELAY = 2.0
DEFAULT_VERIFY_RETRIES = 3
DEFAULT_OFF_PENDING_TTL = 30.0
DEFAULT_POLL_INTERVAL = 5.0
DEFAULT_POLL_SPACING = 0.0
DEFAULT_POLL_GATEWAY_EVERY_N = 10
DEFAULT_POLLING_ENABLED = True

MIN_VERIFY_DELAY = 0.2
MAX_VERIFY_DELAY = 30.0
MIN_VERIFY_RETRIES = 0
MAX_VERIFY_RETRIES = 20
MIN_OFF_PENDING_TTL = 5.0
MAX_OFF_PENDING_TTL = 600.0
MIN_POLL_INTERVAL = 0.0
MAX_POLL_INTERVAL = 3600.0
MIN_POLL_SPACING = 0.0
MAX_POLL_SPACING = 10.0
MIN_POLL_GATEWAY_EVERY_N = 1
MAX_POLL_GATEWAY_EVERY_N = 1000

PLATFORMS = [
    "binary_sensor",
    "button",
    "climate",
    "select",
    "sensor",
    "switch",
]

SIGNAL_UPDATE = f"{DOMAIN}_update"


def signal_new_indoor(entry_id: str) -> str:
    """Dispatcher signal fired when a new indoor unit is discovered at runtime."""
    return f"{DOMAIN}_new_indoor_{entry_id}"


def signal_new_outdoor(entry_id: str) -> str:
    """Dispatcher signal fired when a new outdoor module is discovered at runtime."""
    return f"{DOMAIN}_new_outdoor_{entry_id}"

# Consecutive read failures that flip an entity to "unavailable".
UNAVAILABLE_THRESHOLD = 3

# Consecutive failed writes to the same unit that raise a repair issue.
WRITE_FAILED_ISSUE_THRESHOLD = 3

WRITE_STATUS_IDLE = "idle"
WRITE_STATUS_PENDING = "pending"
WRITE_STATUS_CONFIRMED = "confirmed"
WRITE_STATUS_FAILED = "failed"
WRITE_STATUS_OFF_PENDING = "off_pending"
