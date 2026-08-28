---
title: The device model
blurb: Two chips and two identities, what the registry stores, and how a target string is resolved.
order: 20
updated: 2026-08-27
tags: [registry, swd, targeting]
---

# The device model

This is the conceptual heart of `mbdeploy`. Almost every surprising behaviour
elsewhere in the tool follows from something on this page.

## Two chips, two identities

A micro:bit is two processors on one board:

- the **interface chip**, running DAPLink, which presents the USB CDC serial
  port and the CMSIS-DAP debug probe;
- the **target**, an nRF52833 (nRF51 on a V1), which runs the firmware you flash.

They have unrelated identities, and confusing them is the single most common
mistake:

| | **UID** | **Board name** |
|---|---|---|
| What it is | The USB serial number of the DAPLink **interface** chip | The five-letter word (`tovez`, `gopiv`) the micro:bit runtime derives from `FICR.DEVICEID[1]` on the **target** nRF |
| Looks like | 40–52 hex characters | Five letters, alternating consonant and vowel |
| Who uses it | pyOCD, to select which probe to talk to | Humans, the pairing screen, and `mbdeploy` targeting |
| Where `mbdeploy` gets it | USB enumeration | Read over SWD through the probe |

**The name cannot be computed from the UID.** They belong to different chips.
Any code that tries to slice a name out of a UID string is wrong, and any
apparent correspondence between the two on a particular board is coincidence.

## How the name is read

The name *can* be read through the probe the UID names. `mbdeploy` opens a pyOCD
session against the probe with **`connect_mode="attach"` — no halt, no reset** —
and reads the 32-bit word at `FICR.DEVICEID[1]` (`0x10000064`). This takes about
0.2 s per board, and has three properties that matter:

- It needs **no serial port**, so a board whose port is held by another program
  still reports a name.
- It needs **no cooperating firmware**, so a blank, freshly unboxed, or
  never-flashed board still reports a name.
- It **does not disturb** whatever the board is doing. Attaching is not
  resetting; running firmware keeps running.

The read is attempted first with the caller's `--target-mcu` (default
`nrf52833`) and, if that fails, once more with no target override so pyOCD can
auto-detect a board the guess does not fit (a V1). `FICR.DEVICEID[1]` lives at
the same address on nRF51 and nRF52, so the same read works for both. If both
attempts fail — the probe is busy mid-flash, the part is locked — the name is
simply left blank and nothing else about the registry entry changes.

The encoding itself is the micro:bit runtime's own `microbit_friendly_name()`:
the 32-bit word is written out as five base-5 digits, and digit *i* (counting
from the least significant) selects a letter from column *i* of a fixed
codebook, landing at position `4 - i` of the name. The columns alternate
consonants (`z v g p t`) and vowels (`u o i e a`), which is why every micro:bit
name reads as a pronounceable word. For example `2314287040` encodes `tovez`.

## The announcement

Separately from the SWD read, `probe` opens each board's serial port, holds DTR
and RTS low so opening it does not reset the board, waits 0.3 s, sends
`HELLO\n`, and reads lines for up to 1.6 s looking for the board's own
announcement.

**Two dialects are accepted.** Both carry the same five fields in the same
order — sentinel, role, common name, device name, serial:

| Dialect | Shape | Example |
|---|---|---|
| Colon (relay firmware) | `DEVICE:<role>:<common_name>:<device_name>:<serial>` | `DEVICE:RADIOBRIDGE:relay:getez:1779042496` |
| Space (robot firmware) | `device <role> <common_name> <device_name> <serial>` | `device NEZHA2 robot gopiv 2175407711` |

The two are parsed slightly differently, deliberately: in the colon dialect the
serial may itself contain `:`, so the tail is rejoined; in the space dialect the
serial is a single bare token (the decimal `FICR.DEVICEID[1]`), so any extra
trailing tokens are ignored rather than folded into it.

Only the colon dialect was accepted before 2026-08-27, and the consequence is
still worth knowing because it can persist in an old registry file: every board
running robot firmware failed to parse, so its `role`, `common_name`,
`device_name` and `serial` were never written, while the DEVICE NAME column kept
filling in from the independent SWD path and masked the gap. A board reflashed
from relay firmware to robot firmware therefore kept its stale `RADIOBRIDGE`
role, and `deploy` refused it as "a relay" for days. If you see that, re-run
`probe`.

## The registry

The registry is a JSON file, by default `config/devices.json` relative to the
current working directory, overridable with `--config PATH` on `deploy`, `list`,
`probe`, `connect`, and `serve`. It is keyed by UID.

| Field | Meaning |
|---|---|
| `uid` | Hardware unique id, 40–52 hex characters. The key. Stable forever. |
| `enum` | Small integer assigned once; never reused or changed. Minimum 1; a new board gets `max(existing) + 1`. |
| `port` | Serial device path (`/dev/cu.*` on macOS, `/dev/ttyACM*` on Linux), or `null`. Refreshed on every probe. |
| `role` | Device type from the announcement (e.g. `NEZHA2`, `RADIOBRIDGE`). What the relay guard reads. |
| `common_name` | Human label for the board's job in a classroom ("Jane's robot"). Shown by `list`; **never** matched as a target. |
| `device_name` | The board's five-letter name, as announced. A target. |
| `serial` | The serial field from the announcement. |
| `announcement` | The raw announcement line, verbatim. |
| `board_name` | The board's five-letter name, read from silicon over SWD. A target. |
| `device_id` | The 32-bit `FICR.DEVICEID[1]` that `board_name` encodes. |

`device_name` and `board_name` are the same word reached two ways, and agree
when both are known.

### Invariants

These are what make `list` cheap and trustworthy while keeping ports honest
about their freshness.

- **Entries are never deleted.** A board probed once stays in the file after it
  is unplugged, so a missing board is *visible* (as `CONN=no`) rather than
  absent. The single exception is `probe --clear`, which rebuilds the file from
  the currently connected boards only.
- **`port` is always refreshed** by a probe, even when nothing else about the
  board could be read.
- **Announcement fields are preserved** when a probe yields nothing — a busy
  port, no firmware, a timeout. The last known identity is never cleared by a
  failed read.
- **`enum` is assigned once** and is stable for a given UID.
- **`board_name` is read once** and never re-read. It is fixed in silicon.
- **A corrupt or unreadable registry is treated as empty**, silently.

## Resolving a target

A target token is resolved in this precedence order:

| # | Token looks like | Matched against | Example |
|---|---|---|---|
| 1 | All digits | `enum` | `2` |
| 2 | Contains `/` | A **live** serial-port scan — see below | `/dev/ttyACM0`, `/dev/cu.usbmodem1234` |
| 3 | 40–52 hex characters | `uid` | `990636…` |
| 4 | Anything else | Case-insensitive `device_name`, then `board_name` | `tovez` |

Resolution is deliberately two-layered. The registry lookup itself **refuses** a
path outright rather than matching the recorded `port`; whichever command
accepted the path is responsible for resolving it live. That layering is what
makes the next section possible.

### Why `deploy` and `connect` treat a path differently

They are asymmetric on purpose, and the asymmetry is a safety property, not an
inconsistency.

**`deploy` translates the path against the live map, and refuses rather than
guessing.** pyOCD addresses a board by UID, so a path has to become a UID
somehow. The registry's recorded `port` is only as fresh as the last `probe`,
and the OS re-issues serial port names on every reconnect, so two boards
routinely swap paths. Matching the recorded port and then flashing that entry's
UID would write firmware to a *different, connected* board than the path names —
that is a real wrong-board flash that has actually happened. So `deploy` scans
the live ports of currently connected CMSIS-DAP probes and takes whichever board
is on that path **right now**. When it cannot do that cleanly it stops:

- If no micro:bit is on the path, it errors and lists the micro:bit ports that
  *are* occupied.
- If the board on the path is not in the registry, it errors and tells you to
  run `probe` — the registry entry is where `role` comes from, and `role` is
  what the relay guard reads, so flashing an unregistered UID would mean
  flashing with no relay guard at all.
- If the live map comes back empty even though a probe is connected, it refuses
  rather than falling back to the recorded port. On Linux this usually means the
  user is not in `plugdev`/`dialout`. Target by enum, name, or UID to work
  around it, or fix the group membership.

**`connect` opens the path verbatim.** It wants a port, and the path *is* one,
so it never consults any map, live or recorded. That also means a board that has
never been probed can still be reached by path. For a name, enum, or UID,
`connect` resolves through the registry, requires the board to be connected, and
re-reads its port live for the same staleness reason — falling back to the
entry's recorded `port` only if the live scan yields nothing for that UID.

### `common_name` is never a target

`common_name` is a human label for a board's job — "robot", "Jane's robot" —
assigned by whoever set the fleet up. Two boards can wear the same one, it
changes when a class is reassigned, and it says nothing about which hardware is
in your hand. `list` shows it so you can find a board on a desk. Nothing
resolves it, and error messages never quote it back at you as an identifier.

### Auto-pick

`deploy` with no target auto-picks the unique **non-relay** entry in the
registry. Zero or more than one, and it errors and asks you to be explicit.

Note that auto-pick counts *registry entries*, not connected boards: a registry
holding three non-relay boards is ambiguous even when only one of them is
plugged in. That is also why a registry with no roles at all (every entry
`is_relay() == False`) becomes ambiguous as soon as it holds a second board.
Pass an explicit target rather than retrying the bare command.
