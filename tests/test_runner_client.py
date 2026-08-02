"""Tests for the panel↔runner boundary.

runner_client is the only place the panel crosses into privileged territory, so
its validation and its parsing of runner output are both security-relevant.
"""

import os
import stat
import textwrap

import pytest

import runner_client


class TestValidateDomain:
    @pytest.mark.parametrize(
        "domain",
        [
            "example.com",
            "sub.example.com",
            "a-b.example.co.uk",
            "xn--80ak6aa92e.com",
            "9lives.example.org",
        ],
    )
    def test_accepts_real_domains(self, domain):
        assert runner_client.validate_domain(domain) is True

    @pytest.mark.parametrize(
        "domain",
        [
            "",
            None,
            "localhost",                 # single label
            "example.com/../../etc",     # path traversal
            "../../etc/passwd",
            "/etc/passwd",
            "example.com;rm -rf /",      # command separator
            "example.com rm",            # whitespace
            "example.com|cat",
            "example.com&whoami",
            "$(id).com",
            "`id`.com",
            "-example.com",              # leading hyphen
            "example-.com",              # trailing hyphen
            "example..com",              # empty label
            ".example.com",
            "example.com.",
            "exam ple.com",
            "example.com\nrm -rf /",     # newline injection
            "example.com\x00.evil",      # NUL byte
            'ex"ample.com',              # quote (JSON injection attempt)
            "a" * 250 + ".com",          # over 253 characters
            "a" * 64 + ".com",           # label over 63 characters
        ],
    )
    def test_rejects_hostile_and_malformed(self, domain):
        assert runner_client.validate_domain(domain) is False


class TestValidateType:
    def test_accepts_supported_types(self):
        for site_type in ("static", "php", "wordpress"):
            assert runner_client.validate_type(site_type) is True

    @pytest.mark.parametrize(
        "site_type", ["", "STATIC", "node", "php ", "wordpress;rm", None]
    )
    def test_rejects_everything_else(self, site_type):
        assert runner_client.validate_type(site_type) is False


class TestVerbPath:
    def test_unknown_verb_is_refused(self):
        with pytest.raises(runner_client.RunnerError):
            runner_client._verb_path("site_pwn")

    def test_unknown_verb_cannot_escape_the_runner_dir(self):
        with pytest.raises(runner_client.RunnerError):
            runner_client._verb_path("../../bin/sh")


class TestParseLastJson:
    def test_takes_the_last_json_line(self):
        out = 'noise\n{"status":"error"}\n{"status":"ok","domain":"a.com"}\n'
        assert runner_client._parse_last_json(out) == {
            "status": "ok",
            "domain": "a.com",
        }

    def test_ignores_trailing_blank_lines(self):
        assert runner_client._parse_last_json('{"status":"ok"}\n\n  \n') == {
            "status": "ok"
        }

    def test_empty_output_raises(self):
        with pytest.raises(runner_client.RunnerError):
            runner_client._parse_last_json("   \n\n")

    def test_non_json_raises(self):
        with pytest.raises(runner_client.RunnerError):
            runner_client._parse_last_json("Traceback: boom")

    def test_json_array_is_refused(self):
        with pytest.raises(runner_client.RunnerError):
            runner_client._parse_last_json("[1,2,3]")


class TestRunAgainstFakeVerbs:
    """Exercise _run end to end against stub scripts (no sudo, no root)."""

    @pytest.fixture()
    def fake_runner_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HZ_NO_SUDO", "1")
        monkeypatch.setenv("HZ_RUNNER_DIR", str(tmp_path))
        return tmp_path

    def _write_verb(self, directory, name, body):
        path = directory / "{}.sh".format(name)
        path.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body))
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return path

    def test_successful_verb_is_parsed(self, fake_runner_dir):
        self._write_verb(
            fake_runner_dir,
            "site_create",
            """
            echo "provisioning..."
            echo '{"status":"ok","domain":"a.com","url":"http://a.com/"}'
            """,
        )
        result, log = runner_client.site_create("a.com", "static")
        assert result["status"] == "ok"
        assert result["domain"] == "a.com"
        assert "provisioning..." in log

    def test_failing_verb_without_json_raises(self, fake_runner_dir):
        self._write_verb(
            fake_runner_dir,
            "site_delete",
            """
            echo "something exploded" >&2
            exit 1
            """,
        )
        with pytest.raises(runner_client.RunnerError):
            runner_client.site_delete("a.com")

    def test_missing_verb_script_raises(self, fake_runner_dir):
        with pytest.raises(runner_client.RunnerError):
            runner_client.site_list()

    def test_invalid_domain_never_reaches_the_runner(self, fake_runner_dir):
        # No stub script exists; a RunnerError proves validation ran first
        # rather than the process failing to spawn.
        with pytest.raises(runner_client.RunnerError, match="invalid domain"):
            runner_client.site_create("evil.com; rm -rf /", "static")

    def test_arguments_are_passed_as_argv_not_a_shell_string(self, fake_runner_dir):
        """The verb must receive discrete argv entries — never a shell line."""
        self._write_verb(
            fake_runner_dir,
            "site_create",
            """
            printf '{"status":"ok","argc":"%s","arg1":"%s","arg2":"%s","arg3":"%s"}\\n' \\
                "$#" "$1" "$2" "${3:-}"
            """,
        )
        result, _log = runner_client.site_create("a.com", "php", ssl=True)
        assert result["argc"] == "3"
        assert result["arg1"] == "a.com"
        assert result["arg2"] == "php"
        assert result["arg3"] == "--ssl"

    def test_ssl_flag_omitted_when_not_requested(self, fake_runner_dir):
        self._write_verb(
            fake_runner_dir,
            "site_create",
            """
            printf '{"status":"ok","argc":"%s"}\\n' "$#"
            """,
        )
        result, _log = runner_client.site_create("a.com", "php", ssl=False)
        assert result["argc"] == "2"


class TestSudoUsage:
    def test_sudo_is_used_by_default(self, monkeypatch):
        monkeypatch.delenv("HZ_NO_SUDO", raising=False)
        assert runner_client._use_sudo() is True

    def test_sudo_can_be_disabled_for_local_dev(self, monkeypatch):
        monkeypatch.setenv("HZ_NO_SUDO", "1")
        assert runner_client._use_sudo() is False

    def test_default_runner_dir_is_the_installed_path(self, monkeypatch):
        monkeypatch.delenv("HZ_RUNNER_DIR", raising=False)
        assert runner_client.runner_dir() == "/opt/hostzilla/runner"
        assert os.path.isabs(runner_client.runner_dir())
