"""Translation between Home Assistant entities and ioBroker states.

Each exported entity becomes an ioBroker channel with a few states ("channels").
This module describes them (type, role, unit, limits, writable) and translates
commands from ioBroker back into a fixed set of Home Assistant actions.

It is pure Python (no Home Assistant imports) so it can be tested in isolation.
The feature flags and attribute names below were checked against the Home
Assistant 2026.10 source code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
import re
from typing import Any

UNKNOWN_STATES = ("unknown", "unavailable", "none", "")

# Feature flags (IntFlag values from the HA source)
COVER_OPEN, COVER_CLOSE, COVER_SET_POSITION, COVER_STOP = 1, 2, 4, 8
VALVE_OPEN, VALVE_CLOSE, VALVE_SET_POSITION, VALVE_STOP = 1, 2, 4, 8
LOCK_OPEN = 1
FAN_SET_SPEED, FAN_TURN_OFF, FAN_TURN_ON = 1, 16, 32
CLIMATE_TARGET_TEMPERATURE = 1
MP_PAUSE, MP_VOLUME_SET, MP_TURN_ON, MP_TURN_OFF, MP_PLAY = 1, 4, 128, 256, 16384

LIGHT_BRIGHTNESS_MODES = {"brightness", "color_temp", "hs", "xy", "rgb", "rgbw", "rgbww", "white"}
LIGHT_COLOR_MODES = {"hs", "xy", "rgb", "rgbw", "rgbww"}

SENSOR_ROLES = {
    "temperature": "value.temperature",
    "humidity": "value.humidity",
    "moisture": "value.humidity",
    "pressure": "value.pressure",
    "atmospheric_pressure": "value.pressure",
    "power": "value.power",
    "energy": "value.power.consumption",
    "voltage": "value.voltage",
    "current": "value.current",
    "battery": "value.battery",
    "illuminance": "value.brightness",
    "signal_strength": "value.rssi",
    "frequency": "value.frequency",
    "wind_speed": "value.speed.wind",
    "precipitation": "value.precipitation",
    "aqi": "value.airquality",
}

BINARY_SENSOR_ROLES = {
    "door": "sensor.door",
    "garage_door": "sensor.door",
    "window": "sensor.window",
    "opening": "sensor.window",
    "motion": "sensor.motion",
    "occupancy": "sensor.motion",
    "presence": "sensor.motion",
    "moisture": "sensor.alarm.flood",
    "smoke": "sensor.alarm.fire",
    "heat": "sensor.alarm.fire",
    "carbon_monoxide": "sensor.alarm.co",
    "gas": "sensor.alarm",
    "safety": "sensor.alarm",
    "problem": "indicator.error",
    "tamper": "sensor.alarm",
    "battery": "indicator.lowbat",
    "connectivity": "indicator.reachable",
}

CHANNEL_RE = re.compile(r"^[a-z][a-z_]{0,29}$")
ENTITY_ID_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


class CommandError(Exception):
    """A command from ioBroker is not allowed or not valid (message in German)."""


@dataclass
class Channel:
    """One ioBroker state of an entity."""

    name: str
    type: str  # boolean | number | string
    role: str
    write: bool = False
    read: bool = True
    unit: str | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    states: dict[str, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form (only set fields)."""
        out: dict[str, Any] = {"name": self.name, "type": self.type, "role": self.role, "read": self.read, "write": self.write}
        for key in ("unit", "min", "max", "step", "states"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out


@dataclass
class Action:
    """A Home Assistant action that executes a command."""

    domain: str
    service: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class EntityInfo:
    """Snapshot of an entity (state object of HA, flattened)."""

    entity_id: str
    state: str
    attributes: dict[str, Any]
    temperature_unit: str = "°C"

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]

    @property
    def available(self) -> bool:
        return self.state != "unavailable"

    @property
    def features(self) -> int:
        try:
            return int(self.attributes.get("supported_features") or 0)
        except (TypeError, ValueError):
            return 0

    def attr(self, key: str) -> Any:
        return self.attributes.get(key)


# --------------------------------------------------------------------------- helpers


def to_number(value: Any) -> float | None:
    """Convert to a finite number or None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def clean_number(value: float | None) -> float | int | None:
    """Integers without .0, others rounded to 4 decimals."""
    if value is None:
        return None
    if float(value).is_integer():
        return int(value)
    return round(value, 4)


def known(state: str) -> bool:
    return str(state).lower() not in UNKNOWN_STATES


def rgb_to_hex(rgb: Any) -> str | None:
    if not isinstance(rgb, (list, tuple)) or len(rgb) < 3:
        return None
    try:
        return "#{:02X}{:02X}{:02X}".format(*(max(0, min(255, int(c))) for c in rgb[:3]))
    except (TypeError, ValueError):
        return None


def hex_to_rgb(value: Any) -> list[int]:
    match = HEX_RE.match(str(value).strip())
    if not match:
        raise CommandError("Farbe bitte als #RRGGBB angeben")
    raw = match.group(1)
    return [int(raw[i : i + 2], 16) for i in (0, 2, 4)]


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in ("true", "1", "on", "an", "ein", "yes", "ja"):
        return True
    if text in ("false", "0", "off", "aus", "no", "nein"):
        return False
    raise CommandError("Wahrheitswert (true/false) erwartet")


def as_number(value: Any, channel: Channel) -> float:
    number = to_number(value)
    if number is None:
        raise CommandError("Zahl erwartet")
    if channel.min is not None and number < channel.min:
        raise CommandError(f"Wert {clean_number(number)} ist kleiner als {clean_number(channel.min)}")
    if channel.max is not None and number > channel.max:
        raise CommandError(f"Wert {clean_number(number)} ist größer als {clean_number(channel.max)}")
    return number


def timestamp_ms(value: Any) -> int | None:
    if not known(str(value)):
        return None
    try:
        return int(datetime.fromisoformat(str(value)).timestamp() * 1000)
    except ValueError:
        return None


def _button(name: str, role: str = "button") -> Channel:
    return Channel(name, "boolean", role, write=True, read=False)


# --------------------------------------------------------------------------- per domain


def describe(info: EntityInfo, control: bool) -> dict[str, Channel]:
    """Return the ioBroker channels of an entity.

    ``control`` = the user allows control from ioBroker for this entity.
    """
    channels = _describe(info)
    if not control:
        for channel in channels.values():
            channel.write = False
    channels["available"] = Channel("Erreichbar", "boolean", "indicator.reachable")
    # buttons that can not be written are useless
    return {key: ch for key, ch in channels.items() if ch.read or ch.write}


def _describe(info: EntityInfo) -> dict[str, Channel]:
    d = info.domain
    a = info.attr
    f = info.features

    if d == "light":
        modes = set(a("supported_color_modes") or [])
        out = {"on": Channel("Ein/Aus", "boolean", "switch.light", write=True)}
        if modes & LIGHT_BRIGHTNESS_MODES:
            out["brightness"] = Channel("Helligkeit", "number", "level.dimmer", write=True, unit="%", min=0, max=100)
        if "color_temp" in modes:
            out["color_temp"] = Channel(
                "Farbtemperatur",
                "number",
                "level.color.temperature",
                write=True,
                unit="K",
                min=to_number(a("min_color_temp_kelvin")) or 2000,
                max=to_number(a("max_color_temp_kelvin")) or 6500,
            )
        if modes & LIGHT_COLOR_MODES:
            out["rgb"] = Channel("Farbe", "string", "level.color.rgb", write=True)
        return out

    if d in ("switch", "input_boolean"):
        role = "switch.power" if a("device_class") == "outlet" else "switch"
        return {"on": Channel("Ein/Aus", "boolean", role, write=True)}

    if d == "cover":
        out = {}
        if f & COVER_SET_POSITION:
            out["position"] = Channel("Position", "number", "level.blind", write=True, unit="%", min=0, max=100)
        elif a("current_position") is not None:
            out["position"] = Channel("Position", "number", "value.blind", unit="%", min=0, max=100)
        if f & COVER_OPEN:
            out["open"] = _button("Öffnen", "button.open.blind")
        if f & COVER_CLOSE:
            out["close"] = _button("Schließen", "button.close.blind")
        if f & COVER_STOP:
            out["stop"] = _button("Stopp", "button.stop.blind")
        out["state"] = Channel("Zustand", "string", "text")
        return out

    if d == "climate":
        out = {}
        if f & CLIMATE_TARGET_TEMPERATURE:
            out["target"] = Channel(
                "Solltemperatur",
                "number",
                "level.temperature",
                write=True,
                unit=info.temperature_unit,
                min=to_number(a("min_temp")),
                max=to_number(a("max_temp")),
                step=to_number(a("target_temp_step")),
            )
        if "current_temperature" in info.attributes:
            out["current"] = Channel("Ist-Temperatur", "number", "value.temperature", unit=info.temperature_unit)
        if "current_humidity" in info.attributes:
            out["humidity"] = Channel("Luftfeuchte", "number", "value.humidity", unit="%")
        modes = [str(m) for m in (a("hvac_modes") or [])]
        if modes:
            out["mode"] = Channel("Modus", "string", "level.mode.thermostat", write=True, states={m: m for m in modes})
        if "hvac_action" in info.attributes:
            out["action"] = Channel("Aktion", "string", "text")
        return out

    if d == "fan":
        out = {"on": Channel("Ein/Aus", "boolean", "switch", write=bool(f & (FAN_TURN_ON | FAN_TURN_OFF)))}
        if f & FAN_SET_SPEED:
            out["speed"] = Channel("Geschwindigkeit", "number", "level.speed", write=True, unit="%", min=0, max=100)
        return out

    if d == "lock":
        out = {"unlocked": Channel("Aufgeschlossen", "boolean", "switch.lock", write=True)}
        if f & LOCK_OPEN:
            out["open"] = _button("Öffnen (Falle)", "button.open.door")
        out["state"] = Channel("Zustand", "string", "text")
        return out

    if d == "valve":
        out = {}
        if f & VALVE_SET_POSITION:
            out["position"] = Channel("Position", "number", "level.valve", write=True, unit="%", min=0, max=100)
        out["opened"] = Channel("Offen", "boolean", "switch", write=bool(f & (VALVE_OPEN | VALVE_CLOSE)))
        if f & VALVE_STOP:
            out["stop"] = _button("Stopp", "button.stop")
        out["state"] = Channel("Zustand", "string", "text")
        return out

    if d in ("number", "input_number"):
        return {
            "value": Channel(
                "Wert",
                "number",
                "level",
                write=True,
                unit=a("unit_of_measurement") or None,
                min=to_number(a("min")),
                max=to_number(a("max")),
                step=to_number(a("step")),
            )
        }

    if d in ("select", "input_select"):
        options = [str(o) for o in (a("options") or [])]
        return {"value": Channel("Auswahl", "string", "text", write=True, states={o: o for o in options})}

    if d in ("text", "input_text"):
        return {"value": Channel("Text", "string", "text", write=True)}

    if d in ("button", "input_button"):
        return {"press": _button("Drücken")}

    if d == "scene":
        return {"activate": _button("Aktivieren")}

    if d == "script":
        return {"run": _button("Starten"), "running": Channel("Läuft", "boolean", "indicator.working")}

    if d == "media_player":
        out = {"state": Channel("Zustand", "string", "text")}
        if f & (MP_TURN_ON | MP_TURN_OFF):
            out["on"] = Channel("Ein/Aus", "boolean", "switch.power", write=True)
        if f & (MP_PLAY | MP_PAUSE):
            out["playing"] = Channel("Wiedergabe", "boolean", "media.state", write=True)
        if f & MP_VOLUME_SET:
            out["volume"] = Channel("Lautstärke", "number", "level.volume", write=True, unit="%", min=0, max=100)
        out["title"] = Channel("Titel", "string", "media.title")
        out["artist"] = Channel("Interpret", "string", "media.artist")
        return out

    if d in ("person", "device_tracker"):
        return {"state": Channel("Ort", "string", "text"), "home": Channel("Zu Hause", "boolean", "indicator")}

    if d == "weather":
        out = {"condition": Channel("Wetterlage", "string", "text")}
        if "temperature" in info.attributes:
            out["temperature"] = Channel("Temperatur", "number", "value.temperature", unit=a("temperature_unit") or None)
        if "humidity" in info.attributes:
            out["humidity"] = Channel("Luftfeuchte", "number", "value.humidity", unit="%")
        if "pressure" in info.attributes:
            out["pressure"] = Channel("Luftdruck", "number", "value.pressure", unit=a("pressure_unit") or None)
        if "wind_speed" in info.attributes:
            out["wind_speed"] = Channel("Windgeschwindigkeit", "number", "value.speed.wind", unit=a("wind_speed_unit") or None)
        return out

    if d == "binary_sensor":
        role = BINARY_SENSOR_ROLES.get(str(a("device_class") or ""), "indicator")
        return {"value": Channel("Wert", "boolean", role)}

    if d == "sensor":
        device_class = str(a("device_class") or "")
        if device_class == "timestamp":
            return {"value": Channel("Zeitpunkt", "number", "date")}
        if device_class == "enum":
            options = [str(o) for o in (a("options") or [])]
            return {"value": Channel("Wert", "string", "text", states={o: o for o in options} or None)}
        unit = a("unit_of_measurement") or None
        numeric = unit is not None or a("state_class") is not None or device_class in SENSOR_ROLES
        if not numeric and known(info.state) and to_number(info.state) is not None:
            numeric = True
        if numeric:
            return {"value": Channel("Wert", "number", SENSOR_ROLES.get(device_class, "value"), unit=unit)}
        return {"value": Channel("Wert", "string", "text")}

    # every other kind: state as text, read only
    return {"state": Channel("Zustand", "string", "text")}


def values(info: EntityInfo, channels: dict[str, Channel]) -> dict[str, Any]:
    """Return the current values of the channels.

    A key that is missing means "unknown at the moment, keep the last value"
    (e.g. colour of a light that is off); ``None`` means "no value".
    """
    out: dict[str, Any] = {"available": info.available}
    if not info.available:
        return out
    a = info.attr
    s = info.state
    d = info.domain

    def put(key: str, value: Any) -> None:
        if key in channels and channels[key].read:
            out[key] = value

    if d == "light":
        on = s == "on"
        put("on", on)
        if "brightness" in channels:
            brightness = to_number(a("brightness"))
            if not on:
                put("brightness", 0)
            elif brightness is not None:
                put("brightness", round(brightness / 255 * 100))
        if on and a("color_temp_kelvin") is not None:
            put("color_temp", clean_number(to_number(a("color_temp_kelvin"))))
        if on and rgb_to_hex(a("rgb_color")):
            put("rgb", rgb_to_hex(a("rgb_color")))
    elif d in ("switch", "input_boolean", "fan") and "on" in channels:
        if known(s):
            put("on", s == "on")
        if d == "fan" and "speed" in channels:
            put("speed", clean_number(to_number(a("percentage"))) if s == "on" else 0)
    elif d == "cover":
        put("position", clean_number(to_number(a("current_position"))))
        put("state", s if known(s) else None)
    elif d == "climate":
        put("target", clean_number(to_number(a("temperature"))))
        put("current", clean_number(to_number(a("current_temperature"))))
        put("humidity", clean_number(to_number(a("current_humidity"))))
        put("mode", s if known(s) else None)
        put("action", a("hvac_action"))
    elif d == "lock":
        if s == "locked":
            put("unlocked", False)
        elif s in ("unlocked", "open"):
            put("unlocked", True)
        put("state", s if known(s) else None)
    elif d == "valve":
        put("position", clean_number(to_number(a("current_position"))))
        if s in ("open", "closed"):
            put("opened", s == "open")
        put("state", s if known(s) else None)
    elif d in ("number", "input_number"):
        put("value", clean_number(to_number(s)))
    elif d in ("select", "input_select", "text", "input_text"):
        put("value", s if known(s) else None)
    elif d == "script":
        put("running", s == "on")
    elif d == "media_player":
        put("state", s if known(s) else None)
        put("on", s not in ("off", "standby") and known(s))
        put("playing", s == "playing")
        volume = to_number(a("volume_level"))
        put("volume", round(volume * 100) if volume is not None else None)
        put("title", a("media_title"))
        put("artist", a("media_artist"))
    elif d in ("person", "device_tracker"):
        put("state", s if known(s) else None)
        put("home", s == "home")
    elif d == "weather":
        put("condition", s if known(s) else None)
        for key in ("temperature", "humidity", "pressure", "wind_speed"):
            put(key, clean_number(to_number(a(key))))
    elif d == "binary_sensor":
        put("value", (s == "on") if known(s) else None)
    elif d == "sensor":
        channel = channels.get("value")
        if channel is None:
            return out
        if channel.role == "date":
            put("value", timestamp_ms(s))
        elif channel.type == "number":
            put("value", clean_number(to_number(s)) if known(s) else None)
        else:
            put("value", s if known(s) else None)
    elif "state" in channels:
        put("state", s if known(s) else None)
    return out


def command(info: EntityInfo, channels: dict[str, Channel], channel_name: str, value: Any) -> Action:
    """Translate a command from ioBroker into a Home Assistant action.

    Only the fixed actions below are possible – never an arbitrary service call.
    """
    channel = channels.get(channel_name)
    if channel is None:
        raise CommandError(f"unbekannter Datenpunkt „{channel_name}“")
    if not channel.write:
        raise CommandError("Steuerung für diesen Datenpunkt nicht erlaubt")
    if not info.available:
        raise CommandError("Entität ist in Home Assistant nicht verfügbar")
    d = info.domain
    target = {"entity_id": info.entity_id}

    def with_data(**data: Any) -> dict[str, Any]:
        return {**target, **data}

    if channel.type == "boolean" and not channel.read:
        # buttons: only "true" triggers
        if not as_bool(value):
            raise CommandError("Taster: nur true löst aus")

    if d == "light":
        if channel_name == "on":
            return Action("light", "turn_on" if as_bool(value) else "turn_off", target)
        if channel_name == "brightness":
            pct = as_number(value, channel)
            if pct <= 0:
                return Action("light", "turn_off", target)
            return Action("light", "turn_on", with_data(brightness_pct=round(pct)))
        if channel_name == "color_temp":
            return Action("light", "turn_on", with_data(color_temp_kelvin=round(as_number(value, channel))))
        if channel_name == "rgb":
            return Action("light", "turn_on", with_data(rgb_color=hex_to_rgb(value)))

    if d in ("switch", "input_boolean") and channel_name == "on":
        return Action(d, "turn_on" if as_bool(value) else "turn_off", target)

    if d == "cover":
        if channel_name == "position":
            return Action("cover", "set_cover_position", with_data(position=round(as_number(value, channel))))
        if channel_name in ("open", "close", "stop"):
            return Action("cover", {"open": "open_cover", "close": "close_cover", "stop": "stop_cover"}[channel_name], target)

    if d == "climate":
        if channel_name == "target":
            return Action("climate", "set_temperature", with_data(temperature=as_number(value, channel)))
        if channel_name == "mode":
            mode = str(value)
            if channel.states and mode not in channel.states:
                raise CommandError(f"Modus „{mode}“ gibt es nicht (möglich: {', '.join(channel.states)})")
            return Action("climate", "set_hvac_mode", with_data(hvac_mode=mode))

    if d == "fan":
        if channel_name == "on":
            return Action("fan", "turn_on" if as_bool(value) else "turn_off", target)
        if channel_name == "speed":
            pct = as_number(value, channel)
            if pct <= 0:
                return Action("fan", "turn_off", target)
            return Action("fan", "set_percentage", with_data(percentage=round(pct)))

    if d == "lock":
        if channel_name == "unlocked":
            return Action("lock", "unlock" if as_bool(value) else "lock", target)
        if channel_name == "open":
            return Action("lock", "open", target)

    if d == "valve":
        if channel_name == "position":
            return Action("valve", "set_valve_position", with_data(position=round(as_number(value, channel))))
        if channel_name == "opened":
            return Action("valve", "open_valve" if as_bool(value) else "close_valve", target)
        if channel_name == "stop":
            return Action("valve", "stop_valve", target)

    if d in ("number", "input_number") and channel_name == "value":
        return Action(d, "set_value", with_data(value=as_number(value, channel)))

    if d in ("select", "input_select") and channel_name == "value":
        option = str(value)
        if channel.states and option not in channel.states:
            raise CommandError(f"„{option}“ ist keine gültige Auswahl")
        return Action(d, "select_option", with_data(option=option))

    if d in ("text", "input_text") and channel_name == "value":
        text = str(value)
        max_len = to_number(info.attr("max"))
        if max_len is not None and len(text) > max_len:
            raise CommandError(f"Text ist länger als {int(max_len)} Zeichen")
        return Action(d, "set_value", with_data(value=text))

    if d in ("button", "input_button") and channel_name == "press":
        return Action(d, "press", target)

    if d == "scene" and channel_name == "activate":
        return Action("scene", "turn_on", target)

    if d == "script" and channel_name == "run":
        return Action("script", "turn_on", target)

    if d == "media_player":
        if channel_name == "on":
            return Action("media_player", "turn_on" if as_bool(value) else "turn_off", target)
        if channel_name == "playing":
            return Action("media_player", "media_play" if as_bool(value) else "media_pause", target)
        if channel_name == "volume":
            return Action("media_player", "volume_set", with_data(volume_level=round(as_number(value, channel) / 100, 3)))

    raise CommandError(f"„{channel_name}“ kann nicht gesteuert werden")


def config_payload(
    info: EntityInfo,
    channels: dict[str, Channel],
    *,
    name: str,
    group: str,
    area: str | None,
    control: bool,
) -> dict[str, Any]:
    """Retained config message of an entity (protocol version 1)."""
    return {
        "v": 1,
        "entity_id": info.entity_id,
        "domain": info.domain,
        "name": name,
        "group": group,
        "area": area,
        "control": control,
        "device_class": info.attr("device_class"),
        "channels": {key: ch.as_dict() for key, ch in channels.items()},
    }
