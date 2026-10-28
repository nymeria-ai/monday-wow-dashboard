#!/usr/bin/env python3
"""
Sync data constants from index.html into dashboard copies.

Copies only the single-line data constants (DATA, DATA_MARKET, GEO_DATA_*,
BING_DATA). Everything else in a copy (layout, metrics, filters, JS) is left
alone, so a copy can be customised and still get fresh data on every refresh.

Called automatically at the end of refresh.py and bing_refresh.py.
To add a copy: append its filename to COPIES.
"""
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
SOURCE = SCRIPT_DIR / "index.html"
COPIES = [
    "sem-wow-v2.html",  # Alex Abramovich, Oct 28 2026
]
CONSTS = ["DATA", "DATA_MARKET", "GEO_DATA_BRAND", "GEO_DATA_NONBRAND", "BING_DATA"]


def get_lines(text):
    out = {}
    for name in CONSTS:
        m = re.search(rf"^const {name} = .*;$", text, flags=re.M)
        if m:
            out[name] = m.group(0)
    return out


def sync():
    src = get_lines(SOURCE.read_text())
    changed = []
    for fn in COPIES:
        p = SCRIPT_DIR / fn
        if not p.exists():
            print(f"WARN: copy {fn} missing, skipped", file=sys.stderr)
            continue
        text = p.read_text()
        new = text
        for name, line in src.items():
            new, n = re.subn(rf"^const {name} = .*;$", lambda _: line, new, count=1, flags=re.M)
            if n == 0:
                print(f"WARN: {fn} has no const {name}, skipped that constant", file=sys.stderr)
        if new != text:
            p.write_text(new)
            changed.append(fn)
    print(f"sync_copies: updated {changed or 'nothing'}")
    return [str(SCRIPT_DIR / c) for c in COPIES if (SCRIPT_DIR / c).exists()]


if __name__ == "__main__":
    sync()
