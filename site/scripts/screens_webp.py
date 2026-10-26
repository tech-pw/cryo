#!/usr/bin/env python3
"""Write a WebP copy of every docs screenshot for the site to serve.

The PNGs in site/assets/img/screens stay the source (README.md embeds them and
web/e2e/docs-capture writes them). The site serves the WebP copies and the Pages
deploy leaves the PNGs out. Run after re-capturing screenshots:

    python3 site/scripts/screens_webp.py          # needs Pillow
    python3 site/scripts/screens_webp.py --check  # exit 1 if a WebP is missing or older than its PNG
"""

from __future__ import annotations

import sys
from pathlib import Path

SCREENS = Path(__file__).resolve().parent.parent / "assets" / "img" / "screens"
QUALITY = 75


def stale() -> list[Path]:
    out = []
    for png in sorted(SCREENS.glob("*.png")):
        webp = png.with_suffix(".webp")
        if not webp.exists() or webp.stat().st_mtime < png.stat().st_mtime:
            out.append(png)
    return out


def main() -> int:
    if "--check" in sys.argv:
        missing = [p for p in sorted(SCREENS.glob("*.png")) if not p.with_suffix(".webp").exists()]
        for p in missing:
            print(f"missing WebP for {p.name}", file=sys.stderr)
        return 1 if missing else 0
    from PIL import Image

    for png in stale():
        with Image.open(png) as im:
            im.save(png.with_suffix(".webp"), "WEBP", quality=QUALITY, method=4)
        print(f"wrote {png.with_suffix('.webp').name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
