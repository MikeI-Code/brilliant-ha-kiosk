#!/usr/bin/env python3
"""Turn a native layout.json into an HA dashboard ("Panel Layout") that you then edit in HA's UI.

The panels read that dashboard (LAYOUT_DASHBOARD=panel-layout in kiosk.env) and rebuild their
screens whenever it is saved. Run this once to start from your current layout; afterwards edit in HA.
It will not overwrite a dashboard that already has views unless you pass --force.

Usage:  HA_URL=http://192.168.1.10:8123 HA_TOKEN=<long-lived token> \
          python3 scripts/publish_layout.py [layout.json] [--force]
Needs:  pip install websockets
"""
import asyncio
import json
import os
import pathlib
import re
import sys

import websockets

ROOT = pathlib.Path(__file__).resolve().parent.parent
URL_PATH = "panel-layout"
COLUMNS = {"full": 12, "half": 6, "third": 4, "quarter": 3}
DEFAULT_SIZE = {"scene": "quarter", "alloff": "quarter", "sensor": "third", "media": "full"}


def mdi(icon):
    return f"mdi:{icon}" if icon else None


def card_for(item):
    kind = item.get("type")
    size = item.get("size") or DEFAULT_SIZE.get(kind, "half")
    card = {"grid_options": {"columns": COLUMNS.get(size, 6)}}
    if kind == "alloff":
        card.update(type="button", name=item.get("name") or "All off", icon=mdi(item.get("icon")),
                    tap_action={"action": "perform-action", "perform_action": "light.turn_off",
                                "target": {"entity_id": "all"}})
    elif kind == "media" and not item.get("onlyPlaying"):
        card.update(type="media-control", entity=item["entity"])
    else:
        card.update(type="tile", entity=item["entity"], name=item.get("name") or None, icon=mdi(item.get("icon")))
    return {k: v for k, v in card.items() if v is not None}


def to_lovelace(layout):
    views = []
    for page in layout.get("pages", []):
        view = {"title": page.get("title", "Page"), "icon": mdi(page.get("icon")), "type": "sections",
                "path": re.sub(r"[^a-z0-9]+", "-", page.get("title", "page").lower()).strip("-") or "page"}
        if page.get("type") == "sonos":
            view["path"] = "sonos"
            view["sections"] = [{"type": "grid", "cards": [{"type": "heading", "heading": "Speakers"}] + [
                {"type": "tile", "entity": s["entity"], "name": s.get("name")} for s in page.get("speakers", [])]}]
        else:
            view["sections"] = []
            for sec in page.get("sections", []):
                if sec.get("type") == "hero":
                    cards = [{"type": "weather-forecast", "entity": layout.get("weather"), "forecast_type": "daily",
                              "grid_options": {"columns": 12}}]
                    if layout.get("insideTemp"):
                        cards.append({"type": "tile", "entity": layout["insideTemp"], "name": "Inside"})
                else:
                    cards = [{"type": "heading", "heading": sec["title"]}] if sec.get("title") else []
                    cards += [card_for(i) for i in sec.get("items", [])]
                view["sections"].append({"type": "grid", "cards": cards})
        views.append({k: v for k, v in view.items() if v is not None})
    return {"title": "Panel Layout", "views": views}


async def main():
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv
    path = pathlib.Path(args[0]) if args else ROOT / ".private/layout.json"
    config = to_lovelace(json.loads(path.read_text(encoding="utf-8")))
    base = os.environ["HA_URL"].rstrip("/")
    async with websockets.connect(base.replace("http", "ws", 1) + "/api/websocket", max_size=None) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": os.environ["HA_TOKEN"]}))
        if json.loads(await ws.recv())["type"] != "auth_ok":
            sys.exit("auth failed")
        msg_id = 0

        async def call(msg):
            nonlocal msg_id
            msg_id += 1
            await ws.send(json.dumps({"id": msg_id, **msg}))
            while True:
                reply = json.loads(await ws.recv())
                if reply.get("id") == msg_id:
                    return reply

        dashboards = (await call({"type": "lovelace/dashboards/list"}))["result"]
        if not any(d["url_path"] == URL_PATH for d in dashboards):
            r = await call({"type": "lovelace/dashboards/create", "url_path": URL_PATH, "title": "Panel Layout",
                            "icon": "mdi:tablet-dashboard", "show_in_sidebar": True,
                            "require_admin": True, "mode": "storage"})
            print("create dashboard:", r["success"], r.get("error", ""))
        else:
            cur = await call({"type": "lovelace/config", "url_path": URL_PATH})
            if cur.get("success") and cur["result"].get("views") and not force:
                sys.exit(f"'{URL_PATH}' already has views (edited in HA?). Re-run with --force to overwrite.")
        r = await call({"type": "lovelace/config/save", "url_path": URL_PATH, "config": config})
        print("save config:", r["success"], r.get("error", ""), f"-> {base}/{URL_PATH}")


asyncio.run(main())
