"""Tests for the panel's web-security helpers."""

import pytest

import security


class TestIsSafeNext:
    """?next= must never become an open redirect."""

    @pytest.mark.parametrize(
        "target",
        ["/", "/sites", "/jobs/12", "/sites/create?type=php"],
    )
    def test_accepts_same_origin_paths(self, target):
        assert security.is_safe_next(target) is True

    @pytest.mark.parametrize(
        "target",
        [
            "//evil.example",          # protocol-relative: the original bug
            "///evil.example",
            "http://evil.example",
            "https://evil.example/x",
            "/\\evil.example",         # backslash is normalised by some browsers
            "javascript:alert(1)",
            "evil.example",
            "",
            None,
            "/path\nSet-Cookie: x=1",  # header injection via a stray newline
        ],
    )
    def test_rejects_offsite_and_malformed(self, target):
        assert security.is_safe_next(target) is False


class TestLoginThrottle:
    def test_blocks_after_max_attempts(self):
        throttle = security.LoginThrottle(max_attempts=3, window_seconds=300)
        key = "admin|203.0.113.5"
        assert throttle.is_blocked(key) is False
        for _ in range(3):
            throttle.record_failure(key)
        assert throttle.is_blocked(key) is True

    def test_success_resets_the_counter(self):
        throttle = security.LoginThrottle(max_attempts=2, window_seconds=300)
        key = "admin|203.0.113.5"
        throttle.record_failure(key)
        throttle.reset(key)
        throttle.record_failure(key)
        assert throttle.is_blocked(key) is False

    def test_counters_are_isolated_per_key(self):
        throttle = security.LoginThrottle(max_attempts=2, window_seconds=300)
        for _ in range(2):
            throttle.record_failure("admin|198.51.100.1")
        assert throttle.is_blocked("admin|198.51.100.1") is True
        assert throttle.is_blocked("admin|198.51.100.2") is False

    def test_window_expiry_unblocks(self, monkeypatch):
        throttle = security.LoginThrottle(max_attempts=1, window_seconds=60)
        now = [1000.0]
        monkeypatch.setattr(throttle, "_now", lambda: now[0])
        throttle.record_failure("admin|-")
        assert throttle.is_blocked("admin|-") is True
        now[0] += 61
        assert throttle.is_blocked("admin|-") is False


class TestSecurityHeaders:
    def test_headers_present_on_responses(self, client):
        resp = client.get("/healthz")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]
        assert "object-src 'none'" in resp.headers["Content-Security-Policy"]


class TestSessionCookieConfig:
    def test_cookie_flags(self, app):
        assert app.config["SESSION_COOKIE_HTTPONLY"] is True
        assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
