"""Data-layer tests: users, jobs, dedup and the SQL column allowlist."""

import sqlite3

import pytest

import models


class TestUsers:
    def test_create_and_fetch(self, db_path):
        user_id = models.create_user("karan", "a-long-enough-password")
        row = models.get_user_by_id(user_id)
        assert row["username"] == "karan"
        assert row["role"] == "admin"

    def test_password_is_hashed_not_stored(self, db_path):
        models.create_user("karan", "a-long-enough-password")
        row = models.get_user_by_username("karan")
        assert "a-long-enough-password" not in row["password_hash"]

    def test_verify_user(self, db_path):
        models.create_user("karan", "a-long-enough-password")
        assert models.verify_user("karan", "a-long-enough-password") is not None
        assert models.verify_user("karan", "wrong") is None
        assert models.verify_user("nobody", "whatever") is None

    def test_usernames_are_unique(self, db_path):
        models.create_user("karan", "a-long-enough-password")
        with pytest.raises(sqlite3.IntegrityError):
            models.create_user("karan", "another-password")

    def test_set_password(self, db_path):
        user_id = models.create_user("karan", "a-long-enough-password")
        assert models.set_password(user_id, "the-new-password") is True
        assert models.verify_user("karan", "the-new-password") is not None
        assert models.verify_user("karan", "a-long-enough-password") is None

    def test_set_password_for_missing_user(self, db_path):
        assert models.set_password(4242, "irrelevant") is False


class TestBootstrapAdmin:
    def test_creates_admin_with_a_random_password(self, db_path):
        result = models.bootstrap_admin()
        assert result is not None
        username, password = result
        assert username == "admin"
        # The whole point of the fix: never a guessable default.
        assert password != "admin"
        assert len(password) >= 16
        assert models.verify_user("admin", password) is not None

    def test_passwords_differ_between_installs(self, tmp_path):
        models.set_db_path(str(tmp_path / "a.db"))
        models.init_db()
        _, first = models.bootstrap_admin()
        models.set_db_path(str(tmp_path / "b.db"))
        models.init_db()
        _, second = models.bootstrap_admin()
        models.set_db_path(None)
        assert first != second

    def test_is_a_no_op_when_a_user_exists(self, db_path):
        models.create_user("karan", "a-long-enough-password")
        assert models.bootstrap_admin() is None


class TestJobs:
    def test_create_and_get(self, db_path):
        job_id = models.create_job("site_create", "example.com")
        job = models.get_job(job_id)
        assert job["type"] == "site_create"
        assert job["domain"] == "example.com"
        assert job["status"] == "queued"
        assert job["created_at"]

    def test_update_job(self, db_path):
        job_id = models.create_job("site_create", "example.com")
        models.update_job(job_id, status="ok", log="all good")
        job = models.get_job(job_id)
        assert job["status"] == "ok"
        assert job["log"] == "all good"

    def test_update_rejects_unknown_columns(self, db_path):
        """Column names are interpolated into SQL, so they must be allowlisted."""
        job_id = models.create_job("site_create", "example.com")
        with pytest.raises(ValueError):
            models.update_job(job_id, nonexistent_column="x")
        with pytest.raises(ValueError):
            models.update_job(job_id, **{"status = 'ok', domain": "evil"})

    def test_oversized_logs_are_truncated(self, db_path):
        job_id = models.create_job("site_create", "example.com")
        models.update_job(job_id, log="x" * (models.MAX_LOG_BYTES + 5000))
        stored = models.get_job(job_id)["log"]
        assert len(stored) < models.MAX_LOG_BYTES + 200
        assert stored.endswith("[truncated by Hostzilla panel]")

    def test_find_active_job(self, db_path):
        job_id = models.create_job("site_create", "example.com")
        assert models.find_active_job("site_create", "example.com")["id"] == job_id
        assert models.find_active_job("site_delete", "example.com") is None
        models.update_job(job_id, status="ok")
        assert models.find_active_job("site_create", "example.com") is None

    def test_duplicate_active_jobs_are_refused_by_the_database(self, db_path):
        """Per-process locks cannot dedup across gunicorn workers; the index can."""
        models.create_job("site_create", "example.com")
        with pytest.raises(sqlite3.IntegrityError):
            models.create_job("site_create", "example.com")

    def test_a_finished_job_frees_the_domain(self, db_path):
        first = models.create_job("site_create", "example.com")
        models.update_job(first, status="ok")
        second = models.create_job("site_create", "example.com")
        assert second != first

    def test_different_domains_do_not_collide(self, db_path):
        models.create_job("site_create", "a.example.com")
        models.create_job("site_create", "b.example.com")
        assert len(models.list_jobs()) == 2

    def test_job_counts(self, db_path):
        models.update_job(models.create_job("site_create", "a.com"), status="ok")
        models.update_job(models.create_job("site_create", "b.com"), status="error")
        models.create_job("site_create", "c.com")
        counts = models.job_counts()
        assert counts["ok"] == 1
        assert counts["error"] == 1
        assert counts["queued"] == 1
        assert counts["total"] == 3

    def test_list_jobs_is_newest_first_and_limited(self, db_path):
        for i in range(5):
            models.create_job("site_create", "site{}.example.com".format(i))
        jobs = models.list_jobs(limit=3)
        assert len(jobs) == 3
        assert jobs[0]["domain"] == "site4.example.com"


class TestReapStaleJobs:
    def test_interrupted_jobs_are_failed(self, db_path):
        queued = models.create_job("site_create", "a.example.com")
        running = models.create_job("site_delete", "b.example.com")
        models.update_job(running, status="running")
        done = models.create_job("site_create", "c.example.com")
        models.update_job(done, status="ok")

        assert models.reap_stale_jobs() == 2
        assert models.get_job(queued)["status"] == "error"
        assert models.get_job(running)["status"] == "error"
        assert models.get_job(done)["status"] == "ok"

    def test_reaping_frees_the_dedup_slot(self, db_path):
        """A crashed worker used to block that domain forever."""
        models.create_job("site_create", "example.com")
        models.reap_stale_jobs()
        assert models.create_job("site_create", "example.com")

    def test_reaping_records_a_reason(self, db_path):
        job_id = models.create_job("site_create", "example.com")
        models.reap_stale_jobs()
        assert "interrupted" in models.get_job(job_id)["result_json"]
