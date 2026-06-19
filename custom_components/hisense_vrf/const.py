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
CONF_CONNECT_TIMEOUT = "connect_timeout_s"
CONF_ON_EDGE_FORCE = "on_edge_force"

DEFAULT_PORT = 502
DEFAULT_VERIFY_DELAY = 2.0
DEFAULT_VERIFY_RETRIES = 3
DEFAULT_OFF_PENDING_TTL = 30.0
DEFAULT_POLL_INTERVAL = 5.0
DEFAULT_POLL_SPACING = 0.0
DEFAULT_POLL_GATEWAY_EVERY_N = 10
DEFAULT_POLLING_ENABLED = True
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_ON_EDGE_FORCE = True

# When a power-on bundle is not reflected by the unit on the first round, the
# retry round writes REG_RUN_STOP=0 first (forcing a 0->1 edge that the IR
# remote otherwise steals from the gateway's command tracking), then re-sends the
# ON bundle. See the on-failure investigation: the gateway only relays a run/stop
# change as an edge, so re-sending an identical "1" is a no-op until its slow
# poll re-syncs.
#
# CRITICAL (v1.5.0): the OFF must be *consumed* by the gateway (command slot
# drains back to [0xFF]*5) before the ON is re-sent. The gateway drains its
# per-unit command buffer on its own slow H-NET cycle; if the ON overwrites the
# buffer before the OFF drains, the gateway never transmits the OFF and the edge
# is lost (observed 2026-06-19: slot_after consumed=False after a fixed 1.0s
# sleep -> WRITE_FAILED). So we poll the slot until the OFF drains, up to a
# timeout, instead of sleeping a fixed amount.
ON_EDGE_SETTLE_S = 1.0  # minimum settle before the first drain poll
ON_EDGE_DRAIN_TIMEOUT_S = 8.0  # max wait for the gateway to consume the OFF
ON_EDGE_POLL_INTERVAL_S = 0.5  # interval between drain polls

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
MIN_CONNECT_TIMEOUT = 1.0
MAX_CONNECT_TIMEOUT = 60.0

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
