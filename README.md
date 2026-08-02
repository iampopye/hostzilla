<div align="center">

# 🦖 Hostzilla

### one giant panel. sites included.

**The open-source hosting control panel for Ubuntu.** Install on a fresh server with one command, then create and host websites — static HTML, PHP, or WordPress — from a clean web dashboard. A free, modern alternative to cPanel/WHM.

</div>

<p align="center"><em>Hostzilla is at v0.1. It installs, provisions real sites, and is honest about what it does not do yet — see <a href="#status">Status</a>.</em></p>

---

## What is Hostzilla?

Hostzilla is software you install on your own Ubuntu server. It gives you a web-based control panel to create and host real websites — each with its own domain, MySQL database, isolated PHP-FPM pool, Apache virtual host, and optional free Let's Encrypt SSL — without ever touching the command line.

You own the server. You own the data. No licensing fees, no per-account pricing, no lock-in.

## Features

- **Free & open source** — [AGPL-3.0](LICENSE). Self-host it, modify it, keep it. A
  [commercial licence](COMMERCIAL.md) is available if the AGPL does not suit you.
- **Standard stack** — runs on plain Apache + PHP-FPM. Nothing exotic; everything you provision is normal, portable config you can read and trust.
- **One-command install** — go from a fresh Ubuntu box to a working panel in minutes.
- **Per-site isolation** — every site gets its own database, its own PHP-FPM pool, and its own vhost. Sites don't share a runtime.
- **Automatic SSL** — opt in and Hostzilla issues and installs a Let's Encrypt certificate for you.
- **Auditable by design** — the panel never runs arbitrary shell. It can only invoke three validated, root-owned provisioning verbs through a tight sudoers allowlist.

## Quick Install

On a **fresh Ubuntu 22.04 or 24.04 server**, as root:

```bash
git clone https://github.com/iampopye/hostzilla.git
sudo bash hostzilla/install.sh
```

That's it. The installer provisions Apache, PHP-FPM, MySQL, certbot, and the panel itself, writes its config, creates the unprivileged `hostzilla` service user, and starts everything under systemd. When it finishes it prints your **dashboard URL** and **admin login**.

## What you get

After install, your server has:

- A web dashboard listening on `127.0.0.1:2087`, reverse-proxied by Apache.
- A one-click **Create Site** flow for static, PHP, and WordPress sites.
- For each site you create: an Apache vhost, a docroot under `/var/www`, a MySQL database and user, a dedicated PHP-FPM pool, and (optionally) a live SSL certificate.
- A job queue and live logs so you can watch every provisioning step.
- Config at `/etc/hostzilla/hostzilla.conf`, logs at `/var/log/hostzilla/`, and the panel database at `/var/lib/hostzilla/hostzilla.db`.

## Status

Hostzilla is **v0.1**. Here is exactly where it stands, so you can decide
whether it is useful to you today.

**Works now**

- One-command install on a fresh Ubuntu 22.04 / 24.04 server
- Create static, PHP and WordPress sites from the dashboard
- Per-site Apache vhost, PHP-FPM pool, MySQL database and user
- Optional Let's Encrypt certificate when DNS already points at the server
- Delete a site, including its database
- Async job queue with live status and logs
- Change your panel password

**Not built yet** — no part of the panel does these today

- Visual / drag-and-drop site builder
- Email hosting, DNS management, backups
- Multiple panel users, reseller accounts, billing
- nginx support (Apache only)
- Editing a site after creation, beyond deleting and recreating it

**Known limits**

- One operator account. There is no user management UI.
- The panel assumes it owns Apache, PHP-FPM and MySQL on the machine.
- The login throttle is per worker process, not cluster-wide.
- `install.sh` targets Ubuntu specifically; other distributions are untested.

## Requirements

- A **fresh Ubuntu 22.04 or 24.04** server (a clean VPS or VM is ideal).
- **root access** (the installer needs it to configure system services).
- A public IP, and — to use SSL — a domain whose DNS you can point at the server.

> Run Hostzilla on a dedicated host. The installer assumes it owns Apache, PHP-FPM, and MySQL on the machine.

## Usage

1. **Log in** to the dashboard at the URL the installer printed, using the admin credentials it generated.
2. Click **Create Site**, enter a domain, and pick a type — **static**, **PHP**, or **WordPress**.
3. **Point your DNS** — add an `A` record for the domain pointing at your server's IP.
4. **Enable SSL** — tick the SSL option and, once DNS resolves, Hostzilla automatically issues and installs a Let's Encrypt certificate.

Your site is live. Manage or delete it any time from the **Sites** page.

## Architecture

Hostzilla is four cleanly separated parts:

1. **Panel** — a Flask web app that runs as the unprivileged `hostzilla` user. It serves the dashboard, validates input, and never runs arbitrary shell.
2. **Job runner** — an in-app worker pool that queues `site_create` / `site_delete` as async jobs, captures their output, and streams status and logs to the UI.
3. **Provisioning engine** — a small set of privileged shell verbs (`site_create.sh`, `site_delete.sh`, `site_list.sh`) that are the *only* boundary between the panel and root. The panel calls them through a tightly scoped sudoers allowlist; each verb validates domains, refuses protected hosts, and emits a single JSON result.
4. **Server stack** — standard Apache, PHP-FPM, MySQL, and certbot. Everything Hostzilla provisions is ordinary, portable config.

This split keeps the privileged surface tiny and auditable: the web app can only ever ask for three well-defined, validated operations.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design.

## Roadmap

v0.1 delivers install → log in → create and host real sites with automatic SSL.
A visual site builder is the intended direction but **is not built** — nothing in
the panel does this today.

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the phased plan.

## Contributing

Contributions are welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for how to set up a dev environment, the repo layout, the runner contract, and how to test safely in an LXD sandbox.

## Security

Hostzilla runs privileged provisioning verbs as root through a sudoers
allowlist. If you find a security issue, please report it privately — see
[`SECURITY.md`](SECURITY.md).

## License

[AGPL-3.0-or-later](LICENSE) © 2026 Karan Garg.

Hostzilla is dual-licensed. The AGPL suits anyone self-hosting it. If you want to
build on Hostzilla without the AGPL's source-sharing obligations, see
[COMMERCIAL.md](COMMERCIAL.md).

Contributions require signing the [CLA](CLA.md) — this is what keeps the
commercial option possible.

---

Maintained by Karan Garg —
GitHub [@iampopye](https://github.com/iampopye) ·
X [@mrtechgarg](https://x.com/mrtechgarg) ·
[LinkedIn](https://www.linkedin.com/in/karan-garg-tech/) ·
[kgupta0183@gmail.com](mailto:kgupta0183@gmail.com)
