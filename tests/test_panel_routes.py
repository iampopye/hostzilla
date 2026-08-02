"""Route-level tests: authentication, CSRF, redirects and job plumbing."""

import pytest


class TestAuthenticationRequired:
    @pytest.mark.parametrize(
        "path", ["/", "/sites", "/sites/create", "/jobs", "/jobs/1", "/account"]
    )
    def test_protected_pages_redirect_to_login(self, client, path):
        resp = client.get(path)
        assert resp.status_code in (301, 302)
        assert "/login" in resp.headers["Location"]

    def test_healthz_is_public(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"


class TestLogin:
    def test_valid_credentials_sign_in(self, client, admin_password, csrf_from):
        resp = client.post(
            "/login",
            data={
                "username": "admin",
                "password": admin_password,
                "csrf_token": csrf_from(client, "/login"),
            },
        )
        assert resp.status_code in (301, 302)
        assert "/login" not in resp.headers["Location"]

    def test_wrong_password_is_rejected(self, client, admin_password, csrf_from):
        resp = client.post(
            "/login",
            data={
                "username": "admin",
                "password": "wrong",
                "csrf_token": csrf_from(client, "/login"),
            },
        )
        assert resp.status_code == 200
        assert b"Invalid username or password" in resp.data

    def test_unknown_user_is_rejected(self, client, admin_password, csrf_from):
        resp = client.post(
            "/login",
            data={
                "username": "nobody",
                "password": "whatever",
                "csrf_token": csrf_from(client, "/login"),
            },
        )
        assert resp.status_code == 200
        assert b"Invalid username or password" in resp.data

    def test_login_without_csrf_token_is_refused(self, client, admin_password):
        resp = client.post(
            "/login", data={"username": "admin", "password": admin_password}
        )
        assert resp.status_code == 400

    def test_repeated_failures_are_throttled(
        self, client, admin_password, csrf_from
    ):
        token = csrf_from(client, "/login")
        last = None
        for _ in range(12):
            last = client.post(
                "/login",
                data={
                    "username": "admin",
                    "password": "wrong",
                    "csrf_token": token,
                },
            )
        assert last.status_code == 429

    def test_open_redirect_is_blocked(self, client, admin_password, csrf_from):
        resp = client.post(
            "/login?next=//evil.example",
            data={
                "username": "admin",
                "password": admin_password,
                "csrf_token": csrf_from(client, "/login"),
            },
        )
        assert resp.status_code in (301, 302)
        assert "evil.example" not in resp.headers["Location"]

    def test_safe_next_is_honoured(self, client, admin_password, csrf_from):
        resp = client.post(
            "/login?next=/jobs",
            data={
                "username": "admin",
                "password": admin_password,
                "csrf_token": csrf_from(client, "/login"),
            },
        )
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"].endswith("/jobs")


class TestCsrfOnStateChangingRoutes:
    def test_site_delete_without_token_is_refused(self, logged_in):
        resp = logged_in.post("/sites/example.com/delete")
        assert resp.status_code == 400

    def test_site_create_without_token_is_refused(self, logged_in):
        resp = logged_in.post(
            "/sites/create", data={"domain": "example.com", "type": "static"}
        )
        assert resp.status_code == 400

    def test_wrong_token_is_refused(self, logged_in):
        resp = logged_in.post(
            "/sites/example.com/delete", data={"csrf_token": "not-the-token"}
        )
        assert resp.status_code == 400

    def test_logout_without_token_is_refused(self, logged_in):
        resp = logged_in.post("/logout")
        assert resp.status_code == 400

    def test_get_requests_need_no_token(self, logged_in):
        assert logged_in.get("/sites").status_code == 200


class TestSiteCreateValidation:
    def test_invalid_domain_is_rejected(self, logged_in, csrf_from):
        resp = logged_in.post(
            "/sites/create",
            data={
                "domain": "not a domain",
                "type": "static",
                "csrf_token": csrf_from(logged_in, "/sites/create"),
            },
        )
        assert resp.status_code == 400
        assert b"valid fully-qualified domain" in resp.data

    def test_invalid_type_is_rejected(self, logged_in, csrf_from):
        resp = logged_in.post(
            "/sites/create",
            data={
                "domain": "example.com",
                "type": "nodejs",
                "csrf_token": csrf_from(logged_in, "/sites/create"),
            },
        )
        assert resp.status_code == 400
        assert b"valid site type" in resp.data

    def test_shell_metacharacters_never_queue_a_job(self, logged_in, csrf_from):
        import models

        resp = logged_in.post(
            "/sites/create",
            data={
                "domain": "example.com; rm -rf /",
                "type": "static",
                "csrf_token": csrf_from(logged_in, "/sites/create"),
            },
        )
        assert resp.status_code == 400
        assert models.list_jobs() == []

    def test_delete_of_invalid_domain_is_refused(self, logged_in, csrf_from):
        resp = logged_in.post(
            "/sites/notadomain/delete",
            data={"csrf_token": csrf_from(logged_in, "/sites")},
        )
        assert resp.status_code == 400


class TestAccountPasswordChange:
    def test_page_renders(self, logged_in):
        resp = logged_in.get("/account")
        assert resp.status_code == 200
        assert b"Change password" in resp.data

    def test_wrong_current_password_is_refused(
        self, logged_in, csrf_from
    ):
        resp = logged_in.post(
            "/account",
            data={
                "current_password": "nope",
                "new_password": "a-brand-new-password",
                "confirm_password": "a-brand-new-password",
                "csrf_token": csrf_from(logged_in, "/account"),
            },
        )
        assert resp.status_code == 400
        assert b"current password is not correct" in resp.data

    def test_short_password_is_refused(
        self, logged_in, admin_password, csrf_from
    ):
        resp = logged_in.post(
            "/account",
            data={
                "current_password": admin_password,
                "new_password": "short",
                "confirm_password": "short",
                "csrf_token": csrf_from(logged_in, "/account"),
            },
        )
        assert resp.status_code == 400
        assert b"at least 12 characters" in resp.data

    def test_mismatched_confirmation_is_refused(
        self, logged_in, admin_password, csrf_from
    ):
        resp = logged_in.post(
            "/account",
            data={
                "current_password": admin_password,
                "new_password": "a-brand-new-password",
                "confirm_password": "a-different-password",
                "csrf_token": csrf_from(logged_in, "/account"),
            },
        )
        assert resp.status_code == 400
        assert b"do not match" in resp.data

    def test_password_is_actually_changed(
        self, logged_in, admin_password, csrf_from
    ):
        import models

        new_password = "a-brand-new-password"
        resp = logged_in.post(
            "/account",
            data={
                "current_password": admin_password,
                "new_password": new_password,
                "confirm_password": new_password,
                "csrf_token": csrf_from(logged_in, "/account"),
            },
        )
        assert resp.status_code in (301, 302)
        assert models.verify_user("admin", new_password) is not None
        assert models.verify_user("admin", admin_password) is None
