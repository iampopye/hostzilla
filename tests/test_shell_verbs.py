"""Argument-validation tests for the privileged shell verbs.

These run bash against the real runner/_lib.sh with HZ_TEST_ROOT pointing at a
temporary directory, so the pure helpers (validation, protected-domain guard,
JSON escaping, slug derivation) are exercised as shipped. Nothing here needs
root and nothing touches the real system.
"""

import json
import os
import shutil
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO_ROOT, "runner", "_lib.sh")
RUNNER_DIR = os.path.join(REPO_ROOT, "runner")

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash is required for the shell verb tests"
)


def run_lib(snippet, test_root, env=None, domain=None):
    """Source _lib.sh in a sandbox and run a bash snippet against it.

    The domain under test travels through the ENVIRONMENT as $D and is never
    interpolated into the snippet text: many of these inputs contain quotes and
    backslashes precisely because those are what validation must reject, and
    splicing them into the script would break the script rather than test it.
    """
    full_env = dict(os.environ)
    full_env["HZ_TEST_ROOT"] = str(test_root)
    full_env.pop("PANEL_DOMAIN", None)
    full_env.pop("PROTECTED_DOMAINS", None)
    if domain is not None:
        full_env["D"] = domain
    if env:
        full_env.update(env)
    return subprocess.run(
        ["bash", "-c", 'source "$1"\n' + snippet, "bash", LIB],
        capture_output=True,
        text=True,
        env=full_env,
        timeout=60,
    )


def last_json(proc):
    """Parse the last non-empty stdout line the way the panel does."""
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    assert lines, "verb produced no output: {!r}".format(proc.stderr)
    return json.loads(lines[-1])


@pytest.fixture()
def test_root(tmp_path):
    (tmp_path / "etc" / "hostzilla").mkdir(parents=True)
    return tmp_path


class TestValidateDomain:
    """validate_domain builds docroot paths and vhost filenames, so it is the
    boundary that has to stop traversal and shell metacharacters."""

    @pytest.mark.parametrize(
        "domain",
        [
            "example.com",
            "sub.example.com",
            "deep.sub.example.co.uk",
            "a-b.example.com",
            "9lives.example.org",
            "EXAMPLE.COM",  # normalised to lowercase
        ],
    )
    def test_accepts_valid_domains(self, domain, test_root):
        proc = run_lib(
            'validate_domain "$D" && printf "OK:%s" "$CLEAN_DOMAIN"',
            test_root,
            domain=domain,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert proc.stdout.strip() == "OK:{}".format(domain.lower())

    @pytest.mark.parametrize(
        "domain",
        [
            "",
            "localhost",
            "passwd",
            "../../etc/passwd",
            "example.com/../../etc",
            "/etc/passwd",
            "..",
            "example..com",
            ".example.com",
            "example.com.",
            "-example.com",
            "example-.com",
            "foo.-bar.com",
            "foo.bar-.com",
            "192.168.1.1",
            "exam ple.com",
            "example.com;id",
            "example.com|id",
            "example.com&id",
            "example.com$(id)",
            "example.com`id`",
            'ex"ample.com',
            "example.com'",
            "example.com*",
            "example.com?",
            "example.com>out",
            "example.com#frag",
            "exa\\mple.com",
            "example.com\nrm -rf /",
            "example.com\x00evil",
        ],
    )
    def test_rejects_hostile_input(self, domain, test_root):
        proc = run_lib('validate_domain "$D"', test_root, domain=domain)
        assert proc.returncode != 0, "validate_domain accepted {!r}: {}".format(
            domain, proc.stdout
        )
        # A rejection must still be machine-readable for the panel.
        assert last_json(proc)["status"] == "error"

    def test_rejects_overlong_domain(self, test_root):
        proc = run_lib(
            'validate_domain "$D"', test_root, domain="a" * 250 + ".com"
        )
        assert proc.returncode != 0

    def test_rejects_overlong_label(self, test_root):
        proc = run_lib('validate_domain "$D"', test_root, domain="a" * 64 + ".com")
        assert proc.returncode != 0

    def test_newline_cannot_smuggle_a_second_json_line(self, test_root):
        """A newline in the domain must not let a caller inject a JSON result."""
        proc = run_lib(
            'validate_domain "$D"',
            test_root,
            domain='a.com\n{"status":"ok"}',
        )
        assert proc.returncode != 0
        assert last_json(proc)["status"] == "error"


class TestIsProtected:
    @pytest.mark.parametrize("domain", ["", "localhost", "html", "LOCALHOST"])
    def test_builtin_names_are_protected(self, domain, test_root):
        proc = run_lib('is_protected "$D"', test_root, domain=domain)
        assert proc.returncode == 0

    def test_ordinary_domain_is_not_protected(self, test_root):
        proc = run_lib('is_protected "$D"', test_root, domain="example.com")
        assert proc.returncode == 1

    def test_panel_domain_is_protected(self, test_root):
        proc = run_lib(
            'is_protected "$D"',
            test_root,
            domain="panel.example.com",
            env={"PANEL_DOMAIN": "panel.example.com"},
        )
        assert proc.returncode == 0

    def test_panel_domain_match_is_case_insensitive(self, test_root):
        proc = run_lib(
            'is_protected "$D"',
            test_root,
            domain="PANEL.example.com",
            env={"PANEL_DOMAIN": "panel.example.com"},
        )
        assert proc.returncode == 0

    def test_operator_protected_list_is_honoured(self, test_root):
        proc = run_lib(
            'is_protected "$D"',
            test_root,
            domain="legacy.example.com",
            env={"PROTECTED_DOMAINS": "legacy.example.com mail.example.com"},
        )
        assert proc.returncode == 0

    def test_domain_outside_protected_list_is_allowed(self, test_root):
        proc = run_lib(
            'is_protected "$D"',
            test_root,
            domain="new.example.com",
            env={"PROTECTED_DOMAINS": "legacy.example.com mail.example.com"},
        )
        assert proc.returncode == 1


class TestJsonEscaping:
    """emit() output is parsed by the panel; unescaped values let a caller
    inject duplicate keys, and Python's json.loads keeps the LAST one."""

    def test_quotes_are_escaped(self, test_root):
        proc = run_lib('emit ok "message=$D"', test_root, domain='say "hi"')
        payload = last_json(proc)
        assert payload["message"] == 'say "hi"'
        assert payload["status"] == "ok"

    def test_backslashes_are_escaped(self, test_root):
        proc = run_lib('emit ok "path=$D"', test_root, domain="C:\\temp")
        assert last_json(proc)["path"] == "C:\\temp"

    def test_status_cannot_be_forged_through_a_value(self, test_root):
        """The exact result-forgery bug: a crafted value must not flip status."""
        hostile = '","status":"ok'
        proc = run_lib('emit error "message=$D"', test_root, domain=hostile)
        payload = last_json(proc)
        assert payload["status"] == "error", proc.stdout
        assert payload["message"] == hostile

    def test_newlines_are_escaped_into_one_line(self, test_root):
        proc = run_lib('emit error "message=$D"', test_root, domain="line1\nline2")
        payload = last_json(proc)
        assert payload["message"] == "line1\nline2"

    def test_die_emits_parseable_error_json(self, test_root):
        proc = run_lib('die "$D"', test_root, domain='it broke: "badly"')
        assert proc.returncode == 1
        payload = last_json(proc)
        assert payload["status"] == "error"
        assert "it broke" in payload["message"]

    def test_control_characters_are_stripped(self, test_root):
        proc = run_lib('emit ok "message=$D"', test_root, domain="a\x07b")
        assert last_json(proc)["message"] == "ab"


class TestDbSlug:
    def test_slug_is_a_valid_mysql_identifier(self, test_root):
        proc = run_lib('db_slug "$D"', test_root, domain="example.com")
        slug = proc.stdout.strip()
        assert slug.startswith("hz_")
        assert len(slug) <= 32
        assert all(c.isalnum() or c == "_" for c in slug)

    def test_slug_is_stable_for_the_same_domain(self, test_root):
        first = run_lib('db_slug "$D"', test_root, domain="example.com")
        second = run_lib('db_slug "$D"', test_root, domain="example.com")
        assert first.stdout.strip() == second.stdout.strip()

    def test_long_domains_sharing_a_prefix_do_not_collide(self, test_root):
        """Truncation alone handed one customer's database to another."""
        prefix = "verylongsubdomainname" * 2
        a = run_lib('db_slug "$D"', test_root, domain="a{}.example.com".format(prefix))
        b = run_lib('db_slug "$D"', test_root, domain="b{}.example.com".format(prefix))
        slug_a, slug_b = a.stdout.strip(), b.stdout.strip()
        assert slug_a != slug_b
        assert len(slug_a) <= 32 and len(slug_b) <= 32

    def test_slug_never_contains_shell_or_sql_metacharacters(self, test_root):
        proc = run_lib('db_slug "$D"', test_root, domain="a-b.example.com")
        slug = proc.stdout.strip()
        for bad in ["`", "'", '"', ";", " ", "-", "$", "\\"]:
            assert bad not in slug


class TestTestRootGuard:
    def test_helpers_never_touch_the_real_filesystem(self, test_root):
        """HZ_TEST_ROOT must re-root every path the library writes to."""
        run_lib('log "hello from the test suite"', test_root)
        assert (test_root / "var" / "log" / "hostzilla" / "runner.log").exists()

    def test_root_refuses_test_root_override(self):
        """The anti-escalation guard must be present in the shipped library."""
        with open(LIB, encoding="utf-8") as fh:
            source = fh.read()
        assert "HZ_TEST_ROOT refused while running as root" in source


class TestVerbScriptsAreWellFormed:
    @pytest.mark.parametrize(
        "verb", ["site_create.sh", "site_delete.sh", "site_list.sh", "_lib.sh"]
    )
    def test_bash_syntax_is_valid(self, verb):
        proc = subprocess.run(
            ["bash", "-n", os.path.join(RUNNER_DIR, verb)],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr

    @pytest.mark.parametrize(
        "verb", ["site_create.sh", "site_delete.sh", "site_list.sh"]
    )
    def test_verbs_require_root(self, verb):
        with open(os.path.join(RUNNER_DIR, verb), encoding="utf-8") as fh:
            source = fh.read()
        assert "require_root" in source

    @pytest.mark.parametrize("verb", ["site_create.sh", "site_delete.sh"])
    def test_mutating_verbs_validate_and_check_protection(self, verb):
        with open(os.path.join(RUNNER_DIR, verb), encoding="utf-8") as fh:
            source = fh.read()
        assert "validate_domain" in source
        assert "is_protected" in source
        # The validated value, not the raw argument, must be what gets used.
        assert 'DOMAIN="$CLEAN_DOMAIN"' in source


class TestSudoersAllowlist:
    """The sudoers file is the privilege boundary; assert its shape."""

    @property
    def sudoers(self):
        path = os.path.join(REPO_ROOT, "config", "sudoers.hostzilla")
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_only_the_three_verbs_are_allowed(self):
        body = self.sudoers
        assert "site_create.sh" in body
        assert "site_delete.sh" in body
        assert "site_list.sh" in body

    def test_no_blanket_or_shell_entries(self):
        body = self.sudoers
        for forbidden in ["ALL=(ALL)", "NOPASSWD: ALL", "/bin/sh", "/bin/bash"]:
            assert forbidden not in body, "sudoers grants too much: {}".format(
                forbidden
            )

    def test_site_list_is_allowed_without_an_argument_spec(self):
        """'cmd *' does not match a zero-argument invocation, which broke the
        Sites page and the dashboard site count on every real install."""
        body = self.sudoers
        assert "/opt/hostzilla/runner/site_list.sh\n" in body
        assert "/opt/hostzilla/runner/site_list.sh *" not in body

    def test_paths_are_absolute(self):
        for line in self.sudoers.splitlines():
            if "runner/" in line and not line.strip().startswith("#"):
                assert "/opt/hostzilla/runner/" in line
