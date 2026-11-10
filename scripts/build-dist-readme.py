#!/usr/bin/env python3
"""Build the README of the distribution repo (github.com/MikeI-Code/ha-brilliant-kiosk).

That repo's page is where Home Assistant's "Documentation" link and the repair notices land, so
it carries the full integration documentation: docs/home-assistant-integration.md, with the
panel screenshots on top and every relative link rewritten to an absolute GitHub URL on BRANCH
(main by default; pass nightly to preview unreleased docs).

Usage: scripts/build-dist-readme.py OUTPUT [BRANCH]
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/MikeI-Code/brilliant-ha-kiosk"
IMAGES = "https://raw.githubusercontent.com/MikeI-Code/brilliant-ha-kiosk/main/docs/images/"

SHOTS = [
    [("home.png", "Home tab: clock, weather, forecast, scenes and lights"),
     ("lights.png", "Lights tab"),
     ("climate.png", "Climate tab with thermostats"),
     ("sonos.png", "Music tab with transport, volume and grouping"),
     ("house.png", "House tab with presence, appliances and temperatures")],
    [("security.png", "Security tab: Ring alarm, Schlage lock, doorbells and cameras"),
     ("capture.png", "Capture tab: voice clip, snapshot and video clip"),
     ("saver.jpg", "Screensaver with clock and local weather"),
     ("dim.png", "Two-finger dimming with a live percentage readout")],
]


def absolute(link: str, branch: str) -> str:
    """A link relative to docs/ as an absolute GitHub URL (anchors and URLs are kept)."""
    if link.startswith(("http://", "https://", "mailto:", "#")):
        return link
    path, _, anchor = link.partition("#")
    target = (ROOT / "docs" / path).resolve().relative_to(ROOT).as_posix()
    kind = "tree" if (ROOT / target).is_dir() else "blob"
    return f"{REPO}/{kind}/{branch}/{target}" + (f"#{anchor}" if anchor else "")


def build(branch: str) -> str:
    doc = (ROOT / "docs" / "home-assistant-integration.md").read_text(encoding="utf-8")
    doc = re.sub(r"\]\(([^)\s]+)\)", lambda m: f"]({absolute(m.group(1), branch)})", doc)
    title, _, body = doc.partition("\n")
    rows = "\n".join(
        '<p align="center">\n'
        + "\n".join(f'  <img src="{IMAGES}{f}" width="19%" alt="{alt}">' for f, alt in row)
        + "\n</p>"
        for row in SHOTS
    )
    intro = (
        f"{title}\n\n{rows}\n"
        '<p align="center"><sub>The kiosk this integration manages, on Brilliant panels. Second '
        "row: Security (camera pictures blanked), Capture, the screensaver, and two-finger "
        "dimming.</sub></p>\n\n"
        f"> This repository distributes the integration for Home Assistant's Marketplace and HACS. "
        f"Its code, tests and issues live in [brilliant-ha-kiosk]({REPO}); the kiosk itself is "
        f"documented in the [manual]({REPO}/blob/{branch}/docs/MANUAL.md). If it is useful to "
        f"you, please star [the main repository]({REPO}).\n"
    )
    return intro + body


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    branch = sys.argv[2] if len(sys.argv) > 2 else "main"
    Path(sys.argv[1]).write_text(build(branch), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
