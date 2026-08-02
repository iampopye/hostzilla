<!--
Thanks for contributing to Hostzilla.

Keep the description short but real — what changed and why. If this is your
first pull request here, remember to leave a separate comment saying you agree
to the CLA (see the checklist below).
-->

## What does this change?

<!-- One or two sentences. What is different after this PR? -->

## Why?

<!-- The problem this solves. Link the issue if there is one: Fixes #123 -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Security fix or hardening
- [ ] Documentation
- [ ] Refactor / cleanup (no behaviour change)
- [ ] Build, CI or tooling

## How was this tested?

<!--
Be specific. "Ran the tests" is fine for a docs change; anything touching the
runner or the installer needs to say where you tested it.
-->

- [ ] `pytest` passes
- [ ] `ruff check .` is clean
- [ ] `shellcheck -x -S warning runner/*.sh install.sh` passes *(if shell changed)*
- [ ] `visudo -cf config/sudoers.hostzilla` passes *(if the sudoers policy changed)*
- [ ] Tested end to end on a disposable Ubuntu VM or LXD container *(if provisioning changed)*

Tested on: <!-- e.g. Ubuntu 24.04 LXD container, Python 3.12 -->

## Privileged surface

<!--
Hostzilla's security rests on one boundary: the panel may only ask root for
site_create, site_delete and site_list. If this PR goes anywhere near that,
say so here — it gets a closer read, which is a good thing, not a punishment.
-->

- [ ] This PR does **not** touch `runner/`, `panel/runner_client.py`, `config/sudoers.hostzilla`, or `install.sh`.
- [ ] It does touch one of those. What an attacker could previously do and can no longer do:

  <!-- explain here -->

## Checklist

- [ ] I have read [CONTRIBUTING.md](../CONTRIBUTING.md).
- [ ] My commits are signed off (`git commit -s`).
- [ ] **I have read the [CLA](../CLA.md) and I agree to it.** *(A CLA — not a DCO — is required because Hostzilla is dual-licensed. You keep your copyright. If this is your first PR, please also leave this as a plain comment on the PR.)*
- [ ] Tests cover the new behaviour, or I have explained why they cannot.
- [ ] Docs are updated if this changes how someone installs, configures or uses Hostzilla.
- [ ] This PR does one thing.

## Anything else?

<!-- Screenshots, follow-up work you deliberately left out, decisions you are unsure about. -->
