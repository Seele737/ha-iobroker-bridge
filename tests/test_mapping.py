"""Tests of the translation HA entity <-> ioBroker states (pure Python)."""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "iobroker_bridge"))

import mapping as m  # noqa: E402


def info(entity_id: str, state: str, **attrs):
    return m.EntityInfo(entity_id, state, attrs)


def test_light_with_colour_temperature_and_rgb():
    light = info(
        "light.wohnzimmer",
        "on",
        supported_color_modes=["color_temp", "hs"],
        brightness=128,
        color_temp_kelvin=3000,
        min_color_temp_kelvin=2200,
        max_color_temp_kelvin=6500,
        rgb_color=[255, 128, 0],
        friendly_name="Wohnzimmer",
    )
    ch = m.describe(light, True)
    assert set(ch) == {"on", "brightness", "color_temp", "rgb", "available"}
    assert ch["color_temp"].min == 2200 and ch["color_temp"].max == 6500
    assert ch["on"].role == "switch.light" and ch["brightness"].role == "level.dimmer"
    vals = m.values(light, ch)
    assert vals == {"available": True, "on": True, "brightness": 50, "color_temp": 3000, "rgb": "#FF8000"}
    assert m.command(light, ch, "brightness", 30) == m.Action("light", "turn_on", {"entity_id": "light.wohnzimmer", "brightness_pct": 30})
    assert m.command(light, ch, "brightness", 0).service == "turn_off"
    assert m.command(light, ch, "rgb", "#00ff00").data["rgb_color"] == [0, 255, 0]
    assert m.command(light, ch, "on", False).service == "turn_off"
    with pytest.raises(m.CommandError):
        m.command(light, ch, "color_temp", 9000)  # above max
    with pytest.raises(m.CommandError):
        m.command(light, ch, "rgb", "rot")


def test_light_off_keeps_colour_and_reports_brightness_zero():
    light = info("light.flur", "off", supported_color_modes=["color_temp"], min_color_temp_kelvin=2700, max_color_temp_kelvin=6500)
    ch = m.describe(light, True)
    assert m.values(light, ch) == {"available": True, "on": False, "brightness": 0}


def test_onoff_light_has_no_brightness():
    light = info("light.kueche", "on", supported_color_modes=["onoff"])
    assert set(m.describe(light, True)) == {"on", "available"}


def test_control_off_makes_everything_read_only_and_removes_buttons():
    cover = info("cover.kueche", "open", supported_features=15, current_position=100)
    ch = m.describe(cover, False)
    assert not any(c.write for c in ch.values())
    assert set(ch) == {"position", "state", "available"}
    with pytest.raises(m.CommandError, match="nicht erlaubt"):
        m.command(cover, ch, "position", 20)


def test_cover():
    cover = info("cover.kueche", "closed", supported_features=15, current_position=0)
    ch = m.describe(cover, True)
    assert ch["position"].role == "level.blind" and ch["open"].role == "button.open.blind" and not ch["open"].read
    assert m.values(cover, ch) == {"available": True, "position": 0, "state": "closed"}
    assert m.command(cover, ch, "position", 40).data == {"entity_id": "cover.kueche", "position": 40}
    assert m.command(cover, ch, "stop", True).service == "stop_cover"
    with pytest.raises(m.CommandError, match="nur true"):
        m.command(cover, ch, "open", False)


def test_cover_without_position_feature():
    cover = info("cover.tor", "open", supported_features=3)
    ch = m.describe(cover, True)
    assert "position" not in ch and "stop" not in ch and "open" in ch


def test_climate():
    climate = info(
        "climate.bad",
        "heat",
        supported_features=1,
        temperature=21.5,
        current_temperature=20.3,
        current_humidity=55,
        hvac_modes=["off", "heat"],
        min_temp=7,
        max_temp=30,
        target_temp_step=0.5,
        hvac_action="heating",
    )
    climate.temperature_unit = "°C"
    ch = m.describe(climate, True)
    assert ch["target"].unit == "°C" and ch["target"].min == 7 and ch["target"].step == 0.5
    assert ch["mode"].states == {"off": "off", "heat": "heat"}
    assert m.values(climate, ch) == {
        "available": True,
        "target": 21.5,
        "current": 20.3,
        "humidity": 55,
        "mode": "heat",
        "action": "heating",
    }
    assert m.command(climate, ch, "target", 22).data["temperature"] == 22
    assert m.command(climate, ch, "mode", "off").service == "set_hvac_mode"
    with pytest.raises(m.CommandError, match="gibt es nicht"):
        m.command(climate, ch, "mode", "cool")
    with pytest.raises(m.CommandError, match="größer"):
        m.command(climate, ch, "target", 35)


def test_lock_uses_iobroker_convention_true_is_unlocked():
    lock = info("lock.haustuer", "locked", supported_features=1)
    ch = m.describe(lock, True)
    assert ch["unlocked"].role == "switch.lock"
    assert m.values(lock, ch)["unlocked"] is False
    assert m.command(lock, ch, "unlocked", True).service == "unlock"
    assert m.command(lock, ch, "unlocked", False).service == "lock"
    assert m.command(lock, ch, "open", True).service == "open"
    jammed = info("lock.haustuer", "jammed", supported_features=1)
    assert "unlocked" not in m.values(jammed, ch)


def test_fan_valve_media_player():
    fan = info("fan.bad", "on", supported_features=1 | 16 | 32, percentage=60)
    ch = m.describe(fan, True)
    assert m.values(fan, ch) == {"available": True, "on": True, "speed": 60}
    assert m.command(fan, ch, "speed", 0).service == "turn_off"
    assert m.command(fan, ch, "speed", 33).data["percentage"] == 33

    valve = info("valve.garten", "closed", supported_features=1 | 2 | 4, current_position=0)
    ch = m.describe(valve, True)
    assert m.values(valve, ch) == {"available": True, "position": 0, "opened": False, "state": "closed"}
    assert m.command(valve, ch, "opened", True).service == "open_valve"

    player = info(
        "media_player.kueche",
        "playing",
        supported_features=1 | 4 | 128 | 256 | 16384,
        volume_level=0.25,
        media_title="Song",
        media_artist="Band",
    )
    ch = m.describe(player, True)
    assert m.values(player, ch) == {
        "available": True,
        "state": "playing",
        "on": True,
        "playing": True,
        "volume": 25,
        "title": "Song",
        "artist": "Band",
    }
    assert m.command(player, ch, "volume", 50).data["volume_level"] == 0.5
    assert m.command(player, ch, "playing", False).service == "media_pause"


def test_inputs_select_text_buttons_scene_script():
    number = info("input_number.soll", "21.5", min=10, max=30, step=0.5, unit_of_measurement="°C")
    ch = m.describe(number, True)
    assert ch["value"].unit == "°C" and m.values(number, ch)["value"] == 21.5
    assert m.command(number, ch, "value", 22).domain == "input_number"

    select = info("select.modus", "Auto", options=["Auto", "Aus"])
    ch = m.describe(select, True)
    assert m.command(select, ch, "value", "Aus").data["option"] == "Aus"
    with pytest.raises(m.CommandError):
        m.command(select, ch, "value", "Party")

    text = info("input_text.notiz", "hallo", max=5)
    ch = m.describe(text, True)
    with pytest.raises(m.CommandError, match="länger"):
        m.command(text, ch, "value", "zu lang")

    button = info("button.neustart", "unknown")
    assert m.command(button, m.describe(button, True), "press", True).service == "press"
    scene = info("scene.abend", "unknown")
    assert m.command(scene, m.describe(scene, True), "activate", True) == m.Action("scene", "turn_on", {"entity_id": "scene.abend"})
    script = info("script.gute_nacht", "off")
    ch = m.describe(script, True)
    assert m.values(script, ch) == {"available": True, "running": False}
    assert m.command(script, ch, "run", 1).service == "turn_on"


def test_sensors():
    temp = info("sensor.aussen", "12.34", device_class="temperature", unit_of_measurement="°C", state_class="measurement")
    ch = m.describe(temp, True)
    assert ch["value"].role == "value.temperature" and ch["value"].type == "number" and not ch["value"].write
    assert m.values(temp, ch)["value"] == 12.34
    unknown = info("sensor.aussen", "unknown", device_class="temperature", unit_of_measurement="°C")
    assert m.values(unknown, ch)["value"] is None

    text = info("sensor.wetter", "sonnig")
    assert m.describe(text, True)["value"].type == "string"

    ts = info("sensor.letzte_bewegung", "2026-11-04T12:00:00+00:00", device_class="timestamp")
    ch = m.describe(ts, True)
    assert ch["value"].role == "date" and m.values(ts, ch)["value"] == 1793793600000

    door = info("binary_sensor.haustuer", "on", device_class="door")
    ch = m.describe(door, True)
    assert ch["value"].role == "sensor.door" and m.values(door, ch)["value"] is True

    with pytest.raises(m.CommandError):
        m.command(temp, m.describe(temp, True), "value", 3)


def test_unavailable_entity():
    light = info("light.wohnzimmer", "unavailable", supported_color_modes=["brightness"])
    ch = m.describe(light, True)
    assert m.values(light, ch) == {"available": False}
    with pytest.raises(m.CommandError, match="nicht verfügbar"):
        m.command(light, ch, "on", True)


def test_unknown_domains_are_read_only_text():
    sun = info("sun.sun", "above_horizon")
    ch = m.describe(sun, True)
    assert set(ch) == {"state", "available"} and not ch["state"].write
    assert m.values(sun, ch)["state"] == "above_horizon"
    with pytest.raises(m.CommandError):
        m.command(sun, ch, "state", "below_horizon")


def test_unknown_channel_and_bad_values_are_rejected():
    switch = info("switch.pumpe", "off")
    ch = m.describe(switch, True)
    with pytest.raises(m.CommandError, match="unbekannt"):
        m.command(switch, ch, "brightness", 1)
    with pytest.raises(m.CommandError, match="Wahrheitswert"):
        m.command(switch, ch, "on", "vielleicht")
    assert m.command(switch, ch, "on", "true").service == "turn_on"


def test_config_payload():
    sw = info("switch.pumpe", "on", device_class="outlet")
    ch = m.describe(sw, True)
    cfg = m.config_payload(sw, ch, name="Pumpe", group="Garten", area="Garten", control=True)
    assert cfg["v"] == 1 and cfg["domain"] == "switch" and cfg["group"] == "Garten"
    assert cfg["channels"]["on"] == {"name": "Ein/Aus", "type": "boolean", "role": "switch.power", "read": True, "write": True}
    assert all(m.CHANNEL_RE.match(k) for k in cfg["channels"])
