#!/usr/bin/env bash
# Hostzilla privileged runner — shared library.
# Every verb sources this. It centralizes validation, the protected-domain
# guard, structured logging, and the single-line JSON result the job runner parses.
set -euo pipefail

HZ_ROOT=/opt/hostzilla
HZ_LOG=/var/log/hostzilla/runner.log
HZ_SITES=/etc/hostzilla/sites          # per-site creds/manifest (root:root 600)
HZ_CONF=/etc/hostzilla/hostzilla.conf  # shell-sourceable panel/runner config
WWW=/var/www

# Pull operator-supplied config (PANEL_DOMAIN, ADMIN_EMAIL, PROTECTED_DOMAINS, ...).
# Sourced early so every verb sees the same values. Absent on first boot — that's fine.
if [[ -r "$HZ_CONF" ]]; then
  # shellcheck disable=SC1090
  source "$HZ_CONF"
fi
: "${PANEL_DOMAIN:=}"
: "${ADMIN_EMAIL:=admin@example.com}"
: "${PROTECTED_DOMAINS:=}"
PHPVER="$(ls -d /etc/php/*/fpm 2>/dev/null | sed -E 's#.*/php/([0-9.]+)/fpm#\1#' | sort -V | tail -1)"
PHP_FPM_SVC="php${PHPVER}-fpm"

log() { printf '%s [%s] %s\n' "$(date -u +%FT%TZ)" "${HZ_VERB:-runner}" "$*" >> "$HZ_LOG"; }

# Single-line JSON result on stdout — the LAST such line is what jobs.py parses.
emit() { # emit <status> <key=val>...
  local status="$1"; shift
  local json="{\"status\":\"$status\""
  for kv in "$@"; do json+=",\"${kv%%=*}\":\"${kv#*=}\""; done
  json+="}"
  echo "$json"
}
die() { log "FAIL: $*"; emit error "message=$*"; exit 1; }

# Domains we must NEVER create/delete/touch. Config-driven (no hardcoded hosts):
#   - empty / 'localhost' / 'html'  (system + default-vhost names)
#   - $PANEL_DOMAIN                  (the panel's own hostname, from hostzilla.conf)
#   - any token in $PROTECTED_DOMAINS (space-separated operator allowlist of off-limits hosts)
# Defense in depth: site_create ALSO refuses if /var/www/<domain> already exists
# (no clobber), and site_delete only ever removes sites with a manifest it wrote.
is_protected() {
  local d="${1:-}"
  d="$(printf '%s' "$d" | tr '[:upper:]' '[:lower:]')"
  # built-in / system names always protected
  case "$d" in
    ''|localhost|html) return 0 ;;
  esac
  # the panel's own host (if configured)
  if [[ -n "${PANEL_DOMAIN:-}" ]]; then
    [[ "$d" == "$(printf '%s' "$PANEL_DOMAIN" | tr '[:upper:]' '[:lower:]')" ]] && return 0
  fi
  # operator-supplied extra protected domains (space-separated)
  local p
  for p in ${PROTECTED_DOMAINS:-}; do
    [[ "$d" == "$(printf '%s' "$p" | tr '[:upper:]' '[:lower:]')" ]] && return 0
  done
  return 1
}

CLEAN_DOMAIN=""
validate_domain() { # sets global CLEAN_DOMAIN, or dies (NOT used in $() so die's JSON surfaces)
  local d="${1:-}"
  d="$(printf '%s' "$d" | tr '[:upper:]' '[:lower:]')"
  [[ -n "$d" ]] || die "empty domain"
  [[ ${#d} -le 253 ]] || die "domain too long"
  [[ "$d" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]] || die "invalid domain: $d"
  [[ "$d" != *..* ]] || die "invalid domain (dots): $d"
  CLEAN_DOMAIN="$d"
}

# sanitize a domain into a safe mysql identifier fragment
db_slug() { printf 'hz_%s' "$(printf '%s' "$1" | tr -c 'a-z0-9' '_' | cut -c1-40)"; }

require_root() { [[ "$(id -u)" -eq 0 ]] || die "must run as root"; }
