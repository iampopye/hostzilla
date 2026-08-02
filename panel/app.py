"""Hostzilla panel — Flask application factory and routes.

Dev usage (no installer needed):
    cd panel
    HZ_DEV=1 python app.py     # http://127.0.0.1:2087

Production: served by gunicorn via wsgi.py (exposes `app`).
"""

import json
import os
import sys

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from werkzeug.middleware.proxy_fix import ProxyFix

import auth
import jobs as jobs_mod
import models
import runner_client
import security
from config import get_config

# Minimum length for a password set through the panel. Short enough not to be
# obnoxious, long enough that the login throttle is a meaningful backstop.
MIN_PASSWORD_LENGTH = 12

_login_throttle = security.LoginThrottle()


def _dev_mode():
    return os.environ.get("HZ_DEV", "").lower() in ("1", "true", "yes")


def _load_result(job):
    """Parse a job's stored result_json into a dict (or None)."""
    raw = job.get("result_json") if job else None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _dev_secret_key_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "data", ".secret_key")


def _dev_secret_key():
    """Persist a dev secret so restarts don't invalidate the dev session."""
    path = _dev_secret_key_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            existing = fh.read().strip()
        if existing:
            return existing
    key = os.urandom(32).hex()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(key)
    return key


def resolve_secret_key(cfg):
    """Return the Flask session-signing key, or fail loudly.

    The old code did `os.environ.get("HZ_SECRET_KEY", os.urandom(32).hex())`,
    which had two production failure modes:
      * os.environ.get returns "" (not the default) when the variable is set but
        empty, and Flask cannot sign sessions with an empty key;
      * when unset, each of the four gunicorn workers minted its OWN random key,
        so a cookie signed by one worker was rejected by the other three and
        login appeared to fail at random.
    A missing key is a misconfiguration, so say so instead of limping on.
    """
    key = (os.environ.get("HZ_SECRET_KEY") or "").strip()
    if not key:
        key = (cfg.get("HZ_SECRET_KEY") or "").strip()
    if key:
        return key
    if _dev_mode():
        return _dev_secret_key()
    raise RuntimeError(
        "HZ_SECRET_KEY is not set. The panel cannot sign session cookies "
        "without a key shared by every worker. Set HZ_SECRET_KEY in "
        "/etc/hostzilla/hostzilla.conf (the installer generates one), or "
        "export HZ_DEV=1 for a local development key."
    )


def create_app():
    app = Flask(__name__)
    cfg = get_config()

    app.config["SECRET_KEY"] = resolve_secret_key(cfg)
    app.config["PANEL_CONFIG"] = cfg

    # Apache terminates TLS and reverse-proxies to gunicorn on loopback, setting
    # X-Forwarded-*. Without ProxyFix, Flask builds http:// URLs even when the
    # edge was HTTPS, and every client looks like 127.0.0.1 to the login throttle.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Only set the Secure flag once the panel actually has a hostname to put
        # TLS on. A plain-HTTP install (the state right after install.sh, before
        # certbot runs) would otherwise drop the session cookie and nobody could
        # log in at all.
        SESSION_COOKIE_SECURE=bool(cfg.get("PANEL_DOMAIN"))
        and os.environ.get("HZ_FORCE_HTTPS", "1") not in ("0", "false", "no"),
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,
    )

    # Ensure the schema exists, then clear jobs abandoned by a previous process.
    models.init_db()
    reaped = models.reap_stale_jobs()
    if reaped:
        print(
            "hostzilla: marked {} interrupted job(s) as failed".format(reaped),
            file=sys.stderr,
        )

    # First run only: mint an admin with a RANDOM password and print it once.
    created = models.bootstrap_admin()
    if created:
        username, password = created
        print(
            "hostzilla: created initial admin '{}' with password: {}\n"
            "hostzilla: sign in and change it at /account".format(username, password),
            file=sys.stderr,
        )

    security.init_csrf(app)
    security.init_security_headers(app)

    login_manager = LoginManager()
    login_manager.login_view = "login"
    login_manager.login_message = "Please sign in to continue."
    login_manager.session_protection = "strong"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return auth.PanelUser.from_id(user_id)

    # ---- template globals -------------------------------------------------
    @app.context_processor
    def inject_globals():
        return {
            "brand": "Hostzilla",
            "tagline": "one giant panel. sites included.",
            "panel_domain": cfg.get("PANEL_DOMAIN", ""),
        }

    # ---- auth -------------------------------------------------------------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            throttle_key = "{}|{}".format(
                username.lower(), request.remote_addr or "-"
            )

            if _login_throttle.is_blocked(throttle_key):
                flash(
                    "Too many failed sign-in attempts. Try again in a few minutes.",
                    "error",
                )
                return render_template("login.html"), 429

            user = auth.verify_credentials(username, password)
            if user:
                _login_throttle.reset(throttle_key)
                login_user(user)
                nxt = request.args.get("next")
                if security.is_safe_next(nxt):
                    return redirect(nxt)
                return redirect(url_for("dashboard"))
            _login_throttle.record_failure(throttle_key)
            flash("Invalid username or password.", "error")
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    @login_required
    def logout():
        logout_user()
        flash("Signed out.", "ok")
        return redirect(url_for("login"))

    @app.route("/account", methods=["GET", "POST"])
    @login_required
    def account():
        """Change the signed-in operator's password.

        The installer tells the operator to "log in and change the admin
        password" — until now the panel offered no way to actually do that.
        """
        if request.method == "POST":
            current = request.form.get("current_password") or ""
            new = request.form.get("new_password") or ""
            confirm = request.form.get("confirm_password") or ""

            errors = []
            if not auth.verify_credentials(current_user.username, current):
                errors.append("Your current password is not correct.")
            if len(new) < MIN_PASSWORD_LENGTH:
                errors.append(
                    "New password must be at least {} characters.".format(
                        MIN_PASSWORD_LENGTH
                    )
                )
            if new != confirm:
                errors.append("New password and confirmation do not match.")
            if new and new == current:
                errors.append("New password must differ from the current one.")

            if errors:
                for message in errors:
                    flash(message, "error")
                return render_template("account.html"), 400

            models.set_password(int(current_user.id), new)
            flash("Password updated.", "ok")
            return redirect(url_for("account"))

        return render_template("account.html")

    # ---- dashboard --------------------------------------------------------
    @app.route("/")
    @login_required
    def dashboard():
        counts = models.job_counts()
        recent = models.recent_jobs(limit=8)
        site_count = None
        site_error = None
        try:
            listing = runner_client.site_list()
            if listing.get("status") == "ok":
                site_count = len(listing.get("sites", []))
            else:
                site_error = listing.get("message", "runner error")
        except runner_client.RunnerError as exc:
            site_error = str(exc)
        return render_template(
            "dashboard.html",
            counts=counts,
            recent=recent,
            site_count=site_count,
            site_error=site_error,
        )

    # ---- sites ------------------------------------------------------------
    @app.route("/sites")
    @login_required
    def sites():
        sites_list = []
        error = None
        try:
            listing = runner_client.site_list()
            if listing.get("status") == "ok":
                sites_list = listing.get("sites", [])
            else:
                error = listing.get("message", "runner error")
        except runner_client.RunnerError as exc:
            error = str(exc)
        return render_template("sites.html", sites=sites_list, error=error)

    @app.route("/sites/create", methods=["GET", "POST"])
    @login_required
    def site_create():
        if request.method == "POST":
            domain = (request.form.get("domain") or "").strip().lower()
            site_type = (request.form.get("type") or "static").strip()
            ssl = request.form.get("ssl") in ("on", "true", "1", "yes")

            errors = []
            if not runner_client.validate_domain(domain):
                errors.append("Enter a valid fully-qualified domain.")
            if not runner_client.validate_type(site_type):
                errors.append("Choose a valid site type.")

            if errors:
                for e in errors:
                    flash(e, "error")
                return (
                    render_template(
                        "site_create.html",
                        form={"domain": domain, "type": site_type, "ssl": ssl},
                    ),
                    400,
                )

            job_id, deduped = jobs_mod.submit_job(
                "site_create", domain, {"type": site_type, "ssl": ssl}
            )
            if deduped:
                flash(
                    "A create job for {} is already in progress.".format(domain),
                    "ok",
                )
            else:
                flash("Queued creation of {}.".format(domain), "ok")
            return redirect(url_for("job_detail", job_id=job_id))

        return render_template(
            "site_create.html",
            form={"domain": "", "type": "static", "ssl": False},
        )

    @app.route("/sites/<domain>/delete", methods=["POST"])
    @login_required
    def site_delete(domain):
        domain = (domain or "").strip().lower()
        if not runner_client.validate_domain(domain):
            abort(400)
        job_id, deduped = jobs_mod.submit_job(
            "site_delete", domain, {"purge_db": True}
        )
        if deduped:
            flash("A delete job for {} is already running.".format(domain), "ok")
        else:
            flash("Queued deletion of {}.".format(domain), "ok")
        return redirect(url_for("job_detail", job_id=job_id))

    # ---- jobs -------------------------------------------------------------
    @app.route("/jobs")
    @login_required
    def jobs_list():
        return render_template("jobs.html", jobs=models.list_jobs(limit=100))

    @app.route("/jobs/<int:job_id>")
    @login_required
    def job_detail(job_id):
        job = models.get_job(job_id)
        if not job:
            abort(404)
        return render_template(
            "job_detail.html", job=job, result=_load_result(job)
        )

    @app.route("/jobs/<int:job_id>/status")
    @login_required
    def job_status(job_id):
        job = models.get_job(job_id)
        if not job:
            abort(404)
        return jsonify(
            {
                "id": job["id"],
                "type": job["type"],
                "domain": job["domain"],
                "status": job["status"],
                "result": _load_result(job),
                "log": job.get("log"),
                "created_at": job.get("created_at"),
                "finished_at": job.get("finished_at"),
            }
        )

    # ---- health -----------------------------------------------------------
    @app.route("/healthz")
    def healthz():
        return jsonify({"status": "ok", "service": "hostzilla-panel"})

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PANEL_PORT") or get_config().get("PANEL_PORT") or 2087)
    # NEVER default to debug=True. The Werkzeug debugger exposes an interactive
    # Python console on unhandled exceptions, which on a box whose entire job is
    # running privileged provisioning verbs is a remote code execution hole the
    # moment that port is reachable.
    app.run(
        host=os.environ.get("HZ_BIND", "127.0.0.1"),
        port=port,
        debug=os.environ.get("HZ_DEBUG", "").lower() in ("1", "true", "yes"),
    )
