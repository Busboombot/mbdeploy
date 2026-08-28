---
title: mbdeploy
blurb: What mbdeploy is, where each part of the manual lives, and what is still missing.
order: 0
updated: 2026-08-27
tags: ["micro:bit", pyocd, deployment, mdns]
---

# mbdeploy

`mbdeploy` builds micro:bit firmware and flashes it to a board over USB with
[pyOCD](https://pyocd.io/), addressing boards by a short, stable, human-readable
name instead of a 48-character hardware UID. It keeps a small JSON registry so a
board keeps that identity across replugs, and it refuses by default to reflash a
radio relay. Run as a daemon (`mbdeploy serve`) it advertises each board it can
see over mDNS, so `list`, `connect`, and `deploy` can reach that board from
another machine on the LAN with `--remote`.

Source: <https://github.com/Busboombot/mbdeploy>

> **The machines are documented elsewhere.** This site deliberately contains no
> hostnames, IP addresses, SSH details, or per-node install paths. The machine
> list, SSH access, per-node state, and current deployment status live on the
> internal Robot Garage wiki at **<http://robot-garage.home/doku.php?id=mbdeploy>**,
> reachable **only from the garage LAN**. See
> [Deploying the daemon](/subsystems/mbdeploy/deployment/).

## The manual

- **[Overview and installation](/subsystems/mbdeploy/overview/)** — what problems
  the tool solves, what it depends on, and how to install it on macOS and on
  Linux/aarch64 (all wheels, no compilation, ~83 MB venv).
- **[The device model](/subsystems/mbdeploy/device-model/)** — the conceptual
  heart: two chips and two identities, why a board's five-letter name cannot be
  computed from its UID, what the registry stores and guarantees, and how a
  target string is resolved.
- **[Command reference](/subsystems/mbdeploy/commands/)** — every subcommand and
  flag, the exit-code contract, and the stdout/stderr split scripts depend on.
- **[Building and flashing](/subsystems/mbdeploy/flashing/)** — what `deploy`
  actually runs, automatic recovery of a locked/APPROTECT part, and why pyOCD's
  output is streamed line by line.
- **[The fleet daemon](/subsystems/mbdeploy/fleet-daemon/)** — `serve`, its two
  mDNS services, both wire protocols verbatim, board exclusivity, and the rule
  that a flash preempts a live serial session.
- **[The remote client](/subsystems/mbdeploy/remote-client/)** — `--remote` on
  `list`, `connect`, and `deploy`, and which flags it ignores.
- **[Deploying the daemon](/subsystems/mbdeploy/deployment/)** — systemd units,
  tokens, group membership, and the pointer to the internal wiki where the
  actual machines are recorded.
- **[Troubleshooting](/subsystems/mbdeploy/troubleshooting/)** — the failures
  that look like bugs and are not, and the ones that are.

The tool also carries its own copy of most of this: `mbdeploy --agent` prints a
complete manual to stdout, written for an agent driving the tool
non-interactively.

## Open tasks and known gaps

Read this before trusting any security property, and before filing a bug for
something already known.

**1. `--remote` clients cannot authenticate to a `serve --token` daemon.**
The server implements `AUTH <token>` on both services, with a constant-time
comparison, and rejects anything else with `ERR auth required`. No client sends
it: there is no `--token` flag on `list`, `connect`, or `deploy`, and the client
code never writes an `AUTH` line. A token-protected daemon is therefore
currently **unreachable** by `connect --remote` and `deploy --remote` — they
fail immediately with `auth required`. `list --remote` is unaffected, because it
never opens a socket; it reads mDNS TXT records only, which the token does not
gate. Fixing this means adding `--token`/`--token-file` to the three client
subcommands and sending the handshake before the socket is handed to the console
or the `FLASH` header is written.

**2. The relay guard is inert on any board that never announced.**
Both the local guard and the daemon's guard read the registry's `role` field,
which is populated only from a board's serial announcement. On a board that has
never announced, `role` is empty, `is_relay()` returns `False`, and no `FLASH`
is ever refused for being a relay — `--force-relay` or not. On a fleet of silent
boards the relay tag has nothing to read, so **`--no-flash` and
`--token`/`--token-file` are the real access controls** for such a deployment.
Do not assume the relay guard is doing any work there.

**3. macOS only: `serve` can throw at process exit.**
With hardware attached, `mbdeploy serve` on macOS can hit an
`NSInvalidArgumentException` inside hidapi's `hid_exit()` during interpreter
shutdown. This is a pre-existing hidapi/IOKit thread-safety bug, not an mbdeploy
defect, and it happens after the daemon has already unregistered its mDNS
advertisements and closed its sockets. It was explicitly verified **not** to
reproduce on Linux/aarch64 (47 consecutive clean process exits with real
hardware attached).

## Notes for whoever edits this next

- **The code is normative.** Where a document and the source disagree, the source
  wins — check `--help` output and the implementation before repeating a claim
  from any doc, including this one.
- **No machine specifics here, ever.** Hostnames, IPs, SSH keys or usernames, and
  per-node install paths belong on the internal Robot Garage wiki, which is on
  the garage LAN. This site is world-readable.
- **`docs/design/` in the repo has stale spots.** `specification.md` §11.1 and
  `overview.md` still describe `port_serial_map()` as macOS-only and `ioreg`-based;
  it has been a cross-platform `pyserial` VID:PID scan since the Linux support
  work, and everything those sections derive from that limitation (no ports, no
  announcements, no roles off macOS) no longer holds. Do not carry those claims
  into this site.
- **Two announcement dialects exist**, and only one was parsed before 2026-08-27.
  A registry written by an older build can still hold a stale `role` — see
  [Troubleshooting](/subsystems/mbdeploy/troubleshooting/).
