#!/usr/bin/env python3
"""Build docs/index.html from its source template.

The landing page embeds the panel's real stylesheet so the demo *is* the
product's interface rather than a lookalike that drifts. Rather than maintain
two copies, docs/_landing.src.html carries a marker that this script replaces
with the contents of panel/static/hostzilla.css.

Usage:
    python tools/build_landing_page.py           # write docs/index.html
    python tools/build_landing_page.py --check   # fail if it is out of date

CI runs --check, so editing the panel CSS without rebuilding is caught.
"""

import argparse
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "docs" / "_landing.src.html"
CSS = REPO_ROOT / "panel" / "static" / "hostzilla.css"
OUT = REPO_ROOT / "docs" / "index.html"

CSS_MARKER = "/*__PANEL_CSS__*/"

BANNER = (
    "<!--\n"
    "  GENERATED FILE — do not edit docs/index.html directly.\n"
    "  Source:  docs/_landing.src.html\n"
    "  Rebuild: python tools/build_landing_page.py\n"
    "  The panel stylesheet below is embedded verbatim from\n"
    "  panel/static/hostzilla.css so the demo cannot drift from the product.\n"
    "-->\n"
)


def render():
    src = SRC.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    if CSS_MARKER not in src:
        raise SystemExit("{} is missing the {} marker".format(SRC, CSS_MARKER))
    return BANNER + src.replace(CSS_MARKER, css)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    rendered = render()

    if args.check:
        if not OUT.exists():
            print("FAIL: {} does not exist — run tools/build_landing_page.py".format(OUT))
            return 1
        current = OUT.read_text(encoding="utf-8")
        if current != rendered:
            print(
                "FAIL: docs/index.html is out of date with its source or with\n"
                "      panel/static/hostzilla.css.\n"
                "      Run: python tools/build_landing_page.py"
            )
            return 1
        print("OK: docs/index.html is up to date")
        return 0

    OUT.write_text(rendered, encoding="utf-8")
    print("wrote {} ({:.1f} KB)".format(OUT, len(rendered.encode()) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
