#!/usr/bin/env python3
"""Re-stamp the shared asset URLs with their content hash.

The dashboard's JavaScript and its JSON are fetched on different cache
schedules: a browser revalidates index.html often (it changes often) but keeps
assets/charts.js for days under heuristic freshness, because that file's
Last-Modified is old. A reader then runs new HTML and new data against a
month-old charts.js, and any section whose code is newer than that copy renders
as a blank frame. Naming the file by its content makes a changed file a changed
URL, so this cannot happen.

Run after editing anything in assets/, before committing:

    python3 stamp_assets.py
"""
import hashlib
import pathlib
import re
import sys

ASSETS = ("charts.js", "echarts.min.js")
PAGES = ("index.html", "data.html", "embed.html")


def main() -> int:
    root = pathlib.Path(__file__).parent
    stamps = {
        name: hashlib.sha1((root / "assets" / name).read_bytes()).hexdigest()[:8]
        for name in ASSETS
    }
    changed = []
    for page in PAGES:
        path = root / page
        text = original = path.read_text()
        for name, digest in stamps.items():
            # The optional group makes re-stamping idempotent.
            text = re.sub(
                rf'assets/{re.escape(name)}(\?v=[0-9a-f]+)?"',
                f'assets/{name}?v={digest}"',
                text,
            )
        if text != original:
            path.write_text(text)
            changed.append(page)
    for name, digest in stamps.items():
        print(f"{name}: {digest}")
    print("updated: " + (", ".join(changed) if changed else "nothing (already current)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
