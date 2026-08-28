---
status: pending
---

# `magni` is not running the fleet daemon — no passwordless sudo

## What is wrong

`magni` is the one Nolanet node not running `mbdeploy serve`.
`mbdeploy serve --install-service --system` needs `sudo`, and `jtl` has no
passwordless sudo rule on `magni` (it does on `hodr`, `loki`, and
`meili`). This was hit twice: sprint 003 ticket 007 (`apt clean`) and
ticket 008 (the unit install). The application itself is installed at
`~/mbdeploy` with a working venv — only the service is missing.

## Current state (2026-08-27, re-measured after the sprint closed)

The hardware moved during the arc. `magni` now has **no micro:bit attached
at all**, and `hodr` has **two** (`/dev/ttyACM0` and `/dev/ttyACM1`):

| Node | Boards | Daemon |
|---|---|---|
| magni .147 | **0** | not deployed |
| hodr .148 | 2 — `vevav`, `tigez` | active, enabled |
| loki .149 | 1 — `togov` | active, enabled |
| meili .150 | 1 — `gitev` | active, enabled |

`mbdeploy list --remote` shows all four boards across three hosts, so no
board is currently unreachable. **Deploying to `magni` is therefore not
urgent** — it matters only when a board is plugged back into it.

Worth recording: `hodr` picked up its second board as a live hotplug
arrival and advertised it with no restart, which is the first real-hardware
exercise of both the arrival path and the multi-board-per-host case. The
sprint's planning assumed one board per node throughout.

## Options

1. **Scoped `NOPASSWD` sudoers entry on `magni`**, matching the other
   three nodes, then re-run `mbdeploy serve --install-service --system`
   and `systemctl enable --now mbdeploy`. Most consistent with the rest of
   the cluster.
2. **Run the unit once interactively** with the sudo password to hand.
   One-off, leaves `magni` inconsistent with its siblings for future work.
3. **User unit instead.** `loginctl enable-linger jtl` succeeds
   unprivileged on `magni` and was run — `Linger=yes` is set there now, so
   `mbdeploy serve --install-service --user` plus
   `systemctl --user enable --now mbdeploy` would start at boot with no
   login. Needs no root at all. Diverges from the stakeholder's
   system-unit decision, so it wants an explicit call.

Option 3 is available immediately and needs no credentials. Option 1 is
the tidiest long-term.

## Note

`magni` is the Docker Swarm manager/leader. Whatever is done, do not
disturb the swarm, and do not reboot it to test — reboot survival was
verified on `hodr` instead (sprint 003 ticket 009).

`magni` also carries an unused 528 MB `/var/swap` legacy swapfile (active
swap is zram). It is the largest reclaimable item on that node — 472 MB
free at last measure — but removing it needs root and was never approved.

## Related

- Sprint 003 tickets 007, 008, 009; `docs/acceptance/003-009-multi-node-acceptance.md`.
