#!/usr/bin/env bash
# Hostzilla verb: site_list   (read-only)
# Prints exactly ONE JSON object as its LAST stdout line:
#   {"status":"ok","sites":[{"domain":"..","type":"..","docroot":"..","created":"..","ssl":true|false}, ...]}
# Source of truth: the per-site manifests in /etc/hostzilla/sites/*.env that
# site_create wrote. ssl is true when an -le-ssl Apache vhost OR a live cert exists.
# Never mutates anything; safe to call synchronously from the panel.
HZ_VERB=site_list
source /opt/hostzilla/runner/_lib.sh
require_root

# JSON string escaper for manifest-sourced values (defense in depth; domains are
# already regex-validated at create time, but type/docroot/created are echoed raw).
json_escape() {
  local s="${1:-}"
  s="${s//\\/\\\\}"   # backslash
  s="${s//\"/\\\"}"   # double-quote
  s="${s//$'\t'/\\t}" # tab
  s="${s//$'\n'/\\n}" # newline
  s="${s//$'\r'/\\r}" # carriage return
  printf '%s' "$s"
}

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
  # stable, predictable ordering
  shopt -s nullglob
  for man in $(printf '%s\n' "$HZ_SITES"/*.env | sort); do
    [[ -f "$man" ]] || continue
    # Read manifest values in a SUBSHELL so a malformed .env can't poison our env.
    # Each verb prints "key<TAB>value"; we capture the four fields we expose.
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
  shopt -u nullglob
fi

log "DONE list (${sep:+sites present})"
echo "{\"status\":\"ok\",\"sites\":[${sites_json}]}"
