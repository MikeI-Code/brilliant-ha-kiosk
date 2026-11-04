// Sonos: room picker, big now-playing, transport, volume, grouping, favorites.
import QtQuick 2.15
import "."

Flickable {
    id: page
    property var speakers: []            // [{entity, name}]
    property int sel: 0
    property var st: null
    property var favorites: []
    property string favError: ""
    property real nowSecs: Date.now() / 1000

    readonly property string entity: speakers.length ? speakers[sel].entity : ""
    readonly property var a: st ? st.attributes : ({})
    readonly property bool playing: st && st.state === "playing"
    readonly property var members: a.group_members || []

    contentWidth: width
    contentHeight: body.height + 24 * Theme.u
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    interactive: !Panel.multi            // two-finger gestures must not scroll the page
    flickDeceleration: 4000

    function refresh() { st = entity ? Ha.get(entity) : null; }
    function nameOf(e) { for (var i = 0; i < speakers.length; i++) if (speakers[i].entity === e) return speakers[i].name; return e; }
    function pic(p) { return !p ? "" : (p.indexOf("http") === 0 ? p : Ha.baseUrl + p); }
    function position() {
        if (a.media_position === undefined || !a.media_duration) return 0;
        var p = a.media_position;
        if (playing && a.media_position_updated_at) p += nowSecs - Date.parse(a.media_position_updated_at) / 1000;
        return Math.max(0, Math.min(1, p / a.media_duration));
    }
    // favorites: [{ title: "Stations", items: [browse items] }, ...] — Sonos groups favorites by kind.
    function loadFavorites() {
        if (!entity || !Ha.connected) return;
        Ha.send({ type: "media_player/browse_media", entity_id: entity, media_content_type: "favorites", media_content_id: "" }, function (r) {
            if (!page) return;   // page was rebuilt (layout changed) before the reply arrived
            if (!r.success) { page.favError = "Favorites unavailable"; console.log("sonos: favorites " + JSON.stringify(r.error)); return; }
            var kids = r.result.children || [];
            var loose = kids.filter(function (k) { return k.can_play; });
            var folders = kids.filter(function (k) { return !k.can_play && k.can_expand; });
            var groups = new Array(folders.length);
            function done() {
                if (!page) return;
                var out = groups.filter(function (g) { return g && g.items.length; });
                if (loose.length) out.unshift({ title: "Favorites", items: loose });
                page.favorites = out;
                page.favError = out.length ? "" : "No Sonos favorites yet — star stations and playlists in the Sonos app";
            }
            if (folders.length === 0) { done(); return; }
            var left = folders.length;
            folders.forEach(function (f, i) {
                Ha.send({ type: "media_player/browse_media", entity_id: page.entity, media_content_type: f.media_content_type, media_content_id: f.media_content_id }, function (r2) {
                    groups[i] = { title: f.title, items: r2.success ? (r2.result.children || []).filter(function (k) { return k.can_play; }) : [] };
                    if (--left === 0) done();
                });
            });
        });
    }
    function play(item) {
        Ha.callService("media_player", "play_media", { media_content_id: item.media_content_id, media_content_type: item.media_content_type }, entity);
    }
    function toggleMember(e) {
        if (e === entity) return;
        if (members.indexOf(e) >= 0) Ha.callService("media_player", "unjoin", null, e);
        else Ha.callService("media_player", "join", { group_members: [e] }, entity);
    }

    onEntityChanged: { refresh(); favorites = []; loadFavorites(); }
    Connections {
        target: Ha
        function onEntityUpdated(id) { if (id === page.entity) page.refresh(); }
        function onStatesLoaded() { page.refresh(); page.loadFavorites(); }
    }
    Timer { interval: 1000; running: page.playing && page.visible; repeat: true; onTriggered: page.nowSecs = Date.now() / 1000 }

    Column {
        id: body
        x: Theme.gap; width: parent.width - 2 * Theme.gap
        y: Theme.gap
        spacing: 14 * Theme.u

        // Room picker
        Flickable {
            width: parent.width; height: 40 * Theme.u
            contentWidth: chips.width; clip: true
            flickableDirection: Flickable.HorizontalFlick; boundsBehavior: Flickable.StopAtBounds
            Row {
                id: chips; spacing: 8 * Theme.u
                Repeater {
                    model: page.speakers
                    Rectangle {
                        readonly property bool isSel: index === page.sel
                        height: 36 * Theme.u; width: chipLabel.width + 28 * Theme.u; radius: height / 2
                        color: isSel ? Theme.blue : Theme.card
                        border.color: isSel ? Theme.blue : Theme.border
                        Row {
                            anchors.centerIn: parent; spacing: 5 * Theme.u
                            Rectangle { width: 7 * Theme.u; height: width; radius: width / 2; anchors.verticalCenter: parent.verticalCenter
                                        color: parent.parent.isSel ? Theme.bg : Theme.green
                                        visible: chipState.playing }
                            Label { id: chipLabel; size: 13; font.weight: Font.DemiBold; text: modelData.name
                                    color: parent.parent.isSel ? Theme.bg : Theme.text }
                        }
                        QtObject { id: chipState; property bool playing: false }
                        Connections { target: Ha
                            function onEntityUpdated(id) { if (id === modelData.entity) chipState.playing = (Ha.get(id) || {}).state === "playing"; }
                            function onStatesLoaded() { chipState.playing = (Ha.get(modelData.entity) || {}).state === "playing"; } }
                        Component.onCompleted: chipState.playing = (Ha.get(modelData.entity) || {}).state === "playing"
                        MouseArea { anchors.fill: parent; onClicked: if (!Panel.multi) page.sel = index }
                    }
                }
            }
        }

        // Now playing
        Rectangle {
            width: parent.width; height: np.height + 2 * Theme.pad; radius: Theme.radius
            color: Theme.card; border.color: Theme.border; border.width: Math.max(1, Theme.u * 0.5)
            Column {
                id: np
                x: Theme.pad; y: Theme.pad; width: parent.width - 2 * Theme.pad
                spacing: 12 * Theme.u
                Rectangle {
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: Math.min(parent.width, 220 * Theme.u); height: width; radius: 14 * Theme.u
                    color: Theme.track; clip: true
                    Image {
                        anchors.fill: parent; asynchronous: true; fillMode: Image.PreserveAspectCrop
                        source: page.pic(page.a.entity_picture)
                        sourceSize.width: width; sourceSize.height: height
                        visible: status === Image.Ready
                    }
                    Icon { anchors.centerIn: parent; size: 72; name: "music"; visible: !page.a.entity_picture }
                }
                Column {
                    width: parent.width; spacing: 2 * Theme.u
                    Label { width: parent.width; horizontalAlignment: Text.AlignHCenter; size: 19; font.weight: Font.DemiBold
                            text: page.a.media_title || (page.st ? (page.st.state === "unavailable" ? "Unavailable" : "Nothing playing") : "") }
                    Label { width: parent.width; horizontalAlignment: Text.AlignHCenter; size: 14; color: Theme.textDim
                            text: [page.a.media_artist, page.a.media_album_name].filter(function (x) { return x; }).join(" · ") }
                    Label { width: parent.width; horizontalAlignment: Text.AlignHCenter; size: 12; color: Theme.blue
                            text: page.members.length > 1 ? "Grouped: " + page.members.map(page.nameOf).join(", ") : (page.a.source || "") }
                }
                Rectangle {
                    width: parent.width; height: 4 * Theme.u; radius: height / 2; color: Theme.track
                    visible: !!page.a.media_duration
                    Rectangle { height: parent.height; radius: parent.radius; color: Theme.blue; width: parent.width * page.position() }
                }
                Row {
                    anchors.horizontalCenter: parent.horizontalCenter
                    spacing: 6 * Theme.u
                    Repeater {
                        model: [
                            { i: page.a.shuffle ? "shuffle" : "shuffle-disabled", act: "shuffle", big: false, on: !!page.a.shuffle },
                            { i: "skip-previous", act: "media_previous_track", big: false },
                            { i: page.playing ? "pause-circle" : "play-circle", act: "media_play_pause", big: true },
                            { i: "skip-next", act: "media_next_track", big: false },
                            { i: page.a.repeat === "one" ? "repeat-once" : (page.a.repeat === "all" ? "repeat" : "repeat-off"), act: "repeat", big: false, on: page.a.repeat && page.a.repeat !== "off" }
                        ]
                        Item {
                            width: (modelData.big ? 72 : 52) * Theme.u; height: 72 * Theme.u
                            Icon { anchors.centerIn: parent; size: modelData.big ? 64 : 28; name: modelData.i
                                   color: tb.pressed ? Theme.blue : (modelData.on ? Theme.blue : Theme.text) }
                            MouseArea {
                                id: tb; anchors.fill: parent
                                onClicked: if (!Panel.multi) {
                                    if (modelData.act === "shuffle")
                                        Ha.callService("media_player", "shuffle_set", { shuffle: !page.a.shuffle }, page.entity);
                                    else if (modelData.act === "repeat")
                                        Ha.callService("media_player", "repeat_set",
                                            { repeat: page.a.repeat === "off" || !page.a.repeat ? "all" : (page.a.repeat === "all" ? "one" : "off") }, page.entity);
                                    else Ha.callService("media_player", modelData.act, null, page.entity);
                                }
                            }
                        }
                    }
                }
                Row {
                    width: parent.width; spacing: 10 * Theme.u
                    Item {
                        width: 30 * Theme.u; height: 30 * Theme.u
                        Icon { anchors.centerIn: parent; size: 24; name: page.a.is_volume_muted ? "volume-off" : "volume-high"
                               color: page.a.is_volume_muted ? Theme.red : Theme.text }
                        MouseArea { anchors.fill: parent; onClicked: if (!Panel.multi) Ha.callService("media_player", "volume_mute", { is_volume_muted: !page.a.is_volume_muted }, page.entity) }
                    }
                    Bar {
                        width: parent.width - 40 * Theme.u; fill: Theme.blue
                        value: page.a.volume_level || 0
                        onCommitted: function (v) { Ha.callService("media_player", "volume_set", { volume_level: Math.round(v * 100) / 100 }, page.entity); }
                    }
                }
            }
        }

        // Grouping
        Label { size: 13; color: Theme.textDim; font.weight: Font.DemiBold; text: "PLAY IN THESE ROOMS TOO" }
        Flow {
            width: parent.width; spacing: 8 * Theme.u
            Repeater {
                model: page.speakers
                Rectangle {
                    visible: modelData.entity !== page.entity
                    readonly property bool inGroup: page.members.indexOf(modelData.entity) >= 0
                    height: 40 * Theme.u; width: (parent.width - 8 * Theme.u) / 2; radius: 12 * Theme.u
                    color: inGroup ? Theme.blueSoft : Theme.card; border.color: inGroup ? Theme.blue : Theme.border
                    Row {
                        anchors { left: parent.left; leftMargin: 12 * Theme.u; verticalCenter: parent.verticalCenter }
                        spacing: 8 * Theme.u
                        Icon { size: 20; name: parent.parent.inGroup ? "checkbox-marked-circle" : "checkbox-blank-circle-outline"
                               color: parent.parent.inGroup ? Theme.blue : Theme.textDim; anchors.verticalCenter: parent.verticalCenter }
                        Label { size: 13; text: modelData.name; anchors.verticalCenter: parent.verticalCenter }
                    }
                    MouseArea { anchors.fill: parent; onClicked: if (!Panel.multi) page.toggleMember(modelData.entity) }
                }
            }
        }

        // Favorites (grouped: Stations, Playlists, ...)
        Label { size: 13; color: Theme.textDim; text: page.favError; visible: page.favError !== "" && page.favorites.length === 0; width: parent.width; wrapMode: Text.WordWrap }
        Repeater {
            model: page.favorites
            Column {
              width: body.width; spacing: 8 * Theme.u
              Label { size: 13; color: Theme.textDim; font.weight: Font.DemiBold; text: modelData.title.toUpperCase() }
              Flow {
                width: parent.width; spacing: 8 * Theme.u
                Repeater {
                model: modelData.items
                Rectangle {
                    width: (parent.width - 16 * Theme.u) / 3; height: width + 34 * Theme.u; radius: 12 * Theme.u
                    color: fm.pressed ? Theme.cardPressed : Theme.card; border.color: Theme.border; clip: true
                    Rectangle {
                        id: thumb; x: 6 * Theme.u; y: 6 * Theme.u; width: parent.width - 12 * Theme.u; height: width
                        radius: 8 * Theme.u; color: Theme.track; clip: true
                        Image { anchors.fill: parent; asynchronous: true; fillMode: Image.PreserveAspectCrop
                                source: page.pic(modelData.thumbnail); sourceSize.width: width; sourceSize.height: height
                                visible: status === Image.Ready }
                        Icon { anchors.centerIn: parent; size: 30; name: "star"; visible: !modelData.thumbnail }
                    }
                    Label { anchors { top: thumb.bottom; topMargin: 6 * Theme.u; left: parent.left; right: parent.right; margins: 6 * Theme.u }
                            size: 11; text: modelData.title; horizontalAlignment: Text.AlignHCenter }
                    MouseArea { id: fm; anchors.fill: parent; onClicked: if (!Panel.multi) page.play(modelData) }
                }
                }
              }
            }
        }
    }
}
