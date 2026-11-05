# ioBroker Bridge für Home Assistant

Gegenstück zum ioBroker-Adapter **[homeassistant-bridge](https://github.com/Seele737/iobroker-Homeassistant)** (ab Version 0.5.0).

- **Home Assistant → ioBroker:** Du wählst in Home Assistant aus, welche Geräte an ioBroker gesendet werden. Dort erscheinen sie als Datenpunkte unter `homeassistant-bridge.0.ha` und lassen sich auch von ioBroker aus steuern.
- **Dashboard „ioBroker“:** ein fertiges Dashboard mit allen Geräten aus ioBroker, dem ioBroker-Systemstatus und einer Übersicht, was an ioBroker gesendet wird.

Es wird die **vorhandene MQTT-Integration** von Home Assistant genutzt. Du brauchst also keine zusätzliche Verbindung und kein weiteres Passwort.

*English: Counterpart of the ioBroker adapter "homeassistant-bridge": choose Home Assistant entities that are sent to ioBroker (and can be controlled from there) and get a ready-made "ioBroker" dashboard. Uses the existing MQTT integration.*

## Voraussetzungen

- Home Assistant **2026.10 oder neuer** (getestet mit 2026.10.4 und 2026.11.0)
- MQTT-Integration in Home Assistant ist eingerichtet (z. B. mit dem Mosquitto-Add-on)
- ioBroker-Adapter **homeassistant-bridge 0.5.0 oder neuer**, verbunden mit **demselben** MQTT-Broker

## Installation (HACS bzw. ab HA 2026.11 „Marketplace“)

1. **HACS** öffnen → oben rechts **⋮** → **Benutzerdefinierte Repositories**.
   Ab HA 2026.11 heißt das **Einstellungen → Marketplace**.
2. Als Repository `https://github.com/Seele737/ha-iobroker-bridge` eintragen, Typ **Integration** → **Hinzufügen**.
3. In der Liste **ioBroker Bridge** öffnen → **Herunterladen**.
4. Home Assistant **neu starten** (Einstellungen → System → Neu starten).

> HACS kann nur **öffentliche** GitHub-Repositories laden.

## Einrichten

1. Läuft der ioBroker-Adapter, zeigt Home Assistant unter **Einstellungen → Geräte & Dienste** automatisch **„Entdeckt: ioBroker (homeassistant-bridge.0)“**. Dort auf **Hinzufügen** → **OK** klicken.
   - Erscheint nichts, wähle **Integration hinzufügen → ioBroker Bridge** und trage das Basis-Topic aus dem Adapter ein (Reiter „Erweitert“, Standard `iobroker-ha/0`).
2. In der Integration gibt es die Gruppe **„Standard“**. Mit dem **Zahnrad** wählst du die Entitäten aus, die an ioBroker gehen sollen.
3. Mit **„Gruppe hinzufügen“** legst du weitere Gruppen an. Jede Gruppe hat:

| Einstellung | Standard | Bedeutung |
|---|---|---|
| Entitäten | – | werden an ioBroker gesendet |
| Ganze Bereiche | leer | alle sichtbaren Entitäten eines Bereichs, auch solche, die später hinzukommen |
| Wann senden | sofort | *sofort*, *höchstens alle X s* (spart Last) oder *sofort + regelmäßig* |
| Steuerung aus ioBroker erlauben | an | aus = ioBroker zeigt die Werte nur an |

Entitäten, die selbst aus ioBroker stammen, werden nicht angeboten. Sonst entstünde eine Schleife.

## Was in ioBroker ankommt

Beispiel `light.wohnzimmer`:

| Datenpunkt | Rolle | Bedeutung |
|---|---|---|
| `homeassistant-bridge.0.ha.light.wohnzimmer.on` | `switch.light` | Ein/Aus |
| `….brightness` | `level.dimmer` | Helligkeit 0–100 % |
| `….color_temp` | `level.color.temperature` | Farbtemperatur in Kelvin |
| `….rgb` | `level.color.rgb` | Farbe `#RRGGBB` |
| `….available` | `indicator.reachable` | in HA erreichbar |

Unterstützte Arten:
- **Mit Steuerung:** Schalter, Licht, Rollladen, Thermostat, Lüfter, Schloss, Ventil, Zahl, Auswahl, Text, Taster, Szene, Skript, Mediaplayer (Ein/Aus, Play/Pause, Lautstärke) sowie die Eingabe-Helfer (`input_*`).
- **Nur Anzeige:** Sensor, Binärsensor, Person, Gerätetracker, Wetter.
- **Andere Arten:** erscheinen als Text.

**Steuern:** den Datenpunkt in ioBroker schreiben (`ack=false`). Home Assistant führt eine fest zugeordnete Aktion aus, z. B. `light.turn_on` mit `brightness_pct`. Danach meldet HA den neuen Zustand, und ioBroker setzt `ack=true`.

## Sicherheit

- ioBroker kann **keine beliebigen HA-Dienste** aufrufen. Möglich ist nur eine feste Liste je Art und nur für ausgewählte Entitäten.
- Gruppen ohne Steuerungsrecht: Befehle werden abgelehnt, und die Begründung erscheint in ioBroker unter „Diagnose“.
- Werte werden geprüft, z. B. Min/Max oder gültige Auswahl.
- Gespeicherte (retained) Befehle werden nie ausgeführt. Mehr als 5 Befehle pro Sekunde und Entität werden verworfen.

## Dashboard

**Einstellungen → Dashboards → Dashboard hinzufügen → „ioBroker“** (unter „Community-Dashboards“) → Titel eingeben → **Erstellen**.

| Ansicht | Inhalt |
|---|---|
| Übersicht | Geräte aus ioBroker, sortiert nach den Gruppen des Adapters |
| Räume | dieselben Geräte nach Bereich |
| System | ioBroker-Status, Updates, Verbindung, Link zum ioBroker-Admin |
| Nach ioBroker | was HA an ioBroker sendet, je Gruppe |

Das Dashboard baut sich bei jedem Öffnen neu auf. Mit **Bearbeiten → Kontrolle übernehmen** machst du daraus ein normales Dashboard, das du selbst ändern kannst.

## Fehlersuche

- **Nichts entdeckt:** Sind Adapter und HA mit demselben Broker verbunden? Ist das Basis-Topic gleich? Ist der Adapter mindestens Version 0.5.0?
- **Befehle aus ioBroker kommen nicht an:** Siehe im Adapter die Reiter „Aus Home Assistant“ und „Diagnose“. In HA findest du unter **Einstellungen → Geräte & Dienste → ioBroker Bridge → ⋮ → Diagnose herunterladen** die Zähler und den letzten Fehler.

## Technik (MQTT-Protokoll, Version 1)

Unterhalb des Basis-Topics des Adapters (Standard `iobroker-ha/0`):

| Topic | Richtung | Inhalt |
|---|---|---|
| `bridge/info` | ioBroker → HA, retained | Instanz, Version, `bridge_id`, Admin-Link (für die automatische Erkennung) |
| `ha/status` | HA → ioBroker, retained | `online` / `offline` |
| `ha/<entity_id>/config` | HA → ioBroker, retained | Name, Gruppe, Bereich, Datenpunkte (Typ, Rolle, Einheit, Grenzen, schreibbar) |
| `ha/<entity_id>/state` | HA → ioBroker, retained | `{"available": …, "values": {…}}` |
| `ha/<entity_id>/cmd` | ioBroker → HA, **nicht** retained | `{"channel": "brightness", "value": 50}` |
| `ha/<entity_id>/result` | HA → ioBroker | `{"channel": …, "ok": true/false, "error": …}` |

## Entwicklung

```bash
pip install pytest
```

```bash
pytest tests
```

Die Ende-zu-Ende-Tests mit einem echten Home Assistant, Broker und ioBroker-Adapter liegen im Adapter-Repository (`test/e2e/ha-integration.test.js`).

## Lizenz

MIT – Copyright (c) 2026 Seele737
