#!/usr/bin/env bash
# Hostzilla verb: site_create <domain> <type> [--ssl]
#   type: static | php | wordpress
# Idempotent-ish: refuses if the docroot already exists (no clobber).
# Provisions: docroot + per-site PHP-FPM pool + MySQL DB/user + Apache vhost
#             + starter content, and (optional) Let's Encrypt if the domain
#             publicly resolves to this host.
HZ_VERB=site_create
source /opt/hostzilla/runner/_lib.sh
require_root

validate_domain "${1:-}"; DOMAIN="$CLEAN_DOMAIN"
TYPE="${2:-static}"
WANT_SSL=0; [[ "${3:-}" == "--ssl" ]] && WANT_SSL=1
case "$TYPE" in static|php|wordpress) ;; *) die "invalid type: $TYPE";; esac

is_protected "$DOMAIN" && die "domain is protected: $DOMAIN"
DOCROOT="$WWW/$DOMAIN"
[[ -e "$DOCROOT" ]] && die "docroot already exists, refusing to clobber: $DOCROOT"

log "creating $DOMAIN type=$TYPE ssl=$WANT_SSL php=$PHPVER"

# ---- 1. docroot -----------------------------------------------------------
mkdir -p "$DOCROOT"
SOCK="/run/php/hz-${DOMAIN}.sock"

# ---- 2. MySQL DB + user ---------------------------------------------------
DB="$(db_slug "$DOMAIN")"
DBUSER="$(printf '%s' "$DB" | cut -c1-32)"
DBPASS="$(openssl rand -hex 16)"
mysql --protocol=socket <<SQL || die "mysql provisioning failed"
CREATE DATABASE IF NOT EXISTS \`$DB\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$DBUSER'@'localhost' IDENTIFIED BY '$DBPASS';
ALTER USER '$DBUSER'@'localhost' IDENTIFIED BY '$DBPASS';
GRANT ALL PRIVILEGES ON \`$DB\`.* TO '$DBUSER'@'localhost';
FLUSH PRIVILEGES;
SQL
log "db ok: $DB / $DBUSER"

# ---- 3. per-site PHP-FPM pool --------------------------------------------
POOL="/etc/php/${PHPVER}/fpm/pool.d/hz-${DOMAIN}.conf"
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
env[HZ_DB_NAME] = ${DB}
env[HZ_DB_USER] = ${DBUSER}
env[HZ_DB_PASS] = ${DBPASS}
POOLCONF
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
$dbok = false;
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
    # the latest WordPress core from wordpress.org at create-time (per contract).
    if [[ -d "$HZ_ROOT/templates/wordpress-core" ]]; then
      log "wordpress: using staged core at $HZ_ROOT/templates/wordpress-core"
      cp -a "$HZ_ROOT/templates/wordpress-core/." "$DOCROOT/"
    else
      log "wordpress: no staged core; downloading latest from wordpress.org"
      WP_TMP="$(mktemp -d)"
      if curl -fsSL -m120 -o "$WP_TMP/wp.tar.gz" https://wordpress.org/latest.tar.gz; then
        if tar xzf "$WP_TMP/wp.tar.gz" -C "$WP_TMP" 2>/dev/null && [[ -d "$WP_TMP/wordpress" ]]; then
          cp -a "$WP_TMP/wordpress/." "$DOCROOT/"
          rm -rf "$WP_TMP"
          log "wordpress: core downloaded + extracted"
        else
          rm -rf "$WP_TMP"
          die "wordpress core archive could not be extracted"
        fi
      else
        rm -rf "$WP_TMP"
        die "wordpress core download failed (no network / wordpress.org unreachable) and no staged core at $HZ_ROOT/templates/wordpress-core"
      fi
    fi
    # wp-config from sample
    if [[ -f "$DOCROOT/wp-config-sample.php" ]]; then
      sed -e "s/database_name_here/$DB/" -e "s/username_here/$DBUSER/" \
          -e "s/password_here/$DBPASS/" "$DOCROOT/wp-config-sample.php" > "$DOCROOT/wp-config.php"
    fi
    ;;
esac

# ---- 5. persist site manifest (root-only) ---------------------------------
umask 077
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
chmod 600 "$HZ_SITES/$DOMAIN.env"

# ---- 6. ownership + Apache vhost -----------------------------------------
chown -R www-data:www-data "$DOCROOT"
VHOST="/etc/apache2/sites-available/${DOMAIN}.conf"
cat > "$VHOST" <<VH
<VirtualHost *:80>
    ServerName ${DOMAIN}
    DocumentRoot ${DOCROOT}
    DirectoryIndex index.php index.html
    <Directory ${DOCROOT}>
        Require all granted
        AllowOverride All
    </Directory>
    <FilesMatch \.php\$>
        SetHandler "proxy:unix:${SOCK}|fcgi://localhost"
    </FilesMatch>
    ErrorLog  \${APACHE_LOG_DIR}/${DOMAIN}-error.log
    CustomLog \${APACHE_LOG_DIR}/${DOMAIN}-access.log combined
</VirtualHost>
VH

# ---- 7. reload services (validate first) ---------------------------------
php-fpm${PHPVER} -t 2>/dev/null || { php-fpm${PHPVER} -t; die "php-fpm config test failed"; }
systemctl reload "$PHP_FPM_SVC" || systemctl restart "$PHP_FPM_SVC"
a2ensite "${DOMAIN}.conf" >/dev/null 2>&1 || true
apache2ctl configtest 2>/dev/null || die "apache configtest failed"
systemctl reload apache2

# ---- 8. optional SSL (only if it publicly resolves here) ------------------
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
