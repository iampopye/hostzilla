"""Keep the browser demo's config templates in sync with the real runner.

docs/_landing.src.html ports the vhost, PHP-FPM pool and SQL templating out of
runner/site_create.sh into JavaScript so a visitor can see exactly what
Hostzilla would write on their server. That claim is only true while the two
agree, so this asserts they do. If you change one, change the other.
"""

import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_CREATE = os.path.join(REPO_ROOT, "runner", "site_create.sh")
LANDING_SRC = os.path.join(REPO_ROOT, "docs", "_landing.src.html")
LIB = os.path.join(REPO_ROOT, "runner", "_lib.sh")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def shell():
    return read(SITE_CREATE)


@pytest.fixture(scope="module")
def landing():
    return read(LANDING_SRC)


def heredoc(source, marker):
    """Extract the body of a <<MARKER ... MARKER heredoc."""
    pattern = re.compile(
        r"<<'?{marker}'?\n(.*?)\n{marker}\n".format(marker=marker), re.DOTALL
    )
    match = pattern.search(source)
    assert match, "heredoc {} not found in site_create.sh".format(marker)
    return match.group(1)


def js_lines(source, function_name):
    """Collect the string literals a demo template function concatenates."""
    start = source.index("function {}(s) {{".format(function_name))
    end = source.index("\n  }", start)
    body = source[start:end]
    return re.findall(r'"((?:[^"\\]|\\.)*)"', body)


def js_template_text(source, function_name):
    """Reconstruct the literal skeleton the JS emits, with values elided."""
    parts = js_lines(source, function_name)
    text = "".join(parts)
    # Unescape the JS string escapes we actually use.
    text = text.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
    return text


class TestPhpFpmPool:
    """The pool template carries the site's DB credentials and its isolation
    settings, so a drift here misrepresents the product's security posture."""

    def test_directives_match(self, shell, landing):
        shell_pool = heredoc(shell, "POOLCONF")
        demo_pool = js_template_text(landing, "poolConfig")

        for directive in [
            "user = www-data",
            "group = www-data",
            "listen.owner = www-data",
            "listen.group = www-data",
            "pm = ondemand",
            "pm.max_children = 5",
            "pm.process_idle_timeout = 10s",
            "pm.max_requests = 500",
            "php_admin_value[upload_tmp_dir] = /tmp",
            "php_admin_flag[expose_php] = off",
        ]:
            assert directive in shell_pool, "{} left site_create.sh".format(directive)
            assert directive in demo_pool, "{} missing from the demo".format(directive)

    def test_open_basedir_confinement_is_advertised_accurately(self, shell, landing):
        assert "php_admin_value[open_basedir]" in heredoc(shell, "POOLCONF")
        assert "php_admin_value[open_basedir]" in js_template_text(
            landing, "poolConfig"
        )
        assert ":/tmp:/usr/share/php" in js_template_text(landing, "poolConfig")

    def test_credentials_are_exposed_the_same_way(self, shell, landing):
        shell_pool = heredoc(shell, "POOLCONF")
        demo_pool = js_template_text(landing, "poolConfig")
        for key in ["env[HZ_DB_NAME]", "env[HZ_DB_USER]", "env[HZ_DB_PASS]"]:
            assert key in shell_pool
            assert key in demo_pool


class TestApacheVhost:
    def test_security_directives_match(self, shell, landing):
        shell_vhost = heredoc(shell, "VH")
        demo_vhost = js_template_text(landing, "vhostConfig")

        for directive in [
            "Require all granted",
            "AllowOverride All",
            "Options -Indexes +FollowSymLinks",
            "Require all denied",
            "DirectoryIndex index.php index.html",
        ]:
            assert directive in shell_vhost, "{} left site_create.sh".format(directive)
            assert directive in demo_vhost, "{} missing from the demo".format(directive)

    def test_dotfile_denial_is_present_in_both(self, shell, landing):
        shell_vhost = heredoc(shell, "VH")
        demo_vhost = js_template_text(landing, "vhostConfig")
        assert 'FilesMatch "^\\.' in shell_vhost
        assert 'FilesMatch "^\\.' in demo_vhost
        # .well-known must stay reachable or ACME renewals break.
        assert "well-known" in shell_vhost
        assert "well-known" in demo_vhost

    def test_fastcgi_handler_matches(self, shell, landing):
        assert 'SetHandler "proxy:unix:' in heredoc(shell, "VH")
        assert 'SetHandler "proxy:unix:' in js_template_text(landing, "vhostConfig")


class TestSql:
    def test_statements_match(self, shell, landing):
        demo_sql = js_template_text(landing, "sqlStatements")
        for fragment in [
            "CREATE DATABASE IF NOT EXISTS",
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci",
            "CREATE USER IF NOT EXISTS",
            "ALTER USER",
            "GRANT ALL PRIVILEGES ON",
            "FLUSH PRIVILEGES;",
        ]:
            assert fragment in shell, "{} left site_create.sh".format(fragment)
            assert fragment in demo_sql, "{} missing from the demo".format(fragment)

    def test_grant_is_scoped_to_the_single_database(self, shell, landing):
        # A demo that showed a wider grant than reality would be misleading in
        # exactly the direction that matters.
        assert "GRANT ALL PRIVILEGES ON `$DB`.* TO" in shell
        assert "GRANT ALL PRIVILEGES ON `\" + s.db + \"`.* TO" in read(LANDING_SRC)


class TestManifest:
    def test_fields_match(self, shell, landing):
        shell_manifest = heredoc(shell, "ENV")
        demo_manifest = js_template_text(landing, "manifestFile")
        for field in [
            "DOMAIN=",
            "TYPE=",
            "DOCROOT=",
            "DB_NAME=",
            "DB_USER=",
            "DB_PASS=",
            "PHP_SOCK=",
            "PHP_POOL=",
            "CREATED=",
        ]:
            assert field in shell_manifest
            assert field in demo_manifest


class TestPathDerivation:
    """Paths shown in the demo must be the paths the runner actually uses."""

    @pytest.mark.parametrize(
        "fragment",
        [
            "/var/www/",
            "/run/php/hz-",
            "/etc/apache2/sites-available/",
            "/etc/hostzilla/sites/",
            "fpm/pool.d/hz-",
        ],
    )
    def test_path_shapes_appear_in_both(self, shell, landing, fragment):
        assert fragment in shell or fragment in read(LIB)
        assert fragment in landing


class TestValidationPort:
    """The demo advertises that it uses the runner's real validation rules."""

    def test_demo_enforces_the_same_rules(self, landing):
        for rule in [
            "domain too long",
            "invalid domain (empty label)",
            "invalid domain (leading/trailing dot)",
            "invalid domain (must be fully qualified)",
            "invalid domain (label too long)",
            "invalid domain (label hyphen)",
            "invalid domain (numeric TLD)",
        ]:
            assert rule in landing, "demo is missing the rule: {}".format(rule)

    def test_every_runner_rejection_reason_is_ported(self, landing):
        """Any new rejection reason in _lib.sh must reach the demo too."""
        lib = read(LIB)
        reasons = set(re.findall(r'die "(invalid domain[^"]*?)(?::| \$d|")', lib))
        for reason in reasons:
            cleaned = reason.rstrip(':" ')
            assert cleaned in landing, (
                "validate_domain rejects with {!r} but the demo does not".format(
                    cleaned
                )
            )

    def test_db_slug_shape_matches(self, landing):
        lib = read(LIB)
        assert "cut -c1-19" in lib
        assert "sha256sum" in lib
        assert "cut -c1-8" in lib
        # The JS port must use the same widths or identifiers diverge.
        assert ".slice(0, 19)" in landing
        assert ".slice(0, 8)" in landing
        assert '"hz_" + base + "_"' in landing


class TestDemoHonesty:
    """The demo must never read as a live system."""

    def test_states_it_is_browser_only(self, landing):
        assert "runs entirely in your browser" in landing
        assert "No server is contacted" in landing

    def test_uses_only_documentation_safe_hosts(self, landing):
        """No real host, IP or credential may appear in the repo."""
        # RFC 2606 / RFC 5737 reserved names and ranges only.
        for real_ish in ["192.168.", "10.0.0.", "localhost:", "root@"]:
            assert real_ish not in landing, "suspicious host reference: {}".format(
                real_ish
            )
        assert "203.0.113." in landing  # TEST-NET-3, reserved for documentation
