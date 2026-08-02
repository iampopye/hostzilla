#!/usr/bin/env bash
# Hostzilla privileged runner — shared library.
# Every verb sources this. It centralizes validation, the protected-domain
# guard, structured logging, and the single-line JSON result the job runner parses.
set -euo pipefail

# ---------------------------------------------------------------------------
# Paths.
#
# HZ_TEST_ROOT re-roots every path so the pure helpers below can be exercised by
# the test suite without touching the real system. It is deliberately refused
# when we are running as root: the verbs are reachable through sudo, and sudo's
# default env_reset already strips HZ_*, but an explicit refusal means a future
# env_keep mistake can never turn this into a privilege-escalation primitive.
# ---------------------------------------------------------------------------
if [[ -n "${HZ_TEST_ROOT:-}" ]]; then
  if [[ "$(id -u)" -eq 0 ]]; then
    echo '{"status":"error","message":"HZ_TEST_ROOT refused while running as root"}' >&2
    exit 1
  fi
  HZ_ROOT="$HZ_TEST_ROOT/opt/hostzilla"
  HZ_LOG="$HZ_TEST_ROOT/var/log/hostzilla/runner.log"
  HZ_SITES="$HZ_TEST_ROOT/etc/hostzilla/sites"
  HZ_CONF="$HZ_TEST_ROOT/etc/hostzilla/hostzilla.conf"
  WWW="$HZ_TEST_ROOT/var/www"
  mkdir -p "$(dirname "$HZ_LOG")" "$HZ_SITES" "$WWW"
else
  # shellcheck disable=SC2034  # consumed by the verbs that source this library
  HZ_ROOT=/opt/hostzilla
  HZ_LOG=/var/log/hostzilla/runner.log
  HZ_SITES=/etc/hostzilla/sites          # per-site creds/manifest (root:root 600)
  HZ_CONF=/etc/hostzilla/hostzilla.conf  # shell-sourceable panel/runner config
  WWW=/var/www
fi

# Pull operator-supplied config (PANEL_DOMAIN, ADMIN_EMAIL, PROTECTED_DOMAINS, ...).
# Sourced early so every verb sees the same values. Absent on first boot — that's fine.
if [[ -r "$HZ_CONF" ]]; then
  # shellcheck disable=SC1090
  source "$HZ_CONF"
fi
: "${PANEL_DOMAIN:=}"
: "${ADMIN_EMAIL:=admin@example.com}"
: "${PROTECTED_DOMAINS:=}"

log() { printf '%s [%s] %s\n' "$(date -u +%FT%TZ)" "${HZ_VERB:-runner}" "$*" >> "$HZ_LOG"; }

# ---------------------------------------------------------------------------
# JSON output
#
# Every value that reaches stdout is escaped. Without this, a value containing a
# double quote lets the caller inject arbitrary JSON keys — and because Python's
# json.loads keeps the LAST occurrence of a duplicate key, an injected
# `","status":"ok` would forge a success result for a failed operation.
# ---------------------------------------------------------------------------
json_escape() {
  local s="${1:-}"
  s="${s//\\/\\\\}"   # backslash first
  s="${s//\"/\\\"}"   # double-quote
  s="${s//$'\t'/\\t}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  # strip any remaining C0 control characters, which are illegal raw in JSON
  s="$(printf '%s' "$s" | tr -d '\000-\010\013\014\016-\037')"
  printf '%s' "$s"
}

# Single-line JSON result on stdout — the LAST such line is what jobs.py parses.
emit() { # emit <status> <key=val>...
  local status="$1"; shift
  local json kv
  json="{\"status\":\"$(json_escape "$status")\""
  for kv in "$@"; do
    json+=",\"$(json_escape "${kv%%=*}")\":\"$(json_escape "${kv#*=}")\""
  done
  json+="}"
  printf '%s\n' "$json"
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

# ---------------------------------------------------------------------------
# Domain validation.
#
# This is the security boundary for every path and identifier the verbs build:
# docroot ($WWW/<domain>), vhost filename, FPM pool filename and socket, and the
# manifest filename. It must therefore reject anything that could escape a
# directory or carry shell/JSON metacharacters.
#
# Rules (deliberately stricter than RFC 1035 — this is an allowlist):
#   * lowercased, 1..253 characters total
#   * at least two labels (a bare "etc" or "passwd" is never a customer site)
#   * each label 1..63 chars, [a-z0-9-], not starting or ending with '-'
#   * no empty labels, therefore no "..", no leading/trailing dot
#   * final label must not be all-digits (that would be an IP-ish literal)
# ---------------------------------------------------------------------------
CLEAN_DOMAIN=""
validate_domain() { # sets global CLEAN_DOMAIN, or dies (NOT used in $() so die's JSON surfaces)
  local d="${1:-}"
  d="$(printf '%s' "$d" | tr '[:upper:]' '[:lower:]')"
  [[ -n "$d" ]] || die "empty domain"
  [[ ${#d} -le 253 ]] || die "domain too long"
  # No shell/path metacharacters, no whitespace, no control characters at all.
  [[ "$d" =~ ^[a-z0-9.-]+$ ]] || die "invalid domain: $d"
  [[ "$d" != *..* ]] || die "invalid domain (empty label): $d"
  [[ "$d" != .* && "$d" != *. ]] || die "invalid domain (leading/trailing dot): $d"
  [[ "$d" == *.* ]] || die "invalid domain (must be fully qualified): $d"

  local label
  local -a labels
  IFS='.' read -r -a labels <<< "$d"
  for label in "${labels[@]}"; do
    [[ -n "$label" ]] || die "invalid domain (empty label): $d"
    [[ ${#label} -le 63 ]] || die "invalid domain (label too long): $d"
    [[ "$label" != -* && "$label" != *- ]] || die "invalid domain (label hyphen): $d"
    [[ "$label" =~ ^[a-z0-9-]+$ ]] || die "invalid domain: $d"
  done
  # reject an all-numeric final label, i.e. a bare IPv4 address
  [[ ! "${labels[${#labels[@]}-1]}" =~ ^[0-9]+$ ]] || die "invalid domain (numeric TLD): $d"

  # shellcheck disable=SC2034  # read by the verbs that source this library
  CLEAN_DOMAIN="$d"
}

# ---------------------------------------------------------------------------
# MySQL identifier derived from a domain.
#
# MySQL user names are capped at 32 characters, so a long domain has to be
# truncated. Truncation alone collides: foo.<same 40 chars>.example.com and
# bar.<same 40 chars>.example.com would map to the SAME database, handing one
# customer's data to another. We therefore append a short digest of the FULL
# domain, which keeps the identifier unique, stable and within 32 characters.
# ---------------------------------------------------------------------------
db_slug() {
  local d="${1:-}" base sum
  base="$(printf '%s' "$d" | tr -c 'a-z0-9' '_' | cut -c1-19)"
  sum="$(printf '%s' "$d" | sha256sum | cut -c1-8)"
  printf 'hz_%s_%s' "$base" "$sum"
}

# Newest installed PHP-FPM version, or empty when PHP-FPM is not installed.
detect_php_version() {
  ls -d /etc/php/*/fpm 2>/dev/null \
    | sed -E 's#.*/php/([0-9.]+)/fpm#\1#' \
    | sort -V | tail -1
}

# Verbs that provision PHP call this; site_list does not need PHP at all.
# Without it an absent PHP-FPM silently produced paths like /etc/php//fpm/pool.d.
require_php_fpm() {
  PHPVER="$(detect_php_version)"
  [[ -n "$PHPVER" ]] || die "no PHP-FPM installation found under /etc/php/*/fpm"
  PHP_FPM_SVC="php${PHPVER}-fpm"
}

PHPVER="${PHPVER:-}"
PHP_FPM_SVC="${PHP_FPM_SVC:-}"

require_root() { [[ "$(id -u)" -eq 0 ]] || die "must run as root"; }
