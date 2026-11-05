"""Diagnostics (download in Settings -> Devices & services -> ioBroker Bridge -> ⋮)."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """No secrets are involved (the MQTT connection belongs to the MQTT integration)."""
    exporter = getattr(entry, "runtime_data", None)
    return {
        "entry": {"title": entry.title, "data": dict(entry.data)},
        "groups": [
            {"title": sub.title, "data": dict(sub.data)} for sub in entry.subentries.values()
        ],
        "runtime": exporter.summary() if exporter else None,
    }
