"""Hostzilla panel — Flask application factory and routes.

Dev usage (no installer needed):
    cd /root/hostzilla/panel
    python app.py            # http://127.0.0.1:2087  (admin/admin)

Production: served by gunicorn via wsgi.py (exposes `app`).
"""

import json
import os

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

import auth
import jobs as jobs_mod
import models
import runner_client
from config import get_config


def _load_result(job):
    """Parse a job's stored result_json into a dict (or None)."""
    raw = job.get("result_json") if job else None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def create_app():
    app = Flask(__name__)
    cfg = get_config()

    app.config["SECRET_KEY"] = os.environ.get(
        "HZ_SECRET_KEY", os.urandom(32).hex()
    )
    app.config["PANEL_CONFIG"] = cfg

    # Ensure schema + a usable admin account exist on startup.
    models.init_db()
    models.ensure_default_admin()

    login_manager = LoginManager()
    login_manager.login_view = "login"
    login_manager.login_message = "Please sign in to continue."
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
            user = auth.verify_credentials(username, password)
            if user:
                login_user(user)
                nxt = request.args.get("next")
                if nxt and nxt.startswith("/"):
                    return redirect(nxt)
                return redirect(url_for("dashboard"))
            flash("Invalid username or password.", "error")
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    @login_required
    def logout():
        logout_user()
        flash("Signed out.", "ok")
        return redirect(url_for("login"))

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
                return render_template(
                    "site_create.html",
                    form={"domain": domain, "type": site_type, "ssl": ssl},
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
    app.run(host="127.0.0.1", port=port, debug=True)
