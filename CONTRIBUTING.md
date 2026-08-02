# Contributing to Hostzilla

Thanks for being here. Hostzilla is a hosting control panel that runs privileged
operations on somebody's real server, so the bar for correctness is high — but
the bar for *asking questions* is on the floor. A half-finished patch with a good
question attached is genuinely welcome.

This guide gets you from a clean checkout to a merged pull request.

---

## Before your first pull request: the CLA

Hostzilla is dual-licensed — [AGPL-3.0](LICENSE) for everyone, and a
[commercial licence](COMMERCIAL.md) for organisations that cannot accept the
AGPL's source-sharing obligations. Selling that commercial licence is what funds
the project.

That only works if a single party holds the rights to license the whole
codebase. A DCO is not enough here: a DCO certifies that you had the right to
submit your patch, but it does not grant anyone the rights needed to offer that
patch under a second, non-AGPL licence. So Hostzilla uses a **CLA**, not a DCO.

**You keep your copyright.** You are granting a licence *alongside* your own
rights, not instead of them. Read the whole thing — it is short and it does not
hide anything: **[CLA.md](CLA.md)**.

Signing is two steps, once per contributor:

1. Sign off every commit:

   ```bash
   git commit -s -m "Fix the thing"
   ```

   `-s` appends `Signed-off-by: Your Name <your@email>` using your git identity.

2. On your **first** pull request only, leave one comment:

   ```
   I have read the CLA and I agree to it.
   ```

That is it. Never again on later PRs.

If you are not comfortable with the CLA, that is a completely reasonable
position. Please open an issue describing the fix instead — we would still much
rather have your knowledge than not.

---

## Repository layout

Read this before you go looking for a file:

| Path | What lives there |
| --- | --- |
| `panel/` | The Flask web app. Runs as the unprivileged `hostzilla` user. Flat imports (`import models`) because production runs with `CWD=/opt/hostzilla/panel`. |
| `panel/app.py` | Application factory and all HTTP routes. |
| `panel/runner_client.py` | The **only** place the panel invokes privileged verbs. Validates arguments, shells out via `sudo`, parses one JSON result. |
| `panel/security.py` | CSRF, login throttle, safe-redirect check, response security headers. |
| `panel/models.py` | SQLite data layer. |
| `panel/jobs.py` | In-process worker pool for async `site_create` / `site_delete`. |
| `runner/` | The privileged shell verbs. Root-owned, mode 0750 in production. |
| `runner/_lib.sh` | Shared validation, protected-domain guard, JSON escaping, logging. Every verb sources it. |
| `config/` | `sudoers.hostzilla`, the systemd unit, the Apache vhost, `hostzilla.conf.example`. |
| `install.sh` | The one-command installer. |
| `tests/` | pytest suite. |
| `docs/ARCHITECTURE.md` | The full design spec. Read this for the *why*. |

---

## Set up a development environment

You do **not** need to run `install.sh` to work on the panel. Most changes can be
developed on any machine with Python 3.10+.

```bash
git clone https://github.com/iampopye/hostzilla.git
cd hostzilla

python3 -m venv .venv
source .venv/bin/activate
pip install -r panel/requirements.txt
pip install pytest ruff
```

Run the panel locally:

```bash
cd panel
HZ_DEV=1 python app.py
```

That serves <http://127.0.0.1:2087>. `HZ_DEV=1` supplies a local development
session key so you do not need `/etc/hostzilla/hostzilla.conf` to exist. On first
run the panel mints an admin account with a **random** password and prints it to
stdout once — copy it from the console, then change it at `/account`.

Three environment variables shape local development:

| Variable | Effect |
| --- | --- |
| `HZ_DEV=1` | Uses a development session-signing key instead of requiring `HZ_SECRET_KEY`. **Never set this in production.** |
| `HZ_NO_SUDO=1` | `runner_client` calls the verbs directly instead of through `sudo`. Useful with mock scripts. |
| `HZ_CONF_PATH` | Points the config loader somewhere other than `/etc/hostzilla/hostzilla.conf`. |

Python 3.10 is the floor because that is what Ubuntu 22.04 ships. CI runs the
suite on **3.10 and 3.12** — both must stay green.

### Testing anything that actually provisions

The runner verbs create Apache vhosts, PHP-FPM pools, MySQL databases and system
users. **Do not run them on a machine you care about.** Use a disposable Ubuntu
22.04 or 24.04 VM, or an LXD container:

```bash
lxc launch ubuntu:24.04 hz-dev
lxc exec hz-dev -- bash
# inside the container
apt-get update && apt-get install -y git
git clone https://github.com/iampopye/hostzilla.git
bash hostzilla/install.sh
```

When you are finished, throw it away:

```bash
lxc delete -f hz-dev
```

The `_lib.sh` helpers also honour `HZ_TEST_ROOT`, which re-roots every path
(`/opt`, `/etc`, `/var/www`, the log) under a scratch directory so the pure
helpers can be exercised without touching the real system. It is deliberately
**refused when running as root** — that refusal is a security property, not an
inconvenience. Do not remove it.

---

## Running the tests

```bash
pytest -v                 # the whole suite
pytest tests/test_security.py -v
ruff check .              # lint — must be clean
```

Shell scripts get the same treatment CI gives them:

```bash
for script in runner/*.sh install.sh; do bash -n "$script"; done
shellcheck -x -S warning runner/*.sh install.sh
visudo -cf config/sudoers.hostzilla
```

The whole CI pipeline is in [`.github/workflows/ci.yml`](.github/workflows/ci.yml):
ruff, pytest on 3.10 and 3.12, ShellCheck, and a `visudo` syntax check on the
sudoers allowlist. If it is green locally it will be green there.

---

## The runner contract

This is the part of Hostzilla most worth understanding, because it is the
privilege boundary.

The panel never runs arbitrary shell. It may ask for exactly three things —
`site_create`, `site_delete`, `site_list` — and nothing else. That allowlist is
enforced in two independent places: `VERBS` in `panel/runner_client.py`, and
`config/sudoers.hostzilla`.

Rules for anyone touching this boundary:

- **Validate on both sides.** The panel validates before calling; every verb
  re-validates every argument before using it. Neither side trusts the other.
  Do not delete a check because "the caller already does that".
- **Every verb prints exactly one line of JSON** as its result, and every value
  in it goes through `json_escape`. This is not cosmetic: Python's `json.loads`
  keeps the *last* occurrence of a duplicate key, so an unescaped quote in a
  value lets a failed operation forge `"status":"ok"`.
- **Refuse protected domains.** `''`, `localhost`, `html` and `$PANEL_DOMAIN`
  are always protected, plus anything the operator listed in
  `PROTECTED_DOMAINS`.
- **Adding a fourth verb is a design change, not a patch.** Open an issue first.
  It widens the privileged surface, and it needs a sudoers entry, a
  `runner_client` wrapper, argument validation, tests, and a security review.

---

## Code style

**Python**

- ruff enforces `E`, `F`, `W`, `I`, `B`, `UP` at a 90-character line length. The
  config lives in `pyproject.toml`; run `ruff check .` before you push.
- `UP032` is deliberately off. The panel targets Ubuntu's system Python and is
  written in a plain style — `.format()` is fine, do not mass-convert it.
- Flat imports inside `panel/` (`import models`, not `from panel import models`).
  Production runs with `panel/` as the working directory.
- Prefer the boring, readable construction. This code gets read by operators
  auditing what runs as root on their server.

**Shell**

- `set -euo pipefail` at the top of every script.
- Must pass `shellcheck -x -S warning`. Use `-x` locally so `_lib.sh` is analysed
  in context.
- Quote every expansion. Validate before you use.

**Comments**

Explain *why*, not *what*. The existing codebase does this well — several
comments record a bug that a check exists to prevent. If you add a guard,
say what breaks without it. Those comments are load-bearing; a future
contributor reading `# NOTE ON THE TRAILING WILDCARD` in the sudoers file is
saved a real outage.

---

## Commits

Use short, imperative subject lines that say what changed:

```
Fix the sudoers allowlist so site_list is callable
Harden the privileged runner against JSON injection
Add pytest coverage for the login throttle
```

Guidelines:

- Present tense, imperative mood, no trailing full stop, ~72 characters.
- One logical change per commit. Unrelated fixes get their own commit.
- If the *why* is not obvious from the subject, write a body. Wrap at 72.
- Reference issues in the body: `Fixes #12`.
- **Every commit needs a sign-off** — use `git commit -s`.

Conventional Commits prefixes (`fix:`, `ci:`, `docs:`) appear in the history and
are welcome, but a clear plain-English subject is what actually matters.

---

## Pull requests

1. Branch from `main`: `git checkout -b fix/site-list-sudoers`
2. Make the change. Add or update tests — a bug fix without a regression test
   tends to come back.
3. Run `pytest`, `ruff check .`, and ShellCheck if you touched shell.
4. Push and open a PR. Fill in the template.
5. First PR only: comment `I have read the CLA and I agree to it.`

What gets a PR merged quickly:

- It does one thing, and the description says what and why.
- Tests cover the new behaviour.
- CI is green.
- If it touches the privilege boundary, the description says what an attacker
  could previously do and can no longer do.

`main` is protected. Changes land by squash-merge through a PR; force-pushes and
branch deletion are blocked.

---

## Reporting bugs and asking for features

- **Bugs** — [open a bug report](https://github.com/iampopye/hostzilla/issues/new?template=bug_report.yml).
  Include your Ubuntu version, how you installed, and the relevant lines from
  `/var/log/hostzilla/`.
- **Features** — [open a feature request](https://github.com/iampopye/hostzilla/issues/new?template=feature_request.yml).
- **Questions and ideas** — [GitHub Discussions](https://github.com/iampopye/hostzilla/discussions).
- **Security vulnerabilities** — do **not** open an issue. Follow
  [SECURITY.md](SECURITY.md).

---

## Code of Conduct

Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md). It
applies to issues, pull requests, discussions, and anywhere else the project is
represented.

---

## Maintainer

**Karan Garg** — engineer and community professional.

GitHub [@iampopye](https://github.com/iampopye) ·
X [@mrtechgarg](https://x.com/mrtechgarg) ·
LinkedIn <https://www.linkedin.com/in/karan-garg-tech/> ·
<kgupta0183@gmail.com>
