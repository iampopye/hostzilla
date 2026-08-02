"""Hostzilla panel data layer (SQLite via stdlib sqlite3).

Tables:
  users(id, username, password_hash, role)
  jobs(id, type, domain, params_json, status, result_json, log,
       created_at, finished_at)

DB location resolution order:
  1. explicit set_db_path()
  2. DB_PATH from /etc/hostzilla/hostzilla.conf
  3. HZ_DB_PATH env var
  4. dev fallback: <panel>/data/hostzilla.db
"""

import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager

from werkzeug.security import check_password_hash, generate_password_hash

from config import get_config

_LOCK = threading.Lock()
_DB_PATH = None

# A single provisioning log can be large (a WordPress core download, certbot
# output). Cap what we persist so one runaway job cannot bloat the panel DB.
MAX_LOG_BYTES = 256 * 1024

# Columns update_job() may write. The UPDATE statement has to interpolate column
# names because SQL cannot parameterise them, so the permitted names must come
# from this constant and never from a caller-supplied string.
_UPDATABLE_JOB_COLUMNS = frozenset(
    {"type", "domain", "params_json", "status", "result_json", "log", "finished_at"}
)

ACTIVE_STATUSES = ("queued", "running")


def _default_dev_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "data", "hostzilla.db")


def resolve_db_path():
    """Pick the DB path from config/env with a dev-friendly fallback."""
    cfg = get_config()
    path = cfg.get("DB_PATH") or os.environ.get("HZ_DB_PATH")
    if not path:
        path = _default_dev_path()
    return path


def set_db_path(path):
    global _DB_PATH
    _DB_PATH = path


def get_db_path():
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = resolve_db_path()
    return _DB_PATH


@contextmanager
def get_conn():
    """Yield a sqlite3 connection with row factory and FK enforcement."""
    path = get_db_path()
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'admin'
);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,
    domain      TEXT NOT NULL,
    params_json TEXT,
    status      TEXT NOT NULL DEFAULT 'queued',
    result_json TEXT,
    log         TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_domain ON jobs(domain);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);

-- Dedup is enforced by the DATABASE, not only by an in-process lock. gunicorn
-- runs four workers, each with its own lock, so two simultaneous requests
-- handled by different workers could otherwise both queue a create for the
-- same domain and race each other through the provisioning steps.
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active_unique
    ON jobs(type, domain) WHERE status IN ('queued','running');
"""


def init_db():
    """Create the schema if it does not yet exist."""
    with _LOCK:
        with get_conn() as conn:
            conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def create_user(username, password, role="admin"):
    """Insert a user with a werkzeug-hashed password. Returns row id."""
    pw_hash = generate_password_hash(password)
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
            (username, pw_hash, role),
        )
        return cur.lastrowid


def get_user_by_username(username):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def count_users():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]


def set_password(user_id, new_password):
    """Replace a user's password hash. Returns True when a row was updated."""
    pw_hash = generate_password_hash(new_password)
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, user_id)
        )
        return cur.rowcount > 0


def verify_user(username, password):
    """Return the user dict on success, else None."""
    user = get_user_by_username(username)
    if not user:
        return None
    if check_password_hash(user["password_hash"], password):
        return user
    return None


def bootstrap_admin(username="admin"):
    """Create the first admin with a RANDOM password. Returns (user, password).

    Returns None when any user already exists.

    This deliberately has no well-known fallback password. The previous
    ensure_default_admin() created admin/admin whenever the users table was
    empty, and the panel called it on every startup — so any install whose
    installer bootstrap step had failed came up with default credentials on a
    box that is, by design, reachable from the internet.
    """
    if count_users() != 0:
        return None
    password = secrets.token_urlsafe(18)
    create_user(username, password, role="admin")
    return username, password


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
def _truncate_log(text):
    if text is None:
        return None
    if len(text) <= MAX_LOG_BYTES:
        return text
    return text[:MAX_LOG_BYTES] + "\n... [truncated by Hostzilla panel]"


def create_job(job_type, domain, params_json=None, status="queued"):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO jobs (type, domain, params_json, status) "
            "VALUES (?,?,?,?)",
            (job_type, domain, params_json, status),
        )
        return cur.lastrowid


def update_job(job_id, **fields):
    if not fields:
        return
    unknown = set(fields) - _UPDATABLE_JOB_COLUMNS
    if unknown:
        raise ValueError("not an updatable job column: {}".format(sorted(unknown)))
    if "log" in fields:
        fields["log"] = _truncate_log(fields["log"])
    cols = ", ".join("{} = ?".format(k) for k in fields)
    vals = list(fields.values()) + [job_id]
    with get_conn() as conn:
        conn.execute("UPDATE jobs SET {} WHERE id = ?".format(cols), vals)


def get_job(job_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def list_jobs(limit=100):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def recent_jobs(limit=8):
    return list_jobs(limit=limit)


def find_active_job(job_type, domain):
    """Return a queued/running job for (type, domain) if one exists."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE type = ? AND domain = ? "
            "AND status IN ('queued','running') ORDER BY id DESC LIMIT 1",
            (job_type, domain),
        ).fetchone()
        return dict(row) if row else None


def reap_stale_jobs():
    """Fail jobs left queued/running by a previous process. Returns the count.

    The worker pool lives inside the panel process, so a restart, crash or
    redeploy abandons whatever it was running. Those rows previously stayed
    'running' forever, which also permanently blocked the dedup check from ever
    queueing that (type, domain) pair again.
    """
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = 'error', "
            "result_json = COALESCE(result_json, "
            "'{\"status\":\"error\",\"message\":\"interrupted by panel restart\"}'), "
            "finished_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') "
            "WHERE status IN ('queued','running')"
        )
        return cur.rowcount


def job_counts():
    """Aggregate counts by status for the dashboard."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS c FROM jobs GROUP BY status"
        ).fetchall()
    out = {"queued": 0, "running": 0, "ok": 0, "error": 0, "total": 0}
    for r in rows:
        out[r["status"]] = r["c"]
        out["total"] += r["c"]
    return out
