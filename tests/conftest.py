"""Shared pytest fixtures for the Hostzilla panel test suite.

The panel modules use flat imports (`import models`) because they run with
CWD=/opt/hostzilla/panel in production, so panel/ goes on sys.path here.
"""

import os
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANEL_DIR = os.path.join(REPO_ROOT, "panel")
RUNNER_DIR = os.path.join(REPO_ROOT, "runner")

if PANEL_DIR not in sys.path:
    sys.path.insert(0, PANEL_DIR)

# Never let a developer's real /etc/hostzilla/hostzilla.conf leak into a test
# run, and give the app a stable session key so cookies survive within a test.
os.environ["HZ_CONF_PATH"] = os.path.join(tempfile.gettempdir(), "hz-nonexistent.conf")
os.environ["HZ_SECRET_KEY"] = "test-secret-key-not-used-in-production"
os.environ.setdefault("HZ_NO_SUDO", "1")


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    """Point the data layer at a throwaway SQLite file."""
    import config
    import models

    monkeypatch.setattr(config, "_cache", None, raising=False)
    path = str(tmp_path / "hostzilla.db")
    models.set_db_path(path)
    models.init_db()
    yield path
    models.set_db_path(None)


@pytest.fixture()
def app(db_path, monkeypatch):
    """A Flask app wired to the throwaway DB, with the runner stubbed out."""
    import app as app_module
    import runner_client

    monkeypatch.setattr(
        runner_client, "site_list", lambda: {"status": "ok", "sites": []}
    )

    flask_app = app_module.create_app()
    flask_app.config.update(TESTING=True)
    app_module._login_throttle.clear()
    yield flask_app
    app_module._login_throttle.clear()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def admin_password(app):
    """Give the admin account a known password and return it.

    create_app() calls bootstrap_admin(), which mints an admin with a RANDOM
    password, so this has to overwrite it rather than assume the account is
    absent.
    """
    import models

    password = "correct-horse-battery-staple"
    user = models.get_user_by_username("admin")
    if user:
        models.set_password(user["id"], password)
    else:
        models.create_user("admin", password, role="admin")
    return password


@pytest.fixture()
def logged_in(client, admin_password):
    """A test client with an authenticated session."""
    resp = client.post(
        "/login",
        data={
            "username": "admin",
            "password": admin_password,
            "csrf_token": _csrf_from(client, "/login"),
        },
        follow_redirects=False,
    )
    assert resp.status_code in (301, 302), resp.data[:400]
    return client


def _csrf_from(client, path):
    """Pull the CSRF token out of a rendered form."""
    body = client.get(path).get_data(as_text=True)
    marker = 'name="csrf_token" value="'
    start = body.index(marker) + len(marker)
    return body[start : body.index('"', start)]


@pytest.fixture()
def csrf_from():
    return _csrf_from
