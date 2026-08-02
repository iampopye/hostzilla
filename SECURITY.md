# Security Policy

Hostzilla installs itself on a server as root, configures Apache, PHP-FPM and
MySQL, and keeps a standing sudo grant so an unprivileged web application can ask
root to provision websites. That is an unusually sensitive thing for software to
do, and this policy tries to be honest about it rather than reassuring.

If you find a way to break the boundaries described below, we want to hear about
it, and you will be credited.

---

## Reporting a vulnerability

**Do not open a public issue, pull request, or discussion for a security
problem.**

Email **<kgupta0183@gmail.com>** with `SECURITY` in the subject line.

Helpful to include, in whatever detail you have:

- What the issue is, and what an attacker gains from it.
- Affected version or commit (`cat VERSION`, or the commit SHA).
- Ubuntu version and how Hostzilla was installed.
- Reproduction steps, or a proof-of-concept.
- Whether you are planning to disclose publicly, and roughly when.

You do not need a polished write-up. A rough report of something real is far more
useful than silence.

### What to expect

| Stage | Target |
| --- | --- |
| Acknowledgement that a human has read it | **within 72 hours** |
| Initial assessment — confirmed / not reproducible / need more detail | **within 7 days** |
| Fix or documented mitigation for a confirmed issue | **within 30 days**, sooner for anything reachable pre-authentication or leading to root |

This project is currently maintained by one person. If a deadline is going to
slip you will be told, with a reason and a revised date, rather than left
waiting.

### Disclosure policy

- Coordinated disclosure. Please give us **90 days** from acknowledgement before
  going public — earlier if a fix ships sooner, and we will say when it has.
- A GitHub Security Advisory is published for every confirmed vulnerability once
  a fix is available, with a CVE requested where warranted.
- You are credited by the name and handle you choose, unless you prefer not to
  be.
- There is no bug bounty. This is an unfunded open-source project and pretending
  otherwise would waste your time.
- Reporting in good faith will never be met with legal action. Do not test
  against servers you do not own.

---

## Supported versions

Hostzilla is pre-1.0 (`VERSION` currently reads `0.1.0-dev`). Until 1.0, fixes
land on `main` and there are no backports to older tags.

| Version | Status |
| --- | --- |
| `main` / latest release | Supported — report anything you find |
| Anything older | Not supported. Update before reporting. |

**Supported platforms:** Ubuntu 22.04 LTS and 24.04 LTS, on a host dedicated to
Hostzilla. The installer assumes it owns Apache, PHP-FPM and MySQL on that
machine. Running it on a box with a pre-existing stack is unsupported, and
problems arising from that are configuration issues rather than vulnerabilities.

---

## The threat model, plainly

Hostzilla's whole design rests on one boundary. Everything below describes where
that boundary is and what counts as breaking it.

The panel is a Flask app running as the unprivileged `hostzilla` system user. It
never runs arbitrary shell. It may ask root for exactly three things —
`site_create`, `site_delete`, `site_list` — via a sudoers allowlist. Each verb is
a root-owned script that re-validates every argument it is handed before using
it, and returns a single line of JSON.

**A vulnerability, for our purposes, is anything that lets someone do more than
that boundary is supposed to allow.**

We assume an attacker who can reach the panel over the network, and separately an
attacker who already has an unprivileged account on the server. We do **not**
defend against an attacker who is already root on the host — at that point there
is nothing left to protect.

### In scope

Report anything in this list.

**The panel (`panel/`)**

- Authentication bypass, session fixation or forgery, privilege escalation
  between panel accounts.
- CSRF on any state-changing route; weaknesses in `panel/security.py`
  (token generation or comparison, the login throttle, the safe-redirect check
  in `is_safe_next`, the response security headers).
- SQL injection, path traversal, SSRF, or stored/reflected XSS in the dashboard
  or templates.
- Leakage of secrets into a template, an API response, a log or a job record —
  in particular `HZ_SECRET_KEY` and any per-site database credentials.
- Anything letting an unauthenticated request reach a privileged operation.

**The runner verbs (`runner/`)**

- Argument-validation bypass: a domain, site type, or any other input that
  passes `validate_domain` / the type check but produces an unintended path,
  command, or file write.
- Command injection, or any way to reach a shell through a verb.
- Defeating the protected-domain guard so a verb creates or deletes `localhost`,
  the panel's own `PANEL_DOMAIN`, or anything listed in `PROTECTED_DOMAINS`.
- JSON result forgery — making a failed operation report `"status":"ok"`, or
  injecting keys through an unescaped value. `json_escape` exists precisely
  because `json.loads` keeps the last duplicate key.
- Escaping `HZ_TEST_ROOT`'s refusal-under-root check, or using `HZ_*` variables
  to redirect where root writes.
- TOCTOU races, symlink attacks, or unsafe temporary file handling in a verb.
- Any file created by a verb with weaker ownership or permissions than intended
  — per-site credentials and manifests under `/etc/hostzilla/sites` are meant to
  be `root:root` mode 0600.

**The sudoers policy (`config/sudoers.hostzilla`)**

- Any way for the `hostzilla` user to run a command that is not one of the three
  allowlisted verbs.
- Argument-spec weaknesses that widen what a verb can be called with.
- Anything defeating `env_reset` and letting `HZ_*` or other variables survive
  into the privileged context.

**The installer (`install.sh`)**

- Insecure download, unverified execution, or writable-path hijacks during
  install.
- Files, directories or system users created with excessive permissions.
- Weak generation of the initial admin password or `HZ_SECRET_KEY`, or either of
  them landing in a world-readable file or in shell history.
- Placing a sudoers file that would fail `visudo -cf`.

**Provisioned configuration**

- Generated Apache vhosts or PHP-FPM pools that break isolation between sites —
  one site reading another's files, or executing as another site's user.
- MySQL users granted broader privileges than their own database.

### Out of scope

Not vulnerabilities in Hostzilla. Reported in good faith they will still get a
polite reply, but they will be closed.

- **Vulnerabilities in the third-party software Hostzilla installs** — Apache,
  PHP, PHP-FPM, MySQL, certbot, WordPress, gunicorn, Flask, or any OS package.
  Report those upstream. If Hostzilla *configures* one of them insecurely, that
  is in scope, and the difference matters: tell us about the configuration, not
  the upstream CVE.
- **Documented development placeholders.** `HZ_DEV=1` supplies a known
  development session key and is documented as never-for-production;
  `HZ_NO_SUDO=1` skips the sudo hop for local testing; `tests/conftest.py`
  contains a fixed test secret and a fixed test password. These are deliberate,
  documented, and not used by any installed system. Finding them in the source is
  not a finding.
- **`hostzilla.conf.example`**, which ships with empty and placeholder values.
  The installer generates real secrets at install time.
- Attacks requiring root on the host, or physical access.
- The operator's own choices: a weak admin password chosen after install, the
  panel deliberately exposed without TLS, DNS or registrar compromise, or
  Hostzilla installed on a box already running other services.
- Missing hardening with no demonstrated impact — a header, a version banner, or
  a scanner's default-severity output with no exploit path. Show us the impact.
- Denial of service through sheer traffic volume, and findings that amount to
  "the server is under-resourced".
- Social engineering of the maintainer or of users.

---

## For operators: reducing your own exposure

Not part of the reporting policy, but worth stating.

- Run Hostzilla on a **dedicated host**. It expects to own the web stack.
- Change the generated admin password immediately, and keep the panel behind
  TLS. Set `PANEL_DOMAIN` and issue a certificate for it.
- The panel binds `127.0.0.1:2087` and is reached through Apache's reverse
  proxy. Do not bind it to a public interface.
- List any pre-existing sites on the box in `PROTECTED_DOMAINS` before you touch
  the panel, so a mistaken create or delete can never reach them.
- Keep `/etc/hostzilla/hostzilla.conf` at `root:hostzilla` mode 0640 — it holds
  `HZ_SECRET_KEY`. Regenerating that value invalidates every existing session.
- Keep the underlying OS patched. Hostzilla does not patch Apache, PHP or MySQL
  for you.

---

## Contact

**Karan Garg** — engineer and community professional.

<kgupta0183@gmail.com> ·
GitHub [@iampopye](https://github.com/iampopye) ·
X [@mrtechgarg](https://x.com/mrtechgarg) ·
LinkedIn <https://www.linkedin.com/in/karan-garg-tech/>
