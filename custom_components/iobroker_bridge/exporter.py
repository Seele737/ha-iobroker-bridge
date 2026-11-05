"""Sends the selected Home Assistant entities to ioBroker and executes commands from there.

Topics (below the base topic of the ioBroker adapter, default ``iobroker-ha/0``):

- ``<base>/ha/status``               retained ``online`` / ``offline``
- ``<base>/ha/<entity_id>/config``   retained JSON: name, group, area, channels (type, role, unit, limits, writable)
- ``<base>/ha/<entity_id>/state``    retained JSON: ``{"available": .., "values": {..}, "lc": <ms>}``
- ``<base>/ha/<entity_id>/cmd``      from ioBroker, NOT retained: ``{"channel": "brightness", "value": 50}``
- ``<base>/ha/<entity_id>/result``   answer to a command: ``{"channel": .., "ok": true|false, "error": ..}``
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
import json
import logging
import time
from typing import Any

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Context, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_interval

from . import mapping
from .const import (
    CONF_AREAS,
    CONF_BASE_TOPIC,
    CONF_BRIDGE_ID,
    CONF_CONTROL,
    CONF_ENTITIES,
    CONF_INTERVAL,
    CONF_MODE,
    DEFAULT_INTERVAL,
    MAX_COMMANDS_PER_SECOND,
    MODE_COMBINED,
    MODE_IMMEDIATE,
    MODE_THROTTLE,
    SERVICE_TIMEOUT,
    SUBENTRY_GROUP,
    availability_topic,
    ha_topic,
    info_topic,
)
from .helpers import area_name, entities_in_areas, entity_area_id, iobroker_entity_ids

_LOGGER = logging.getLogger(__name__)

COLLECT_SECONDS = 2.0
RESOLVE_DELAY = 2.0


@dataclass
class Group:
    """A group (subentry): which entities, how often, control allowed."""

    subentry_id: str
    name: str
    entities: list[str]
    areas: list[str]
    mode: str
    interval: int
    control: bool


@dataclass
class Export:
    """State of one exported entity."""

    entity_id: str
    group: Group
    config: str = ""
    state: str = ""
    last_sent: float = 0.0
    pending: CALLBACK_TYPE | None = None
    channels: dict[str, mapping.Channel] = field(default_factory=dict)


class Exporter:
    """Runtime of one config entry (one ioBroker bridge)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.base: str = entry.data[CONF_BASE_TOPIC]
        self.bridge_id: str | None = entry.data.get(CONF_BRIDGE_ID)
        self.groups: list[Group] = self._build_groups()
        self.exports: dict[str, Export] = {}
        self.bridge_info: dict[str, Any] = {}
        self.bridge_online: bool | None = None
        self.stats: dict[str, Any] = {"sent": 0, "commands_ok": 0, "commands_rejected": 0, "last_error": None}
        self.duplicates: list[str] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._group_unsubs: list[CALLBACK_TYPE] = []
        self._track: CALLBACK_TYPE | None = None
        self._resolve_timer: CALLBACK_TYPE | None = None
        self._cmd_times: dict[str, deque[float]] = {}
        self._stopped = False

    # ------------------------------------------------------------------ life cycle

    def _build_groups(self) -> list[Group]:
        return [
            Group(
                subentry_id=sub.subentry_id,
                name=sub.title,
                entities=list(sub.data.get(CONF_ENTITIES, [])),
                areas=list(sub.data.get(CONF_AREAS, [])),
                mode=sub.data.get(CONF_MODE, MODE_IMMEDIATE),
                interval=int(sub.data.get(CONF_INTERVAL, DEFAULT_INTERVAL)),
                control=bool(sub.data.get(CONF_CONTROL, True)),
            )
            for sub in self.entry.subentries.values()
            if sub.subentry_type == SUBENTRY_GROUP
        ]

    def _start_group_timers(self) -> None:
        for unsub in self._group_unsubs:
            unsub()
        self._group_unsubs = [
            async_track_time_interval(
                self.hass,
                lambda _now, g=group: self._schedule(self._async_publish_group(g)),
                timedelta(seconds=group.interval),
            )
            for group in self.groups
            if group.mode == MODE_COMBINED
        ]

    async def async_apply_settings(self) -> None:
        """Groups were added, changed or deleted: apply them without a restart."""
        if self._stopped:
            return
        self.groups = self._build_groups()
        self._start_group_timers()
        self._resolve()
        await self.async_publish_all()

    async def async_start(self) -> None:
        """Subscribe, clean up stale entries, publish everything."""
        self._unsubs.append(await mqtt.async_subscribe(self.hass, info_topic(self.base), self._on_info))
        self._unsubs.append(await mqtt.async_subscribe(self.hass, availability_topic(self.base), self._on_availability))
        self._unsubs.append(await mqtt.async_subscribe(self.hass, ha_topic(self.base, "+", "cmd"), self._on_command, qos=1))
        stale = await self._async_collect_published()
        self._resolve()
        for entity_id in stale - set(self.exports):
            await self._async_clear(entity_id)
        await self._async_publish(ha_topic(self.base, "status"), "online", retain=True)
        await self.async_publish_all()
        for event_type in (
            er.EVENT_ENTITY_REGISTRY_UPDATED,
            dr.EVENT_DEVICE_REGISTRY_UPDATED,
            ar.EVENT_AREA_REGISTRY_UPDATED,
        ):
            self._unsubs.append(self.hass.bus.async_listen(event_type, self._on_registry_changed))
        self._start_group_timers()
        self._unsubs.append(mqtt.async_subscribe_connection_status(self.hass, self._on_connection))

    async def async_stop(self) -> None:
        """Unsubscribe and report offline."""
        self._stopped = True
        for unsub in [*self._unsubs, *self._group_unsubs]:
            unsub()
        self._unsubs.clear()
        self._group_unsubs.clear()
        if self._track:
            self._track()
            self._track = None
        if self._resolve_timer:
            self._resolve_timer()
        for export in self.exports.values():
            if export.pending:
                export.pending()
        try:
            await self._async_publish(ha_topic(self.base, "status"), "offline", retain=True)
        except Exception:  # noqa: BLE001 - MQTT may already be gone at shutdown
            _LOGGER.debug("Could not publish offline status")

    @staticmethod
    async def async_remove_all(hass: HomeAssistant, base: str) -> None:
        """Integration removed: delete all retained topics of this integration."""
        found: set[str] = set()

        @callback
        def _collect(msg: mqtt.ReceiveMessage) -> None:
            if msg.payload:
                found.add(msg.topic)

        unsub = await mqtt.async_subscribe(hass, ha_topic(base, "+", "+"), _collect)
        await asyncio.sleep(COLLECT_SECONDS)
        unsub()
        for topic in found:
            if topic.endswith(("/config", "/state")):
                await mqtt.async_publish(hass, topic, "", qos=1, retain=True)
        await mqtt.async_publish(hass, ha_topic(base, "status"), "", qos=1, retain=True)

    # ------------------------------------------------------------------ which entities

    def _resolve(self) -> None:
        """Compute entity -> group from the groups (explicit entities first, then areas)."""
        excluded = iobroker_entity_ids(self.hass, self.bridge_id)
        wanted: dict[str, Group] = {}
        duplicates: list[str] = []
        for group in self.groups:
            for entity_id in group.entities:
                if entity_id in excluded or not mapping.ENTITY_ID_RE.match(entity_id):
                    continue
                if entity_id in wanted:
                    duplicates.append(entity_id)
                    continue
                wanted[entity_id] = group
        for group in self.groups:
            for entity_id in entities_in_areas(self.hass, group.areas):
                if entity_id not in excluded and entity_id not in wanted and mapping.ENTITY_ID_RE.match(entity_id):
                    wanted[entity_id] = group
        self.duplicates = duplicates

        removed = [eid for eid in self.exports if eid not in wanted]
        for entity_id in removed:
            export = self.exports.pop(entity_id)
            if export.pending:
                export.pending()
            self._schedule(self._async_clear(entity_id))
        for entity_id, group in wanted.items():
            export = self.exports.get(entity_id)
            if export is None:
                self.exports[entity_id] = Export(entity_id, group)
            elif export.group is not group:
                export.group = group

        if self._track:
            self._track()
        self._track = async_track_state_change_event(self.hass, list(self.exports), self._on_state_event)

    @callback
    def _on_registry_changed(self, _event: Event) -> None:
        """Areas, names or devices changed: resolve again (debounced)."""
        if self._resolve_timer:
            self._resolve_timer()

        @callback
        def _run(_now: Any) -> None:
            self._resolve_timer = None
            if self._stopped:
                return
            self._resolve()
            self._schedule(self.async_publish_all())

        self._resolve_timer = async_call_later(self.hass, RESOLVE_DELAY, _run)

    # ------------------------------------------------------------------ publishing

    def _info(self, state: State) -> mapping.EntityInfo:
        return mapping.EntityInfo(
            entity_id=state.entity_id,
            state=state.state,
            attributes=dict(state.attributes),
            temperature_unit=self.hass.config.units.temperature_unit,
        )

    async def async_publish_all(self, force: bool = False) -> None:
        """Publish config and state of all exported entities."""
        for entity_id in list(self.exports):
            await self._async_publish_entity(entity_id, force=force)

    async def _async_publish_group(self, group: Group) -> None:
        for export in list(self.exports.values()):
            if export.group is group:
                await self._async_publish_entity(export.entity_id, force=True)

    async def _async_publish_entity(self, entity_id: str, force: bool = False) -> None:
        export = self.exports.get(entity_id)
        if export is None:
            return
        state = self.hass.states.get(entity_id)
        if state is None:
            # entity does not exist (yet) – report as unavailable once a config exists
            if export.config:
                await self._async_send_state(export, {"available": False}, None, force)
            return
        info = self._info(state)
        control = export.group.control
        channels = mapping.describe(info, control)
        export.channels = channels
        config = mapping.config_payload(
            info,
            channels,
            name=state.name,
            group=export.group.name,
            area=area_name(self.hass, entity_area_id(self.hass, entity_id)),
            control=control,
        )
        config_json = json.dumps(config, ensure_ascii=False, sort_keys=True)
        if force or config_json != export.config:
            await self._async_publish(ha_topic(self.base, entity_id, "config"), config_json, retain=True)
            export.config = config_json
        lc = int(state.last_changed.timestamp() * 1000)
        await self._async_send_state(export, mapping.values(info, channels), lc, force)

    async def _async_send_state(self, export: Export, values: dict[str, Any], lc: int | None, force: bool) -> None:
        payload = json.dumps(
            {"available": values.get("available", False), "values": values, "lc": lc},
            ensure_ascii=False,
            sort_keys=True,
        )
        if not force and payload == export.state:
            return
        await self._async_publish(ha_topic(self.base, export.entity_id, "state"), payload, retain=True)
        export.state = payload
        export.last_sent = time.monotonic()

    @callback
    def _on_state_event(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        export = self.exports.get(entity_id)
        if export is None or self._stopped:
            return
        group = export.group
        if group.mode == MODE_THROTTLE:
            wait = group.interval - (time.monotonic() - export.last_sent)
            if wait > 0:
                if export.pending is None:

                    @callback
                    def _later(_now: Any, eid: str = entity_id) -> None:
                        exp = self.exports.get(eid)
                        if exp is not None:
                            exp.pending = None
                            self._schedule(self._async_publish_entity(eid))

                    export.pending = async_call_later(self.hass, wait, _later)
                return
        self._schedule(self._async_publish_entity(entity_id))

    async def _async_clear(self, entity_id: str) -> None:
        """Entity no longer exported: remove its retained topics."""
        await self._async_publish(ha_topic(self.base, entity_id, "config"), "", retain=True)
        await self._async_publish(ha_topic(self.base, entity_id, "state"), "", retain=True)

    async def _async_collect_published(self) -> set[str]:
        """Entity ids that are still published (retained) from an earlier run."""
        found: set[str] = set()
        prefix = ha_topic(self.base, "")

        @callback
        def _collect(msg: mqtt.ReceiveMessage) -> None:
            if msg.payload and msg.topic.startswith(prefix) and msg.topic.endswith("/config"):
                found.add(msg.topic[len(prefix) : -len("/config")])

        unsub = await mqtt.async_subscribe(self.hass, ha_topic(self.base, "+", "config"), _collect)
        await asyncio.sleep(COLLECT_SECONDS)
        unsub()
        return found

    async def _async_publish(self, topic: str, payload: str, retain: bool = False) -> None:
        await mqtt.async_publish(self.hass, topic, payload, qos=1, retain=retain)
        self.stats["sent"] += 1

    def _schedule(self, coro: Any) -> None:
        self.entry.async_create_background_task(self.hass, coro, f"iobroker_bridge publish {self.base}")

    @callback
    def _on_connection(self, connected: bool) -> None:
        """Broker connection back: send everything again (broker may have lost retained data)."""
        if connected and not self._stopped:

            async def _again() -> None:
                await self._async_publish(ha_topic(self.base, "status"), "online", retain=True)
                await self.async_publish_all(force=True)

            self._schedule(_again())

    # ------------------------------------------------------------------ info from the adapter

    @callback
    def _on_info(self, msg: mqtt.ReceiveMessage) -> None:
        try:
            info = json.loads(msg.payload) if msg.payload else {}
        except ValueError:
            return
        if isinstance(info, dict):
            self.bridge_info = info

    @callback
    def _on_availability(self, msg: mqtt.ReceiveMessage) -> None:
        self.bridge_online = str(msg.payload).strip().lower() == "online"

    # ------------------------------------------------------------------ commands from ioBroker

    @callback
    def _on_command(self, msg: mqtt.ReceiveMessage) -> None:
        prefix = ha_topic(self.base, "")
        if not msg.topic.startswith(prefix) or not msg.topic.endswith("/cmd"):
            return
        entity_id = msg.topic[len(prefix) : -len("/cmd")]
        self._schedule(self._async_command(entity_id, msg.payload, msg.retain))

    async def _async_command(self, entity_id: str, payload: Any, retained: bool) -> None:
        channel_name = ""
        try:
            if retained:
                raise mapping.CommandError("gespeicherter (retained) Befehl ignoriert")
            try:
                data = json.loads(payload)
            except (TypeError, ValueError) as err:
                raise mapping.CommandError("Befehl ist kein gültiges JSON") from err
            if not isinstance(data, dict) or not isinstance(data.get("channel"), str):
                raise mapping.CommandError("Befehl braucht „channel“ und „value“")
            channel_name = data["channel"]
            export = self.exports.get(entity_id)
            if export is None:
                raise mapping.CommandError("Entität wird nicht an ioBroker übertragen")
            if not export.group.control:
                raise mapping.CommandError("Steuerung aus ioBroker ist für diese Gruppe ausgeschaltet")
            if not self._rate_ok(entity_id):
                raise mapping.CommandError("zu viele Befehle – bitte langsamer")
            state = self.hass.states.get(entity_id)
            if state is None:
                raise mapping.CommandError("Entität gibt es in Home Assistant nicht")
            info = self._info(state)
            channels = mapping.describe(info, True)
            action = mapping.command(info, channels, channel_name, data.get("value"))
            await asyncio.wait_for(
                self.hass.services.async_call(
                    action.domain, action.service, action.data, blocking=True, context=Context()
                ),
                SERVICE_TIMEOUT,
            )
        except mapping.CommandError as err:
            await self._async_result(entity_id, channel_name, False, str(err))
            return
        except TimeoutError:
            await self._async_result(entity_id, channel_name, False, "Home Assistant hat nicht rechtzeitig geantwortet")
            return
        except Exception as err:  # noqa: BLE001 - every error goes back to ioBroker
            await self._async_result(entity_id, channel_name, False, f"Fehler in Home Assistant: {err}")
            return
        self.stats["commands_ok"] += 1
        await self._async_result(entity_id, channel_name, True, None)

    def _rate_ok(self, entity_id: str) -> bool:
        now = time.monotonic()
        times = self._cmd_times.setdefault(entity_id, deque())
        while times and now - times[0] > 1.0:
            times.popleft()
        if len(times) >= MAX_COMMANDS_PER_SECOND:
            return False
        times.append(now)
        return True

    async def _async_result(self, entity_id: str, channel: str, ok: bool, error: str | None) -> None:
        if not ok:
            self.stats["commands_rejected"] += 1
            self.stats["last_error"] = f"{entity_id} {channel}: {error}"
            _LOGGER.warning("Befehl aus ioBroker für %s (%s) abgelehnt: %s", entity_id, channel, error)
        payload: dict[str, Any] = {"channel": channel, "ok": ok}
        if error:
            payload["error"] = error
        if mapping.ENTITY_ID_RE.match(entity_id):
            await self._async_publish(ha_topic(self.base, entity_id, "result"), json.dumps(payload, ensure_ascii=False))

    # ------------------------------------------------------------------ for dashboard and diagnostics

    def summary(self) -> dict[str, Any]:
        """Overview for the dashboard and the diagnostics."""
        groups = []
        for group in self.groups:
            groups.append(
                {
                    "name": group.name,
                    "mode": group.mode,
                    "interval": group.interval,
                    "control": group.control,
                    "entities": sorted(e.entity_id for e in self.exports.values() if e.group is group),
                }
            )
        return {
            "title": self.entry.title,
            "base_topic": self.base,
            "bridge_id": self.bridge_id,
            "instance": self.bridge_info.get("instance"),
            "version": self.bridge_info.get("version"),
            "admin_url": self.bridge_info.get("admin_url"),
            "online": self.bridge_online,
            "groups": groups,
            "duplicates": self.duplicates,
            "stats": dict(self.stats),
        }
