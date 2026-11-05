"""Config flow: set up the connection to an ioBroker bridge, manage groups."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import voluptuous as vol

from homeassistant.components import mqtt
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    AreaSelector,
    AreaSelectorConfig,
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from homeassistant.helpers.service_info.mqtt import MqttServiceInfo

from .const import (
    BASE_TOPIC_RE,
    CONF_AREAS,
    CONF_BASE_TOPIC,
    CONF_BRIDGE_ID,
    CONF_CONTROL,
    CONF_ENTITIES,
    CONF_INSTANCE,
    CONF_INTERVAL,
    CONF_MODE,
    DEFAULT_BASE_TOPIC,
    DEFAULT_GROUP,
    DEFAULT_INTERVAL,
    DOMAIN,
    MAX_INTERVAL,
    MIN_INTERVAL,
    MODE_IMMEDIATE,
    MODES,
    SUBENTRY_GROUP,
    info_topic,
)
from .helpers import iobroker_entity_ids

INFO_TIMEOUT = 5
CONF_NAME = "name"


def default_group_data() -> dict[str, Any]:
    """Settings of a new group ("Standard")."""
    return {CONF_ENTITIES: [], CONF_AREAS: [], CONF_MODE: MODE_IMMEDIATE, CONF_INTERVAL: DEFAULT_INTERVAL, CONF_CONTROL: True}


def parse_info(payload: Any) -> dict[str, Any] | None:
    """Parse the retained info message of the adapter."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", "replace")
    if not payload:
        return None
    try:
        info = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(info, dict) or not isinstance(info.get("bridge_id"), str):
        return None
    return info


class IoBrokerBridgeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up an ioBroker bridge (found automatically or entered by hand)."""

    VERSION = 1

    def __init__(self) -> None:
        self._base: str | None = None
        self._info: dict[str, Any] = {}

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        """Groups are subentries."""
        return {SUBENTRY_GROUP: GroupSubentryFlow}

    async def async_step_mqtt(self, discovery_info: MqttServiceInfo) -> ConfigFlowResult:
        """The adapter published <base>/bridge/info – offer the setup."""
        suffix = "/bridge/info"
        if not discovery_info.topic.endswith(suffix):
            return self.async_abort(reason="invalid_discovery_info")
        info = parse_info(discovery_info.payload)
        if info is None:
            return self.async_abort(reason="invalid_discovery_info")
        base = discovery_info.topic[: -len(suffix)]
        await self.async_set_unique_id(base)
        self._abort_if_unique_id_configured()
        self._base = base
        self._info = info
        self.context["title_placeholders"] = {"name": f"ioBroker ({info.get('instance') or base})"}
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask the user before setting up a discovered bridge."""
        if user_input is not None:
            return self._create_entry()
        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "instance": str(self._info.get("instance") or "?"),
                "version": str(self._info.get("version") or "?"),
                "base": str(self._base),
            },
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manual setup: enter the base topic of the adapter."""
        if not await mqtt.async_wait_for_mqtt_client(self.hass):
            return self.async_abort(reason="mqtt_not_configured")
        errors: dict[str, str] = {}
        base = DEFAULT_BASE_TOPIC
        if user_input is not None:
            base = str(user_input[CONF_BASE_TOPIC]).strip().strip("/")
            if not BASE_TOPIC_RE.match(base):
                errors[CONF_BASE_TOPIC] = "invalid_topic"
            else:
                await self.async_set_unique_id(base)
                self._abort_if_unique_id_configured()
                info = await self._async_read_info(base)
                if info is None:
                    errors["base"] = "no_bridge"
                else:
                    self._base = base
                    self._info = info
                    return self._create_entry()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_BASE_TOPIC, default=base): TextSelector()}),
            errors=errors,
        )

    async def _async_read_info(self, base: str) -> dict[str, Any] | None:
        """Wait a few seconds for the retained info message of the adapter."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()

        @callback
        def _received(msg: mqtt.ReceiveMessage) -> None:
            info = parse_info(msg.payload)
            if info is not None and not future.done():
                future.set_result(info)

        unsubscribe = await mqtt.async_subscribe(self.hass, info_topic(base), _received)
        try:
            return await asyncio.wait_for(future, INFO_TIMEOUT)
        except TimeoutError:
            return None
        finally:
            unsubscribe()

    def _create_entry(self) -> ConfigFlowResult:
        instance = str(self._info.get("instance") or self._base)
        return self.async_create_entry(
            title=f"ioBroker ({instance})",
            data={
                CONF_BASE_TOPIC: self._base,
                CONF_BRIDGE_ID: self._info.get("bridge_id"),
                CONF_INSTANCE: instance,
            },
            subentries=[
                {
                    "subentry_type": SUBENTRY_GROUP,
                    "title": DEFAULT_GROUP,
                    "unique_id": None,
                    "data": default_group_data(),
                }
            ],
        )


class GroupSubentryFlow(ConfigSubentryFlow):
    """Add or change a group: which entities go to ioBroker and how."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add a group."""
        return await self._async_form("user", user_input)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Change a group."""
        return await self._async_form("reconfigure", user_input)

    async def _async_form(self, step_id: str, user_input: dict[str, Any] | None) -> SubentryFlowResult:
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry() if step_id == "reconfigure" else None
        own_id = subentry.subentry_id if subentry else None
        others = [s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_GROUP and s.subentry_id != own_id]
        errors: dict[str, str] = {}

        if user_input is not None:
            name = str(user_input.get(CONF_NAME, "")).strip()
            mode = user_input.get(CONF_MODE, MODE_IMMEDIATE)
            interval = int(user_input.get(CONF_INTERVAL) or DEFAULT_INTERVAL)
            if not name:
                errors[CONF_NAME] = "name_required"
            elif any(s.title.strip().lower() == name.lower() for s in others):
                errors[CONF_NAME] = "name_exists"
            if mode not in MODES:
                errors[CONF_MODE] = "invalid_mode"
            if not MIN_INTERVAL <= interval <= MAX_INTERVAL:
                errors[CONF_INTERVAL] = "invalid_interval"
            if not errors:
                data = {
                    CONF_ENTITIES: list(user_input.get(CONF_ENTITIES) or []),
                    CONF_AREAS: list(user_input.get(CONF_AREAS) or []),
                    CONF_MODE: mode,
                    CONF_INTERVAL: interval,
                    CONF_CONTROL: bool(user_input.get(CONF_CONTROL, True)),
                }
                if subentry is not None:
                    return self.async_update_and_abort(entry, subentry, title=name, data=data)
                return self.async_create_entry(title=name, data=data)
            current = {**default_group_data(), **user_input}
        elif subentry is not None:
            current = {**default_group_data(), **subentry.data, CONF_NAME: subentry.title}
        else:
            current = {**default_group_data(), CONF_NAME: ""}

        # not offered: entities that come from ioBroker (loop) and entities of other groups
        exclude = set(iobroker_entity_ids(self.hass, entry.data.get(CONF_BRIDGE_ID)))
        for other in others:
            exclude.update(other.data.get(CONF_ENTITIES, []))
        exclude.difference_update(current.get(CONF_ENTITIES) or [])

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=current.get(CONF_NAME, "")): TextSelector(),
                vol.Optional(CONF_ENTITIES, default=list(current.get(CONF_ENTITIES) or [])): EntitySelector(
                    EntitySelectorConfig(multiple=True, exclude_entities=sorted(exclude))
                ),
                vol.Optional(CONF_AREAS, default=list(current.get(CONF_AREAS) or [])): AreaSelector(
                    AreaSelectorConfig(multiple=True)
                ),
                vol.Required(CONF_MODE, default=current.get(CONF_MODE, MODE_IMMEDIATE)): SelectSelector(
                    SelectSelectorConfig(options=MODES, translation_key="mode", mode=SelectSelectorMode.LIST)
                ),
                vol.Required(CONF_INTERVAL, default=current.get(CONF_INTERVAL, DEFAULT_INTERVAL)): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_INTERVAL,
                        max=MAX_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_CONTROL, default=bool(current.get(CONF_CONTROL, True))): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)
