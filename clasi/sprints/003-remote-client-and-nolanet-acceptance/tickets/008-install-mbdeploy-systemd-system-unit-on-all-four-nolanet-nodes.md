---
id: 008
title: Install mbdeploy + systemd system unit on all four Nolanet nodes
status: open
use-cases: [SUC-014]
depends-on: ['006', '007']
github-issue: ''
issue: mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# Install mbdeploy + systemd system unit on all four Nolanet nodes

## Description

On each of `magni`, `hodr`, `loki`, `meili` (SSH as `jtl`,
`~/.ssh/raspi-cluster_ed25519`): install this sprint's mbdeploy build
into a venv (`loki` already has one at `~/mbdeploy-test` — reuse or
refresh it rather than starting over), then run
`mbdeploy serve --install-service --system` and
`systemctl enable --now mbdeploy`.

**Gate on ticket 001's finding first.** If ticket 001 found the
hidapi-exit crash reproduces on Linux, do not proceed with this ticket
until team-lead/stakeholder has decided how to handle it — installing
a daemon known to crash at every `systemctl stop`/reboot across all
four production nodes is not an acceptable outcome to walk into
silently.

`jtl` is already in `plugdev` and `dialout` on all four nodes — no new
udev rule needed (confirmed in sprint 001). No change to the
`raspi-cluster` Ansible repo (this sprint's Out of Scope) — this is a
one-time SSH/`systemctl` sequence run from `mbdeploy`'s own tooling/docs
(§9, ticket 006), not an Ansible playbook.

## Acceptance Criteria

- [ ] Ticket 001's hidapi finding is checked and, if blocking, this
      ticket does not proceed past that point (see Description).
- [ ] mbdeploy (this sprint's build) is installed into a venv on all
      four nodes.
- [ ] `mbdeploy probe` on each node shows its board's port as
      `/dev/ttyACM0` (or similar), not `null` — the sprint-001
      prerequisite's real check, re-verified here since it's easy for a
      fresh venv/install to silently regress.
- [ ] `mbdeploy serve --print-service --system` output looks correct on
      at least one node before installing for real.
- [ ] `mbdeploy serve --install-service --system` succeeds on all four
      nodes; `systemctl enable --now mbdeploy` brings each one up.
- [ ] `systemctl status mbdeploy` shows `active (running)` on all four
      nodes.
- [ ] `journalctl -u mbdeploy` shows the daemon's own startup log line
      on each node (this is also exercised end-to-end in ticket 009,
      but a first check here catches an install-time problem early).

## Testing

- **Existing tests to run**: none in the automated suite — this is an
  install/ops ticket. The automated suite (`uv run pytest`) should
  already be green from tickets 001-006 before starting this one.
- **New tests to write**: none — verification is the manual
  `systemctl`/`journalctl` checks above; the end-to-end functional
  proof is ticket 009.
- **Verification command**: manual, over SSH per node
  (`systemctl status mbdeploy`, `journalctl -u mbdeploy -n 50`).
