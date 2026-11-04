// Convert a Home Assistant dashboard (Lovelace config) into the native panel layout, so the
// screens can be edited in HA's own drag-and-drop dashboard editor.
//
// Mapping (see README "Editing the screens"):
//   view                    -> tab (title, icon). A view whose URL/path is "music" (or "sonos") -> the Music tab,
//                              with every media_player on it as a speaker.
//   heading card            -> starts a titled section
//   tile / button / entity  -> a tile; kind comes from the entity's domain
//   weather-forecast card   -> the clock/weather hero; a temperature sensor in the same section
//                              becomes the hero's "inside" temperature
//   button that runs light.turn_off on "all"  -> the All-off button (tap twice)
//   button with any other perform-action      -> an action button
//   entities / grid / vertical-stack cards    -> flattened into sections
//   grid_options.columns 12/6/4/3            -> full / half / third / quarter width
.pragma library

function stripMdi(i) { return i ? String(i).replace(/^mdi:/, "") : ""; }

var DEFAULT_SIZE = { light: "half", "switch": "half", scene: "quarter", climate: "half",
                     media: "full", sensor: "third", binary: "half", alloff: "quarter" };

function sizeOf(card, kind) {
    var c = card.grid_options && card.grid_options.columns;
    if (c === "full") return "full";
    if (typeof c === "number") return c >= 12 ? "full" : c >= 6 ? "half" : c >= 4 ? "third" : "quarter";
    return DEFAULT_SIZE[kind] || "half";
}

function kindFor(entity) {
    var d = String(entity || "").split(".")[0];
    if (d === "light") return "light";
    if (["switch", "input_boolean", "fan", "automation", "siren", "humidifier"].indexOf(d) >= 0) return "switch";
    if (d === "scene" || d === "script") return "scene";
    if (d === "climate") return "climate";
    if (d === "media_player") return "media";
    if (d === "binary_sensor") return "binary";
    if (d === "weather") return "weather";
    return "sensor";
}

// Returns {domain, service, data, target} for a perform-action / call-service tap action, or null.
function actionOf(card) {
    var t = card.tap_action;
    if (!t || (t.action !== "perform-action" && t.action !== "call-service")) return null;
    var svc = String(t.perform_action || t.service || "").split(".");
    if (svc.length !== 2) return null;
    return { domain: svc[0], service: svc[1], data: t.data || t.service_data || null,
             target: (t.target && t.target.entity_id) || (t.data && t.data.entity_id) || null };
}

// One card -> zero or more items (entities/grid/stack cards expand). Item = layout tile spec.
function itemsOf(card) {
    if (!card || typeof card !== "object") return [];
    var out = [];
    if (card.type === "entities" || card.type === "glance") {
        (card.entities || []).forEach(function (e) {
            if (typeof e === "string") e = { entity: e };
            if (e && e.entity) out = out.concat(itemsOf({ type: "tile", entity: e.entity, name: e.name, icon: e.icon }));
        });
        return out;
    }
    if (card.type === "grid" || card.type === "vertical-stack" || card.type === "horizontal-stack") {
        (card.cards || []).forEach(function (c) { out = out.concat(itemsOf(c)); });
        return out;
    }
    if (card.type === "weather-forecast") return [{ type: "hero", entity: card.entity || "" }];
    if (card.type === "heading") return [{ type: "heading", title: card.heading || "" }];

    var act = actionOf(card);
    if (act) {
        var all = act.domain === "light" && act.service === "turn_off" &&
                  (act.target === "all" || (act.target && act.target[0] === "all"));
        var kind = all ? "alloff" : "scene";
        return [{ type: kind, name: card.name || (all ? "All off" : ""), icon: stripMdi(card.icon) ||
                  (all ? "lightbulb-group-off-outline" : ""), size: sizeOf(card, kind), action: all ? undefined : act }];
    }
    if (!card.entity) return [];
    var k = kindFor(card.entity);
    if (k === "weather") return [{ type: "hero", entity: card.entity }];
    var it = { type: k, entity: card.entity, name: card.name || "", icon: stripMdi(card.icon), size: sizeOf(card, k) };
    if (k === "media") it.onlyPlaying = card.type === "tile";   // tile = only while playing; media-control = always
    return [it];
}

// Lovelace config -> { weather, insideTemp, pages: [...] }. nameOf(entity) gives HA friendly names.
function convert(config, nameOf) {
    var layout = { weather: "", insideTemp: "", pages: [], source: "ha" };
    (config.views || []).forEach(function (view) {
        var page = { title: view.title || "Page", icon: stripMdi(view.icon) || "view-dashboard" };
        // Gather every card in order, as groups (one per HA section, or one per card in masonry views).
        var groups = [];
        if (view.sections) view.sections.forEach(function (s) { groups.push(s.cards || []); });
        if (view.cards) view.cards.forEach(function (c) {
            if ((c.type === "entities" || c.type === "grid") && c.title)
                groups.push([{ type: "heading", heading: c.title }, c]);
            else groups.push([c]);
        });

        var vpath = String(view.path || "").toLowerCase();
        if (vpath === "music" || vpath === "sonos") {
            page.type = "music"; page.speakers = [];
            groups.forEach(function (g) { g.forEach(function (c) { itemsOf(c).forEach(function (it) {
                if (it.type === "media") page.speakers.push({ entity: it.entity, name: it.name || nameOf(it.entity) });
            }); }); });
            layout.pages.push(page);
            return;
        }

        page.sections = [];
        groups.forEach(function (g) {
            var items = [];
            g.forEach(function (c) { items = items.concat(itemsOf(c)); });
            var cur = null;
            var hero = null;
            items.forEach(function (it) {
                if (it.type === "heading") { cur = { title: it.title, items: [] }; page.sections.push(cur); return; }
                if (it.type === "hero") {
                    if (it.entity && !layout.weather) layout.weather = it.entity;
                    hero = { type: "hero" }; page.sections.push(hero); cur = null; return;
                }
                // A temperature sensor next to the weather card is the hero's inside temperature.
                if (hero && it.type === "sensor" && !layout.insideTemp && !cur) { layout.insideTemp = it.entity; return; }
                if (!cur) { cur = { title: "", items: [] }; page.sections.push(cur); }
                cur.items.push(it);
            });
        });
        page.sections = page.sections.filter(function (s) { return s.type === "hero" || s.items.length > 0; });
        page.sections.forEach(function (s) {
            if (s.items && s.items.every(function (i) { return i.type === "media" && i.onlyPlaying; }))
                s.hideWhenEmpty = true;
        });
        layout.pages.push(page);
    });
    return layout;
}
