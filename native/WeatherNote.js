// One-line weather note from an hourly forecast, worked out locally (no AI):
// "Rain until about 3 PM", "Snow from 1 PM", "Storms from 6 PM", or "" when nothing is coming.
.pragma library

var WET = { "rainy": "Rain", "pouring": "Heavy rain", "lightning-rainy": "Storms", "lightning": "Storms",
            "snowy": "Snow", "snowy-rainy": "Sleet", "hail": "Hail" };

function wetKind(cond, pop) {
    if (WET[cond]) return WET[cond];
    return pop !== undefined && pop >= 60 ? "Rain" : "";
}
function hourText(d) {
    var h = d.getHours(), ap = h < 12 ? "AM" : "PM";
    h = h % 12 || 12;
    return h + " " + ap;
}
// hourly: [{datetime, condition, precipitation_probability}], current: current condition string.
function note(hourly, current, hoursAhead) {
    if (!hourly || !hourly.length) return "";
    var n = Math.min(hourly.length, hoursAhead || 12);
    var nowWet = WET[current] || "";
    if (nowWet) {
        for (var i = 0; i < n; i++) {
            if (!wetKind(hourly[i].condition, hourly[i].precipitation_probability))
                return nowWet + " until about " + hourText(new Date(hourly[i].datetime));
        }
        return nowWet + " for the next " + n + " hours";
    }
    for (var j = 0; j < n; j++) {
        var k = wetKind(hourly[j].condition, hourly[j].precipitation_probability);
        if (k) return j === 0 ? k + " starting soon" : k + " from " + hourText(new Date(hourly[j].datetime));
    }
    return "";
}
