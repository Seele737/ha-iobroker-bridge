"""Constants of the ioBroker Bridge integration."""

from __future__ import annotations

import re
from typing import Final

DOMAIN: Final = "iobroker_bridge"
VERSION: Final = "0.1.0"

CONF_BASE_TOPIC: Final = "base_topic"
CONF_BRIDGE_ID: Final = "bridge_id"
CONF_INSTANCE: Final = "instance"

# group subentries
SUBENTRY_GROUP: Final = "group"
CONF_ENTITIES: Final = "entities"
CONF_AREAS: Final = "areas"
CONF_MODE: Final = "mode"
CONF_INTERVAL: Final = "interval"
CONF_CONTROL: Final = "control"

MODE_IMMEDIATE: Final = "immediate"
MODE_THROTTLE: Final = "throttle"
MODE_COMBINED: Final = "combined"
MODES: Final = [MODE_IMMEDIATE, MODE_THROTTLE, MODE_COMBINED]

DEFAULT_BASE_TOPIC: Final = "iobroker-ha/0"
DEFAULT_GROUP: Final = "Standard"
DEFAULT_INTERVAL: Final = 60
MIN_INTERVAL: Final = 1
MAX_INTERVAL: Final = 86400

# protocol with the ioBroker adapter (homeassistant-bridge >= 0.5.0)
PROTOCOL_VERSION: Final = 1
BASE_TOPIC_RE: Final = re.compile(r"^[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+)*$")

# commands: at most this many per entity and second
MAX_COMMANDS_PER_SECOND: Final = 5
SERVICE_TIMEOUT: Final = 10

STATIC_URL: Final = "/iobroker_bridge_static"
DASHBOARD_JS: Final = "iobroker-dashboard.js"


def info_topic(base: str) -> str:
    """Retained info message of the ioBroker adapter."""
    return f"{base}/bridge/info"


def availability_topic(base: str) -> str:
    """Availability (LWT) of the ioBroker adapter."""
    return f"{base}/bridge/availability"


def ha_topic(base: str, *parts: str) -> str:
    """Topics of this integration below <base>/ha."""
    return "/".join([base, "ha", *parts])
