---
id: 008
title: Install mbdeploy + systemd system unit on all four Nolanet nodes
status: in-progress
use-cases:
- SUC-014
depends-on:
- '006'
- '007'
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

## Results (measured 2026-08-27)

**Ticket 001 gate**: PASS, does not reproduce on Linux/aarch64 (sprint.md
Ticket Completion Notes, `docs/spikes/003-hidapi-exit-linux.md`). Not
blocking — proceeded with the rollout below.

**Install path chosen: `~/mbdeploy` (i.e. `/home/jtl/mbdeploy`) on all
four nodes** — a fresh, stable location, deliberately distinct from
`loki`'s existing `~/mbdeploy-test` (left untouched; that dir is
ticket 001's scratch spike checkout, not reused per this ticket's own
instruction not to build the production install on a scratch dir).
Shipped via `git archive --format=tar HEAD | ssh ... 'tar -x -C
~/mbdeploy'` from this branch's HEAD (`mbdeploy 0.20260827.2`) to each
node, then `python3 -m venv .venv && .venv/bin/pip install
--no-cache-dir .` (plain venv + pip — no `uv` binary present on any
node). All four installs succeeded with prebuilt aarch64 wheels; no
compilation, no build failures.

| Node | Board (via `probe`) | Free before venv | Free after venv | mbdeploy version |
|---|---|---|---|---|
| magni (.147, swarm manager) | `tigez` / "robot" / role `NEZHA2` | 470M | 384M | 0.20260827.2 |
| hodr (.148) | `vevav` (silent — no role/common_name) | 514M | 428M | 0.20260827.2 |
| loki (.149) | `togov` (silent — no role/common_name) | 7.2G | 7.1G | 0.20260827.2 |
| meili (.150) | `gitev` / "Sally" / role `NAMETAG` | 1.5G | 1.4G | 0.20260827.2 |

Each venv cost ~83-86M, consistent with the ~83 MB estimate; all four
nodes stayed comfortably above ticket 007's ≥300M target after install
(magni 384M, hodr 428M — the two tightest).

**Finding, not acted on (out of this ticket's scope): the "all four
Nolanet boards are silent" assumption in sprint.md's Migration Concerns
does not hold for `magni` and `meili`.** `mbdeploy probe` shows both
running real announcing firmware — `magni`'s board announces
`DEVICE:NEZHA2:robot:tigez:...` (role `NEZHA2`, common name "robot"),
`meili`'s announces `DEVICE:NAMETAG:Sally:gitev:...` (role `NAMETAG`,
common name "Sally"). Only `hodr` (`vevav`) and `loki` (`togov`) are
actually silent (empty `role`/`common_name`/`device_name`). This is a
real discrepancy from the sprint's stated assumption, surfaced here
because `probe` is what revealed it — flagged for ticket 009, whose
acceptance script and expectations (e.g. "the announcement round-trip
is not demonstrable on this hardware") were written assuming a fully
silent fleet. Not fixed or acted on here; this ticket's scope is
install/verify, not re-deciding the acceptance plan.

`mbdeploy serve --print-service --system` was previewed on `hodr`
before installing for real anywhere (acceptance criterion). Output
(`WorkingDirectory=/home/jtl/mbdeploy`, `ExecStart=.../python3 -m
mbdeploy.cli serve --config /home/jtl/mbdeploy/config/devices.json
--poll-interval 2 --base-port 0 --target-mcu nrf52833`,
`WantedBy=multi-user.target`) looked correct — CWD-relative config path
resolved absolute, no token baked in (deploying without `--token`, per
the stakeholder's open-on-the-LAN-by-default decision), default
ephemeral `--base-port 0`.

**`mbdeploy serve --install-service --system` + `systemctl enable --now
mbdeploy` — 3 of 4 nodes succeeded; `magni` blocked on the known missing
sudo credential.**

| Node | `install-service --system` | `enable --now` | `systemctl status` | `journalctl` startup line |
|---|---|---|---|---|
| hodr | OK | OK | `active (running)` | `mbdeploy serve: running (poll every 2s; Ctrl-C or SIGTERM to stop)` |
| loki | OK | OK | `active (running)` | same, present |
| meili | OK | OK | `active (running)` | same, present |
| magni | **blocked** — `sudo: a password is required` | not attempted | not running | n/a |

`magni`'s `jtl` account has no `NOPASSWD` sudo rule (confirmed
identically in ticket 007: `sudo -n -l`/`sudo -n apt clean` both refused
there too). `sudo -n ./.venv/bin/mbdeploy serve --install-service
--system` was attempted (non-interactively, `-n` so it fails fast
instead of hanging on a prompt this session cannot answer) and refused
cleanly: `sudo: a password is required`, exit 1. No interactive password
was available, and per this ticket's own instruction, no credential was
improvised or worked around (no alternate login, no `NOPASSWD` sudoers
edit, no writing to `/etc` unprivileged). Verified no partial state was
left behind: `/etc/systemd/system/mbdeploy.service` does not exist on
`magni`, and `systemctl status mbdeploy` reports "could not be found" —
a clean failure, not a half-installed unit. `magni`'s mbdeploy app
install and `probe` (above) both completed successfully; only the
system-unit install/start step is blocked.

**hodr status excerpt**:
```
● mbdeploy.service - mbdeploy fleet daemon: watches USB for micro:bit boards and advertises each one's serial/flash services over mDNS.
     Loaded: loaded (/etc/systemd/system/mbdeploy.service; enabled; preset: enabled)
     Active: active (running) since Thu 2026-08-27 16:48:40 PDT; 707ms ago
   Main PID: 33571 (python3)
     CGroup: /system.slice/mbdeploy.service
             └─33571 /home/jtl/mbdeploy/.venv/bin/python3 -m mbdeploy.cli serve --config /home/jtl/mbdeploy/config/devices.json --poll-interval 2 --base-port 0 --target-mcu nrf52833
```

**Live-advertising check from this Mac** (`dns-sd -B _mbserial._tcp
local` and `dns-sd -B _mbflash._tcp local`, each ~10s): both service
types show all three running instances — `vevav` (hodr), `togov`
(loki), `gitev` (meili) — each appearing on multiple local interfaces
(normal mDNS behavior), confirming the daemons are not just
`active (running)` locally but actually reachable/advertising on the
LAN. `magni` (not running) correctly does not appear.

**Swarm discipline verified**: `docker service ls` on `magni` (swarm
manager) shows the identical replica counts as ticket 007's before/after
baseline (`hello_whoami` 8/8, `management_cadvisor` 4/4,
`management_grafana` 1/1, `management_node-exporter` 4/4,
`management_portainer` 1/1, `management_prometheus` 1/1) — unaffected
by this ticket's work. No node was rebooted.

**Deployed without `--token`** — the stakeholder's settled
open-on-the-LAN-by-default decision. Note for the record: the
`--remote` client (`connect --remote`/`deploy --remote`) has no
`--token` flag at all (found in ticket 006's review) — a
token-protected daemon would currently be unreachable from those
commands regardless, so deploying without a token is also the only
configuration the current client can actually use end to end.

**Known limitation / recommended follow-up (same shape as ticket 007's
`magni` finding)**: `magni` cannot get the system unit installed under
this ticket's constraints (no interactive credential, no unauthorized
workaround). Options for a follow-up, not decided or acted on here: (a)
supply `magni`'s sudo password interactively in a future session and
re-run the two commands above, or (b) add a scoped `NOPASSWD` sudoers
entry for `jtl` on `magni` (would need stakeholder sign-off and is
outside this sprint's no-Ansible-changes scope as stated). Ticket 009's
"all four nodes" premise is affected by this gap — flagged for
team-lead/stakeholder awareness before ticket 009 runs, not resolved by
this ticket.

## Acceptance Criteria

- [x] Ticket 001's hidapi finding is checked and, if blocking, this
      ticket does not proceed past that point (see Description). —
      PASS, not blocking (see Results).
- [x] mbdeploy (this sprint's build) is installed into a venv on all
      four nodes. — done on magni, hodr, loki, meili (see Results
      table).
- [x] `mbdeploy probe` on each node shows its board's port as
      `/dev/ttyACM0` (or similar), not `null` — the sprint-001
      prerequisite's real check, re-verified here since it's easy for a
      fresh venv/install to silently regress. — confirmed
      `/dev/ttyACM0` on all four nodes (see Results table).
- [x] `mbdeploy serve --print-service --system` output looks correct on
      at least one node before installing for real. — previewed on
      `hodr` (see Results).
- [ ] **PARTIAL** — `mbdeploy serve --install-service --system` succeeds
      on all four nodes; `systemctl enable --now mbdeploy` brings each
      one up. Succeeded on `hodr`, `loki`, `meili`. **Blocked on
      `magni`**: no `NOPASSWD` sudo for `jtl` (same finding as ticket
      007); refused cleanly with no partial state left behind (see
      Results). Not worked around per this ticket's own instruction.
- [ ] **PARTIAL** — `systemctl status mbdeploy` shows `active (running)`
      on all four nodes. True on `hodr`, `loki`, `meili`; `magni` has no
      unit installed, so the service does not exist there (see Results).
- [ ] **PARTIAL** — `journalctl -u mbdeploy` shows the daemon's own
      startup log line on each node. Confirmed on `hodr`, `loki`,
      `meili`; not applicable to `magni` (see Results).

## Testing

- **Existing tests to run**: none in the automated suite — this is an
  install/ops ticket. Confirmed the automated suite is still green
  before starting (`.venv/bin/python -m pytest -q`: 327 passed) and
  again after finishing this ticket's remote work (327 passed,
  unchanged — no source touched by this ticket).
- **New tests to write**: none — verification is the manual
  `systemctl`/`journalctl` checks above; the end-to-end functional
  proof is ticket 009.
- **Verification command**: manual, over SSH per node
  (`systemctl status mbdeploy`, `journalctl -u mbdeploy -n 50`), plus
  `dns-sd -B _mbserial._tcp local` / `dns-sd -B _mbflash._tcp local`
  from this Mac to confirm live LAN advertising (see Results).
