#!/usr/bin/env bash
# Hostzilla verb: site_create <domain> <type> [--ssl]
#   type: static | php | wordpress
# Refuses if the docroot already exists (no clobber).
# Provisions: docroot + per-site PHP-FPM pool + MySQL DB/user + Apache vhost
#             + starter content, and (optional) Let's Encrypt if the domain
#             publicly resolves to this host.
#
# ATOMICITY: every provisioning step registers an undo action. If a later step
# fails, the undo actions run in reverse, so a failed create leaves NO partial
# state behind. Without this, a half-created site was unrecoverable: the docroot
# survived, and the "docroot already exists" guard then refused every retry.
HZ_VERB=site_create
# Resolve the library relative to this script so the verbs also work from a
# checkout, not only from the installed /opt/hostzilla layout.
# shellcheck source=runner/_lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lib.sh"
require_root
require_php_fpm

validate_domain "${1:-}"; DOMAIN="$CLEAN_DOMAIN"
TYPE="${2:-static}"
WANT_SSL=0; [[ "${3:-}" == "--ssl" ]] && WANT_SSL=1
case "$TYPE" in static|php|wordpress) ;; *) die "invalid type: $TYPE";; esac

is_protected "$DOMAIN" && die "domain is protected: $DOMAIN"
DOCROOT="$WWW/$DOMAIN"
[[ -e "$DOCROOT" ]] && die "docroot already exists, refusing to clobber: $DOCROOT"

# ---- rollback machinery ---------------------------------------------------
HZ_COMMITTED=0
HZ_UNDO=()
undo_push() { HZ_UNDO+=("$1"); }
rollback() {
  local rc=$? i
  [[ "$HZ_COMMITTED" -eq 1 ]] && return 0
  log "rollback: create failed (rc=$rc), undoing ${#HZ_UNDO[@]} step(s)"
  for (( i=${#HZ_UNDO[@]}-1 ; i>=0 ; i-- )); do
    # Undo steps are our own literal strings built from validated values.
    eval "${HZ_UNDO[$i]}" >>"$HZ_LOG" 2>&1 || log "rollback: step $i failed (continuing)"
  done
  log "rollback: complete for $DOMAIN"
}
trap rollback EXIT

log "creating $DOMAIN type=$TYPE ssl=$WANT_SSL php=$PHPVER"

# ---- 1. docroot -----------------------------------------------------------
mkdir -p "$DOCROOT"
undo_push "rm -rf '$DOCROOT'"
SOCK="/run/php/hz-${DOMAIN}.sock"

# ---- 2. MySQL DB + user ---------------------------------------------------
DB="$(db_slug "$DOMAIN")"
DBUSER="$DB"
DBPASS="$(openssl rand -hex 16)"
mysql --protocol=socket <<SQL || die "mysql provisioning failed"
CREATE DATABASE IF NOT EXISTS \`$DB\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$DBUSER'@'localhost' IDENTIFIED BY '$DBPASS';
ALTER USER '$DBUSER'@'localhost' IDENTIFIED BY '$DBPASS';
GRANT ALL PRIVILEGES ON \`$DB\`.* TO '$DBUSER'@'localhost';
FLUSH PRIVILEGES;
SQL
undo_push "mysql --protocol=socket -e \"DROP DATABASE IF EXISTS \\\`$DB\\\`; DROP USER IF EXISTS '$DBUSER'@'localhost'; FLUSH PRIVILEGES;\""
log "db ok: $DB / $DBUSER"

# ---- 3. per-site PHP-FPM pool --------------------------------------------
# The pool file carries the site's DB password, so it must not be world-readable
# (the default umask left it 0644, exposing every site's credentials to any
# local user). php-fpm's master process reads it as root before dropping privs.
POOL="/etc/php/${PHPVER}/fpm/pool.d/hz-${DOMAIN}.conf"
( umask 077
cat > "$POOL" <<POOLCONF
; Hostzilla per-site pool for ${DOMAIN}
[hz-${DOMAIN}]
user = www-data
group = www-data
listen = ${SOCK}
listen.owner = www-data
listen.group = www-data
pm = ondemand
pm.max_children = 5
pm.process_idle_timeout = 10s
pm.max_requests = 500
chdir = ${DOCROOT}
php_admin_value[open_basedir] = ${DOCROOT}:/tmp:/usr/share/php
php_admin_value[upload_tmp_dir] = /tmp
php_admin_flag[expose_php] = off
env[HZ_DB_NAME] = ${DB}
env[HZ_DB_USER] = ${DBUSER}
env[HZ_DB_PASS] = ${DBPASS}
POOLCONF
)
chown root:root "$POOL"
chmod 0640 "$POOL"
undo_push "rm -f '$POOL'"
log "pool ok: $POOL -> $SOCK"

# ---- 4. starter content ---------------------------------------------------
case "$TYPE" in
  static)
    cat > "$DOCROOT/index.html" <<HTML
<!doctype html><meta charset="utf-8"><title>${DOMAIN}</title>
<body style="font:16px system-ui;display:grid;place-items:center;height:100vh;margin:0;background:#05100b;color:#a7f3d0">
<div style="text-align:center"><div style="font-size:3rem">🦖</div>
<h1>${DOMAIN}</h1><p style="opacity:.6">Provisioned by Hostzilla.</p></div>
HTML
    ;;
  php)
    cat > "$DOCROOT/index.php" <<'PHP'
<?php
header('Content-Type: text/html; charset=utf-8');
$c = @mysqli_connect('localhost', getenv('HZ_DB_USER'), getenv('HZ_DB_PASS'), getenv('HZ_DB_NAME'));
$dbok = (bool)$c;
echo "<!doctype html><meta charset=utf-8><body style='font:16px system-ui;background:#05100b;color:#a7f3d0;display:grid;place-items:center;height:100vh;margin:0'>";
echo "<div style='text-align:center'><div style='font-size:3rem'>🦖</div><h1>".htmlspecialchars($_SERVER['SERVER_NAME'])."</h1>";
echo "<p>PHP ".PHP_VERSION." · DB ".($dbok?'connected ✅':'unavailable ❌')."</p>";
echo "<p style='opacity:.5'>Provisioned by Hostzilla.</p></div>";
PHP
    ;;
  wordpress)
    # Prefer a pre-staged offline core (fast, air-gap friendly); otherwise fetch
    # the latest WordPress core from wordpress.org at create-time.
    if [[ -d "$HZ_ROOT/templates/wordpress-core" ]]; then
      log "wordpress: using staged core at $HZ_ROOT/templates/wordpress-core"
      cp -a "$HZ_ROOT/templates/wordpress-core/." "$DOCROOT/"
    else
      log "wordpress: no staged core; downloading latest from wordpress.org"
      WP_TMP="$(mktemp -d)"
      undo_push "rm -rf '$WP_TMP'"
      if curl -fsSL -m120 -o "$WP_TMP/wp.tar.gz" https://wordpress.org/latest.tar.gz; then
        if tar xzf "$WP_TMP/wp.tar.gz" -C "$WP_TMP" 2>/dev/null && [[ -d "$WP_TMP/wordpress" ]]; then
          cp -a "$WP_TMP/wordpress/." "$DOCROOT/"
          rm -rf "$WP_TMP"
          log "wordpress: core downloaded + extracted"
        else
          die "wordpress core archive could not be extracted"
        fi
      else
        die "wordpress core download failed (no network / wordpress.org unreachable) and no staged core at $HZ_ROOT/templates/wordpress-core"
      fi
    fi
    # wp-config from sample, with REAL salts.
    #
    # The sample ships eight literal "put your unique phrase here" placeholders.
    # Copying those verbatim gives every Hostzilla WordPress site identical auth
    # keys, so a session cookie stolen from one site is forgeable on all of them.
    # Salts are hex, so they can never contain sed/awk replacement metacharacters.
    if [[ -f "$DOCROOT/wp-config-sample.php" ]]; then
      SALTS=""
      for k in AUTH_KEY SECURE_AUTH_KEY LOGGED_IN_KEY NONCE_KEY \
               AUTH_SALT SECURE_AUTH_SALT LOGGED_IN_SALT NONCE_SALT; do
        SALTS+="define('${k}', '$(openssl rand -hex 32)');"$'\n'
      done
      sed -e "s/database_name_here/$DB/" -e "s/username_here/$DBUSER/" \
          -e "s/password_here/$DBPASS/" "$DOCROOT/wp-config-sample.php" \
        | awk -v salts="$SALTS" '
            /put your unique phrase here/ { if (!done) { printf "%s", salts; done=1 } ; next }
            { print }
          ' > "$DOCROOT/wp-config.php"
    fi
    ;;
esac

# ---- 5. persist site manifest (root-only) ---------------------------------
( umask 077
cat > "$HZ_SITES/$DOMAIN.env" <<ENV
DOMAIN=$DOMAIN
TYPE=$TYPE
DOCROOT=$DOCROOT
DB_NAME=$DB
DB_USER=$DBUSER
DB_PASS=$DBPASS
PHP_SOCK=$SOCK
PHP_POOL=$POOL
CREATED=$(date -u +%FT%TZ)
ENV
)
chmod 600 "$HZ_SITES/$DOMAIN.env"
undo_push "rm -f '$HZ_SITES/$DOMAIN.env'"

# ---- 6. ownership + Apache vhost -----------------------------------------
chown -R www-data:www-data "$DOCROOT"
# wp-config.php holds DB credentials: keep it out of reach of other local users.
[[ -f "$DOCROOT/wp-config.php" ]] && chmod 0640 "$DOCROOT/wp-config.php"
VHOST="/etc/apache2/sites-available/${DOMAIN}.conf"
cat > "$VHOST" <<VH
<VirtualHost *:80>
    ServerName ${DOMAIN}
    DocumentRoot ${DOCROOT}
    DirectoryIndex index.php index.html
    <Directory ${DOCROOT}>
        Require all granted
        AllowOverride All
        # No directory listings — an empty or partially uploaded site must not
        # expose a browsable file index.
        Options -Indexes +FollowSymLinks
    </Directory>
    # Never serve dotfiles: .git, .env and friends routinely leak credentials.
    # .well-known stays reachable so ACME http-01 renewals keep working.
    <FilesMatch "^\.">
        Require all denied
    </FilesMatch>
    <DirectoryMatch "/\.(?!well-known)">
        Require all denied
    </DirectoryMatch>
    <FilesMatch \.php\$>
        SetHandler "proxy:unix:${SOCK}|fcgi://localhost"
    </FilesMatch>
    ErrorLog  \${APACHE_LOG_DIR}/${DOMAIN}-error.log
    CustomLog \${APACHE_LOG_DIR}/${DOMAIN}-access.log combined
</VirtualHost>
VH
undo_push "a2dissite '${DOMAIN}.conf' >/dev/null 2>&1; rm -f '$VHOST'; apache2ctl configtest >/dev/null 2>&1 && systemctl reload apache2"

# ---- 7. reload services (validate first) ---------------------------------
php-fpm"${PHPVER}" -t 2>/dev/null || { php-fpm"${PHPVER}" -t; die "php-fpm config test failed"; }
systemctl reload "$PHP_FPM_SVC" || systemctl restart "$PHP_FPM_SVC" || die "could not reload $PHP_FPM_SVC"
a2ensite "${DOMAIN}.conf" >/dev/null 2>&1 || die "a2ensite failed for ${DOMAIN}.conf"
apache2ctl configtest 2>/dev/null || die "apache configtest failed"
systemctl reload apache2 || die "apache reload failed"

# ---- 8. optional SSL (only if it publicly resolves here) ------------------
# Past this point the site is live and serving. SSL is best-effort: a certbot
# failure must NOT tear down a working site, so we commit before attempting it.
HZ_COMMITTED=1

SSL="none"
if [[ "$WANT_SSL" -eq 1 ]]; then
  myip="$(curl -s -m5 https://api.ipify.org || true)"
  resolved="$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || true)"
  if [[ -n "$myip" && "$resolved" == "$myip" ]]; then
    if certbot --apache -d "$DOMAIN" -n --agree-tos -m "$ADMIN_EMAIL" --redirect >/dev/null 2>&1; then
      SSL="active"
    else SSL="failed"; fi
  else SSL="skipped_dns"; log "ssl skipped: $DOMAIN resolves to '$resolved' not '$myip'"; fi
fi

log "DONE $DOMAIN"
emit ok domain="$DOMAIN" type="$TYPE" url="http://$DOMAIN/" docroot="$DOCROOT" db="$DB" php_sock="$SOCK" ssl="$SSL"
