#!/usr/bin/env python3
"""Create/update the 'wall-panel' dashboard in Home Assistant from .private/wall-panel.yaml (or the path given).

Usage:  HA_URL=http://192.168.1.10:8123 HA_TOKEN=<long-lived token> python3 scripts/publish_dashboard.py [dashboard.yaml]
Needs:  pip install pyyaml websockets
"""
import asyncio
import json
import os
import pathlib
import sys

import websockets
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
URL_PATH = "wall-panel"


async def main():
    base = os.environ["HA_URL"].rstrip("/")
    token = os.environ["HA_TOKEN"]
    path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".private/wall-panel.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    ws_url = base.replace("http", "ws", 1) + "/api/websocket"
    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": token}))
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
            r = await call({"type": "lovelace/dashboards/create", "url_path": URL_PATH, "title": "Wall Panel",
                            "icon": "mdi:tablet-dashboard", "show_in_sidebar": True,
                            "require_admin": False, "mode": "storage"})
            print("create dashboard:", r["success"], r.get("error", ""))
        r = await call({"type": "lovelace/config/save", "url_path": URL_PATH, "config": config})
        print("save config:", r["success"], r.get("error", ""))
        themes = (await call({"type": "frontend/get_themes"}))["result"]["themes"]
        print("Panel Dark theme installed in HA:", "Panel Dark" in themes)


asyncio.run(main())
