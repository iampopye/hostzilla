# Hostzilla — System Architecture & Technical Specification

**Status:** Draft v1 (Accepted direction) · **Date:** 2026-06-03 · **Owner:** Architecture

> **Hostzilla — one giant panel. sites included.**

This document is the build-driving spec for Hostzilla, an open-source hosting control panel (cPanel/WHM-class) with a built-in website builder. Where a design choice has trade-offs, the decision is made and justified inline. Companion: `ROADMAP.md`.

---

## 1. Product Vision & Positioning

**One-liner:** *Hostzilla is the open-source hosting control panel with a Webflow-grade site builder built in — host, design, and resell client websites from one panel, at a flat per-server price that never punishes you for growing.*

**The wedge.** The control-panel market splits cleanly:
- **Panels** (cPanel, Plesk, RunCloud, Ploi, CloudPanel, Coolify) own hosting infrastructure but ship **no real visual builder** — the few that bolt one on rely on the same third-party Sitejet, which is basic and *destructively* wipes the document root on publish.
- **Builders** (Wix, Webflow, Framer) own design UX but have **no multi-tenant hosting, no reseller, no email/DNS**.

**Nobody owns the intersection.** Hostzilla targets it: cPanel-class hosting (email + DNS + SSL + reseller) **plus** a first-class visual builder with **safe atomic publishing**, OSS-core, monetized as managed SaaS, aimed at **white-label agencies and freelancers fleeing per-account price hikes**. Unlike cPanel, which leaves design to third parties, Hostzilla ships the builder as a first-party feature.

**What makes Hostzilla different (in priority order):**
1. **Built-in visual builder** with safe, atomic, rollback-able publishing (never wipes docroot — the explicit Sitejet failure mode).
2. **AI content generation** grounded in the site's own context, via Hostzilla's native content engine. No competitor panel ships this.
3. **Flat per-server pricing**, never per-account — the direct antidote to the 2026 per-account hikes elsewhere in the market.
4. **White-label multi-tenant reseller** as a first-class, highest-margin tier.
5. **Full stack the modern dev-panels stripped out**: email, DNS, Let's Encrypt SSL, automated backups, in the box.

---

## 2. Scope

### 2.1 MVP (what ships first to win the wedge)
- Hostzilla boots on a clean **Apache** droplet, secured by default, with no phone-home of any kind.
- Provision a website end-to-end: vhost + Linux user + PHP-FPM pool + DNS + Let's Encrypt SSL.
- **Static site builder** (GrapesJS) embedded in the panel; **template picker** with 6–10 starter templates.
- **Publish pipeline:** render GrapesJS JSON → static HTML/CSS → **atomic staging-swap** into the vhost docroot → SSL ensured → one-click rollback.
- **1-click WordPress** track (parallel) via wp-cli + an FSE block theme.
- **AI content** generates validated, locally-grounded copy into the WordPress track.
- A coherent **REST API** (Hostzilla is API-first); a modern SPA is the only UI.
- Single-server, single-tenant-per-server operator login (operator, not customer billing yet).

### 2.2 Later (post-MVP → SaaS)
- Multi-tenant accounts, reseller hierarchy, resource packages, **billing**.
- Multi-server control plane + per-server **agent** (managed-SaaS, BYO-VPS model).
- Email hosting + DNS management surfaced in the new UI.
- Backups (incl. off-box/object storage), staging environments, custom-domain automation at scale.
- Builder richness: multi-page, asset library, forms relay, draft/publish, SEO editor.
- Premium React-class builder tier (Puck + panel-side SSG).

### 2.3 Explicit non-goals
- **Not** running the builder runtime on customer vhosts. Authoring is centralized in the panel; only static output reaches a vhost.
- **Not** bundling AGPL builder engines (Silex, Webstudio) or proprietary SaaS editors (Builder.io, Plasmic).
- **Not** nginx support in MVP (Apache is the single supported topology for v1; nginx is net-new and deferred).
- **No per-account pricing**, ever — architectural and product commitment.

---

## 3. Foundational Decisions

Hostzilla is built from the ground up as a standalone product. The decisions below define the spine of the system: the web server, the backend stack, the privilege model, and the split between hosting plumbing and the differentiating builder.

### 3.1 What Hostzilla owns end-to-end
Hostzilla owns the full provisioning and hosting surface as first-party code:

| Subsystem | Responsibility |
|---|---|
| **Provisioning core** | Canonical create-vhost routine: vhost + Linux user + PHP-FPM pool. |
| **Apache driver** | Apache vhost generation to `/etc/apache2/sites-enabled/` with `suexec` + `mod_proxy_fcgi`. |
| **SSL** | Let's Encrypt issuance/renewal via certbot with an Apache deploy-hook. |
| **DB / DNS / Email drivers** | MySQL, PowerDNS, and Postfix/Dovecot subsystem drivers. |
| **Tenancy / ACL / Reseller** | WHM-equivalent reseller hierarchy, a fine-grained ACL flag set, and cgroups-v2 package limits — the multi-tenant spine. |
| **WordPress deploy** | wp-cli install path; foundation for the dynamic builder track. |

### 3.2 The differentiators Hostzilla builds net-new
- The **site builder** (static GrapesJS track).
- The **coherent REST API** + React SPA.
- The **builder publish pipeline** (render → atomic swap → rollback).
- The **agent** for multi-server managed SaaS (later).
- **Billing** + customer (vs operator) account model (later).
- The **builder/content service** that powers AI content generation and reversible WordPress edits.

### 3.3 Web server: **Apache, standalone**
**Decision: Apache (mod_proxy_fcgi + suexec + PHP-FPM). No other web server in the stack.**

Justification:
- The managed droplet runs **Apache** — matching the deployment target gives Hostzilla a single supported topology and removes a whole class of "works in one engine, breaks in another" risk.
- Hostzilla's vhost generator emits **real** Apache vhost configs to `/etc/apache2/sites-enabled/` with `suexec proxy_fcgi`. The privileged boundary (§8) is the only root-touching seam, and it is Hostzilla's own design — there is no third-party root daemon anywhere in the stack.
- The static-builder publish model (write files to docroot, reload server) is web-server-agnostic and trivially satisfied by Apache.

We use standard Apache page-cache strategies and certbot with an Apache deploy-hook for SSL. This is a deliberate choice of **operational simplicity and a single supported topology**.

### 3.4 Backend stack: **Django control plane + extracted content service**
**Decision: the panel is a Django application; the AI content/WordPress engine is a standalone service.**

- The **Django control plane** provides privilege separation, a multi-app structure, the ACL/reseller model, DNS/SSL/email/vhost provisioning, and the MySQL ORM. This is the panel.
- The **builder/content engine** is a standalone Python service (framework-light) that the panel calls over an internal API. It owns AI content generation (model-agnostic at the `client.chat` seam) and a tested wp-cli wrapper with snapshots, per-domain locks, multi-layer cache flush, and reversible content edits.

The two seams between panel and content service: (1) vhost/host lookup resolves against the panel's vhost registry; (2) WordPress mutations run through the panel's privileged wp-cli runner (§8). The generate/validate/edit logic is self-contained.

Hostzilla targets a current Django release and tracks LTS upgrades as a standing workstream; version upgrades do not block MVP.

---

## 4. System Architecture

```
                            ┌──────────────────────────────────────────────┐
                            │                 BROWSER (SPA)                 │
                            │   React/TS panel UI  +  GrapesJS editor (in   │
                            │   an iframe/route, served BY the panel)       │
                            └───────────────┬──────────────────────────────┘
                                            │ HTTPS, token/session auth
                                            │ REST (API-first)
        ┌───────────────────────────────────▼───────────────────────────────────┐
        │                          CONTROL PLANE  (Django)                       │
        │                                                                        │
        │  ┌────────────┐  ┌──────────────┐  ┌───────────────┐  ┌─────────────┐  │
        │  │  REST API   │  │ AuthZ / ACL  │  │  Tenancy /     │  │  Billing    │  │
        │  │  (DRF)      │  │ (ACLManager) │  │  Reseller      │  │  (later)    │  │
        │  └─────┬──────┘  └──────────────┘  │  packages      │  └─────────────┘  │
        │        │                            └───────────────┘                  │
        │  ┌─────▼──────────────────────────────────────────────────────────┐    │
        │  │   Manager classes (in-process):                                 │    │
        │  │   WebsiteManager · DNS · SSL · Mail · MySQL · Backups           │    │
        │  └─────┬───────────────────────────────┬──────────────────────────┘    │
        │        │                               │                               │
        │  ┌─────▼─────────┐            ┌─────────▼──────────────────────────┐    │
        │  │  Job/Worker   │            │  Builder Service (Hostzilla)       │    │
        │  │  (Celery/RQ + │◄──────────►│  AI content engine                 │    │
        │  │  Redis/DB)    │  enqueue   │  WordPress mutate engine           │    │
        │  └─────┬─────────┘            │  GrapesJS project store + publish  │    │
        │        │                      └─────────┬──────────────────────────┘    │
        │  ┌─────▼─────────┐                      │                               │
        │  │  Postgres     │   project JSON,      │ publish = render→stage→swap   │
        │  │  (panel DB)   │   accounts, jobs     │                               │
        │  └───────────────┘                      │                               │
        └───────────────────┬─────────────────────┴──────────────────────────────┘
                            │  PRIVILEGED BOUNDARY (the root-touching seam, §8)
                            │  MVP: local sudoers-scoped runner
                            │  SaaS: typed RPC over SSH/mTLS to per-server AGENT
        ┌───────────────────▼────────────────────────────────────────────────────┐
        │                 MANAGED SERVER  (customer's BYO VPS / droplet)          │
        │                                                                         │
        │   AGENT (privileged, audited)  ──►  applies vhost confs, runs wp-cli,   │
        │                                     issues certs, restarts services     │
        │   ┌─────────────────────────────────────────────────────────────────┐  │
        │   │ Apache (suexec + mod_proxy_fcgi)  ·  PHP-FPM pools  ·  MySQL      │  │
        │   │ /var/www/<domain>/   ◄── static publish (atomic swap)            │  │
        │   │ wp-cli sites (dynamic track)  ·  Postfix/Dovecot  ·  certbot      │  │
        │   └─────────────────────────────────────────────────────────────────┘  │
        └─────────────────────────────────────────────────────────────────────────┘
```

**MVP collapses this:** control plane and managed server are the **same box**; the "agent" is a local sudoers-scoped runner, not a remote daemon. The diagram's split is the SaaS end-state; the boundary is designed in from day one so going multi-server is a deployment change, not a rewrite.

---

## 5. Module Map

| Module | Build posture | MVP? | Notes |
|---|---|---|---|
| **Provisioning** | First-party core | Yes | Apache-only path; create vhost + Linux user + FPM pool. |
| **DNS** | First-party (PowerDNS) | Later UI / MVP backend | Surface in UI post-MVP. |
| **Email** | First-party (Postfix/Dovecot) | Later | The cPanel-leaver differentiator. |
| **SSL** | First-party; certbot + Apache deploy-hook | Yes | Auto-issue on domain attach, independent of builder. |
| **Backups** | First-party; off-box/object-storage target | Later | For SaaS phase. |
| **Users / Reseller / Multi-tenancy** | First-party `ACLManager` + `packages` | Operator MVP; customer model later | Fine-grained ACL flags, reseller `owner` hierarchy, cgroups-v2 package limits. |
| **Billing** | Build net-new | SaaS phase | Per-server + per-published-site dimensions; never per-account. |
| **Site Builder (static)** | Build net-new (GrapesJS) | Yes | §7. The differentiator. |
| **Site Builder (dynamic/WP)** | First-party wp-cli + content engine | Yes (parallel track) | wp-cli + FSE theme + AI content. |
| **REST API** | Build net-new (DRF) | Yes | §6. |
| **SPA UI** | Build net-new (React/TS) | Yes | The only UI. |
| **Job/Worker** | Build net-new | Yes | Durable broker (Celery/RQ + Redis or DB-backed), not in-process. |
| **Agent (per-server)** | Build net-new | SaaS phase | The privileged runner on managed boxes (§8). |

---

## 6. API Surface (API-first)

Hostzilla exposes a **single coherent DRF REST API**; the SPA and the builder are pure clients. The API calls the **manager classes in-process** (`WebsiteManager`, DNS, SSL, etc.).

**Auth:** token (DRF token / JWT) for API clients; session for the SPA; CSRF enforced on unsafe verbs. Per-resource ownership checks via `ACLManager`.

**Representative resources (REST, plural nouns, ownership-scoped):**

```
POST   /api/v1/auth/login
GET    /api/v1/accounts/me
GET    /api/v1/accounts                  # reseller: sub-accounts (ACL-gated)
GET    /api/v1/packages

GET    /api/v1/sites                      # hosting accounts / vhosts owned by caller
POST   /api/v1/sites                      # provision (async → returns job)
GET    /api/v1/sites/{id}
DELETE /api/v1/sites/{id}
GET    /api/v1/sites/{id}/ssl             # issue/renew Let's Encrypt
GET    /api/v1/sites/{id}/dns
GET    /api/v1/sites/{id}/email
GET    /api/v1/sites/{id}/backups

# Builder (static)
GET    /api/v1/sites/{id}/builder/project    # GrapesJS JSON (storage.onLoad)
PUT    /api/v1/sites/{id}/builder/project    # storage.onSave
POST   /api/v1/sites/{id}/builder/assets
POST   /api/v1/sites/{id}/builder/publish    # render→stage→swap (async → job)
POST   /api/v1/sites/{id}/builder/rollback   # restore previous revision
GET    /api/v1/templates                      # starter templates (project JSON)

# Builder (dynamic / WP + AI)
POST   /api/v1/sites/{id}/wordpress          # 1-click WP (wp-cli, async)
POST   /api/v1/sites/{id}/content/generate   # AI generate→validate→retry (async)
POST   /api/v1/sites/{id}/content/publish    # write generated content into WP

# Jobs (async everything)
GET    /api/v1/jobs/{id}
GET    /api/v1/jobs/{id}/stream              # SSE log tail
```

**Principle:** every OS-mutating action is **async → returns a job**; the UI polls or streams. The flow is `submit → execute → status → SSE log`, backed by a durable broker.

---

## 7. Site-Builder Integration

**Chosen engine: GrapesJS core (BSD-3-Clause)** for the MVP static track. Rationale: it is a vanilla-JS embeddable library, self-hosted storage via `onSave`/`onLoad` to our REST API, and it exports plain static HTML/CSS that maps directly onto "write files to docroot." React builders (Puck/Webstudio) add an SSR/build runtime per site and (Webstudio/Silex) carry AGPL — deferred to a premium tier with a panel-side SSG step. Builder.io/Plasmic rejected (editor not self-hostable).

**Where it runs:** in the browser, **served by the panel** at `/sites/:id/builder`. Never installed on a customer vhost. The vhost only ever receives rendered static output.

**Two tracks, complementary, not competing:**
- **Static track (GrapesJS):** fast, cheap, secure, trivially backed up. The MVP backbone and the headline feature.
- **Dynamic track (WordPress + FSE + AI content):** for users needing CMS/blog/store. Fully automated via wp-cli; AI writes validated, locally-grounded content. This is where Hostzilla's content engine lives.

**The publish pipeline (the core mechanism — and Hostzilla's safety differentiator vs Sitejet):**
1. Load project JSON; server-side render `index.html` + `style.css` (+ per-page HTML) via GrapesJS export.
2. Copy referenced assets into the build.
3. **Atomic deploy:** write to a staging dir `/var/www/<domain>.tmp`, then **swap** (symlink/rename) to live docroot. A half-written publish never serves. **The previous revision is retained for one-click rollback.** Hostzilla *never* wipes the docroot (the named Sitejet failure mode).
4. Reload Apache only if config changed (pure file writes usually don't need it).
5. Purge cache if enabled.
6. SSL: ensured at domain-attach time via certbot + `--deploy-hook` Apache reload; renewal via certbot systemd timer.

**Builder data model:** `builder_project` (GrapesJS JSON, in Postgres), `builder_asset` (on disk/object storage, keyed by site), `builder_revision` (published snapshots for rollback), `template` (starter project JSON, panel-managed/versioned).

---

## 8. Security & Privilege Model

### 8.1 The root-touching boundary (the most important security decision)
Hostzilla separates the unprivileged web application from the small set of operations that must touch root, and crosses that boundary through its own **scoped, allowlist-based privileged runner** — never free-form shell strings.

- **MVP (single box):** a **scoped-sudoers privileged runner**. The Django process runs unprivileged; a small, audited helper backed by a tight sudoers allowlist exposes a **fixed set of parameterized operations** (create-vhost, write-config, issue-cert, run-wp-cli-for-site, reload-apache). No free-form shell strings cross the boundary.
- **SaaS (multi-server):** the same operations become a **typed RPC to a per-server agent** over SSH/mTLS. The agent is the only root-capable component on a managed box; the control plane never holds root on customer servers.

**Hard rules for the boundary:**
- **Allowlist, not blacklist.** A closed set of typed operations with validated, escaped arguments. No shell-string assembly anywhere.
- **No `@csrf_exempt` JSON endpoints.** CSRF on unsafe verbs; tokens for machine clients.
- **Argument validation at the boundary**, not just in the UI.
- **Full audit trail** of every privileged operation.
- Secrets (`SECRET_KEY`, DB passwords, admin seed) come from an env/secret store; `ALLOWED_HOSTS` is explicitly configured; there is no phone-home of any kind.

### 8.2 Multi-tenancy isolation
- **Authorization:** `ACLManager` enforces ownership on every resource (`resource.owner == user OR acl.admin`). Reseller hierarchy via an `owner` self-reference. A fine-grained ACL flag set + `packages` (cgroups-v2: memory/CPU/IO/inode/proc limits) bound each account's footprint.
- **OS-level isolation:** per-site Linux user + suexec + per-site PHP-FPM pool — one site cannot read another's files or run as another's user.
- **Builder isolation:** authoring is centralized in the panel; a customer's published output is static files in *their* docroot only. No cross-tenant builder runtime.
- **Tenancy in the API:** every query is owner-scoped at the serializer/queryset level (default-deny), not just UI-hidden.

---

## 9. Deployment & How the SaaS Hosts Customers

**Monetization model: OSS-core + paid managed control-plane, BYO-infra, per-server pricing.**

- **OSS Core** (self-host, free forever): the panel + builder engine. Drives adoption and the migration story. License posture in §10.
- **Managed Cloud** (paid control-plane): Hostzilla runs the orchestration, updates, security patching, monitoring, backups, and the builder publish/CDN layer. **Customer brings their own VPS** (Hetzner/DO/Vultr) → our COGS stays near zero; we don't compete on raw hosting margin.
- **Pricing dimensions:** **per-server + per-published-site**. Never per-account.
- **White-label / Agency tier** (highest margin): sub-accounts, client billing pass-through, your-brand branding.
- **Builder-led upsells:** premium templates, AI generation credits, e-commerce, managed email deliverability.

**Topology evolution:**
- **MVP:** one box = control plane + managed server + builder. Operator login only.
- **SaaS:** central control plane (Django + Postgres + Redis + worker fleet) coordinates many **agent-equipped** customer servers. The privileged boundary (§8) is already an RPC seam, so this is a deployment change.

**GTM wedge:** a turnkey **migration path** importing email + DNS + accounts from cPanel/Plesk — convert the 2026 price-hike pain event into a switch.

---

## 10. Risks & Open Questions

| # | Risk / Question | Severity | Mitigation / Direction |
|---|---|---|---|
| 1 | **GPLv3 obligations.** Hostzilla is released under GPLv3. | Med | SaaS *use* does not trigger GPLv3 distribution (no AGPL-style network clause), so running the managed control plane without shipping it is fine. Any self-host/on-prem edition or installer we **distribute** is GPLv3 + source-available, which is exactly the OSS-core strategy. Get legal review before any new distribution channel. |
| 2 | **Builder engine licensing/maturity.** | Low-Med | GrapesJS core is **BSD-3** (commercial-safe, no attribution), mature, TS-rewritten. The commercial Studio SDK is optional and would reintroduce lock-in — **use core only.** Avoid AGPL engines (Silex, Webstudio) entirely. |
| 3 | **Privileged runner is the deepest security workstream.** | High | Allowlist-based runner (§8). This is the riskiest single workstream; security-review it before any commercial use. |
| 4 | **Subsystem hardening.** Email, DNS internals, backups, container, firewall, and security middleware need a hardening pass before being trusted in production. | Med | Audit-and-harden in Milestone 0/1. |
| 5 | **Path/config sweep.** Install root and interpreter paths must be made configurable rather than hardcoded. | Med | Path-sweep workstream; chip away from M0. |
| 6 | **Apache caching trade-offs.** | Low | Standard page-cache strategies; revisit only if benchmarks demand. |
| 7 | **AI content quality/cost at scale.** The content engine is model-agnostic at the `client.chat` seam. | Low | Swap to a hosted LLM if needed; gate behind credits (monetization). |

---

## 11. Decision Log (ADR-style summary)

- **ADR-001 Apache standalone** — Accepted. Single supported web-server topology.
- **ADR-002 Django control plane + standalone content service** — Accepted. Panel owns provisioning; content engine is a separate service.
- **ADR-003 GrapesJS (BSD-3) static builder as MVP** — Accepted. WP+FSE+AI as parallel dynamic track; React builders deferred.
- **ADR-004 New DRF REST API + React SPA** — Accepted. API-first, single SPA UI.
- **ADR-005 Allowlist privileged runner** — Accepted. Scoped sudoers locally; RPC-to-agent seam for multi-server.
- **ADR-006 OSS-core + managed per-server SaaS, BYO-infra** — Accepted. Never per-account.
- **ADR-007 GPLv3** — Accepted. Hostzilla is released under GPLv3.
