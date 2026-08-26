# mbdeploy — Agent Manual

This manual is written for AI coding agents (and power users) driving
`mbdeploy` non-interactively. It documents the full command surface, the
device model, exit-code contract, and copy-paste recipes for the common
build-and-flash workflows on a micro:bit fleet.

If you only need a quick reminder, run `mbdeploy --help` or
`mbdeploy <subcommand> --help`. This document is the complete reference.

---

## 1. What mbdeploy does

`mbdeploy` builds micro:bit firmware and flashes it to one or more
connected micro:bit devices over USB using [pyOCD](https://pyocd.io/).
It maintains a small JSON **device registry** so that boards can be
addressed by a stable, human-friendly name or number instead of their
long hardware UID.

A typical fleet has two kinds of boards:

- **Robots / end devices** (e.g. role `Nezha2`) — the boards you normally
  flash.
- **Relays / bridges** (role contains `RELAY` or `BRIDGE`, e.g.
  `RADIOBRIDGE`) — radio gateways that you usually do **not** want to
  reflash by accident. `mbdeploy` refuses to deploy to a relay unless you
  pass `--force-relay`.

---

## 2. Command surface

```
mbdeploy [--version] [--agent] <subcommand> [options]
```

### Top-level flags

| Flag        | Effect |
|-------------|--------|
| `--version` | Print the installed mbdeploy version and exit. |
| `--agent`   | Print this agent manual to stdout and exit. |
| `-h`, `--help` | Print short usage and exit. |

`--version` and `--agent` are handled before any subcommand, so
`mbdeploy --version` and `mbdeploy --agent` work without naming a
subcommand.

### Subcommands

| Subcommand | Purpose |
|------------|---------|
| `build`    | Compile the micro:bit firmware. |
| `deploy`   | Flash firmware to a micro:bit device. |
| `list`     | List every known device, connected or not, with names from the saved registry. |
| `probe`    | Actively probe every connected device and update the registry. Use `--clear` to rebuild it from live devices only. |
| `connect`  | Open a serial session with a device, or send it one line and print the reply. |

Both `list` and `probe` print the same table:

```
ENUM  CONN  DEVICE NAME  COMMON NAME  ROLE          PORT                     UID
1     yes   tovez        robot        NEZHA2        /dev/cu.usbmodem2121102  99063602...
2     no    gopiv                                                            99063602...
```

- `CONN` is `yes` when the board is attached over USB right now, `no` when it is
  only remembered from an earlier `probe`. Connected boards sort first.
- `PORT` is blank for a disconnected board — its remembered port no longer exists.
- `DEVICE NAME` is the board's five-letter micro:bit name (see §3.1).
- Boards that have never been probed still appear, with their name read live.

`list` accepts `--fast`, which skips that live name read (see §3.1).

---

## 3. The device registry

The registry is a JSON file, by default `config/devices.json` (relative to
the current working directory). Override it with `--config PATH` on the
`deploy`, `list`, and `probe` subcommands.

Each entry is keyed by the board's UID and carries:

| Field         | Meaning |
|---------------|---------|
| `uid`         | Hardware unique id (40–52 hex chars). Stable forever. |
| `enum`        | Small integer assigned once; never reused or changed. |
| `port`        | `/dev/cu.*` serial port. Refreshed on every `probe`. |
| `role`        | Device type from its `DEVICE:` announcement (e.g. `Nezha2`, `RADIOBRIDGE`). |
| `common_name` | Human label for the board's role in a classroom ("Jane's robot"). Shown by `list`; **never** matched as a target. |
| `device_name` | The board's own five-letter name, from its `DEVICE:` announcement. A target. |
| `serial`      | Serial reported in the announcement. |
| `board_name`  | Five-letter micro:bit name read from the hardware (see §3.1). |
| `device_id`   | The 32-bit `FICR.DEVICEID[1]` that `board_name` encodes. |

Registry invariants worth knowing as an agent:

- **Entries are never deleted.** A board that was probed once stays in the
  file even when unplugged.
- **`port` is always refreshed** by `probe`; identity fields (`role`,
  `common_name`, …) are **preserved** if a probe can't read a fresh
  announcement (port busy, no firmware, timeout).
- **`enum` is assigned once** and is stable for a given UID.
- **`board_name` is read once** and never re-read — it is fixed in silicon.

Because of this, `list` is cheap and trustworthy for names, but `port`
values are only as fresh as the last `probe`.

### 3.1 Board names and the UID

A micro:bit's five-letter name (`tovez`, `gopiv`, …) is derived by its runtime
from `FICR.DEVICEID[1]` on the **target** nRF, in base 5 over a fixed
consonant/vowel codebook.

That name **cannot be computed from the UID.** The UID is the USB serial number
of the separate on-board DAPLink interface chip; the two ids are unrelated. Any
attempt to slice the name out of the UID string is wrong.

It *can* be read through the probe the UID names: `mbdeploy` attaches with pyOCD
(`connect_mode="attach"` — no halt, no reset) and reads `DEVICEID[1]`, about
0.2 s per board. This needs neither a serial port nor cooperating firmware, so a
blank or freshly unboxed board still reports its name.

- `probe` reads and caches it in `board_name` for every board that lacks one.
- `list` uses the cached value, and reads live only for connected boards the
  registry does not know. `list --fast` skips those reads.
- If the read fails (probe busy mid-flash, locked part), the name is simply
  blank; nothing else about the entry changes.

---

## 4. Addressing a device (target resolution)

The `deploy` subcommand takes an optional positional `target`. It is
resolved in this precedence order:

1. **All digits** → matched against `enum`. Example: `2`
2. **Contains `/`** (e.g. starts with `/dev/`) → matched against the **live**
   `ioreg` port map: whichever board is on that path *right now* is the one
   flashed. Example: `/dev/cu.usbmodem1234`
3. **40–52 hex chars** → matched against `uid`.
4. **Anything else** → case-insensitive match on the board's own five-letter
   name: `device_name` (announced) then `board_name` (read from silicon).
   Example: `tovez`

Step 2 is deliberately **not** a registry lookup. The registry's `port` is only
as fresh as the last `probe`, and macOS re-issues `/dev/cu.usbmodem*` names on
every reconnect, so two boards routinely swap paths. Matching the recorded port
and then flashing that entry's UID would write firmware to a *different*,
connected board than the path names. `deploy` therefore resolves the path
live and refuses rather than guessing when it cannot:

- The board on the path must already be in the registry — its entry is where
  `role` comes from, and `role` is what the relay guard reads. Run `probe` once
  for a board `deploy` has never seen.
- If no micro:bit is on that path, `deploy` errors and lists the ports that are
  occupied.
- If the live map is unavailable (it is read from macOS `ioreg`), `deploy`
  errors rather than falling back to the recorded port. Target by enum, name,
  or UID on other platforms.

A board is addressed by its **own** name — the five-letter word (`tovez`,
`gopiv`) its runtime derives from `FICR.DEVICEID[1]`, shown in the DEVICE NAME
column of `list`. A `common_name` is **never** a target. That field is a human
label for the board's role in a classroom ("Jane's robot"), assigned by whoever
set the fleet up: two boards can wear the same one, it changes when a class is
reassigned, and it says nothing about which hardware is in your hand. `list`
shows it so you can find a board on a desk; nothing resolves it.

If `target` is omitted, `mbdeploy` **auto-picks** the unique non-relay
device in the registry. If there are zero or more than one non-relay
devices, it errors and asks you to be explicit.

`connect` resolves targets the same way, with two deliberate differences:

- Its `target` is **required** — there is no auto-pick.
- Step 2 needs no lookup at all: an explicit `/dev/...` path is opened
  verbatim. `connect` wants a port and the path *is* one, so unlike `deploy`
  — which must translate the path to a UID for pyOCD — it never consults any
  map, live or recorded. That also means a never-probed board can be reached
  by path.

For a name, enum, or UID, `connect` re-reads the port live rather than trusting
the registry, for the same staleness reason.

---

## 5. Exit codes

`mbdeploy` follows the standard contract: **`0` = success, non-zero =
failure.** Always check the exit code rather than scraping stdout.

Common non-zero cases for `deploy`:

- Target token matched no registry entry.
- Resolved device is a relay and `--force-relay` was not given.
- Resolved device is not currently connected (not in the live probe list).
- The build step (`--build` / `--clean`) failed.
- `pyocd flash` failed *and* the automatic mass-erase recovery also failed
  (see §6.5), or `pyocd reset` returned non-zero.

Non-zero cases for `connect`:

- Target token matched no registry entry, or the board is not connected.
- The serial port could not be opened (wrong path, or another program — a
  serial monitor, an editor's terminal — already has it).
- A message was sent and **nothing came back** within `--timeout`.

Error messages are written to **stderr**; normal output goes to stdout. For
`connect` that split matters: the board's reply is the only thing on stdout, so
`mbdeploy connect tovez STATUS` can be piped or captured directly.

---

## 6. Recipes

### 6.1 Discover the fleet

Always probe first when ports may have changed (e.g. after replugging):

```bash
mbdeploy probe
mbdeploy list
```

`probe` opens each serial port, updates names/ports, and records the hardware
board name of anything new; `list` is a fast read of the saved registry merged
with the current live probes. `list` shows unplugged boards too, marked
`CONN=no`, so a board missing from your fleet is visible rather than absent.

If you want to discard stale registry entries and rebuild from the currently
connected devices only, run:

```bash
mbdeploy probe --clear
```

### 6.2 Build, then deploy to the only robot

When exactly one non-relay device is connected and registered:

```bash
mbdeploy build
mbdeploy deploy --build
```

`deploy --build` compiles first, then flashes. Use `deploy` alone to flash
a pre-built `MICROBIT.hex`.

### 6.3 Deploy to a specific device

By enum:

```bash
mbdeploy deploy 2
```

By friendly name:

```bash
mbdeploy deploy gutov
```

By port or UID:

```bash
mbdeploy deploy /dev/cu.usbmodem1234
mbdeploy deploy F1A2...  # full 40–52 hex UID
```

### 6.4 Clean build before deploying

```bash
mbdeploy deploy gutov --clean
```

`--clean` implies a build, then flashes.

### 6.5 Locked / protected devices (automatic recovery)

If a board's nRF52833 is in a locked or protected state (APPROTECT set, or a
protected SoftDevice region at address `0x0`), the flash algorithm rejects
every erase and `deploy` would otherwise fail with something like
`flash erase sector failure (... result code 0x67)`.

`deploy` handles this automatically: when the initial `pyocd flash` fails, it
runs a CTRL-AP **mass erase** (`pyocd erase --mass`, i.e. `ERASEALL`) — the
only operation that clears APPROTECT — and then retries the flash once. You
don't need to intervene. If the mass erase itself fails, `deploy` aborts with
a non-zero exit code rather than retrying blindly.

To recover a board manually (e.g. outside `deploy`):

```bash
pyocd erase -t nrf52833 -u <uid> --mass
pyocd flash -t nrf52833 -u <uid> MICROBIT.hex
pyocd reset -t nrf52833 -u <uid>
```

### 6.6 Flash a relay on purpose

Relays are guarded. Override deliberately:

```bash
mbdeploy deploy bridge1 --force-relay
```

### 6.7 Flash a custom hex / non-default MCU

```bash
mbdeploy deploy 2 --hex build/MICROBIT.hex --target-mcu nrf52833
```

### 6.8 Use a non-default registry location

```bash
mbdeploy probe  --config /path/to/devices.json
mbdeploy deploy 2 --config /path/to/devices.json
```

### 6.9 Talk to a board over serial

Send one line and read the answer:

```bash
mbdeploy connect tovez "HELLO"
```

Everything after the target is joined with spaces and sent as a single
newline-terminated line, so quoting is optional:

```bash
mbdeploy connect tovez SET SPEED 50      # sends "SET SPEED 50\n"
```

Options may appear anywhere, including between the target and the message:

```bash
mbdeploy connect tovez --baud 9600 "HELLO"
mbdeploy connect tovez "HELLO" --timeout 5
```

Omit the message for an interactive session — stdin goes to the board, the
board's output to stdout, until Ctrl-D or Ctrl-C:

```bash
mbdeploy connect tovez
```

Connecting holds DTR low, so opening the port does **not** reset the board;
whatever it was doing keeps running.

#### How the reply is bounded

`--timeout` (default 2 s) is the budget for the *whole* exchange, not a
per-read timeout:

- The board has that long to say anything at all.
- Once it has answered and then stayed quiet for ~0.4 s, the read stops early,
  so a multi-line reply is returned whole without waiting out the timeout.
- A board that streams continuously (telemetry, an `ack` loop) is cut off at
  `--timeout` rather than hanging the command. Raise `--timeout` for a board
  that is slow to answer; that same flag caps how much of a chatty board's
  stream you capture.

#### Agent notes

- **Check the exit code, not the text.** A board that answered exits 0; silence
  within the timeout exits 1.
- **One port, one owner.** A serial port cannot be shared. `connect` fails if a
  monitor already holds it, and while `connect` is running, `probe` cannot read
  that board's `DEVICE:` announcement.
- **Prefer a name or enum over a path** when scripting — ports shift across
  reconnects. Use a path only when you truly mean that exact port.

---

## 7. Build options (`build` and `deploy --build`)

| Option           | Effect |
|------------------|--------|
| `--clean`        | Clean before building (on `deploy`, implies `--build`). |
| `--verbose`      | Show full build output. |
| `-j N`           | Run the build with N parallel jobs. |
| `--build-cmd CMD`| Override the build command (`build` subcommand only). |

---

## 8. Agent operating tips

- **Probe before deploy** for any board `mbdeploy` has not seen; the registry
  entry is what supplies its `role`, and without one `deploy` refuses a path
  target rather than flash a board it cannot relay-check.
- **Prefer `enum` or the five-letter device name** over a port path when scripting — ports
  shift across reconnects, enums and names do not. A path is resolved against
  the live map, so it is never *wrong*, but it may name a different board than
  it did an hour ago.
- **Trust the exit code.** Don't infer success from stdout text.
- **Never reflash a relay implicitly.** If you intend to, say so explicitly
  with `--force-relay`; otherwise let the guard protect the gateway.
- **Disambiguate.** If auto-pick errors as "ambiguous", pass an explicit
  target rather than retrying the bare command.
- **Use `connect` to check firmware, not `probe`.** `probe` rewrites the
  registry; `connect` only reads. To confirm a board is alive after a deploy,
  `mbdeploy connect <target> HELLO` and check the exit code.
