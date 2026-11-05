/*
 * ioBroker dashboard for Home Assistant (dashboard strategy "custom:iobroker").
 *
 * Settings -> Dashboards -> Add dashboard -> "ioBroker".
 * The dashboard is rebuilt every time it is opened, so it is always up to date.
 * "Edit -> Take control" turns it into a normal dashboard you can change yourself.
 *
 * Data: websocket command "iobroker_bridge/dashboard" of the integration + hass.states.
 */

const STRATEGY_TYPE = 'iobroker';
const SKIP_DOMAINS = new Set(['notify']);

const TEXTS = {
    de: {
        title: 'ioBroker',
        overview: 'Übersicht',
        rooms: 'Räume',
        system: 'System',
        toIobroker: 'Nach ioBroker',
        noGroup: 'Weitere Geräte',
        noArea: 'Ohne Bereich',
        noDevices:
            'Noch keine Geräte aus ioBroker. Wähle im ioBroker-Adapter **homeassistant-bridge** Datenpunkte oder Geräte aus – sie erscheinen dann automatisch hier.',
        noExport:
            'Noch nichts ausgewählt. Unter **Einstellungen → Geräte & Dienste → ioBroker Bridge** legst du in einer Gruppe fest, welche Home-Assistant-Geräte an ioBroker gesendet werden.',
        noIntegration:
            'Die Integration **ioBroker Bridge** ist nicht eingerichtet oder nicht geladen. Bitte unter **Einstellungen → Geräte & Dienste** prüfen.',
        bridge: 'Bridge',
        instance: 'Instanz',
        version: 'Adapter-Version',
        connection: 'Verbindung',
        online: 'verbunden',
        offline: 'getrennt',
        unknown: 'unbekannt',
        openAdmin: 'ioBroker-Admin öffnen',
        control: 'ioBroker darf steuern',
        readOnly: 'nur Anzeige',
        mode: { immediate: 'sofort', throttle: 'höchstens alle {s} s', combined: 'sofort + alle {s} s' },
        duplicates: 'In mehreren Gruppen ausgewählt (zählt nur in der ersten): {list}',
        description: 'Alle Geräte aus ioBroker, der ioBroker-Systemstatus und was an ioBroker gesendet wird.',
    },
    en: {
        title: 'ioBroker',
        overview: 'Overview',
        rooms: 'Rooms',
        system: 'System',
        toIobroker: 'To ioBroker',
        noGroup: 'Other devices',
        noArea: 'No area',
        noDevices:
            'No devices from ioBroker yet. Select states or devices in the ioBroker adapter **homeassistant-bridge** – they then appear here automatically.',
        noExport:
            'Nothing selected yet. In **Settings → Devices & services → ioBroker Bridge** a group defines which Home Assistant devices are sent to ioBroker.',
        noIntegration:
            'The integration **ioBroker Bridge** is not set up or not loaded. Please check **Settings → Devices & services**.',
        bridge: 'Bridge',
        instance: 'Instance',
        version: 'Adapter version',
        connection: 'Connection',
        online: 'connected',
        offline: 'disconnected',
        unknown: 'unknown',
        openAdmin: 'Open ioBroker admin',
        control: 'ioBroker may control',
        readOnly: 'display only',
        mode: { immediate: 'immediately', throttle: 'at most every {s} s', combined: 'immediately + every {s} s' },
        duplicates: 'Selected in several groups (only the first counts): {list}',
        description: 'All devices from ioBroker, the ioBroker system status and what is sent to ioBroker.',
    },
};

function texts(language) {
    return String(language || '').toLowerCase().startsWith('de') ? TEXTS.de : TEXTS.en;
}

/** Tile features that fit the kind of entity (only those that HA offers). */
function features(stateObj) {
    if (!stateObj) {
        return [];
    }
    const domain = stateObj.entity_id.split('.')[0];
    const attrs = stateObj.attributes || {};
    const supported = Number(attrs.supported_features || 0);
    switch (domain) {
        case 'light': {
            const modes = attrs.supported_color_modes || [];
            return modes.some(m => m !== 'onoff') ? [{ type: 'light-brightness' }] : [];
        }
        case 'cover':
            return supported & 4 ? [{ type: 'cover-position' }] : supported & 3 ? [{ type: 'cover-open-close' }] : [];
        case 'climate':
            return attrs.temperature !== undefined ? [{ type: 'target-temperature' }] : [];
        case 'fan':
            return supported & 1 ? [{ type: 'fan-speed' }] : [];
        case 'lock':
            return [{ type: 'lock-commands' }];
        case 'valve':
            return supported & 3 ? [{ type: 'valve-open-close' }] : [];
        case 'media_player':
            return [{ type: 'media-player-playback' }];
        case 'number':
        case 'input_number':
            return [{ type: 'numeric-input' }];
        case 'select':
        case 'input_select':
            return [{ type: 'select-options' }];
        default:
            return [];
    }
}

function tile(hass, entityId, stripPrefix) {
    const card = { type: 'tile', entity: entityId };
    const stateObj = hass.states[entityId];
    const friendly = stateObj && stateObj.attributes.friendly_name;
    if (stripPrefix && typeof friendly === 'string' && friendly.startsWith(stripPrefix) && friendly.length > stripPrefix.length) {
        // "ioBroker Speicherplatz frei" -> "Speicherplatz frei" (the section already says ioBroker)
        card.name = friendly.slice(stripPrefix.length).trim();
    }
    const list = features(hass.states[entityId]);
    if (list.length) {
        card.features = list;
        card.features_position = 'bottom';
    }
    return card;
}

function heading(text, icon, style) {
    const card = { type: 'heading', heading: text, heading_style: style || 'title' };
    if (icon) {
        card.icon = icon;
    }
    return card;
}

function markdown(content) {
    return { type: 'markdown', content };
}

function sortByName(hass, ids) {
    const name = id => String((hass.states[id] && hass.states[id].attributes.friendly_name) || id).toLowerCase();
    return [...ids].sort((a, b) => name(a).localeCompare(name(b)));
}

function areaOf(hass, entityId) {
    const entity = hass.entities && hass.entities[entityId];
    let areaId = entity && entity.area_id;
    if (!areaId && entity && entity.device_id && hass.devices && hass.devices[entity.device_id]) {
        areaId = hass.devices[entity.device_id].area_id;
    }
    return areaId && hass.areas && hass.areas[areaId] ? hass.areas[areaId].name : null;
}

/** Sections, one per key, sorted by name (the fallback key last). */
function sectionsBy(hass, ids, keyOf, fallback, icon) {
    const buckets = new Map();
    for (const id of ids) {
        const key = keyOf(id) || fallback;
        if (!buckets.has(key)) {
            buckets.set(key, []);
        }
        buckets.get(key).push(id);
    }
    const keys = [...buckets.keys()].sort((a, b) => (a === fallback) - (b === fallback) || a.localeCompare(b));
    return keys.map(key => ({
        type: 'grid',
        cards: [heading(key, icon), ...sortByName(hass, buckets.get(key)).map(id => tile(hass, id))],
    }));
}

function bridgeInfo(t, bridge) {
    const state = bridge.online === true ? t.online : bridge.online === false ? t.offline : t.unknown;
    const lines = [
        `**${t.bridge}:** ${bridge.title}`,
        `**${t.instance}:** ${bridge.instance || bridge.base_topic}`,
        `**${t.version}:** ${bridge.version || '?'}`,
        `**${t.connection}:** ${state}`,
    ];
    if (bridge.admin_url && /^https?:\/\//.test(bridge.admin_url)) {
        lines.push('', `[${t.openAdmin}](${bridge.admin_url})`);
    }
    return markdown(lines.join('  \n'));
}

class IoBrokerDashboardStrategy extends HTMLElement {
    static async generate(config, hass) {
        const t = texts(hass.language || (hass.locale && hass.locale.language));
        let data;
        try {
            data = await hass.callWS({ type: 'iobroker_bridge/dashboard' });
        } catch (err) {
            return {
                title: t.title,
                views: [{ title: t.overview, path: 'iobroker', type: 'sections', sections: [{ type: 'grid', cards: [markdown(t.noIntegration)] }] }],
            };
        }
        const bridges = data.bridges || [];
        const devices = [];
        const system = [];
        const groupOf = {};
        for (const bridge of bridges) {
            for (const e of bridge.iobroker_entities || []) {
                if (SKIP_DOMAINS.has(e.entity_id.split('.')[0]) || !hass.states[e.entity_id]) {
                    continue;
                }
                if (e.system) {
                    system.push(e.entity_id);
                } else {
                    devices.push(e.entity_id);
                    groupOf[e.entity_id] = e.group;
                }
            }
        }

        const overviewSections = devices.length
            ? sectionsBy(hass, devices, id => groupOf[id], t.noGroup, 'mdi:folder-outline')
            : [{ type: 'grid', cards: [markdown(t.noDevices)] }];
        const roomSections = devices.length
            ? sectionsBy(hass, devices, id => areaOf(hass, id), t.noArea, 'mdi:sofa-outline')
            : [{ type: 'grid', cards: [markdown(t.noDevices)] }];

        const systemSections = bridges.map(bridge => ({
            type: 'grid',
            cards: [
                heading(bridge.title, 'mdi:server-network'),
                bridgeInfo(t, bridge),
                ...sortByName(
                    hass,
                    system.filter(id => (bridge.iobroker_entities || []).some(e => e.entity_id === id)),
                ).map(id => tile(hass, id, 'ioBroker ')),
            ],
        }));

        const exportSections = [];
        for (const bridge of bridges) {
            for (const group of bridge.groups || []) {
                const mode = (t.mode[group.mode] || group.mode).replace('{s}', group.interval);
                const ids = (group.entities || []).filter(id => hass.states[id]);
                exportSections.push({
                    type: 'grid',
                    cards: [
                        heading(group.name, 'mdi:export'),
                        heading(`${mode} · ${group.control ? t.control : t.readOnly}`, null, 'subtitle'),
                        ...(ids.length ? sortByName(hass, ids).map(id => tile(hass, id)) : [markdown(t.noExport)]),
                    ],
                });
            }
            if ((bridge.duplicates || []).length) {
                exportSections.push({ type: 'grid', cards: [markdown(t.duplicates.replace('{list}', bridge.duplicates.join(', ')))] });
            }
        }
        if (!exportSections.length) {
            exportSections.push({ type: 'grid', cards: [markdown(t.noExport)] });
        }

        return {
            title: t.title,
            views: [
                { title: t.overview, path: 'iobroker', icon: 'mdi:home-automation', type: 'sections', max_columns: 4, sections: overviewSections },
                { title: t.rooms, path: 'raeume', icon: 'mdi:sofa-outline', type: 'sections', max_columns: 4, sections: roomSections },
                { title: t.system, path: 'system', icon: 'mdi:server', type: 'sections', max_columns: 4, sections: systemSections.length ? systemSections : [{ type: 'grid', cards: [markdown(t.noIntegration)] }] },
                { title: t.toIobroker, path: 'nach-iobroker', icon: 'mdi:export', type: 'sections', max_columns: 4, sections: exportSections },
            ],
        };
    }
}

if (!customElements.get(`ll-strategy-dashboard-${STRATEGY_TYPE}`)) {
    customElements.define(`ll-strategy-dashboard-${STRATEGY_TYPE}`, IoBrokerDashboardStrategy);
}

// shows the tile "ioBroker" in Settings -> Dashboards -> Add dashboard
window.customStrategies = window.customStrategies || [];
if (!window.customStrategies.some(s => s.type === STRATEGY_TYPE && s.strategyType === 'dashboard')) {
    const t = texts(navigator.language);
    window.customStrategies.push({
        type: STRATEGY_TYPE,
        strategyType: 'dashboard',
        name: t.title,
        description: t.description,
        documentationURL: 'https://github.com/Seele737/ha-iobroker-bridge',
    });
}
