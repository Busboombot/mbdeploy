---
id: '007'
title: Disk reclamation on magni and hodr
status: done
use-cases:
- SUC-014
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

## Results (measured 2026-08-27)

Both nodes' `jtl` account is in the `docker` group, so `docker` commands
needed no `sudo` on either node; only `apt clean` needs root.

### magni (.147 — swarm manager)

**Before**
```
$ df -h /
Filesystem      Size  Used Avail Use% Mounted on
/dev/mmcblk0p2  6.8G  6.0G  472M  93% /

$ docker system df
TYPE            TOTAL     ACTIVE    SIZE      RECLAIMABLE
Images          5         5         198.9MB   0B (0%)
Containers      13        4         200.7kB   135.2kB (67%)
```

**Actions**
- `docker image prune -a -f` → `Total reclaimed space: 0B`. Verified,
  not assumed: all 5 images are referenced by at least one container —
  4 running task containers plus 9 retained *exited* swarm task
  containers (the default task-history retention). `docker image
  prune -a` will not remove an image while any container — running or
  stopped — still references it, and removing containers was not
  approved for this ticket, so 0B is the correct outcome, not a failed
  run.
- `apt clean` → **blocked**: `jtl` on `magni` has no `NOPASSWD` sudo
  rule (`sudo -n -l` and `sudo -n apt clean` both report "a password is
  required"). No interactive password was available in this session,
  and this session's safety controls correctly refused an attempt to
  probe for alternate credentials (e.g. root SSH login) — that path was
  not pursued further. Net effect on the ticket's goal is negligible:
  `/var/cache/apt/archives` measured **8.0K** before any action on
  *both* nodes, so a successful `apt clean` would not have moved the
  needle here.

**After**
```
$ df -h /
Filesystem      Size  Used Avail Use% Mounted on
/dev/mmcblk0p2  6.8G  6.0G  472M  93% /
```
Free space unchanged at 472M — expected given the above.

**Other finding (reported, not acted on)**: `du -xh --max-depth=1 /`
shows `/usr` (4.7G, OS packages) and `/var` (882M) as the two largest
consumers of the 6.8G root filesystem. Inside `/var`, a **528M
`/var/swap` file** (`-rw------- root root 528M`) is the single largest
item and appears to be an unused legacy swapfile — the node's active
swap is `/dev/zram0` (`cat /proc/swaps`), not `/var/swap`. Removing or
disabling it could free ~528M, but this needs root (unavailable this
session), is a swap/memory-management change rather than a docker/apt
cleanup, and was not stakeholder-approved for this ticket — flagged for
a follow-up decision rather than improvised. `journalctl --disk-usage`
on magni is 3.7M, too small for vacuuming to help.

**Service verification**: `docker service ls` (magni is the swarm
manager) is identical before and after:
```
hello_whoami               replicated   8/8
management_cadvisor        global       4/4
management_grafana         replicated   1/1
management_node-exporter   global       4/4
management_portainer       replicated   1/1
management_prometheus      replicated   1/1
```
`docker ps` before/after: the same 4 containers (`hello_whoami.5`,
`hello_whoami.1`, `management_node-exporter...`,
`management_cadvisor...`), same "Up 8 hours" status — unaffected.

### hodr (.148 — worker)

**Before**
```
$ df -h /
Filesystem      Size  Used Avail Use% Mounted on
/dev/mmcblk0p2  6.8G  6.1G  337M  95% /

$ docker system df
TYPE            TOTAL     ACTIVE    SIZE      RECLAIMABLE
Images          5         3         198.6MB   49.12MB (24%)
Containers      5         4         102.4kB   36.86kB (36%)
```

**Actions**
- `docker image prune -a -f` → removed 2 unused images (`prom/node-
  exporter` duplicate tag `e9cff4fc67b1`, 4 months old, 0 referencing
  containers; `traefik/whoami` duplicate tag `200689790a0a`, 17 months
  old, 0 referencing containers). `Total reclaimed space: 15.18MB`.
- `apt clean` → ran successfully (`jtl` has `(ALL) NOPASSWD: ALL` sudo
  on `hodr`, confirmed via `sudo -n -l`). The archives dir was already
  8.0K pre-clean, so this step's own direct contribution was
  negligible; most of the measured gain below is attributable to the
  image prune (Docker's dedup-aware 15.18MB estimate understates the
  actual overlay2 filesystem-level gain — a known characteristic of
  that storage driver, not a discrepancy to be concerned about).

**After**
```
$ df -h /
Filesystem      Size  Used Avail Use% Mounted on
/dev/mmcblk0p2  6.8G  5.9G  516M  93% /
```
Free space **337M → 516M (+179M)**.

**Service verification**: `docker ps` before/after: the same 4
containers (`hello_whoami.7`, `hello_whoami.3`,
`management_node-exporter...`, `management_cadvisor...`), same
"Up 8 hours" status — unaffected. (`docker service ls` correctly
refuses on a worker node — "This node is not a swarm manager"; swarm
state was verified via `docker service ls` on `magni` above.)

### Target

Target: **≥300 MB free** on each node — roughly 3.6x the ~83 MB venv,
chosen as comfortable headroom for `pip` transient install space
rather than a bare minimum.

| Node  | Before | After | Target | Met? |
|-------|--------|-------|--------|------|
| magni | 472M   | 472M  | ≥300M  | Yes — already met before cleanup; `docker image prune -a` verified 0B reclaimable, `apt clean` blocked (see above) |
| hodr  | 337M   | 516M  | ≥300M  | Yes — met after cleanup; +179M gained |

## Acceptance Criteria

- [ ] **PARTIAL** — `docker image prune -a` ran successfully on
      `magni` over SSH (0B reclaimed; verified correct, see Results).
      `apt clean` did **not** run on `magni`: blocked by missing sudo
      credentials (no `NOPASSWD` rule, unlike `hodr`). Net impact on
      the goal is negligible — see Results.
- [x] Same on `hodr` — both `docker image prune -a` and `apt clean`
      ran successfully (see Results).
- [x] `df -h` before/after recorded for both nodes (see Results): hodr
      increased 337M → 516M (+179M); magni held steady at 472M
      (already above target — see Results for why the two approved
      commands did not increase it further on this node).
- [x] Target set at ≥300 MB free (≈3.6x the 83 MB venv). Both nodes
      meet it after cleanup: magni 472M, hodr 516M.
- [x] Verified: `docker service ls` (magni) and `docker ps` (both
      nodes) identical before/after — no disruption. See Results.

**Known limitation / recommended follow-up**: `apt clean` could not be
run on `magni` due to a missing sudo credential for `jtl` (asymmetric
with `hodr`, which has `NOPASSWD: ALL`). This has ~0 practical impact
(the apt cache was already 8.0K), but for full literal compliance a
future pass could either (a) supply the `magni` sudo password
interactively and re-run `apt clean`, or (b) add a scoped `NOPASSWD`
sudoers entry for maintenance commands on `magni` to match `hodr` —
out of scope for this ticket (no Ansible changes here, per this
sprint's Out of Scope note) and not acted on without stakeholder
sign-off.

## Testing

- **Existing tests to run**: none — no source change.
- **New tests to write**: none — this is an operational ticket.
- **Verification command**: manual, over SSH (`df -h`, `docker system
  df`, `docker ps`, before and after).
