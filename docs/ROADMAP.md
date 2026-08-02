# Hostzilla — Phased Build Plan

**Status:** Draft v1 · **Date:** 2026-06-03 · Companion: `ARCHITECTURE.md`

> **Hostzilla — one giant panel. sites included.**

Phased from **M0 (panel boots + provisions 1 site)** → **MVP (builder publishes a real site end-to-end)** → **SaaS (multi-tenant + billing)**. Each phase lists goals, **workstreams** (each ownable by one engineer), and a **Definition of Done (DoD)**. Effort estimates assume a small senior team (2–4 engineers) and are deliberately conservative — the privileged-runner is genuinely hard and the hosting surface is broad.

> Sequencing rule: nothing that touches root ships without a security review. Git from day one.

---

## Milestone 0 — Panel Boots & Provisions One Site
**Theme:** Stand Hostzilla up, secured by default, on a clean Apache droplet, and provision a single working vhost. No builder, no full UI yet.
**Effort:** ~3–4 weeks.

### Workstreams
- **W0.1 Repo & hygiene (owner: Platform).** Initialize Git on the `panel/` tree. Add CI, branch protection, and a clean dependency manifest. Ensure no proprietary or non-redistributable assets ship in the tree.
- **W0.2 Secure defaults (owner: Security).** Source `SECRET_KEY` and DB passwords from an env/secret store; set an explicit `ALLOWED_HOSTS`; rotate the admin seed; confirm there is no phone-home of any kind.
- **W0.3 Apache boot (owner: Platform).** Stand the Django app up on the Apache droplet. The web-server selector resolves to Apache as the single supported topology.
- **W0.4 Privileged runner v0 (owner: Security).** Implement the **scoped-sudoers runner** exposing a *fixed* set of operations (create-vhost, write-config, reload-apache). Allowlist args; no shell-string assembly. This is the critical-path workstream.
- **W0.5 Provision one site (owner: Provisioning).** Drive the create-vhost routine + Apache vhost setup through the new runner to produce a working vhost + Linux user + PHP-FPM pool, serving a placeholder page over HTTP.
- **W0.6 Subsystem hardening pass 1 (owner: any).** Harden the subsystems that M0/MVP depend on: the website/provisioning core and the security middleware. Record what is production-ready.

### Definition of Done
- Fresh Apache droplet → installer brings up the Django panel with **no phone-home** and **no shipped secrets**.
- A single API/CLI call provisions one vhost via the **new privileged runner** (sudoers-scoped, allowlist) and serves a page.
- Repo is under Git with CI; no non-redistributable assets in the tree.
- Hardening notes filed for the core modules.

---

## Milestone 1 — Clean API + SPA Shell
**Theme:** API-first foundation and the modern UI. Provisioning becomes a first-class API resource.
**Effort:** ~4–6 weeks.

### Workstreams
- **W1.1 DRF API layer (owner: API).** Add DRF; build `/api/v1/auth`, `/api/v1/sites` (list/create/get/delete), `/api/v1/jobs`. Calls manager classes in-process. CSRF on unsafe verbs; token auth for clients. **No `@csrf_exempt`.**
- **W1.2 Durable job runner (owner: Platform).** Build the async runner (submit→execute→status→SSE tail, dedup, per-type timeouts) backed by **Celery/RQ + Redis (or DB-backed)** on **Postgres** — not an in-process pool. Every OS-mutating API call → a job.
- **W1.3 Auth/ACL (owner: Security).** Wire `ACLManager` ownership checks into DRF querysets/serializers (default-deny, owner-scoped). Operator login for MVP (customer accounts later).
- **W1.4 React/TS SPA shell (owner: Frontend).** New SPA: login, site list, provision-site form, job/log viewer (SSE). Design-system-light but coherent.
- **W1.5 SSL automation (owner: Provisioning).** certbot + Apache `--deploy-hook` reload, triggered on domain attach; renewal via systemd timer. Surface `/api/v1/sites/{id}/ssl`.

### Definition of Done
- A user logs into the **SPA**, provisions a site through the **DRF API**, watches the job stream live, and the site is served over **HTTPS** (auto Let's Encrypt).
- Jobs survive a worker restart (durable queue, Postgres) — no orphan-sweep workaround needed.

---

## Milestone 2 — MVP: Static Builder Publishes a Real Site
**Theme:** The differentiator. GrapesJS embedded, template picker, atomic publish, rollback — end to end.
**Effort:** ~6–8 weeks.

### Workstreams
- **W2.1 GrapesJS embed (owner: Frontend).** Mount GrapesJS core (BSD-3) + `grapesjs-preset-webpage` at `/sites/:id/builder`. Wire `storage.onSave`/`onLoad` to `/api/v1/sites/{id}/builder/project`.
- **W2.2 Builder data + assets (owner: API).** `builder_project` (JSON in Postgres), `builder_asset` (disk/object store), `builder_revision`. Asset upload endpoint; serve assets to the editor.
- **W2.3 Publish pipeline (owner: Provisioning).** `POST .../publish`: render JSON → `index.html`+`style.css` (+ per-page) → copy assets → **atomic staging-swap** into `/var/www/<domain>` → reload if needed → cache purge. **Never wipe docroot.** Retain prior revision → `POST .../rollback`. Runs as a job through the privileged runner.
- **W2.4 Starter templates (owner: Content/Frontend).** 6–10 curated templates as GrapesJS project JSON (business, portfolio, landing, restaurant, etc.). Template picker = clone JSON into a new site.
- **W2.5 WordPress dynamic track (owner: Provisioning).** 1-click WP via wp-cli + an FSE block theme (install, activate, seed patterns, set homepage). `POST /api/v1/sites/{id}/wordpress`.
- **W2.6 Builder/content service (owner: Builder).** Stand up the standalone content service: AI content generation plus the wp-cli mutation engine with snapshots, per-domain locks, and reversible edits. Wire its two seams to the panel: vhost lookup via the panel's vhost registry; WordPress mutations via the panel's privileged wp-cli runner. Expose `/content/generate` + `/content/publish`.
- **W2.7 Migration importer v0 (owner: Platform, optional-in-MVP).** Import accounts/DNS/email from a cPanel/Plesk source — the GTM wedge. Can slip to early SaaS phase if MVP timeline is tight.

### Definition of Done
- A user picks a **template**, edits it in the **in-panel GrapesJS builder**, hits **Publish**, and a real site is served at their domain over HTTPS — via **atomic swap with the previous revision kept** (rollback works; docroot never wiped).
- Parallel: a user creates a **WordPress** site and **AI-generates validated, locally-grounded content** into it via the content service.
- This is the demoable product that beats cPanel's "no first-party builder."

---

## Milestone 3 — SaaS: Multi-Tenant, Multi-Server, Billing
**Theme:** Turn the single-box panel into the managed-SaaS control plane with BYO-infra, reseller, and billing.
**Effort:** ~8–12 weeks.

### Workstreams
- **W3.1 Customer tenancy model (owner: API).** Promote the operator model to full customer accounts + reseller hierarchy (`owner` self-reference), resource packages (cgroups-v2 limits), default-deny ownership on every resource.
- **W3.2 Per-server agent (owner: Platform/Security).** Turn the local privileged runner into a **remote agent** on customer BYO servers. Control plane → agent via **typed RPC over SSH/mTLS**. Control plane holds **no root** on customer boxes. Security review gate.
- **W3.3 Multi-server control plane (owner: Platform).** Server registry, health, fan-out jobs to the right agent, per-server worker routing. Logs to object storage (not local files).
- **W3.4 Billing (owner: Billing).** **Per-server + per-published-site**, never per-account. Subscriptions, usage metering, white-label client billing pass-through. Integrate a payment provider.
- **W3.5 White-label / agency tier (owner: API/Frontend).** Sub-accounts, custom branding, your-brand-not-ours, client billing — the highest-margin tier.
- **W3.6 Email + DNS in the UI (owner: Provisioning/Frontend).** Surface the email and DNS subsystems (hardened) in the SPA — the full stack cPanel-leavers need.
- **W3.7 Backups (owner: Platform).** Off-box/object-storage backups incl. builder revisions and WP sites.

### Definition of Done
- An agency signs up, **connects its own VPS**, gets it managed by the central control plane via the agent, provisions client sites, **builds + publishes** them, manages **email/DNS/SSL**, and is **billed per-server + per-published-site** — under **its own brand** for sub-accounts.
- No per-account fees anywhere. Control plane never holds root on customer servers.

---

## Cross-Cutting / Continuous
- **Security reviews** gate every milestone that touches root (M0 W0.4, M3 W3.2 mandatory).
- **GPLv3 compliance:** Hostzilla is released under GPLv3; any new distribution channel for a self-host/on-prem edition or installer gets a legal review (does not block internal SaaS use).
- **Subsystem hardening** continues through M0–M3 for email, DNS, backups, firewall, and container modules.
- **Django LTS upgrades** tracked but non-blocking for MVP.
- **Path/config sweep** to make install root and interpreter paths configurable — chip away from M0.

---

## Effort Summary

| Milestone | Outcome | Rough effort |
|---|---|---|
| **M0** | Panel boots on Apache, secured, provisions 1 site via new runner | 3–4 wk |
| **M1** | Clean DRF API + React SPA + durable jobs + auto-SSL | 4–6 wk |
| **M2 (MVP)** | GrapesJS builder publishes a real site end-to-end + WP/AI track | 6–8 wk |
| **M3 (SaaS)** | Multi-tenant + multi-server agent + billing + white-label | 8–12 wk |

**Critical-path risks to the timeline:** (1) the privileged-runner replacement (M0 W0.4 → M3 W3.2); (2) the breadth of the hosting subsystem hardening; (3) GPLv3 distribution clearance for any new self-host channel.
