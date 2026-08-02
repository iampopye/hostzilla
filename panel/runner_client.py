"""Single boundary between the panel and the privileged runner verbs.

The panel NEVER runs arbitrary shell. It only invokes the three allowlisted
scripts via `sudo /opt/hostzilla/runner/<verb>.sh ...`, captures stdout, and
parses the LAST stdout line as a JSON object (the documented contract).

Runner directory is overridable with HZ_RUNNER_DIR for dev/testing.
"""

import json
import os
import re
import shutil
import subprocess

DEFAULT_RUNNER_DIR = "/opt/hostzilla/runner"

VALID_TYPES = {"static", "php", "wordpress"}

# Conservative domain validation in the panel too (runner is authoritative).
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)"
    r"(?:\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$"
)

# Verbs and how many positional args (beyond flags) they expect, for clarity.
VERBS = {"site_create", "site_delete", "site_list"}


class RunnerError(Exception):
    pass


def runner_dir():
    return os.environ.get("HZ_RUNNER_DIR", DEFAULT_RUNNER_DIR)


def _use_sudo():
    """Skip sudo when HZ_NO_SUDO=1 (handy for local dev with mock scripts)."""
    return os.environ.get("HZ_NO_SUDO", "") not in ("1", "true", "yes")


def validate_domain(domain):
    return bool(domain) and bool(_DOMAIN_RE.match(domain))


def validate_type(site_type):
    return site_type in VALID_TYPES


def _verb_path(verb):
    if verb not in VERBS:
        raise RunnerError("unknown verb: {}".format(verb))
    return os.path.join(runner_dir(), "{}.sh".format(verb))


def _parse_last_json(stdout):
    """Return the last line of stdout parsed as a JSON object."""
    last = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        last = line
    if last is None:
        raise RunnerError("runner produced no output")
    try:
        obj = json.loads(last)
    except json.JSONDecodeError as exc:
        raise RunnerError("runner output not JSON: {}".format(exc))
    if not isinstance(obj, dict):
        raise RunnerError("runner JSON was not an object")
    return obj


def _run(verb, args, timeout=900):
    """Execute a verb, returning (result_dict, combined_log)."""
    path = _verb_path(verb)
    cmd = []
    if _use_sudo():
        sudo = shutil.which("sudo") or "sudo"
        cmd.append(sudo)
    cmd.append(path)
    cmd.extend(args)

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RunnerError("runner not found: {}".format(exc))
    except subprocess.TimeoutExpired:
        raise RunnerError("runner timed out after {}s".format(timeout))

    log = proc.stdout or ""
    try:
        result = _parse_last_json(log)
    except RunnerError:
        if proc.returncode != 0:
            raise RunnerError(
                "runner exited {} with no JSON result".format(proc.returncode)
            )
        raise
    return result, log


# ---------------------------------------------------------------------------
# Public verb wrappers
# ---------------------------------------------------------------------------
def site_create(domain, site_type, ssl=False):
    if not validate_domain(domain):
        raise RunnerError("invalid domain: {}".format(domain))
    if not validate_type(site_type):
        raise RunnerError("invalid site type: {}".format(site_type))
    args = [domain, site_type]
    if ssl:
        args.append("--ssl")
    return _run("site_create", args)


def site_delete(domain, purge_db=True):
    if not validate_domain(domain):
        raise RunnerError("invalid domain: {}".format(domain))
    args = [domain]
    if purge_db:
        args.append("--purge-db")
    return _run("site_delete", args)


def site_list():
    """Synchronous read-only call used by the Sites page."""
    result, _log = _run("site_list", [], timeout=60)
    return result
