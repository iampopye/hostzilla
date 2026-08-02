#!/usr/bin/env bash
# ============================================================================
#  Hostzilla 🦖 — one-command installer.  "one giant panel. sites included."
#
#  Stands up the Hostzilla control panel on a FRESH Ubuntu 22.04 / 24.04 server:
#    Apache + PHP-FPM + MySQL + certbot, the Flask panel under gunicorn (systemd),
#    the privileged provisioning runner behind a tight sudoers allowlist, and an
#    admin login with a freshly generated random password.
#
#  Run as root from the repo checkout:    sudo ./install.sh
#  Idempotent-friendly: safe to re-run; existing config/admin are preserved.
# ============================================================================
set -euo pipefail

# ---- constants (the INSTALLED layout — see the integration contract) --------
HZ_PREFIX=/opt/hostzilla
HZ_PANEL="$HZ_PREFIX/panel"
HZ_RUNNER="$HZ_PREFIX/runner"
HZ_ETC=/etc/hostzilla
HZ_CONF="$HZ_ETC/hostzilla.conf"
HZ_SITES="$HZ_ETC/sites"
HZ_LIB=/var/lib/hostzilla
HZ_DB="$HZ_LIB/hostzilla.db"
HZ_LOGDIR=/var/log/hostzilla
HZ_USER=hostzilla
PANEL_PORT_DEFAULT=2087
INSTALL_LOG="$HZ_LOGDIR/install.log"

# repo root = the directory this script lives in
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- pretty logging ---------------------------------------------------------
C_G=$'\033[1;32m'; C_Y=$'\033[1;33m'; C_R=$'\033[1;31m'; C_C=$'\033[1;36m'; C_0=$'\033[0m'
step()  { printf '%s==>%s %s\n' "$C_C" "$C_0" "$*"; log "STEP $*"; }
ok()    { printf '%s  ✓%s %s\n' "$C_G" "$C_0" "$*"; log "OK   $*"; }
warn()  { printf '%s  !%s %s\n' "$C_Y" "$C_0" "$*"; log "WARN $*"; }
fail()  { printf '%s  ✗ %s%s\n' "$C_R" "$*" "$C_0" >&2; log "FAIL $*"; exit 1; }
log()   { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >> "$INSTALL_LOG" 2>/dev/null || true; }

# ---- 0. preflight: root + Ubuntu --------------------------------------------
mkdir -p "$HZ_LOGDIR"
log "===== Hostzilla install started ====="

step "Checking prerequisites (root + Ubuntu)"
[[ "$(id -u)" -eq 0 ]] || fail "must run as root (try: sudo ./install.sh)"
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ "${ID:-}" == "ubuntu" ]] || warn "non-Ubuntu '${ID:-unknown}' detected; proceeding best-effort"
  case "${VERSION_ID:-}" in
    22.04|24.04) ok "Ubuntu ${VERSION_ID} supported" ;;
    *) warn "Ubuntu ${VERSION_ID:-?} is untested (supported: 22.04, 24.04); proceeding" ;;
  esac
else
  warn "/etc/os-release missing; cannot confirm distro"
fi
[[ -d "$SRC_DIR/panel" ]]  || fail "panel/ not found next to install.sh ($SRC_DIR)"
[[ -d "$SRC_DIR/runner" ]] || fail "runner/ not found next to install.sh ($SRC_DIR)"
ok "Running from repo checkout: $SRC_DIR"

export DEBIAN_FRONTEND=noninteractive

# ---- 1. apt packages --------------------------------------------------------
step "Installing system packages (apt-get)"
apt-get update -y >>"$INSTALL_LOG" 2>&1 || fail "apt-get update failed (see $INSTALL_LOG)"
PKGS=(
  apache2 php-fpm php-mysql mysql-server
  certbot python3-certbot-apache
  python3-venv python3-pip
  curl unzip openssl rsync
)
apt-get install -y "${PKGS[@]}" >>"$INSTALL_LOG" 2>&1 \
  || fail "package install failed (see $INSTALL_LOG)"
ok "Installed: ${PKGS[*]}"

# ---- 2. apache modules ------------------------------------------------------
step "Enabling Apache modules"
a2enmod proxy proxy_fcgi proxy_http rewrite ssl headers setenvif >>"$INSTALL_LOG" 2>&1 \
  || fail "a2enmod failed"
ok "proxy proxy_fcgi proxy_http rewrite ssl headers setenvif"

# ---- 3. service user --------------------------------------------------------
step "Creating service user '$HZ_USER'"
if id "$HZ_USER" >/dev/null 2>&1; then
  ok "user '$HZ_USER' already exists"
else
  useradd --system --home-dir "$HZ_PANEL" --shell /usr/sbin/nologin "$HZ_USER" \
    || fail "useradd $HZ_USER failed"
  ok "created system user '$HZ_USER'"
fi

# ---- 4. directory layout ----------------------------------------------------
step "Creating directory layout under /opt, /etc, /var"
mkdir -p "$HZ_PREFIX" "$HZ_SITES" "$HZ_LIB" "$HZ_LOGDIR"
chmod 0700 "$HZ_SITES"            # per-site manifests are root:600
ok "layout: $HZ_PREFIX  $HZ_ETC  $HZ_LIB  $HZ_LOGDIR"

# ---- 5. copy panel + runner -------------------------------------------------
step "Installing panel -> $HZ_PANEL"
mkdir -p "$HZ_PANEL"
# preserve a pre-existing venv + data across re-installs
rsync -a --delete \
  --exclude 'venv/' --exclude 'data/' --exclude '__pycache__/' --exclude '*.pyc' \
  "$SRC_DIR/panel/" "$HZ_PANEL/" >>"$INSTALL_LOG" 2>&1 \
  || cp -a "$SRC_DIR/panel/." "$HZ_PANEL/"   # rsync absent? fall back to cp
ok "panel files in place"

step "Installing runner -> $HZ_RUNNER (root-owned, 0750)"
mkdir -p "$HZ_RUNNER"
cp -a "$SRC_DIR/runner/." "$HZ_RUNNER/"
chown -R root:root "$HZ_RUNNER"
chmod 0750 "$HZ_RUNNER"
chmod 0750 "$HZ_RUNNER"/*.sh
ok "runner verbs: $(find "$HZ_RUNNER" -maxdepth 1 -name '*.sh' -printf '%f ')"

# ---- 6. python venv + deps --------------------------------------------------
step "Building Python virtualenv + installing requirements"
if [[ ! -x "$HZ_PANEL/venv/bin/python" ]]; then
  python3 -m venv "$HZ_PANEL/venv" || fail "venv creation failed"
fi
"$HZ_PANEL/venv/bin/pip" install --upgrade pip >>"$INSTALL_LOG" 2>&1 || warn "pip self-upgrade failed (continuing)"
REQ="$HZ_PANEL/requirements.txt"
[[ -f "$REQ" ]] || REQ="$SRC_DIR/requirements.txt"
if [[ -f "$REQ" ]]; then
  "$HZ_PANEL/venv/bin/pip" install -r "$REQ" >>"$INSTALL_LOG" 2>&1 \
    || fail "pip install -r $REQ failed (see $INSTALL_LOG)"
  ok "installed requirements from $REQ"
else
  warn "no requirements.txt found; installing baseline (flask gunicorn bcrypt flask-login)"
  "$HZ_PANEL/venv/bin/pip" install flask gunicorn bcrypt flask-login >>"$INSTALL_LOG" 2>&1 \
    || fail "baseline pip install failed"
fi
# gunicorn is the ExecStart binary — make sure it's present regardless
"$HZ_PANEL/venv/bin/pip" show gunicorn >/dev/null 2>&1 \
  || "$HZ_PANEL/venv/bin/pip" install gunicorn >>"$INSTALL_LOG" 2>&1 \
  || fail "gunicorn not installed"

# ---- 7. write /etc/hostzilla/hostzilla.conf ---------------------------------
step "Writing $HZ_CONF"
if [[ -f "$HZ_CONF" ]]; then
  ok "config already present — leaving it untouched"
  # shellcheck disable=SC1090
  source "$HZ_CONF"
  ADMIN_EMAIL="${ADMIN_EMAIL:-admin@example.com}"
  PANEL_DOMAIN="${PANEL_DOMAIN:-}"
  # Upgrade path: configs written before the session key existed (or edited to
  # blank it) leave every gunicorn worker signing cookies with its own random
  # key, which breaks login in a way that looks intermittent. Add one in place
  # rather than silently shipping a broken panel.
  if [[ -z "${HZ_SECRET_KEY:-}" ]]; then
    warn "config has no HZ_SECRET_KEY — generating one (existing sessions will end)"
    printf '\n# Flask session signing key (added by installer). Keep secret.\nHZ_SECRET_KEY="%s"\n' \
      "$(openssl rand -hex 32)" >> "$HZ_CONF"
    chgrp "$HZ_USER" "$HZ_CONF" 2>/dev/null || true
    chmod 0640 "$HZ_CONF"
    ok "added HZ_SECRET_KEY to $HZ_CONF"
  fi
else
  # ADMIN_EMAIL: env override > interactive prompt (if a tty) > default
  ADMIN_EMAIL="${ADMIN_EMAIL:-}"
  if [[ -z "$ADMIN_EMAIL" ]]; then
    if [[ -t 0 ]]; then
      read -r -p "Admin email for Let's Encrypt registration [admin@example.com]: " _in || true
      ADMIN_EMAIL="${_in:-admin@example.com}"
    else
      ADMIN_EMAIL="admin@example.com"
    fi
  fi
  PANEL_DOMAIN="${PANEL_DOMAIN:-}"
  # A STABLE Flask session secret shared by all gunicorn workers. Without this,
  # each worker would mint its own random key and signed session cookies would be
  # rejected by sibling workers — breaking login. Loaded by systemd as an env var
  # (EnvironmentFile) and honored by the panel's SECRET_KEY lookup.
  HZ_SECRET_KEY="$(openssl rand -hex 32)"
  umask 027
  cat > "$HZ_CONF" <<CONF
# Hostzilla configuration — sourced by bash AND read by the panel.
# Simple KEY="value" lines only. Reload: systemctl reload hostzilla
PANEL_DOMAIN="${PANEL_DOMAIN}"
ADMIN_EMAIL="${ADMIN_EMAIL}"
PROTECTED_DOMAINS=""
DB_PATH="${HZ_DB}"
PANEL_PORT="${PANEL_PORT_DEFAULT}"
# Flask session signing key (stable across workers/restarts). Keep secret.
HZ_SECRET_KEY="${HZ_SECRET_KEY}"
CONF
  # root:hostzilla 0640 — readable by the panel user + runner, not world-readable
  # (it now holds the session secret).
  chgrp "$HZ_USER" "$HZ_CONF" 2>/dev/null || true
  chmod 0640 "$HZ_CONF"
  ok "wrote config (ADMIN_EMAIL=$ADMIN_EMAIL, DB_PATH=$HZ_DB, session key generated)"
fi

# ---- 8. sudoers allowlist (validate before placing) -------------------------
step "Installing sudoers allowlist /etc/sudoers.d/hostzilla"
SUDO_SRC="$SRC_DIR/config/sudoers.hostzilla"
[[ -f "$SUDO_SRC" ]] || fail "missing $SUDO_SRC"
TMP_SUDO="$(mktemp)"
cp "$SUDO_SRC" "$TMP_SUDO"
if visudo -cf "$TMP_SUDO" >>"$INSTALL_LOG" 2>&1; then
  install -m 0440 -o root -g root "$TMP_SUDO" /etc/sudoers.d/hostzilla
  rm -f "$TMP_SUDO"
  ok "sudoers validated + installed (panel may run ONLY the 3 verbs)"
else
  rm -f "$TMP_SUDO"
  fail "sudoers file failed visudo -cf — refusing to install"
fi

# ---- 9. MySQL: ensure running + root-via-socket works -----------------------
step "Ensuring MySQL is running and root-via-unix-socket works"
systemctl enable --now mysql >>"$INSTALL_LOG" 2>&1 \
  || systemctl enable --now mysql.service >>"$INSTALL_LOG" 2>&1 \
  || fail "could not start mysql"
# Wait for the socket to accept connections.
for _ in $(seq 1 30); do
  if mysqladmin --protocol=socket ping >/dev/null 2>&1; then break; fi
  sleep 1
done
# The runner connects as root over the unix socket (auth_socket on Ubuntu). Confirm.
if mysql --protocol=socket -e "SELECT 1" >/dev/null 2>&1; then
  ok "root@localhost via unix socket: OK"
else
  warn "root socket auth not working yet; attempting to (re)assert auth_socket plugin"
  # Best-effort: on a fresh box root already uses auth_socket; this is a no-op safety net.
  mysql --protocol=socket <<'SQL' >>"$INSTALL_LOG" 2>&1 || warn "could not assert auth_socket (non-fatal)"
ALTER USER 'root'@'localhost' IDENTIFIED WITH auth_socket;
FLUSH PRIVILEGES;
SQL
  mysql --protocol=socket -e "SELECT 1" >/dev/null 2>&1 \
    && ok "root socket auth: OK" \
    || warn "root socket auth still failing — site provisioning may need manual MySQL fixup"
fi

# ---- 10. panel DB init + admin user with RANDOM password --------------------
step "Initializing panel database + admin user"
export DB_PATH="$HZ_DB"
ADMIN_USER="admin"
ADMIN_PASS_FILE="$HZ_ETC/admin_password"     # written only on first creation

PY="$HZ_PANEL/venv/bin/python"
BOOT_OUT="$(mktemp)"

# The panel is first-party code in this repo, so call its real interface
# directly. (This step previously introspected models.create_user() with the
# inspect module and tried three speculative entrypoints, which meant a genuine
# failure was indistinguishable from "the panel exposes a different API".)
#
# The generated password is passed through the ENVIRONMENT, never argv: command
# lines are world-readable via /proc, so an argv password leaks to every local
# user for as long as the process runs.
set +e
(
  cd "$HZ_PANEL" || exit 90
  HZ_ADMIN_USER="$ADMIN_USER" "$PY" - <<'PYEOF'
import os, sys
sys.path.insert(0, os.getcwd())
import models

models.init_db()
admin_user = os.environ["HZ_ADMIN_USER"]

if models.get_user_by_username(admin_user):
    print("INIT_OK exists")
else:
    # bootstrap_admin() generates the random password itself and refuses to
    # create a well-known default.
    created = models.bootstrap_admin(admin_user)
    if created is None:
        print("INIT_OK exists")
    else:
        _username, password = created
        print("INIT_OK created")
        print("ADMIN_PASS " + password)
PYEOF
) >"$BOOT_OUT" 2>&1
RC=$?
set -e

# Keep the password out of the install log; log everything else.
grep -v '^ADMIN_PASS ' "$BOOT_OUT" >> "$INSTALL_LOG" || true

if [[ $RC -ne 0 ]]; then
  warn "panel DB bootstrap failed (rc=$RC). Detail:"
  grep -v '^ADMIN_PASS ' "$BOOT_OUT" | sed 's/^/    /' | tail -20
fi

ADMIN_PASS=""
if grep -q '^INIT_OK created' "$BOOT_OUT"; then
  ADMIN_PASS="$(sed -n 's/^ADMIN_PASS //p' "$BOOT_OUT" | head -1)"
  umask 077
  printf '%s\n' "$ADMIN_PASS" > "$ADMIN_PASS_FILE"
  chmod 0600 "$ADMIN_PASS_FILE"
  ADMIN_CREATED=1
  ok "panel DB initialized; admin '$ADMIN_USER' created (password saved to $ADMIN_PASS_FILE)"
elif grep -q '^INIT_OK exists' "$BOOT_OUT"; then
  ADMIN_CREATED=0
  ok "panel DB initialized; admin '$ADMIN_USER' already existed (password unchanged)"
else
  ADMIN_CREATED=-1
  warn "could not confirm admin creation — check $INSTALL_LOG"
fi
shred -u "$BOOT_OUT" 2>/dev/null || rm -f "$BOOT_OUT"

# ---- 11. ownership ----------------------------------------------------------
step "Setting ownership (panel + state -> $HZ_USER)"
chown -R "$HZ_USER":"$HZ_USER" "$HZ_PANEL" "$HZ_LIB"
chown -R "$HZ_USER":"$HZ_USER" "$HZ_LOGDIR" 2>/dev/null || true
# runner stays root-owned; manifests dir stays root-only
chown -R root:root "$HZ_RUNNER" "$HZ_SITES"
chmod 0700 "$HZ_SITES"
ok "ownership applied"

# ---- 12. systemd service ----------------------------------------------------
step "Installing + enabling systemd service"
install -m 0644 "$SRC_DIR/config/hostzilla.service" /etc/systemd/system/hostzilla.service \
  || fail "could not install hostzilla.service"
systemctl daemon-reload
systemctl enable hostzilla >>"$INSTALL_LOG" 2>&1 || fail "systemctl enable hostzilla failed"
systemctl restart hostzilla >>"$INSTALL_LOG" 2>&1 || warn "service restart failed — check: journalctl -u hostzilla"
sleep 2
if systemctl is-active --quiet hostzilla; then
  ok "hostzilla.service is active (gunicorn on 127.0.0.1:${PANEL_PORT_DEFAULT})"
else
  warn "hostzilla.service not active yet — inspect: journalctl -u hostzilla -n50"
fi

# ---- 13. panel Apache vhost -------------------------------------------------
step "Installing panel Apache vhost + reloading Apache"
install -m 0644 "$SRC_DIR/config/hostzilla-apache.conf" /etc/apache2/sites-available/hostzilla.conf \
  || fail "could not install panel vhost"
# Make Hostzilla the default site; drop the stock Apache landing page.
a2dissite 000-default >>"$INSTALL_LOG" 2>&1 || true
a2ensite hostzilla >>"$INSTALL_LOG" 2>&1 || fail "a2ensite hostzilla failed"
if apache2ctl configtest >>"$INSTALL_LOG" 2>&1; then
  systemctl reload apache2 || systemctl restart apache2 || warn "apache reload failed"
  systemctl enable apache2 >>"$INSTALL_LOG" 2>&1 || true
  ok "panel vhost enabled; Apache reloaded"
else
  fail "apache configtest failed — see $INSTALL_LOG"
fi

# ---- 14. final banner -------------------------------------------------------
SERVER_IP="$(curl -s -m5 https://api.ipify.org 2>/dev/null || true)"
[[ -n "$SERVER_IP" ]] || SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[[ -n "$SERVER_IP" ]] || SERVER_IP="<server-ip>"
URL="http://${SERVER_IP}/"
[[ -n "${PANEL_DOMAIN:-}" ]] && URL="http://${PANEL_DOMAIN}/"

printf '\n'
printf '%s' "$C_G"
cat <<'BANNER'
   __  __         __  _   ____
  / / / /__  ___ / /_(_) / /_ /  __ _
 / /_/ / _ \(_-</ __/ / /  '_/  '_// _ \
/_/ /_/\___/___/\__/_/_/_/\_\/_/  /___/   🦖
   one giant panel. sites included.
BANNER
printf '%s' "$C_0"
printf '\n%s┌──────────────────────────────────────────────────────────────┐%s\n' "$C_C" "$C_0"
printf '%s│%s  Hostzilla is installed.\n' "$C_C" "$C_0"
printf '%s│%s\n' "$C_C" "$C_0"
printf '%s│%s  Dashboard : %s%s%s\n' "$C_C" "$C_0" "$C_G" "$URL" "$C_0"
printf '%s│%s  Username  : %sadmin%s\n' "$C_C" "$C_0" "$C_G" "$C_0"
if [[ "${ADMIN_CREATED:-0}" == "1" ]]; then
  printf '%s│%s  Password  : %s%s%s\n' "$C_C" "$C_0" "$C_G" "$ADMIN_PASS" "$C_0"
  printf '%s│%s              (also saved root-only at %s)\n' "$C_C" "$C_0" "$ADMIN_PASS_FILE"
elif [[ "${ADMIN_CREATED:-0}" == "0" ]]; then
  printf '%s│%s  Password  : (unchanged — admin already existed)\n' "$C_C" "$C_0"
else
  printf '%s│%s  Password  : %s(admin not confirmed — see %s)%s\n' "$C_C" "$C_0" "$C_Y" "$INSTALL_LOG" "$C_0"
fi
printf '%s│%s\n' "$C_C" "$C_0"
printf '%s│%s  Next steps:\n' "$C_C" "$C_0"
printf '%s│%s    1. Log in and change the admin password.\n' "$C_C" "$C_0"
printf '%s│%s    2. Point a hostname at this server, set PANEL_DOMAIN in\n' "$C_C" "$C_0"
printf '%s│%s       %s, then run:\n' "$C_C" "$C_0" "$HZ_CONF"
printf '%s│%s         certbot --apache -d $PANEL_DOMAIN --agree-tos -m %s --redirect\n' "$C_C" "$C_0" "${ADMIN_EMAIL}"
printf '%s│%s    3. Create your first site from the dashboard.\n' "$C_C" "$C_0"
printf '%s│%s\n' "$C_C" "$C_0"
printf '%s│%s  Logs: %s | journalctl -u hostzilla\n' "$C_C" "$C_0" "$INSTALL_LOG"
printf '%s└──────────────────────────────────────────────────────────────┘%s\n\n' "$C_C" "$C_0"

log "===== Hostzilla install finished ====="
ok "Done."
