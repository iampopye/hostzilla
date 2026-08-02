"""Configuration loader for the Hostzilla panel.

Reads the shell-sourceable file /etc/hostzilla/hostzilla.conf (KEY="value"
lines) so the panel and the installer/runner share one source of truth.
Falls back to environment variables and sane dev defaults so the panel also
runs from a checkout without the installer.
"""

import os
import re

CONF_PATH = os.environ.get("HZ_CONF_PATH", "/etc/hostzilla/hostzilla.conf")

_DEFAULTS = {
    "PANEL_DOMAIN": "",
    "ADMIN_EMAIL": "admin@example.com",
    "PROTECTED_DOMAINS": "",
    "DB_PATH": "",
    "PANEL_PORT": "2087",
}

_KV_RE = re.compile(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$')

_cache = None


def _strip_value(raw):
    """Strip inline comments and surrounding quotes from a shell value."""
    raw = raw.strip()
    if raw and raw[0] in ("'", '"'):
        quote = raw[0]
        end = raw.find(quote, 1)
        if end != -1:
            return raw[1:end]
        return raw[1:]
    # unquoted: cut at first unescaped comment marker
    hash_pos = raw.find("#")
    if hash_pos != -1:
        raw = raw[:hash_pos]
    return raw.strip()


def _parse_conf_file(path):
    values = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                m = _KV_RE.match(line)
                if not m:
                    continue
                key, raw = m.group(1), m.group(2)
                values[key] = _strip_value(raw)
    except OSError:
        pass
    return values


def load_config(refresh=False):
    """Build the merged config dict: defaults < file < environment."""
    global _cache
    if _cache is not None and not refresh:
        return _cache

    cfg = dict(_DEFAULTS)
    cfg.update(_parse_conf_file(CONF_PATH))

    for key in _DEFAULTS:
        if os.environ.get(key):
            cfg[key] = os.environ[key]

    _cache = cfg
    return cfg


def get_config(refresh=False):
    return load_config(refresh=refresh)


def get(key, default=None):
    return load_config().get(key, default)
