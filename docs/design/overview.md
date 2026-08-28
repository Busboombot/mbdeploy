# mbdeploy — Project Overview

`mbdeploy` is a standalone command-line tool for building micro:bit
firmware and flashing it, over USB via pyOCD, to one board out of a
classroom fleet — addressed by a short, stable name rather than by a
48-character hardware UID.

This document is context. The normative behaviour lives in
`specification.md`, and the workflows the tool must support live in
`usecases.md`. Where any of the three disagrees with the shipped source,
the source is authoritative — these documents were reverse-engineered
from it.

## Who uses it

- **Classroom operators** — the teacher or student running a lab of
  micro:bit robots. They plug in a board, want to know which board it
  is, build the firmware, flash it, and then talk to it over serial to
  check that it came up.
- **AI coding agents** — the reason `mbdeploy --agent` exists. An agent
  drives the tool non-interactively, cannot see the desk, and must be
  able to trust the exit code rather than scraping human-readable text.
  The command surface is shaped around that: a strict `0`/non-zero
  contract, the board's serial reply alone on stdout, and every status
  line on stderr.

## The problem

A fleet of a dozen micro:bits presents three related problems, and every
significant design decision in `mbdeploy` traces back to one of them.

**Boards have no usable address.** pyOCD identifies a board by the USB
serial number of its on-board DAPLink interface chip — a 40-to-52-character
hex string. Nobody can type that, and nothing about it says which board on
the bench it names. Meanwhile the board *does* have a human-scale
identity: the five-letter word (`tovez`, `gopiv`) its runtime derives from
`FICR.DEVICEID[1]` and shows when pairing. But that word belongs to a
*different chip* — the target nRF — so it cannot be computed from the UID.
`mbdeploy` closes the gap by attaching through the debug probe the UID
names and reading `DEVICEID[1]` over SWD, then caching the result in a
persistent registry keyed by UID.

**Ports move.** macOS re-issues `/dev/cu.usbmodem*` names on every
reconnect, so two boards routinely trade paths between sessions. A
remembered port is a trap: acting on a stale one means acting on a
different board than the path names. That has already produced a real
wrong-board flash, and the code carries the fix as an explicit invariant —
`deploy` resolves a `/dev/…` path against the *live* `ioreg` map and
refuses rather than guessing, and `resolve_target()` will not match a path
against the registry at all.

**Relays must not be reflashed by accident.** A fleet has robots and it
has one or two radio bridges. Flashing robot firmware onto the bridge
takes the whole classroom off the air. `mbdeploy` reads the board's role
out of its `DEVICE:` announcement, treats anything containing `RELAY` or
`BRIDGE` as a gateway, and refuses to flash it unless `--force-relay`
says the operator meant it.

## The shape of the solution

Five subcommands over a small JSON registry:

| Subcommand | Role in the workflow |
|------------|----------------------|
| `probe`    | Walks every connected probe, refreshes ports, captures announcements, reads missing board names over SWD, and saves the registry. |
| `list`     | Prints every board the registry has ever seen plus anything newly attached, with a `CONN` column. Cheap; `--fast` makes it cheaper. |
| `build`    | Shells out to the project's `build.py`. |
| `deploy`   | Resolves a target, checks the relay guard, confirms the board is live, optionally builds, then flashes and resets — recovering a locked part with a CTRL-AP mass erase if the first flash fails. |
| `connect`  | Opens the board's serial port with DTR held low, and either runs one request/response exchange or hands the port to the user. |

The registry (`config/devices.json`, CWD-relative) is deliberately
append-only: entries are never deleted, `enum` is assigned once, `port` is
always refreshed, and a probe that reads nothing leaves the last known
identity intact rather than clearing it. That is what makes `list`
trustworthy for names while keeping ports honest about their freshness.

## Current state

Shipped and working: all five subcommands, the top-level `--version` and
`--agent` flags, the SWD name read, the relay guard, live port resolution
for `deploy`, mass-erase recovery, and both serial modes. Version
`0.20260826.1`; Python ≥ 3.10; depends on `pyocd>=0.44.1` and
`pyserial>=3.5`. The bundled agent manual (`--agent`) is the
user-facing companion to this document set.

One limitation is structural rather than incidental, and is stated plainly
here because it shapes what can be relied on: **`port_serial_map()` shells
out to macOS `ioreg` and returns `{}` on every other platform.** Off
macOS, `deploy` refuses `/dev/…` targets by design, `probe` records
`port: null` and therefore never captures an announcement, and the PORT
column is empty. Enum, name, and UID targeting work everywhere.
