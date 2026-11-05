"""Small helpers shared by the config flow, the exporter and the dashboard."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er


def iobroker_entity_ids(hass: HomeAssistant, bridge_id: str | None) -> set[str]:
    """Entities that come FROM ioBroker (MQTT entities of the adapter).

    They are never sent back to ioBroker – that would create a loop.
    The adapter builds every unique_id as "<bridge_id>_<key>".
    """
    if not bridge_id:
        return set()
    prefix = f"{bridge_id}_"
    return {
        entry.entity_id
        for entry in er.async_get(hass).entities.values()
        if entry.platform == "mqtt" and (entry.unique_id or "").startswith(prefix)
    }


def entity_area_id(hass: HomeAssistant, entity_id: str) -> str | None:
    """Area of an entity (own area or the area of its device)."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return None
    if entry.area_id:
        return entry.area_id
    if entry.device_id:
        device = dr.async_get(hass).async_get(entry.device_id)
        if device is not None:
            return device.area_id
    return None


def area_name(hass: HomeAssistant, area_id: str | None) -> str | None:
    """Name of an area."""
    if not area_id:
        return None
    area = ar.async_get(hass).async_get_area(area_id)
    return area.name if area else None


def entities_in_areas(hass: HomeAssistant, area_ids: list[str]) -> list[str]:
    """Visible, enabled entities of the given areas (like HA does for area targets)."""
    if not area_ids:
        return []
    wanted = set(area_ids)
    result = []
    for entry in er.async_get(hass).entities.values():
        if entry.disabled_by or entry.hidden_by or entry.entity_category:
            continue
        if entity_area_id(hass, entry.entity_id) in wanted:
            result.append(entry.entity_id)
    return sorted(result)
