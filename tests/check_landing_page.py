#!/usr/bin/env python3
"""Assert that docs/index.html makes ZERO external requests.

The landing page is published from a repository that must stay auditable and
must not leak visitor data to third parties, so it embeds every asset. This
script is run by CI and fails the build if anything points off-host.
"""

import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = REPO_ROOT / "docs" / "index.html"

# Attributes that cause the browser to fetch something.
FETCHING_ATTRS = re.compile(
    r"""\b(?:src|href|srcset|poster|data|action|formaction)\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
CSS_URL = re.compile(r"""url\(\s*["']?([^"')]+)["']?\s*\)""", re.IGNORECASE)
IMPORT_RULE = re.compile(r"""@import\s+["']([^"']+)["']""", re.IGNORECASE)

# Anything that is unambiguously a remote fetch.
REMOTE_PREFIXES = ("http://", "https://", "//", "ftp://", "ws://", "wss://")

# Substrings that must never appear in a self-contained page.
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


def is_local(reference: str) -> bool:
    """True when a reference resolves without leaving the page."""
    ref = reference.strip()
    if not ref:
        return True
    # In-page anchors, inline data and mail/telephone links are all fine.
    if ref.startswith(("#", "data:", "mailto:", "tel:")):
        return True
    return not ref.lower().startswith(REMOTE_PREFIXES)


def main() -> int:
    if not PAGE.exists():
        print("FAIL: {} does not exist".format(PAGE))
        return 1

    html = PAGE.read_text(encoding="utf-8")
    failures = []

    for pattern, label in (
        (FETCHING_ATTRS, "attribute"),
        (CSS_URL, "css url()"),
        (IMPORT_RULE, "@import"),
    ):
        for match in pattern.finditer(html):
            reference = match.group(1)
            if not is_local(reference):
                failures.append(
                    "external {} reference: {}".format(label, reference[:120])
                )

    lowered = html.lower()
    for needle in FORBIDDEN_SUBSTRINGS:
        if needle.lower() in lowered:
            failures.append("forbidden content: {}".format(needle))

    # The demo must be labelled honestly.
    if "simulated" not in lowered:
        failures.append(
            "the embedded demo must be labelled as simulated (no 'simulated' text found)"
        )

    # Basic SEO / accessibility requirements for a launch page.
    required = {
        "<title>": "a <title> element",
        'name="description"': "a meta description",
        'property="og:title"': "an Open Graph title",
        'property="og:description"': "an Open Graph description",
        "application/ld+json": "JSON-LD structured data",
        "SoftwareApplication": "a SoftwareApplication schema",
        'lang="en"': "a language attribute",
        "prefers-color-scheme": "a dark/light colour scheme",
        "viewport": "a responsive viewport meta tag",
    }
    for needle, description in required.items():
        if needle.lower() not in lowered:
            failures.append("missing {}".format(description))

    # Maintainer contact routes the owner asked to be present.
    for needle, description in {
        "github.com/iampopye": "the GitHub profile link",
        "x.com/mrtechgarg": "the X profile link",
        "linkedin.com/in/karan-garg-tech": "the LinkedIn profile link",
        "kgupta0183@gmail.com": "the contact email",
    }.items():
        if needle.lower() not in lowered:
            failures.append("missing {}".format(description))

    if failures:
        print("docs/index.html is NOT self-contained / complete:")
        for failure in failures:
            print("  - {}".format(failure))
        return 1

    size_kb = len(html.encode("utf-8")) / 1024
    print("OK: docs/index.html is self-contained ({:.1f} KB)".format(size_kb))
    return 0


if __name__ == "__main__":
    sys.exit(main())
