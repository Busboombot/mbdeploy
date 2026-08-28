---
title: Overview and installation
blurb: What mbdeploy solves, what it depends on, and how to install it on macOS and Linux.
order: 10
updated: 2026-08-27
tags: [install, pyocd, raspberry-pi]
---

# Overview and installation

## What it is

`mbdeploy` is a standalone command-line tool that builds micro:bit firmware and
flashes it over USB via [pyOCD](https://pyocd.io/), to one board out of a
classroom fleet. It keeps a persistent JSON registry of every board it has seen,
so boards can be addressed by a short stable name rather than by a 48-character
hardware UID. Since the fleet daemon landed it also serves boards over the
network: `mbdeploy serve` on a host with micro:bits attached makes each of them
reachable from any machine on the same LAN.

## Who it is for

- **Classroom operators.** Plug in a board, find out which board it is, build the
  firmware, flash it, then talk to it over serial to check that it came up.
- **AI coding agents.** The reason `mbdeploy --agent` exists. An agent drives the
  tool non-interactively, cannot see the desk, and must be able to trust the exit
  code rather than scraping human-readable text. The whole command surface is
  shaped around that: a strict `0` / non-zero contract, the board's serial reply
  alone on stdout, every status line on stderr.

## The three problems it exists to solve

Nearly every design decision in `mbdeploy` traces back to one of these.

**Boards have no usable address.** pyOCD identifies a board by the USB serial
number of its on-board DAPLink interface chip — 40 to 52 hex characters. Nobody
can type that, and nothing about it says which board on the bench it names.
Meanwhile the board *does* have a human-scale identity: the five-letter word
(`tovez`, `gopiv`) its runtime derives and shows when pairing. But that word
belongs to a *different chip*, so it cannot be computed from the UID.
`mbdeploy` closes the gap by attaching through the debug probe the UID names and
reading the target's device id over SWD. See
[The device model](/subsystems/mbdeploy/device-model/).

**Ports move.** The OS re-issues serial port names on every reconnect, so two
boards routinely trade paths between sessions. A remembered port is a trap:
acting on a stale one means acting on a different board than the path names.
That has already produced a real wrong-board flash, and the fix is now an
explicit invariant in the code — `deploy` resolves a `/dev/…` path against the
*live* port map and refuses rather than guessing.

**Relays must not be reflashed by accident.** A fleet has robots and it has one
or two radio bridges. Flashing robot firmware onto the bridge takes the whole
classroom off the air. `mbdeploy` reads a board's role out of its announcement,
treats anything containing `RELAY` or `BRIDGE` as a gateway, and refuses to flash
it unless `--force-relay` says the operator meant it. (This guard depends on the
board having announced a role at all — see the caveat in
[Open tasks](/subsystems/mbdeploy/).)

## The shape of the tool

| Subcommand | Role in the workflow |
|------------|----------------------|
| `probe`   | Walks every connected probe, refreshes ports, captures announcements, reads missing board names over SWD, saves the registry. |
| `list`    | Prints every board the registry has ever seen plus anything newly attached, with a `CONN` column. Cheap; `--fast` makes it cheaper. |
| `build`   | Shells out to the project's `build.py`. |
| `deploy`  | Resolves a target, checks the relay guard, confirms the board is live, optionally builds, then flashes and resets — recovering a locked part with a mass erase if the first flash fails. |
| `connect` | Opens the board's serial port with DTR held low, and either runs one request/response exchange or hands the port to the user. |
| `serve`   | Runs the fleet daemon: watches USB and advertises each board's serial and flash services over mDNS. |

Full detail in the [command reference](/subsystems/mbdeploy/commands/).

## Requirements

| | |
|---|---|
| Python | ≥ 3.10 |
| Dependencies | `pyocd>=0.44.1`, `pyserial>=3.5`, `zeroconf>=0.150.0` (plus their transitive requirements — 23 packages in total) |
| Platforms | macOS and Linux, including Raspberry Pi OS on aarch64 |
| Hardware | A micro:bit whose on-board DAPLink interface enumerates as USB `0d28:0204` |

`mbdeploy` never needs a compiler at install time on either platform: every
dependency ships as a prebuilt wheel, aarch64 included.

## Installing

The tool is normally installed with `pipx`, into its own isolated environment:

```bash
pipx install git+https://github.com/Busboombot/mbdeploy.git
```

For local development, clone and install editable:

```bash
git clone https://github.com/Busboombot/mbdeploy.git
pipx install --editable ./mbdeploy
```

Re-install after editing the source:

```bash
pipx install --editable --force ./mbdeploy
```

Confirm what you actually got:

```bash
mbdeploy --version     # prints "mbdeploy <version>"
mbdeploy --agent       # prints the bundled agent manual to stdout
```

`pyocd` is a declared dependency, so it is importable inside the same
environment even when its console script is not on `PATH`. `mbdeploy` therefore
always invokes it as `<python> -m pyocd`, never as a bare `pyocd` — a pipx
install works with no extra setup.

## Where it expects to be run

Run `mbdeploy` from the root of the project whose firmware you are deploying.
All project paths are relative to the current working directory:

| Path | Used by | Override |
|---|---|---|
| `./build.py` | `build`, `deploy --build` | `--build-cmd` (on `build`) |
| `./MICROBIT.hex` | `deploy` | `--hex PATH` |
| `./config/devices.json` | `deploy`, `list`, `probe`, `connect`, `serve` | `--config PATH` |

This matters for `serve` under a service manager, which gives the process no
useful working directory of its own — see
[Deploying the daemon](/subsystems/mbdeploy/deployment/).

## Linux and Raspberry Pi

`mbdeploy` behaves identically on Linux. The live port map is a `pyserial`
VID:PID scan rather than any OS-specific tool, so nothing about target
resolution or the registry is macOS-only. Four Linux-specific facts are worth
knowing, all verified on Raspberry Pi OS (Debian Bookworm, aarch64, Python
3.13.5) against real micro:bit V2 hardware:

- **Port naming.** A micro:bit enumerates as `/dev/ttyACM0`, not the macOS
  `/dev/cu.usbmodem*` spelling. Use that form when targeting by path.
- **Group membership is the whole of the access story.** The user running
  `mbdeploy` must be in **`plugdev`** (raw USB, needed by pyOCD) and
  **`dialout`** (the serial port). Without both, the live port map comes back
  empty even though a probe is connected, `deploy` refuses a `/dev/…` target,
  and no announcement can be read:

  ```bash
  sudo usermod -aG plugdev,dialout <user>
  ```

  Re-login (or reboot) before testing — group membership is only picked up by a
  new session.
- **No new udev rule is needed on Raspberry Pi OS.** It already ships
  `/lib/udev/rules.d/70-microbit.rules`, which matches
  `SUBSYSTEM=="usb", ATTR{idVendor}=="0d28"` and applies `TAG+="uaccess"`. Both
  `/dev/ttyACM0` and the underlying USB device node come up `root:plugdev 0660`,
  so `plugdev` membership alone is what grants headless access. Do **not** add a
  `MODE="0666"` rule for this vendor id — an early draft of this document
  proposed one and it is unnecessary.
- **pyOCD runs headless and unprivileged.** With the two group memberships in
  place, `python -m pyocd list` enumerates the probe and identifies the target as
  nRF52833 / micro:bit V2 with no root and no additional pyOCD configuration.

**Install footprint on aarch64:** all 23 dependencies resolve to prebuilt
aarch64 wheels on Raspberry Pi OS — there is no compilation step at any point —
and a fresh virtualenv with `mbdeploy` installed measures about **83 MB**. That
is small enough to sit on a Pi alongside whatever else the node runs.
