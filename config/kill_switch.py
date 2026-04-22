"""
Kill-switch routing: gates all outbound traffic.

When KILL_SWITCH is unset or any value other than "enabled",
all outbound is routed to STAFF_SINK. This is the safe default.
"""
import logging
import os

from config.models import Destination

logger = logging.getLogger(__name__)

_ENABLED_VALUE = "enabled"


def get_outbound_destination() -> Destination:
    """
    Read KILL_SWITCH env var and return the appropriate Destination.

    Returns STAFF_SINK for any value other than exactly "enabled".
    Logs a warning when the value is absent or unrecognised.
    """
    value = os.environ.get("KILL_SWITCH", "")

    if value == _ENABLED_VALUE:
        return Destination.PROSPECT

    if value == "":
        logger.warning("KILL_SWITCH is not set — routing all outbound to staff sink")
    else:
        logger.warning(
            "KILL_SWITCH value %r is unrecognised — routing all outbound to staff sink",
            value,
        )

    return Destination.STAFF_SINK
