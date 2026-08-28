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
| `serve`    | Run the fleet daemon: watch USB and advertise each board's serial/flash services over mDNS (see §9). |

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
| `port`        | Serial port (`/dev/cu.*` on macOS, `/dev/ttyACM*` on Linux). Refreshed on every `probe`. |
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
   serial-port map (a `pyserial` VID:PID scan, on macOS or Linux alike):
   whichever board is on that path *right now* is the one flashed. Example:
   `/dev/cu.usbmodem1234` on macOS, `/dev/ttyACM0` on Linux.
3. **40–52 hex chars** → matched against `uid`.
4. **Anything else** → case-insensitive match on the board's own five-letter
   name: `device_name` (announced) then `board_name` (read from silicon).
   Example: `tovez`

Step 2 is deliberately **not** a registry lookup. The registry's `port` is only
as fresh as the last `probe`, and the OS re-issues serial port names (e.g.
`/dev/cu.usbmodem*` on macOS) on every reconnect, so two boards routinely swap
paths. Matching the recorded port and then flashing that entry's UID would
write firmware to a *different*, connected board than the path names. `deploy`
therefore resolves the path live and refuses rather than guessing when it
cannot:

- The board on the path must already be in the registry — its entry is where
  `role` comes from, and `role` is what the relay guard reads. Run `probe` once
  for a board `deploy` has never seen.
- If no micro:bit is on that path, `deploy` errors and lists the ports that are
  occupied.
- If the live map comes back empty even though a probe is connected, `deploy`
  errors rather than falling back to the recorded port — this can happen on
  Linux if the current user lacks `plugdev`/`dialout` group membership (see
  "Linux / Raspberry Pi setup" below). Target by enum, name, or UID to work
  around it, or fix the group membership and retry.

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

### 4.1 Linux / Raspberry Pi setup

`mbdeploy` works identically on Linux — the live port map is a `pyserial`
VID:PID scan, not an OS-specific tool, so nothing above is macOS-only. Facts
below were verified on Nolanet, a 4-node Raspberry Pi 3B cluster (`magni`,
`hodr`, `loki`, `meili`) running Debian Bookworm (aarch64) with Python 3.13.5
and one micro:bit per node.

- **Port naming.** A micro:bit enumerates as `/dev/ttyACM0` (not the macOS
  `/dev/cu.usbmodem*` spelling). Use that form when targeting by path.
- **Group membership.** The user running `mbdeploy` needs to be in two
  groups: `plugdev` (raw USB access, needed by pyOCD) and `dialout` (the
  serial port). Without both, the live port map comes back empty even
  though a probe is connected, and `deploy` refuses a `/dev/...` target
  (see §4 above) — the fix is `sudo usermod -aG plugdev,dialout <user>`
  followed by a re-login.
- **No new udev rule is needed.** Raspberry Pi OS already ships
  `/lib/udev/rules.d/70-microbit.rules`, which matches
  `SUBSYSTEM=="usb", ATTR{idVendor}=="0d28", TAG+="uaccess"`. Both
  `/dev/ttyACM0` and the underlying USB device node come up
  `root:plugdev 0660`, so `plugdev` membership alone is what grants
  headless access — there is nothing to add. Do **not** write a
  `MODE="0666"` rule for this vendor ID; it is unnecessary on Raspberry Pi
  OS and was verified as such during this project's Linux support work.
- **pyOCD runs headless, unprivileged.** With group membership in place,
  `pyocd list` enumerates the probe and identifies the target as
  nrf52833 / micro:bit V2 without root and without any additional pyOCD
  configuration.
- **Install footprint.** All 23 of mbdeploy's dependencies (`pyocd` 0.45.1,
  `zeroconf`, `pyserial`, and their transitive requirements) resolve to
  prebuilt aarch64 wheels on Raspberry Pi OS — no compilation step — and a
  fresh virtualenv measures about 83 MB.

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
mbdeploy deploy /dev/cu.usbmodem1234  # macOS
mbdeploy deploy /dev/ttyACM0          # Linux
mbdeploy deploy F1A2...               # full 40–52 hex UID
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

---

## 9. Serving a fleet over the network

`serve` turns this host into a network-facing daemon for every micro:bit
plugged into it. A USB watcher keeps a live per-board state in sync with
what's actually connected, and each connected board gets two independent
mDNS-advertised TCP services — a raw serial relay and a flash-over-the-
network protocol — so `list --remote`, `connect --remote`, and
`deploy --remote` run from another machine on the LAN can reach it
without that machine having any local registry entry, or even a USB
connection, for the board at all.

```bash
mbdeploy serve
```

runs in the foreground until SIGINT/SIGTERM (systemd sends SIGTERM on
stop). On either signal it unregisters every mDNS advertisement and
closes every listener socket before exiting — it never self-daemonizes
and never writes a pidfile; §9.6 below is how you actually run it as a
background service.

### 9.1 The two mDNS services and how a board is named

Each connected board is advertised under two service types, rooted at
the same instance name:

| Service type | Purpose | Wire protocol |
|---|---|---|
| `_mbserial._tcp.local.` | Raw serial pass-through | §9.2 |
| `_mbflash._tcp.local.`  | Flash-over-network + status | §9.3 |

The instance name — what you pass as `target` to `--remote` — is chosen
once, when the board first shows up, from this fallback chain:

1. **`board_name`** — the five-letter name read over SWD during arrival
   (§3.1). This is the path that actually runs in production on
   Nolanet: all four boards there run no announcing firmware, so this
   is not a rarely-hit fallback — it's the only identity source that
   works for them.
2. **`device_name`** — from a `DEVICE:` announcement, used if the SWD
   read failed.
3. **`mb-<last 8 of uid>`** — last resort, so a board is always
   nameable even with neither of the above.

`--service-name NAME` (a `serve` flag) overrides this chain entirely,
for every board that process manages. Only meaningful for a
single-board host (Nolanet's case) — on a multi-board host every board
would get the same name and collide, relying on zeroconf's own
`name (2)` rename to tell them apart.

Each service's TXT record carries `uid`, `role`, `common_name`, `enum`,
and `port`. **`port` is the network TCP port for that specific service**
(what `list --remote`/`resolve_board` connect to) — never the board's
local `/dev/ttyACM0`, which is not exposed on the wire at all.

### 9.2 `_mbserial._tcp`: the serial relay

Connect to a board's serial-service port and (if the daemon was started
with `--token`/`--token-file`) send `AUTH <token>\n`. What comes back is
a **raw, unframed byte pipe** to the board's local serial port, opened
at 115200 baud — `serve` has no `--baud` flag; the daemon always opens
the port the same fixed way regardless of who connects. There is no
other handshake: the first bytes you see are the board's own output,
exactly as if you had run a local `connect`.

- **Exclusive, never preempting.** A second connection while one is
  already live gets `ERR busy` and is dropped immediately. A serial
  session can never displace anything else occupying the board — only
  an incoming `FLASH` can do that (§9.4).
- **`AUTH`, only if configured.** The connection has 5s to send
  `AUTH <token>\n`; a match gets `OK` and the session proceeds as above.
  A missing, malformed, or wrong-token line gets `ERR auth required` and
  the connection is closed — checked before anything else, including
  the busy check.
- **`connect --remote` cannot authenticate.** There is no `--token`
  flag on `list`/`connect`/`deploy`; the `--remote` client side does not
  send `AUTH`. Against a `serve --token`/`--token-file` daemon,
  `connect --remote`/`deploy --remote` fail immediately with `auth
  required`. `list --remote` is unaffected either way, because it never
  opens either socket — it only reads mDNS TXT records, which are not
  gated by the token at all.

### 9.3 `_mbflash._tcp`: `INFO` and `FLASH`

One command line, then — for `FLASH` — a binary payload:

```
INFO
→ OK {"uid": "...", "board_name": "togov", "role": "", "port": "...", "connected": true}

FLASH <nbytes> [sha256=<hex>] [force-relay]
→ OK send
  <exactly nbytes of raw hex bytes>
→ LOG <pyocd progress line>        (zero or more, as flashing proceeds)
→ OK flashed                        -- or, on failure --
→ ERR <reason>
```

`INFO` never touches the board's occupant state — it answers
identically whether the board is idle, mid-session, or mid-flash.
`deploy --remote` always sends `sha256=<hex>` (computed client-side over
the exact bytes being sent); `serve_flash` verifies it whenever present.

Every `ERR` line `serve_flash` can send, verbatim, and what triggers it:

| Line | When |
|---|---|
| `ERR auth required` | Missing/wrong `AUTH` (only checked if `--token`/`--token-file` is set) |
| `ERR unknown command` | The command line is neither `INFO` nor `FLASH` |
| `ERR flash disabled` | `--no-flash` is set — checked before the header is even parsed |
| `ERR bad header` | The byte count is missing or non-numeric |
| `ERR relay refused — send force-relay` | The board's `role` matches the relay guard and `force-relay` wasn't sent |
| `ERR busy` | A flash is already in flight against this board (two flashes never race) |
| `ERR short payload` | Fewer than the declared byte count arrived within 30s |
| `ERR sha256 mismatch` | The declared hash doesn't match what was received |
| `ERR flash failed (exit N)` | `pyocd flash`, including its mass-erase recovery (§6.5), still failed |

`deploy --remote` maps `OK flashed` to exit 0 and any `ERR ...` to exit
1, relaying every `LOG` line to stderr as it arrives so a multi-second
flash shows progress instead of silence followed by a result.

### 9.4 Board exclusivity and the flash-preempts-serial rule

A board has exactly one occupant at a time: idle, a live serial session,
or an in-flight flash.

- A `FLASH` against an **idle** board, or one with an **in-flight
  flash** already, behaves as §9.3 describes (claims it, or `ERR busy`).
- A `FLASH` against a board with a **live serial session** **preempts**
  it: the daemon tears the serial session down (closes its socket,
  releases the local serial port) and waits up to 2s for it to actually
  exit before proceeding with the flash. The serial client simply sees
  its connection close — it is not told why. This is deliberate: a live
  `connect --remote` session must never be able to block a
  `deploy --remote` to the same board indefinitely.
- **Unplugging** a board (USB departure) tears down whatever currently
  occupies it — idle, session, or flash — the same way, and additionally
  unregisters both mDNS advertisements and closes both listener sockets.
  A client mid-session sees its connection drop; a client mid-flash sees
  the connection drop with no terminal `OK`/`ERR` line at all.

### 9.5 `--remote` on `list`, `connect`, `deploy`

`--remote` is a plain flag on all three subcommands. No separate host
argument and no config file — the existing `target` positional is
reused as the board's mDNS instance name (§9.1) to resolve.

```bash
mbdeploy list --remote                            # every board currently advertising on the LAN
mbdeploy connect --remote togov "HELLO"           # one-shot, over _mbserial._tcp
mbdeploy connect --remote togov                   # interactive session
mbdeploy deploy --remote togov --hex build/MICROBIT.hex
mbdeploy deploy --remote togov --force-relay      # relay guard is server-side (§9.3); see the caveat below
```

`list --remote`'s table drops the local-only `CONN`/`PORT` columns (a
board reached over mDNS is, by construction, live right now, and can
carry two different network ports — one per service type, neither of
which is "the" right value for a single PORT column) and adds a `HOST`
column instead:

```
ENUM  DEVICE NAME  COMMON NAME  ROLE          HOST           UID
      togov                                   192.168.1.42   99063602...
```

**Mutual exclusion with a device path**, checked before any mDNS lookup
or socket I/O:

- `connect --remote /dev/ttyACM0` and `deploy --remote /dev/ttyACM0`
  both fail with `Error: --remote cannot be combined with a device path
  ('/dev/ttyACM0').` — `--remote` names a board on the network, a
  `/dev/...` path names a local device, and combining them can't mean
  anything coherent.
- `deploy --remote` with no target fails with `Error: --remote requires
  a target board name -- unlike local deploy, there is no local
  registry of remote boards to auto-pick from.`

**Flags that are ignored, not rejected**, in `--remote` mode — each
documented in its own `--help` text too:

- `connect --remote --baud N`: the daemon already has the board's local
  port open at whatever baud it started with (115200, fixed — see
  §9.2); there is nothing for `--baud` to change from the client side.
- `list --remote --fast` / `--target-mcu`: `list --remote` never reads a
  board name over SWD in the first place (names come from mDNS, not a
  debug probe), so there is no SWD read for `--fast` to skip.

**The silent-board behavior — read this before assuming a bug.** A
board running no announcing firmware answers nothing on its serial
port, so `connect --remote <name> "HELLO"` gets no reply and exits 1.
This is correct, by design — a local `connect` to the same unannounced
board behaves identically — not a failure specific to `--remote`. All
four Nolanet boards are currently in exactly this state
(`role`/`common_name`/`device_name` are all empty strings). The mDNS
instance name still works even so, because it comes from `board_name`,
read over SWD independently of any firmware announcement (§9.1) — so
`list --remote` and `deploy --remote` are fully demonstrable against a
silent fleet even though a serial round-trip is not. Use `INFO`
(§9.3) to confirm a silent board is present and connected without
expecting it to say anything back.

**The relay guard is inert on a silent board — a real limitation, not a
defect to quietly work around.** `serve_flash`'s relay refusal reads
`role` off the board's registry entry
(`is_relay(board.entry.get("role"))`); on a board that has never
announced, `role` is `""`, so `is_relay("")` is `False` and `FLASH` is
never refused for being a relay, `--force-relay` or not. On a fleet of
silent boards like Nolanet's, this means the relay tag simply has
nothing to read — **`--no-flash` and `--token`/`--token-file` are the
actual access controls** for such a deployment, and should be relied on
as such rather than assuming the relay guard is doing any work.

### 9.6 Deploying `serve` under systemd (Nolanet-style)

`serve --print-service` renders the systemd unit for the exact `serve`
invocation you'd otherwise run, to stdout, touching nothing on disk:

```bash
cd /path/to/project   # WorkingDirectory is baked in from this CWD
mbdeploy serve --print-service --base-port 9000 --target-mcu nrf52833
```

`serve --install-service` writes that same unit to disk instead, and
still exits without running the daemon — you run `daemon-reload`/
`enable --now` yourself:

```bash
sudo mbdeploy serve --install-service --base-port 9000 --target-mcu nrf52833
sudo systemctl daemon-reload
sudo systemctl enable --now mbdeploy
```

**The system unit is the default.** `--install-service` with neither
`--system` nor `--user` writes `/etc/systemd/system/mbdeploy.service`
(`WantedBy=multi-user.target`) and requires root/sudo. This is a
deliberate choice for a deployment like Nolanet's, not an arbitrary
default: a systemd **`--user`** unit does **not** start at boot or
survive logout on a host with the default `Linger=no` — which is
Nolanet's actual state on every node — unless an operator separately
runs `loginctl enable-linger <user>`. Pass `--user` only when that
tradeoff is acceptable (e.g. a workstation you leave logged in), or
combine it with the linger command:

```bash
mbdeploy serve --install-service --user
loginctl enable-linger <user>   # required for the unit to survive logout/reboot
```

**A token is never written into `ExecStart`.**
`--install-service --token SECRET` writes `SECRET` to a fresh,
mode-`0600` file (`/etc/mbdeploy/token` for the system scope,
`~/.config/mbdeploy/token` for `--user`) and bakes `--token-file
<that path>` into the generated `ExecStart` line instead — never the
literal secret, which `systemctl cat` would otherwise make
world-readable to anyone on the box. `--token-file PATH`, naming a file
you already created yourself, is used as given. `--print-service
--token SECRET` is refused outright: `--print-service` touches no
filesystem, so there is nowhere to put the secret; use `--token-file`
with `--print-service`, or switch to `--install-service`.

```bash
sudo mbdeploy serve --install-service --token-file /etc/mbdeploy/token
# or let --install-service create the file for you:
sudo mbdeploy serve --install-service --token 'a-shared-secret'
```

**No new udev rule is needed on Raspberry Pi OS.** As §4.1 already
covers for local use, Raspberry Pi OS ships
`/lib/udev/rules.d/70-microbit.rules` (`TAG+="uaccess"` on the
micro:bit's vendor id), and `plugdev` group membership is what actually
grants headless USB access on top of that — there is nothing to add.
Do **not** write a `MODE="0666"` rule for this; it is unnecessary and
was verified as such during this project's Linux support work.

**Group membership the service user needs**, since `serve` does both
flashing (raw USB, via pyOCD) and serial relaying, unlike a purely
read-only tool:

```bash
sudo usermod -aG plugdev,dialout <service-user>
```

— `plugdev` for pyOCD's raw USB access, `dialout` for the serial port:
exactly the same two groups §4.1 already documents for local
`deploy`/`connect`. Re-login (or reboot) for the new group membership
to take effect before starting the service.
