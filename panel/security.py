"""Cross-cutting web security helpers for the Hostzilla panel.

Kept dependency-free on purpose: the panel's whole install story is "a venv with
four packages", and pulling in Flask-WTF just for a CSRF token would add
WTForms and its transitive tree to a privileged host.

Provides:
  * a session-bound CSRF token with constant-time verification
  * a safe redirect check for the ?next= parameter
  * a small fixed-window login throttle
  * the response security headers
"""

import hmac
import secrets
import threading
import time
from urllib.parse import urlparse

from flask import abort, request, session

CSRF_SESSION_KEY = "_csrf_token"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"

# Methods that may change state and therefore require a token.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------
def csrf_token():
    """Return this session's CSRF token, creating it on first use."""
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def _submitted_token():
    return (
        request.form.get(CSRF_FORM_FIELD)
        or request.headers.get(CSRF_HEADER)
        or ""
    )


def validate_csrf():
    """Abort with 400 unless the request carries this session's CSRF token.

    Without this, any page on the internet could POST to /sites/<domain>/delete
    and destroy a logged-in operator's site and database using their ambient
    session cookie.
    """
    expected = session.get(CSRF_SESSION_KEY)
    submitted = _submitted_token()
    if not expected or not submitted:
        abort(400, description="Missing CSRF token.")
    if not hmac.compare_digest(str(expected), str(submitted)):
        abort(400, description="Invalid CSRF token.")


def init_csrf(app):
    """Enforce CSRF on every unsafe request and expose the token to templates."""

    @app.before_request
    def _csrf_protect():
        if request.method in UNSAFE_METHODS:
            validate_csrf()

    @app.context_processor
    def _inject_csrf():
        return {"csrf_token": csrf_token}


# ---------------------------------------------------------------------------
# Redirect safety
# ---------------------------------------------------------------------------
def is_safe_next(target):
    """True only for same-origin, path-relative redirect targets.

    `startswith("/")` alone is not enough: "//evil.example" and "/\\evil.example"
    both start with a slash and both are treated by browsers as protocol-relative
    absolute URLs, which turns the login form into an open redirect.
    """
    if not target or not isinstance(target, str):
        return False
    if "\\" in target or "\n" in target or "\r" in target:
        return False
    if not target.startswith("/"):
        return False
    if target.startswith("//"):
        return False
    parsed = urlparse(target)
    return not parsed.scheme and not parsed.netloc


# ---------------------------------------------------------------------------
# Login throttle
# ---------------------------------------------------------------------------
class LoginThrottle:
    """Fixed-window failure counter, keyed by username and client address.

    In-process and therefore per-gunicorn-worker: it raises the cost of online
    password guessing but is not a distributed rate limiter. Documented as such
    in SECURITY.md rather than overstated here.
    """

    def __init__(self, max_attempts=8, window_seconds=300):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._buckets = {}

    def _now(self):
        return time.monotonic()

    def _prune(self, now):
        stale = [
            key
            for key, (_count, started) in self._buckets.items()
            if now - started > self.window_seconds
        ]
        for key in stale:
            del self._buckets[key]

    def is_blocked(self, key):
        now = self._now()
        with self._lock:
            self._prune(now)
            entry = self._buckets.get(key)
            if not entry:
                return False
            count, started = entry
            if now - started > self.window_seconds:
                del self._buckets[key]
                return False
            return count >= self.max_attempts

    def record_failure(self, key):
        now = self._now()
        with self._lock:
            self._prune(now)
            count, started = self._buckets.get(key, (0, now))
            if now - started > self.window_seconds:
                count, started = 0, now
            self._buckets[key] = (count + 1, started)

    def reset(self, key):
        with self._lock:
            self._buckets.pop(key, None)

    def clear(self):
        with self._lock:
            self._buckets.clear()


# ---------------------------------------------------------------------------
# Response headers
# ---------------------------------------------------------------------------
# The panel serves only its own CSS and JS and embeds no third-party content, so
# it can afford a genuinely restrictive policy. 'unsafe-inline' is allowed for
# script-src only because job_detail.html carries one inline bootstrap call;
# keep it out of the templates and this can tighten further.
CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "object-src 'none'"
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


def init_security_headers(app):
    @app.after_request
    def _headers(response):
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response
