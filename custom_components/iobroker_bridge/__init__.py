"""ioBroker Bridge: send Home Assistant entities to ioBroker (and control them from there).

Counterpart of the ioBroker adapter "homeassistant-bridge" (version 0.5.0 or newer).
Uses the existing MQTT integration of Home Assistant – no extra connection, no password.
"""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import mqtt
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import CONF_BASE_TOPIC, DASHBOARD_JS, DOMAIN, STATIC_URL, VERSION
from .exporter import Exporter
from .websocket import async_register_websocket

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type IoBrokerConfigEntry = ConfigEntry[Exporter]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the websocket command and the dashboard (once)."""
    async_register_websocket(hass)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "frontend"), cache_headers=False)]
    )
    add_extra_js_url(hass, f"{STATIC_URL}/{DASHBOARD_JS}?v={VERSION}")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: IoBrokerConfigEntry) -> bool:
    """Start sending the selected entities to ioBroker."""
    if not await mqtt.async_wait_for_mqtt_client(hass):
        raise ConfigEntryNotReady("Die MQTT-Integration ist nicht bereit")
    exporter = Exporter(hass, entry)
    entry.runtime_data = exporter
    # registered first, so no change of a group can get lost while starting
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await exporter.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: IoBrokerConfigEntry) -> bool:
    """Stop (ioBroker shows the values as no longer current)."""
    await entry.runtime_data.async_stop()
    return True


async def _async_update_listener(hass: HomeAssistant, entry: IoBrokerConfigEntry) -> None:
    """A group was added, changed or deleted: apply it right away (no restart needed)."""
    await entry.runtime_data.async_apply_settings()


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Integration deleted: remove everything it published from the broker."""
    if await mqtt.async_wait_for_mqtt_client(hass):
        await Exporter.async_remove_all(hass, entry.data[CONF_BASE_TOPIC])
