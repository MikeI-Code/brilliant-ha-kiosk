#!/usr/bin/env python3
"""Generate native/icons.json: name -> codepoint for every Material Design Icon, so any icon
picked in Home Assistant's icon picker (mdi:...) renders on the panel.

Usage: python3 tools/gen_icons.py path/to/materialdesignicons.css
  (css from https://cdn.jsdelivr.net/npm/@mdi/font@7.4.47/css/materialdesignicons.css; the matching
   font is native/fonts/materialdesignicons-webfont.ttf — keep the versions in step)
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

css = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
codes = {m.group(1): int(m.group(2), 16)
         for m in re.finditer(r'\.mdi-([a-z0-9-]+)::before\s*\{\s*content:\s*"\\([0-9A-Fa-f]+)"', css)}
out = ROOT / "native" / "icons.json"
out.write_text(json.dumps(codes, separators=(",", ":"), sort_keys=True), encoding="utf-8", newline="\n")
print(f"{len(codes)} icons -> {out.relative_to(ROOT)}")
