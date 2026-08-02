#!/usr/bin/env bash
# Hostzilla verb: site_delete <domain> [--purge-db]
# Tears down a Hostzilla-provisioned site. Refuses anything not recorded in
# /etc/hostzilla/sites (so it can NEVER remove a pre-existing rental/system site).
HZ_VERB=site_delete
source /opt/hostzilla/runner/_lib.sh
require_root

validate_domain "${1:-}"; DOMAIN="$CLEAN_DOMAIN"
PURGE_DB=0; [[ "${2:-}" == "--purge-db" ]] && PURGE_DB=1

is_protected "$DOMAIN" && die "domain is protected: $DOMAIN"
MAN="$HZ_SITES/$DOMAIN.env"
[[ -f "$MAN" ]] || die "not a Hostzilla-managed site (no manifest): $DOMAIN"
# shellcheck disable=SC1090
source "$MAN"

log "deleting $DOMAIN (purge_db=$PURGE_DB)"
a2dissite "${DOMAIN}.conf" >/dev/null 2>&1 || true
rm -f "/etc/apache2/sites-available/${DOMAIN}.conf"
# drop any LE ssl vhost certbot may have created
rm -f "/etc/apache2/sites-available/${DOMAIN}-le-ssl.conf"
a2dissite "${DOMAIN}-le-ssl.conf" >/dev/null 2>&1 || true
apache2ctl configtest 2>/dev/null && systemctl reload apache2 || true

rm -f "$PHP_POOL"
systemctl reload "$PHP_FPM_SVC" 2>/dev/null || systemctl restart "$PHP_FPM_SVC" 2>/dev/null || true

# docroot — only ever under /var/www/<domain>, validated
[[ "$DOCROOT" == "$WWW/$DOMAIN" ]] && rm -rf "$DOCROOT"

if [[ "$PURGE_DB" -eq 1 ]]; then
  mysql --protocol=socket <<SQL || log "warn: db drop failed"
DROP DATABASE IF EXISTS \`$DB_NAME\`;
DROP USER IF EXISTS '$DB_USER'@'localhost';
FLUSH PRIVILEGES;
SQL
  log "db purged: $DB_NAME"
fi

rm -f "$MAN"
log "DONE delete $DOMAIN"
emit ok domain="$DOMAIN" purged_db="$PURGE_DB"
