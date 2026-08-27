# mbdeploy

A standalone command-line tool for building and deploying micro:bit firmware to
one or more devices via pyOCD.

It identifies every connected micro:bit by its pyOCD **Unique ID**, joins that to
the board's live serial port (found with a `pyserial` VID:PID scan, on macOS or
Linux alike) and its firmware `DEVICE:` announcement, and keeps a persistent
registry at `config/devices.json` (relative to the project you run it in).
`probe` records each board and assigns it a stable enumeration number (1..N);
`list` and `probe` show every known board with a `CONN` column saying whether
it is plugged in right now, while `deploy` targets a specific known device by
enum number, name, serial path, or UID and refuses to flash a board recorded
as the radio relay unless `--force-relay` is given. A `/dev/…` path is
resolved against the **live** serial-port mapping — the board on that port
right now — never against the registry's remembered `port`, which goes stale
as the OS renames ports across reconnects.

## Addressing a board

A board is addressed by its **own** five-letter name (`tovez`, `gopiv`), by its
enum number, by UID, or by `/dev/` path. The `COMMON NAME` column — "robot",
"Jane's robot" — is a human label for the board's role, shown so you can find a
board on a desk. It is never a target: two boards can share one, and it changes
when a class is reassigned.

## Board names

Every micro:bit has a five-letter name (`tovez`, `gopiv`, …) that its runtime
derives from `FICR.DEVICEID[1]` on the target nRF — the same name the board
broadcasts when pairing. That word is **not** encoded in the UID: the UID is the
USB serial number of the on-board DAPLink interface chip, a different chip with
a different id.

`mbdeploy` gets the name anyway, by attaching through the probe the UID names
and reading `DEVICEID[1]` over SWD (an attach, not a halt or reset — it does not
disturb running firmware). So a board shows a name even if it has never been
flashed with announcing firmware, or its serial port is busy. `probe` caches the
name in the registry; `list` reads it live only for boards the registry doesn't
know yet, and `list --fast` skips that read entirely.

## Installation

Install the latest from GitHub:

```bash
pipx install git+https://github.com/Busboombot/mbdeploy.git
```

Or clone and install editable for local development:

```bash
git clone https://github.com/Busboombot/mbdeploy.git
pipx install --editable ./mbdeploy
```

Re-install after editing source:

```bash
pipx install --editable --force ./mbdeploy
```

Run `mbdeploy` from the root of the project whose firmware you're deploying so it
finds `./build.py`, `./MICROBIT.hex`, and `./config/devices.json`. All project
paths are CWD-relative, with `--config` / `--hex` / `--build-cmd` overrides.

`mbdeploy` runs on Linux (including Raspberry Pi OS) as well as macOS — see
"Linux / Raspberry Pi setup" in `mbdeploy --agent` for group-membership and
udev details specific to headless boards.

## Subcommands

| Subcommand | Description |
|------------|-------------|
| `build`    | Compile the micro:bit firmware. |
| `deploy`   | Flash firmware to one or more micro:bit devices. |
| `list`     | List all known micro:bit devices and whether each is connected. Use `--fast` to skip reading names over SWD. |
| `probe`    | Probe all connected devices and update the registry. Use `--clear` to rebuild it from live devices only. |
| `connect`  | Open a serial connection to a board, or send it one line and print the reply. |
| `serve`    | Run the fleet daemon: watch USB and advertise each board's serial/flash services over mDNS. |

Run `mbdeploy --help` or `mbdeploy <subcommand> --help` for full usage.

## Serving a fleet over the network

`serve` turns this host into a daemon: it watches USB and advertises
each connected board's serial port and flash access over mDNS
(`_mbserial._tcp` / `_mbflash._tcp`), so another machine on the LAN can
reach it with `--remote` on `list`, `connect`, and `deploy` — the same
target syntax as the local commands, resolved over mDNS instead of a
local registry. `--remote` is mutually exclusive with a `/dev/…` target
(rejected before any network I/O), and `deploy --remote` requires an
explicit target since there is no local registry of remote boards to
auto-pick from. See "Serving a fleet over the network" in
`mbdeploy --agent` for the wire protocol, access controls
(`--token`/`--token-file`/`--no-flash`), and systemd deployment
(`--print-service`/`--install-service`).

## Talking to a board

`connect` opens the board's serial port — at 115200 baud unless `--baud` says
otherwise — and either hands it to you interactively or runs a single exchange:

```bash
mbdeploy connect tovez                      # interactive; Ctrl-D or Ctrl-C to exit
mbdeploy connect tovez "HELLO"              # send one line, print the reply, exit
mbdeploy connect tovez --baud 9600 "HELLO"  # same, at 9600 baud
```

Anything after the target is joined with spaces, sent as one newline-terminated
line, and the board's answer is printed to stdout. The reply is complete once
the board falls quiet, and the whole exchange is capped by `--timeout` (2 s by
default), so a board that streams telemetry can't hang the command. Exit status
is 0 when the board answered and 1 when it said nothing, so a script can just
check the exit code.

The reply goes to stdout and every status line to stderr, so
`mbdeploy connect tovez STATUS` pipes cleanly. Boards are addressed the same way
as with `deploy` — enum, name, or UID — plus a raw `/dev/...` path (e.g.
`/dev/cu.usbmodem1234` on macOS, `/dev/ttyACM0` on Linux), which is opened
verbatim and so works even for a board that has never been probed. Connecting
holds DTR low, so it does not reset the board.

## Top-level flags

| Flag        | Description |
|-------------|-------------|
| `--version` | Print the installed mbdeploy version and exit. |
| `--agent`   | Print the detailed agent manual (usage, recipes, device model) and exit. |

`mbdeploy --agent` prints a complete manual aimed at AI coding agents and
power users driving the tool non-interactively.
