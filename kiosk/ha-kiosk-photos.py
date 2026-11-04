"""Screensaver photos from Home Assistant's media library (loaded by ha-kiosk-bridge).

Upload photos in Home Assistant (Media -> My media, from the app or browser), optionally into a
folder named by HA_PHOTO_FOLDER, and every panel mirrors them into /var/lib/ha-kiosk/ha-photos
every PHOTO_SYNC_MINS minutes. Photos removed in HA are removed from the panels too.

Uses HA's websocket API (media_source/browse_media + resolve_media) with the panel's token, and
aiohttp from Brilliant's Python. Subfolders are included.
"""
import asyncio
import os
import re
import threading
import time

import aiohttp

DEST = "/var/lib/ha-kiosk/ha-photos"
IMAGE_EXT = (".jpg", ".jpeg", ".png")


def _safe(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "photo"


class PhotoSync:
    def __init__(self, env):
        m = re.match(r"(https?://[^/\s]+)", env.get("HA_URL", ""))
        self.base = m.group(1) if m else ""
        self.token = open("/etc/ha-kiosk/token", encoding="utf-8").read().strip()
        folder = env.get("HA_PHOTO_FOLDER", "").strip().strip("/")
        self.root = "media-source://media_source/local/" + (folder if folder else ".")
        self.every = max(5, int(env.get("PHOTO_SYNC_MINS", "30"))) * 60
        self.last_note = ""

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        time.sleep(20)                       # let the panel finish booting first
        while True:
            try:
                note = asyncio.run(self.sync())
            except Exception as e:
                note = f"photos: sync failed: {e}"
            if note != self.last_note:      # log changes only
                print(note, flush=True)
                self.last_note = note
            time.sleep(self.every)

    async def sync(self):
        os.makedirs(DEST, exist_ok=True)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as http:
            async with http.ws_connect(self.base.replace("http", "ws", 1) + "/api/websocket", max_msg_size=0) as ws:
                await ws.receive_json()
                await ws.send_json({"type": "auth", "access_token": self.token})
                if (await ws.receive_json()).get("type") != "auth_ok":
                    return "photos: HA auth failed"
                seq = 0

                async def call(msg):
                    nonlocal seq
                    seq += 1
                    await ws.send_json({"id": seq, **msg})
                    while True:
                        r = await ws.receive_json()
                        if r.get("id") == seq:
                            return r

                # Walk the folder (and subfolders) for images.
                images, todo = {}, [self.root]
                while todo:
                    r = await call({"type": "media_source/browse_media", "media_content_id": todo.pop()})
                    if not r.get("success"):
                        return f"photos: no HA media folder {self.root} ({r.get('error', {}).get('message', '')})"
                    for c in r["result"].get("children") or []:
                        cid = c["media_content_id"]
                        if c.get("can_expand"):
                            todo.append(cid)
                        elif c.get("media_class") == "image" or cid.lower().endswith(IMAGE_EXT):
                            rel = cid.split("/local/", 1)[-1]
                            images[_safe(rel)] = cid

                have = {f for f in os.listdir(DEST) if not f.endswith(".part")}
                added = 0
                for name, cid in images.items():
                    if name in have:
                        continue
                    r = await call({"type": "media_source/resolve_media", "media_content_id": cid, "expires": 600})
                    if not r.get("success"):
                        continue
                    async with http.get(self.base + r["result"]["url"]) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.read()
                    with open(os.path.join(DEST, name + ".part"), "wb") as f:
                        f.write(data)
                    os.replace(os.path.join(DEST, name + ".part"), os.path.join(DEST, name))
                    added += 1
                removed = 0
                for name in have - set(images):
                    os.remove(os.path.join(DEST, name))
                    removed += 1
                return f"photos: {len(images)} from HA ({self.root.split('/local/')[-1]}), +{added} -{removed}"
