#!/usr/bin/env bash
# Hostzilla verb: site_delete <domain> [--purge-db]
# Tears down a Hostzilla-provisioned site. Refuses anything not recorded in
# /etc/hostzilla/sites (so it can NEVER remove a pre-existing rental/system site).
HZ_VERB=site_delete
# shellcheck source=runner/_lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lib.sh"
require_root

validate_domain "${1:-}"; DOMAIN="$CLEAN_DOMAIN"
PURGE_DB=0; [[ "${2:-}" == "--purge-db" ]] && PURGE_DB=1

is_protected "$DOMAIN" && die "domain is protected: $DOMAIN"
MAN="$HZ_SITES/$DOMAIN.env"
[[ -f "$MAN" ]] || die "not a Hostzilla-managed site (no manifest): $DOMAIN"

# Read the manifest in a SUBSHELL. Sourcing it straight into this shell under
# `set -u` meant a manifest written by an older version (or truncated by a full
# disk) left DOCROOT/PHP_POOL/DB_NAME unset, and the script then aborted with an
# "unbound variable" error and emitted no JSON at all — the panel only saw a
# parse failure and the site stayed half-deleted. Defaults keep teardown going,
# and the guards below, not the manifest, decide what may be removed.
manifest_field() {
  # The assignments below are read back through the indirect expansion "${!1}",
  # which shellcheck cannot follow — hence the SC2034/SC2030 suppressions.
  # shellcheck disable=SC2034,SC2030
  ( set +euo pipefail
    DOMAIN=""; TYPE=""; DOCROOT=""; DB_NAME=""; DB_USER=""; PHP_SOCK=""; PHP_POOL=""
    # shellcheck disable=SC1090
    source "$MAN" 2>/dev/null || true
    printf '%s' "${!1}" )
}
M_DOCROOT="$(manifest_field DOCROOT)"
M_DB_NAME="$(manifest_field DB_NAME)"
M_DB_USER="$(manifest_field DB_USER)"
M_PHP_POOL="$(manifest_field PHP_POOL)"

log "deleting $DOMAIN (purge_db=$PURGE_DB)"
a2dissite "${DOMAIN}.conf" >/dev/null 2>&1 || true
rm -f "/etc/apache2/sites-available/${DOMAIN}.conf"
# drop any LE ssl vhost certbot may have created
a2dissite "${DOMAIN}-le-ssl.conf" >/dev/null 2>&1 || true
rm -f "/etc/apache2/sites-available/${DOMAIN}-le-ssl.conf"
apache2ctl configtest 2>/dev/null && systemctl reload apache2 || true

# PHP-FPM pool. Only ever a hz-<domain>.conf inside a pool.d directory: a
# manifest that somehow named /etc/passwd must not turn this into an arbitrary
# root-owned file deletion.
EXPECTED_POOL_BASENAME="hz-${DOMAIN}.conf"
if [[ -n "$M_PHP_POOL" ]]; then
  if [[ "$M_PHP_POOL" == /etc/php/*/fpm/pool.d/"$EXPECTED_POOL_BASENAME" ]]; then
    rm -f "$M_PHP_POOL"
  else
    log "warn: manifest PHP_POOL '$M_PHP_POOL' outside expected pool.d path; not removing"
  fi
else
  # Manifest predates PHP_POOL, or lost it: fall back to the deterministic path.
  rm -f /etc/php/*/fpm/pool.d/"$EXPECTED_POOL_BASENAME"
fi
PHPVER="$(detect_php_version)"
if [[ -n "$PHPVER" ]]; then
  systemctl reload "php${PHPVER}-fpm" 2>/dev/null \
    || systemctl restart "php${PHPVER}-fpm" 2>/dev/null || true
fi

# docroot — only ever exactly $WWW/<validated domain>, never what the manifest claims
if [[ -n "$M_DOCROOT" && "$M_DOCROOT" != "$WWW/$DOMAIN" ]]; then
  log "warn: manifest DOCROOT '$M_DOCROOT' != '$WWW/$DOMAIN'; removing only the canonical path"
fi
[[ -d "$WWW/$DOMAIN" ]] && rm -rf "${WWW:?}/${DOMAIN:?}"

if [[ "$PURGE_DB" -eq 1 ]]; then
  # Identifiers are re-derived from the validated domain when the manifest is
  # incomplete, so we never interpolate an unvalidated string into SQL.
  [[ -n "$M_DB_NAME" ]] || M_DB_NAME="$(db_slug "$DOMAIN")"
  [[ -n "$M_DB_USER" ]] || M_DB_USER="$M_DB_NAME"
  if [[ "$M_DB_NAME" =~ ^hz_[a-z0-9_]+$ && "$M_DB_USER" =~ ^hz_[a-z0-9_]+$ ]]; then
    mysql --protocol=socket <<SQL || log "warn: db drop failed"
DROP DATABASE IF EXISTS \`$M_DB_NAME\`;
DROP USER IF EXISTS '$M_DB_USER'@'localhost';
FLUSH PRIVILEGES;
SQL
    log "db purged: $M_DB_NAME"
  else
    log "warn: refusing to drop non-Hostzilla identifiers ('$M_DB_NAME'/'$M_DB_USER')"
  fi
fi

rm -f "$MAN"
log "DONE delete $DOMAIN"
emit ok domain="$DOMAIN" purged_db="$PURGE_DB"
