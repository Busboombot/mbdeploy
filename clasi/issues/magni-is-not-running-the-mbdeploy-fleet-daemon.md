---
status: pending
---

# `magni` is not running the fleet daemon — no passwordless sudo

## What is wrong

Three of Nolanet's four nodes run `mbdeploy serve` as a systemd system
unit and advertise their board over mDNS:

| Node | Board | Daemon |
|---|---|---|
| hodr .148 | `vevav` | active, enabled |
| loki .149 | `togov` | active, enabled |
| meili .150 | `gitev` | active, enabled |
| **magni .147** | `tigez` | **not deployed** |

`magni` has the application installed at `~/mbdeploy` with a working venv,
and `mbdeploy probe` there correctly reports `/dev/ttyACM0` — only the
service is missing. `mbdeploy serve --install-service --system` needs
`sudo`, and `jtl` has no passwordless sudo rule on `magni` (it does on
`hodr`, `loki`, and `meili`). This was hit twice: sprint 003 ticket 007
(`apt clean`) and ticket 008 (the unit install).

Consequence: `mbdeploy list --remote` shows 3 boards, not 4, and `tigez`
is unreachable over the LAN.

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
