---
title: Deploying the daemon
blurb: Installing mbdeploy serve as a systemd service on a node with boards attached — and where the actual machines are documented.
order: 70
updated: 2026-08-27
tags: [systemd, deployment, raspberry-pi, operations]
---

# Deploying the daemon

> ## The machines are on the internal wiki
>
> This page is **generic**. It tells you how to install and run the daemon on
> *a* node; it deliberately names no hosts.
>
> **The machine list, SSH access, per-node state, and current deployment status
> live on the internal Robot Garage wiki:**
>
> # <http://robot-garage.home/doku.php?id=mbdeploy>
>
> That wiki is reachable **only from the garage LAN**. If you are looking for
> which nodes exist, which of them have boards plugged in, where mbdeploy is
> installed on each, which daemons are currently running, or how to log in —
> that is the page, and it is the only place that information belongs. Nothing
> of the sort is published here, because this site is world-readable.
>
> If you change the fleet — a node added or removed, a board moved, an install
> path or service changed, a daemon down — update that page in the same piece of
> work.

## What a deployment is

One host, one `mbdeploy serve` process, one systemd unit. The host needs USB
access to the boards; everything else reaches those boards over the LAN with
`--remote`. Several boards on one host is fine — the watcher picks up a hotplug
arrival and advertises the new board with no restart.

**It cannot be a container.** A Docker Swarm service cannot reach a DAPLink
probe (no `--device`, no `--privileged`), which is why a systemd **system** unit
is the supported shape and the default.

## Prepare the host

1. **Install `mbdeploy`** for the account that will run the service — see
   [Overview and installation](/subsystems/mbdeploy/overview/). On aarch64 every
   dependency is a prebuilt wheel; nothing compiles, and a fresh venv is about
   83 MB.

2. **Put the service user in two groups.** `serve` both flashes (raw USB, via
   pyOCD) and relays serial, so it needs both:

   ```bash
   sudo usermod -aG plugdev,dialout <service-user>
   ```

   `plugdev` for pyOCD's raw USB access, `dialout` for the serial port. Re-login
   or reboot before starting the service — group membership only applies to new
   sessions.

3. **Do not add a udev rule.** On Raspberry Pi OS none is needed:
   `/lib/udev/rules.d/70-microbit.rules` already ships, tagging the micro:bit's
   vendor id with `uaccess`, and both `/dev/ttyACM*` and the underlying USB node
   come up `root:plugdev 0660`. `plugdev` membership is what grants headless
   access. In particular, do **not** write a `MODE="0666"` rule — an early draft
   of this documentation proposed one, and it is unnecessary.

4. **mDNS needs no special setup.** The daemon's own zeroconf stack coexists
   cleanly with a running `avahi-daemon`; this was verified on real hardware,
   including registration, browsing from a separate process, and UTF-8 TXT
   round-trips. No config change, and no `avahi-publish` shim, is required.

## Generate the unit

`serve --print-service` renders the systemd unit for the exact `serve`
invocation you would otherwise have typed, to stdout, touching nothing on disk:

```bash
cd /path/to/project    # WorkingDirectory is baked in from this directory
mbdeploy serve --print-service --base-port 9000 --target-mcu nrf52833
```

Everything that affects the daemon is baked into `ExecStart` with its effective
value — including `--config` resolved to an absolute path, because a service
manager gives the process no useful working directory to resolve a relative one
against. Flags that configure the *rendering* (`--print-service`,
`--install-service`, `--system`, `--user`) are naturally left out.

The command in `ExecStart` is the installed `mbdeploy` console script if one is
on `PATH` (resolved to an absolute path), and otherwise
`<python> -m mbdeploy.cli` for the interpreter mbdeploy is importable from.

The generated unit is a plain `Type=simple` service with
`After=`/`Wants=network-online.target`, `Restart=on-failure`, `RestartSec=2`, the
baked `WorkingDirectory` and `ExecStart`, and an `[Install]` section. Regenerate
it rather than hand-editing it when the invocation changes.

## Install it

`serve --install-service` writes that same unit to disk and exits **without**
running the daemon; you run `daemon-reload` and `enable --now` yourself:

```bash
sudo mbdeploy serve --install-service --base-port 9000 --target-mcu nrf52833
sudo systemctl daemon-reload
sudo systemctl enable --now mbdeploy
```

### System is the default, and that is a decision

`--install-service` with neither `--system` nor `--user` writes
`/etc/systemd/system/mbdeploy.service` with `WantedBy=multi-user.target`, and
requires root.

A systemd **`--user`** unit does **not** start at boot and does not survive
logout on a host with the default `Linger=no`. Pass `--user` only when that
tradeoff is acceptable — a workstation you stay logged into — or pair it with
linger:

```bash
mbdeploy serve --install-service --user
loginctl enable-linger <user>    # required to start at boot / survive logout
```

A `--user` install writes `~/.config/systemd/user/mbdeploy.service` with
`WantedBy=default.target`, needs no root, and prints a reminder about linger to
stderr.

### Tokens are never written into `ExecStart`

A literal `--token SECRET` in a unit file would be readable by anyone on the box
via `systemctl cat`. So:

- **`--install-service --token SECRET`** writes the secret to a fresh
  mode-`0600` file — `/etc/mbdeploy/token` for a system unit,
  `~/.config/mbdeploy/token` for `--user` — and bakes `--token-file <that path>`
  into `ExecStart` instead of the secret.
- **`--token-file PATH`**, naming a file you created yourself, is resolved to an
  absolute path and passed straight through.
- **`--print-service --token SECRET` is refused outright.** `--print-service`
  touches no filesystem, so there is nowhere to put the secret and it must not be
  emitted literally. Use `--token-file` with `--print-service`, or switch to
  `--install-service`.

```bash
sudo mbdeploy serve --install-service --token-file /etc/mbdeploy/token
# or let the install create the file for you:
sudo mbdeploy serve --install-service --token 'a-shared-secret'
```

Before you enable a token, read the client-side gap: **no `--remote` client can
authenticate today**, so a token-protected daemon is unreachable by
`connect --remote` and `deploy --remote`. See
[The fleet daemon](/subsystems/mbdeploy/fleet-daemon/#access-controls). If what
you want is to stop remote flashing, `--no-flash` works today and the token does
not.

## Verify

On the node:

```bash
systemctl is-active mbdeploy
systemctl is-enabled mbdeploy
journalctl -u mbdeploy -b
```

A healthy start is two lines and nothing else — systemd's own `Started …`, then

```
mbdeploy serve: running (poll every 2s; Ctrl-C or SIGTERM to stop)
```

During a flash the journal additionally carries pyOCD's own progress output and,
if a locked part was recovered, the one mass-erase notice. Tracebacks are not
normal.

From another machine on the same LAN:

```bash
mbdeploy list --remote
```

Every board on every node running the daemon should appear, one row each. A node
whose daemon is not installed or not running simply contributes no rows — which
is why "a board is missing" and "a node is down" look identical from here, and
why the node inventory on the internal wiki matters.

`systemctl stop mbdeploy` makes a node's boards vanish from `list --remote`
within a poll interval, and `start` brings them back; that is also the closest
you can get to testing unplug/replug without physical access.

## Upgrading a node

Update the source on the node, reinstall into its environment, then restart the
unit:

```bash
systemctl restart mbdeploy
```

A restart drops any live sessions and any flash in flight — the client sees its
connection close — so restart when nothing is mid-deploy. The advertisements come
back once the watcher's next poll re-enumerates the boards, typically within a
few seconds of the service becoming active.

Record what you changed, on which node, on the internal wiki page above.
