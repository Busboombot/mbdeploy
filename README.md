# mbdeploy

A standalone command-line tool for building and deploying micro:bit firmware to
one or more devices via pyOCD.

It identifies every connected micro:bit by its pyOCD **Unique ID**, joins that to
the board's `/dev/cu.*` serial port (via `ioreg` on macOS) and its firmware
`DEVICE:` announcement, and keeps a persistent registry at `config/devices.json`
(relative to the project you run it in). `probe` records each board and assigns
it a stable enumeration number (1..N); `list` and `probe` show every known board
with a `CONN` column saying whether it is plugged in right now, while `deploy`
targets a specific known device by enum number, name, serial path, or UID and
refuses to flash a board recorded as the radio relay unless `--force-relay` is
given.

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

## Subcommands

| Subcommand | Description |
|------------|-------------|
| `build`    | Compile the micro:bit firmware. |
| `deploy`   | Flash firmware to one or more micro:bit devices. |
| `list`     | List all known micro:bit devices and whether each is connected. Use `--fast` to skip reading names over SWD. |
| `probe`    | Probe all connected devices and update the registry. Use `--clear` to rebuild it from live devices only. |

Run `mbdeploy --help` or `mbdeploy <subcommand> --help` for full usage.

## Top-level flags

| Flag        | Description |
|-------------|-------------|
| `--version` | Print the installed mbdeploy version and exit. |
| `--agent`   | Print the detailed agent manual (usage, recipes, device model) and exit. |

`mbdeploy --agent` prints a complete manual aimed at AI coding agents and
power users driving the tool non-interactively.
