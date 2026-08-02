#!/usr/bin/env python3
"""Assert that docs/index.html loads ZERO external subresources.

"Self-contained" means the browser fetches nothing from another host while
rendering the page: no CDN scripts, no web fonts, no remote images, no iframes,
no XHR. Ordinary outbound hyperlinks (<a href> to GitHub, X, LinkedIn) are the
point of a landing page and are explicitly allowed — following one is the
visitor's choice, not a silent request.

Run by CI; fails the build on any violation.
"""

import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = REPO_ROOT / "docs" / "index.html"

REMOTE_PREFIXES = ("http://", "https://", "//", "ftp://", "ws://", "wss://")

# Attributes whose value the browser fetches automatically.
LOADING_ATTRS = re.compile(
    r"""\b(src|srcset|poster|formaction|action)\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
# href only pulls a subresource on <link>; on <a> it is a plain hyperlink.
LINK_TAG = re.compile(r"""<link\b[^>]*>""", re.IGNORECASE)
HREF_VALUE = re.compile(r"""\bhref\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
REL_VALUE = re.compile(r"""\brel\s*=\s*["']([^"']+)["']""", re.IGNORECASE)

# <link> rels that cause a fetch. Others (canonical, alternate, me, author) are
# pure metadata and are allowed to point anywhere.
FETCHING_RELS = {
    "stylesheet",
    "preload",
    "prefetch",
    "preconnect",
    "dns-prefetch",
    "icon",
    "shortcut icon",
    "apple-touch-icon",
    "manifest",
    "modulepreload",
    "prerender",
}

CSS_URL = re.compile(r"""url\(\s*["']?([^"')]+)["']?\s*\)""", re.IGNORECASE)
IMPORT_RULE = re.compile(r"""@import\s+["']([^"']+)["']""", re.IGNORECASE)

FORBIDDEN_SUBSTRINGS = [
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdn.jsdelivr.net",
    "cdnjs.cloudflare.com",
    "unpkg.com",
    "googletagmanager.com",
    "google-analytics.com",
    "<iframe",
    "integrity=",  # only meaningful for remote subresources
]

REQUIRED = {
    "<title>": "a <title> element",
    'name="description"': "a meta description",
    'property="og:title"': "an Open Graph title",
    'property="og:description"': "an Open Graph description",
    "application/ld+json": "JSON-LD structured data",
    "softwareapplication": "a SoftwareApplication schema",
    'lang="en"': "a language attribute",
    "prefers-color-scheme": "a dark/light colour scheme",
    "viewport": "a responsive viewport meta tag",
}

REQUIRED_CONTACTS = {
    "github.com/iampopye": "the GitHub profile link",
    "x.com/mrtechgarg": "the X profile link",
    "linkedin.com/in/karan-garg-tech": "the LinkedIn profile link",
    "kgupta0183@gmail.com": "the contact email",
}

# The demo must never read as a live backend.
REQUIRED_HONESTY = [
    "runs entirely in your browser",
    "no server is contacted",
    "simulated",
]


def is_remote(reference):
    ref = reference.strip()
    if not ref or ref.startswith(("#", "data:", "mailto:", "tel:")):
        return False
    return ref.lower().startswith(REMOTE_PREFIXES)


def main():
    if not PAGE.exists():
        print("FAIL: {} does not exist".format(PAGE))
        return 1

    html = PAGE.read_text(encoding="utf-8")
    lowered = html.lower()
    failures = []

    for match in LOADING_ATTRS.finditer(html):
        attr, reference = match.group(1), match.group(2)
        if is_remote(reference):
            failures.append(
                "external {}= subresource: {}".format(attr, reference[:100])
            )

    for tag in LINK_TAG.finditer(html):
        rel = REL_VALUE.search(tag.group(0))
        rels = set(rel.group(1).lower().split()) if rel else set()
        if not (rels & FETCHING_RELS):
            continue  # canonical, alternate, me — metadata, never fetched
        href = HREF_VALUE.search(tag.group(0))
        if href and is_remote(href.group(1)):
            failures.append("external <link> href: {}".format(href.group(1)[:100]))

    for pattern, label in ((CSS_URL, "css url()"), (IMPORT_RULE, "@import")):
        for match in pattern.finditer(html):
            if is_remote(match.group(1)):
                failures.append("external {}: {}".format(label, match.group(1)[:100]))

    for needle in FORBIDDEN_SUBSTRINGS:
        if needle.lower() in lowered:
            failures.append("forbidden content: {}".format(needle))

    for needle, description in REQUIRED.items():
        if needle.lower() not in lowered:
            failures.append("missing {}".format(description))

    for needle, description in REQUIRED_CONTACTS.items():
        if needle.lower() not in lowered:
            failures.append("missing {}".format(description))

    for needle in REQUIRED_HONESTY:
        if needle.lower() not in lowered:
            failures.append(
                "the demo must be labelled honestly — missing {!r}".format(needle)
            )

    if failures:
        print("docs/index.html is NOT self-contained / complete:")
        for failure in failures:
            print("  - {}".format(failure))
        return 1

    print(
        "OK: docs/index.html is self-contained ({:.1f} KB, no external "
        "subresources)".format(len(html.encode("utf-8")) / 1024)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
