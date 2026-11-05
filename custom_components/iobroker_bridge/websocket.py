"""Websocket command for the ioBroker dashboard."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

# entities of the adapter that belong to the "System" view (keys of the adapter)
SYSTEM_KEYS = ("sys_", "upd_", "bridge_")


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    """Register the command once."""
    websocket_api.async_register_command(hass, ws_dashboard)


def iobroker_entities(hass: HomeAssistant, bridge_id: str | None) -> list[dict[str, Any]]:
    """Entities that come from ioBroker, with their adapter group."""
    if not bridge_id:
        return []
    prefix = f"{bridge_id}_"
    result = []
    for entry in er.async_get(hass).entities.values():
        unique_id = entry.unique_id or ""
        if entry.platform != "mqtt" or not unique_id.startswith(prefix) or entry.disabled_by:
            continue
        key = unique_id[len(prefix) :]
        state = hass.states.get(entry.entity_id)
        group = state.attributes.get("gruppe") if state else None
        result.append(
            {
                "entity_id": entry.entity_id,
                "group": group if isinstance(group, str) else None,
                "system": key.startswith(SYSTEM_KEYS),
            }
        )
    return sorted(result, key=lambda e: e["entity_id"])


@websocket_api.websocket_command({vol.Required("type"): "iobroker_bridge/dashboard"})
@callback
def ws_dashboard(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Everything the dashboard needs: bridges, ioBroker entities, exported entities."""
    bridges = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        exporter = entry.runtime_data
        summary = exporter.summary()
        summary["iobroker_entities"] = iobroker_entities(hass, exporter.bridge_id)
        bridges.append(summary)
    connection.send_result(msg["id"], {"bridges": bridges})
