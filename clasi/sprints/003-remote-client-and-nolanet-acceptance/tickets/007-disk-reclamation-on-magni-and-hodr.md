---
id: '007'
title: Disk reclamation on magni and hodr
status: open
use-cases: [SUC-014]
depends-on: []
github-issue: ''
issue: mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# Disk reclamation on magni and hodr

## Description

`magni` (472 MB free, 93% used) and `hodr` (337 MB free, 95% used) do
not have enough headroom to install a fresh mbdeploy venv (~83 MB) plus
whatever `pip`/`apt` transient space each install needs — verified, not
estimated. This is stakeholder-approved: run, over SSH as `jtl`
(`~/.ssh/raspi-cluster_ed25519`), on both nodes:

```
docker image prune -a
apt clean
```

No change to the `raspi-cluster` Ansible repo — this is a one-time
manual cleanup, per this sprint's own Out of Scope note, not an
automated/repeatable Ansible task.

This ticket has no code dependency on tickets 001-006 and could run at
any point once SSH access is available; it is sequenced here (after the
client work) because it belongs to the rollout half of the sprint, not
because anything blocks it earlier.

## Acceptance Criteria

- [ ] `docker image prune -a` and `apt clean` run successfully on
      `magni` over SSH.
- [ ] Same on `hodr`.
- [ ] `df -h` (or equivalent) on both nodes, before and after, is
      recorded in this ticket showing free space increased.
- [ ] Free space on both nodes afterward is enough to install an ~83 MB
      mbdeploy venv with headroom to spare (a specific target, e.g.
      ≥300 MB free, recorded here rather than left vague).
- [ ] Nothing else running on either node (the Docker Swarm itself, or
      any other service) is disrupted — `docker ps`/`systemctl status`
      spot-checked after the prune, not just before.

## Testing

- **Existing tests to run**: none — no source change.
- **New tests to write**: none — this is an operational ticket.
- **Verification command**: manual, over SSH (`df -h`, `docker system
  df`, `docker ps`, before and after).
