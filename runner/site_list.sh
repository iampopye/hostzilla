#!/usr/bin/env bash
# Hostzilla verb: site_list   (read-only)
# Prints exactly ONE JSON object as its LAST stdout line:
#   {"status":"ok","sites":[{"domain":"..","type":"..","docroot":"..","created":"..","ssl":true|false}, ...]}
# Source of truth: the per-site manifests in /etc/hostzilla/sites/*.env that
# site_create wrote. ssl is true when an -le-ssl Apache vhost OR a live cert exists.
# Never mutates anything; safe to call synchronously from the panel.
HZ_VERB=site_list
# shellcheck source=runner/_lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lib.sh"
require_root

# json_escape() lives in _lib.sh so every verb escapes output identically.

# Does this domain have SSL? An -le-ssl vhost (certbot --apache) or a live cert dir.
has_ssl() {
  local d="$1"
  [[ -f "/etc/apache2/sites-available/${d}-le-ssl.conf" ]] && return 0
  [[ -d "/etc/letsencrypt/live/${d}" ]] && return 0
  return 1
}

log "listing sites"

sep=""
sites_json=""
if [[ -d "$HZ_SITES" ]]; then
  # Glob straight into a sorted array. The previous `for man in $(printf ... | sort)`
  # split on IFS, so any manifest whose name contained whitespace was silently
  # torn into fragments.
  shopt -s nullglob
  mapfile -t manifests < <(printf '%s\n' "$HZ_SITES"/*.env | LC_ALL=C sort)
  shopt -u nullglob
  for man in "${manifests[@]}"; do
    [[ -f "$man" ]] || continue
    # Read manifest values in a SUBSHELL so a malformed .env can't poison our env.
    fields="$(
      # shellcheck disable=SC1090
      ( set +euo pipefail
        DOMAIN=""; TYPE=""; DOCROOT=""; CREATED=""
        source "$man" 2>/dev/null || true
        printf '%s\t%s\t%s\t%s' "$DOMAIN" "$TYPE" "$DOCROOT" "$CREATED" )
    )"
    IFS=$'\t' read -r m_domain m_type m_docroot m_created <<<"$fields"
    # Fall back to the filename if the manifest lacked DOMAIN.
    if [[ -z "$m_domain" ]]; then
      m_domain="$(basename "$man" .env)"
    fi
    if has_ssl "$m_domain"; then ssl_val="true"; else ssl_val="false"; fi
    obj="{\"domain\":\"$(json_escape "$m_domain")\""
    obj+=",\"type\":\"$(json_escape "$m_type")\""
    obj+=",\"docroot\":\"$(json_escape "$m_docroot")\""
    obj+=",\"created\":\"$(json_escape "$m_created")\""
    obj+=",\"ssl\":${ssl_val}}"
    sites_json+="${sep}${obj}"
    sep=","
  done
fi

log "DONE list (${sep:+sites present})"
printf '%s\n' "{\"status\":\"ok\",\"sites\":[${sites_json}]}"
