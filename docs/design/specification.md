# mbdeploy — Behavioural Specification

This is the full behavioural specification for `mbdeploy`, reverse-engineered
from the shipped source. It is normative for what the tool does today.

Where this document and the user-facing docs (`README.md`, the bundled
`--agent` manual) disagree, **the code is authoritative**; every known
disagreement is recorded in §12 rather than quietly resolved.

Source of truth for each section:

| Area | Module |
|------|--------|
| Discovery, registry, name decoding, target resolution | `src/mbdeploy/devices.py` |
| Subcommands, parser, deploy flow, port resolution | `src/mbdeploy/cli.py` |
| Serial sessions | `src/mbdeploy/console.py` |
| Build orchestration | `src/mbdeploy/builder.py` |
| Bundled reference manual | `src/mbdeploy/agent_manual.md` |

---

## 1. Purpose and scope

`mbdeploy` builds micro:bit firmware and flashes it to a connected
micro:bit over USB using pyOCD, and maintains a persistent JSON registry
so boards can be addressed by a stable, human-scale identifier instead of
their hardware UID. It also opens serial sessions to a board, for checking
that firmware came up.

Out of scope for this specification: anything not in the shipped source.
In particular there is no network service, no remote deployment, and no
mDNS discovery.

## 2. Runtime context

- **Entry point**: console script `mbdeploy` → `mbdeploy.cli:main`.
- **Python**: `>=3.10`.
- **Dependencies**: `pyocd>=0.44.1`, `pyserial>=3.5`. `pyserial` is
  imported defensively in `devices.py` (`try/except` → `serial = None`),
  so the package still imports on a machine without it; every serial code
  path then degrades to a clean failure rather than an `ImportError`.
- **Version**: read at import time from installed package metadata
  (`importlib.metadata.version("mbdeploy")`), falling back to
  `"0.0.0+unknown"` when the package is not installed. Current declared
  version: `0.20260826.1`. There is no hardcoded version string.
- **pyOCD invocation**: subprocess calls use
  `[sys.executable, "-m", "pyocd"]`, never a bare `pyocd` on `PATH`.
  `mbdeploy` is typically installed with `pipx` into an isolated venv
  where pyocd is importable but its console script is not on `PATH`.
- **Paths are CWD-relative.** The tool is run from the root of the
  firmware project it is deploying, so it finds `./build.py`,
  `./MICROBIT.hex` and `./config/devices.json`. Each is overridable
  (`--build-cmd`, `--hex`, `--config`).

Defaults:

| Constant | Value | Where |
|----------|-------|-------|
| Registry path | `config/devices.json` | `cli._DEFAULT_CONFIG` |
| Hex path | `MICROBIT.hex` | `cli._DEFAULT_HEX` |
| Target MCU | `nrf52833` | `cli._DEFAULT_MCU`, `devices.DEFAULT_MCU` |
| Baud | `115200` | `cli._DEFAULT_BAUD`, `devices.BAUD_RATE` |
| `connect` timeout | `2.0` s | `cli._DEFAULT_CONNECT_TIMEOUT` |

---

## 3. The device model

### 3.1 Two chips, two identities

A micro:bit carries two microcontrollers that matter here:

- The **DAPLink interface chip**, which presents the CMSIS-DAP debug probe
  and the USB CDC serial port. Its USB serial number is what pyOCD calls
  the probe's *unique id*. `mbdeploy` calls this the **UID**: a hex string
  of 40–52 characters, stable forever for a given board, and the only
  handle pyOCD accepts for addressing a board (`--uid`).
- The **target nRF** (nRF52833 on a V2; nRF51 on a V1), which runs the
  user's firmware. Its `FICR.DEVICEID[1]` — a 32-bit factory-programmed
  word at address `0x10000064` (`devices.FICR_DEVICEID1`) — is what the
  micro:bit runtime hashes into the board's five-letter friendly name.

**The friendly name cannot be derived from the UID.** The two ids belong
to different chips and are unrelated. Any attempt to slice the name out of
the UID string is wrong. This is the single most load-bearing fact in the
device model: it is why the registry exists at all, and why a dedicated
SWD read (§3.3) exists to bridge the two identities.

### 3.2 The five-letter name and the base-5 codebook

`devices.friendly_name(device_id) -> str` reproduces CODAL's
`microbit_friendly_name()`. The 32-bit word is written out as five base-5
digits. Digit *i* (counting from the least significant) selects a letter
from column *i* of the codebook and lands at position `4 - i` of the
printed name — so the most significant digit is printed first.

The codebook (`devices._NAME_CODEBOOK`) alternates consonants and vowels:

| Column (from LSB) | Letters (digit 0…4) |
|-------------------|---------------------|
| 0 | `z v g p t` |
| 1 | `u o i e a` |
| 2 | `z v g p t` |
| 3 | `u o i e a` |
| 4 | `z v g p t` |

The result is always exactly five alphabetic characters, for every 32-bit
input. Ground truths pinned by the test suite, read from real boards:

| `FICR.DEVICEID[1]` | Name |
|--------------------|------|
| `2314287040` | `tovez` |
| `2175407711` | `gopiv` |
| `1784514240` | `getez` |
| `1198504156` | `vevov` |
| `0` | `zuzuz` (the lowest name) |

The input is masked to 32 bits (`n & 0xFFFFFFFF`) before decoding.

### 3.3 Reading the name over SWD

`devices.read_device_id(uid, target_mcu="nrf52833") -> int | None` opens a
pyOCD session against the probe named by `uid` and reads the 32-bit word
at `FICR_DEVICEID1`. Its properties are deliberate:

- `connect_mode="attach"` — **no halt and no reset.** Reading a board's
  name must not disturb firmware that is running. This is what makes the
  read safe to do during `list`.
- `blocking=False` — a probe that is busy (for example mid-flash) fails
  fast instead of waiting.
- `auto_unlock=False` — reading a name must never mass-erase a locked
  part as a side effect.
- Logging: wrapped in `devices._quiet_pyocd()`, which raises the `pyocd`
  logger above `CRITICAL` for the duration so pyOCD chatter cannot
  interleave with the table output.
- **Two attempts**: `target_override=target_mcu`, then
  `target_override=None`. `DEVICEID` lives at the same address on nRF51
  and nRF52, so the second, auto-detecting attempt lets a V1 micro:bit be
  read even though the caller's target guess (`nrf52833`) does not fit it.
- Returns `None` if pyOCD is unavailable, the probe is busy, or the target
  refuses the connection (a locked part). Never raises.

Cost is roughly 0.2 s per board. It needs neither a serial port nor
cooperating firmware, so a blank, freshly unboxed, or silent board still
reports its name.

`devices.read_board_name(uid, target_mcu)` is the convenience wrapper:
`friendly_name(read_device_id(...))`, or `None` when the id could not be
read.

Who reads it, and when:

- `probe` reads and caches it into `board_name` for every board that does
  not already have one, and stores the raw word in `device_id`.
- `probe` **never re-reads a cached name** — it is fixed in silicon.
- `list` uses the cached value and reads live only for a *connected* board
  that has no `device_name` and no `board_name` recorded.
- `list --fast` skips the live read entirely.
- A failed read leaves the entry otherwise untouched; the name is simply
  blank.

### 3.4 Roles and relays

A fleet contains two kinds of board:

- **Robots / end devices** — role such as `Nezha2`. The boards normally
  flashed.
- **Relays / bridges** — radio gateways, role such as `RADIOBRIDGE`.
  Reflashing one silently takes the classroom's radio link down.

`devices.is_relay(role) -> bool` is the guard's whole definition:

```
is_relay(None)  -> False
is_relay("")    -> False
otherwise       -> "RELAY" in role.upper() or "BRIDGE" in role.upper()
```

It is a case-insensitive **substring** test on two tokens, so it matches
both the historical `RADIORELAY` and the firmware's actual `RADIOBRIDGE`,
and any future role that embeds either word. `Nezha2` is not a relay.
A board with no known role is not a relay — see §12 D7 for the field
consequence of that.

### 3.5 The `DEVICE:` announcement contract

`devices.probe_type(port, timeout_s=1.6) -> dict | None` opens a serial
port, sends `HELLO\n`, and parses the board's announcement.

Sequence:

1. Return `None` immediately if `pyserial` is absent.
2. Construct `serial.Serial(baudrate=115200, timeout=0.12, dsrdtr=False,
   rtscts=False)`, set `.port`, clear `.dtr` and `.rts`, then `open()` —
   modem lines low *before* the port opens, so probing does not reset the
   board.
3. Sleep 0.3 s to let the port settle, then `reset_input_buffer()`.
4. Write `b"HELLO\n"` and flush.
5. Read lines until `timeout_s` (1.6 s) elapses. Each line is decoded
   UTF-8 with `errors="ignore"` and stripped.
6. Return on the first line that parses; otherwise `None`.
7. Close the port in a `finally`. Any exception anywhere returns `None`.

**Two announcement dialects are accepted.** Both carry the same five
fields in the same order — sentinel, role, common name, device name,
serial:

| Dialect | Shape | Detection | Split | Field count | Example |
|---------|-------|-----------|-------|-------------|---------|
| Colon (relay) | `DEVICE:<role>:<common_name>:<device_name>:<serial>` | `text.startswith("DEVICE:")` | `text.split(":")` | `len(parts) >= 5` | `DEVICE:RADIOBRIDGE:relay:getez:1779042496` |
| Space (robot) | `device <role> <common_name> <device_name> <serial>` | `text.startswith("device ")` | `text.split()` | `len(parts) >= 5` | `device NEZHA2 robot vevov 1198504156` |

Field extraction differs between the two, and the difference is
intentional:

- **Colon dialect** — the serial may itself contain `:`, so the tail is
  rejoined: `fields = (parts[1], parts[2], parts[3], ":".join(parts[4:]))`.
- **Space dialect** — the serial is a single bare token (the decimal
  `FICR.DEVICEID[1]`), so any extra trailing tokens are *not* part of it
  and are ignored: `fields = (parts[1], parts[2], parts[3], parts[4])`.

On a match, `probe_type` returns:

```
{"role": …, "common_name": …, "device_name": …, "serial": …, "raw": <full line>}
```

`raw` is stored in the registry as `announcement`.

**Why two dialects exist** (recorded in the source, and worth preserving):
only the colon dialect was accepted until 2026-08-27. The v6 wire protocol
dropped `:` as a field separator when it retired v5, and the robot
announcement line went with it; this parser was not updated to follow. The
consequence in the field was that every robot failed to type —
`probe_type` returned `None`, `probe_all` took its preserve-existing-fields
branch, and `role`/`common_name`/`device_name`/`serial` were never
written. The DEVICE NAME column still filled in, because `board_name`
comes from the independent SWD path, which masked the gap. A board
reflashed from relay firmware to robot firmware therefore kept its stale
`RADIOBRIDGE` role for days, and `mbdeploy deploy <that board>` refused
with "is a relay" on a board that had been a robot the whole time.

---

## 4. The device registry

### 4.1 Location and format

A single JSON object at `config/devices.json` relative to the current
working directory, overridable with `--config PATH` on `deploy`, `list`,
`probe`, **and `connect`**. Keyed by UID; each value is one device entry.

- `devices.load_devices(path)` returns `{}` on a missing or invalid file
  (`OSError` or `ValueError`). A corrupt registry is never fatal.
- `devices.save_devices(devices, path)` creates parent directories as
  needed and writes `json.dumps(..., indent=2, sort_keys=True)` plus a
  trailing newline. Sorted keys keep the file diff-stable.

### 4.2 Fields

| Field | Type | Meaning | Source |
|-------|------|---------|--------|
| `uid` | string | Hardware unique id, 40–52 hex chars. Stable forever. The pyOCD probe address. | Probe enumeration |
| `enum` | int ≥ 1 | Small integer assigned once; never reused or changed. | `assign_enum` |
| `port` | string \| null | `/dev/cu.*` serial port. Refreshed on every `probe`; `null` when no mapping exists. | `port_serial_map` |
| `role` | string | Device type, e.g. `Nezha2`, `RADIOBRIDGE`. What the relay guard reads. | Announcement |
| `common_name` | string | Human label for the board's role in a classroom ("Jane's robot"). Displayed only; **never** a target. | Announcement |
| `device_name` | string | The board's own five-letter name, as announced. A target. | Announcement |
| `serial` | string | Serial reported in the announcement. | Announcement |
| `announcement` | string | The raw announcement line, verbatim. | Announcement (`raw`) |
| `board_name` | string | The five-letter name read from silicon. A target. | SWD read |
| `device_id` | int | The 32-bit `FICR.DEVICEID[1]` that `board_name` encodes. | SWD read |

Only `uid`, `enum` and `port` are written unconditionally. Every other
field appears once its source has produced a value.

### 4.3 Invariants

These four are stated in `devices.py`'s module docstring and are the
registry's contract:

1. **Entries are never deleted.** Once a UID is recorded it stays in the
   file, even when the board is unplugged. (`probe --clear` is the one
   deliberate exception — it starts from an empty dict rather than
   deleting anything; see §7.5.)
2. **`port` is always refreshed** from the current `port_serial_map()`
   result, *even when the HELLO probe fails*. A port is only ever as fresh
   as the last probe, and the registry never pretends otherwise.
3. **Prior announcement fields are preserved** — `role`, `common_name`,
   `device_name`, `serial`, `announcement` — when `probe_type` returns
   `None` (port busy, or silent). The last known identity is never
   cleared. A board whose port is momentarily held by a serial monitor
   must not lose its role, because losing `role` disarms the relay guard.
4. **`enum` is assigned once** and never changes for a given UID.

A fifth, stated in the agent manual and enforced by `probe_all`:
**`board_name` is read once** and never re-read, because it is fixed in
silicon.

The consequence for callers: `list` is cheap and trustworthy for *names*,
and `port` values are only as fresh as the last `probe`.

### 4.4 Merge algorithm — `probe_all`

`devices.probe_all(config_path, clear=False, target_mcu="nrf52833") -> list[dict]`

```
devices = {} if clear else load_devices(config_path)
probes  = flashable_probes()
uids    = {p["uid"] for p in probes}
ports   = port_serial_map(uids)        # scan restricted to real micro:bits

for each probe p:
    entry = devices.get(uid, {})
    entry["uid"]  = uid
    entry["port"] = ports.get(uid)               # always refresh; may be None
    if "enum" not in entry:
        entry["enum"] = assign_enum(devices, uid)
    info = probe_type(entry["port"]) if entry["port"] else None
    if info:
        entry["announcement"] = info["raw"]
        entry["role"], entry["common_name"] = info["role"], info["common_name"]
        entry["device_name"], entry["serial"] = info["device_name"], info["serial"]
    # else: preserve existing announcement fields unchanged
    if not entry.get("board_name"):
        device_id = read_device_id(uid, target_mcu)
        if device_id is not None:
            entry["device_id"]  = device_id
            entry["board_name"] = friendly_name(device_id)
    devices[uid] = entry

save_devices(devices, config_path)
return list(devices.values())
```

Notes that matter:

- The `ioreg` scan is restricted to the UIDs of connected CMSIS-DAP
  probes, so a non-micro:bit USB serial device can never be
  mis-attributed to a board.
- A board with no serial port gets an entry with `port: null`, and its
  announcement is not attempted at all.
- Each entry is written back into `devices` at the end of its iteration,
  so a second new board in the same run sees the first's freshly assigned
  enum and does not collide with it.
- The returned list includes boards that were *not* seen this run, because
  it is `list(devices.values())` over the whole merged registry.

### 4.5 Enumeration

`devices.assign_enum(devices, uid) -> int`:

- If `uid` already has an `enum`, return it unchanged.
- Otherwise return `max(existing enums, default=0) + 1`.

The minimum assigned value is therefore `1`. Enums are never reused, so a
gap in the sequence is normal and expected after boards leave the fleet.

---

## 5. Device discovery

### 5.1 `flashable_probes() -> list[{uid, description}]`

Every connected CMSIS-DAP probe pyOCD could flash. Primary path is the
pyOCD Python API: `ConnectHelper.get_all_connected_probes(blocking=False)`,
keeping each probe's `unique_id` and `description`. Probes without a
`unique_id` are dropped.

On *any* exception it falls back to `_flashable_probes_cli()`, which runs
`[sys.executable, "-m", "pyocd", "list"]` with a 30 s timeout and scrapes
the first `[0-9a-fA-F]{40,52}` token from each output line, using the whole
line as the description. A subprocess failure yields `[]`.

`devices.connected_uids() -> set[str]` is the UID-set projection of the
same call.

### 5.2 `port_serial_map(known=None) -> dict[uid, port]` — macOS only

Maps USB serial number (which equals the pyOCD UID) to `/dev/cu.*` port by
running `ioreg -r -c IOUSBHostDevice -l` with a 10 s timeout and scanning
its output line by line: a `"USB Serial Number" = "…"` line arms a current
serial, and the next `"IOCalloutDevice" = "…"` line pairs it with a port.
`setdefault` keeps the first callout device seen for a serial.

When `known` is given, only those UIDs are recorded, so a non-micro:bit
serial port can never be mis-attributed.

**It returns `{}` off macOS**, and on any `FileNotFoundError`,
`SubprocessError`, or non-zero `ioreg` exit. See §11 for the full
consequences of that.

### 5.3 Live vs. remembered

Three commands consult live state, and each does so for its own reason:

- `list` — to fill the `CONN` column and prefer a live port over a
  remembered one.
- `deploy` — to translate a `/dev/…` path into a UID, and to confirm the
  resolved board is actually attached before flashing.
- `connect` — to re-read a named board's current port.

---

## 6. Target resolution

Resolution happens in two layers: `devices.resolve_target()` handles
stable identifiers, and each command handles port paths itself. That split
is the whole point — see §6.2.

### 6.1 `resolve_target(token, devices) -> entry`

Precedence, in order:

| # | Token shape | Test | Matched against |
|---|-------------|------|-----------------|
| 1 | All digits | `token.isdigit()` | `enum` |
| 2 | Contains `/` | `startswith("/dev/") or "/" in token` | **Refused** — raises `ValueError` |
| 3 | 40–52 hex chars | `re.fullmatch(r"[0-9a-fA-F]{40,52}", token)` | `uid` |
| 4 | Anything else | — | `NAME_FIELDS`, case-insensitively |

`devices.NAME_FIELDS = ("device_name", "board_name")` — both are the
board's own five-letter micro:bit name, reached two ways: announced, or
read from silicon. They agree when both are known, and `device_name` is
checked first, so an announced name wins a tie against another board's
`board_name`.

Every failure raises `ValueError` with a descriptive message:
`No device found with enum N`, `No device found with uid '…'`,
`No device found matching '…'`, or the port-path refusal text.

**Why case 2 refuses.** The only port `resolve_target` could match is the
`port` recorded in the registry, and that is no fresher than the last
`probe_all`. macOS re-issues `/dev/cu.usbmodem*` names on every reconnect,
so two boards routinely swap paths between probes. Returning the entry
that *used to* sit on a path is worse than returning nothing, because the
caller then acts on that entry's UID — a different board than the path
names. That was a real wrong-board flash in `deploy`. A caller that
accepts a path must resolve it itself.

### 6.2 `deploy` — a path is resolved against the live map

`cli._deploy_entry(target, registry)`:

- A token that is **not** a path goes straight to `resolve_target`.
- A path is translated through the **live** `ioreg` mapping, restricted to
  connected CMSIS-DAP probes. Whichever board is on that port *right now*
  is the one that gets flashed.

`deploy` cannot use the path directly the way `connect` does, because
pyOCD addresses a board by UID; the path must be translated. So it
translates it live, and refuses rather than guessing in all three failure
modes:

| Situation | Behaviour |
|-----------|-----------|
| Live map empty and no probes connected | `ValueError`: "cannot resolve port '…': no micro:bit is connected." |
| Live map empty but probes *are* connected (off macOS, or `ioreg` failed) | `ValueError` naming `ioreg` and explicitly refusing to fall back to the recorded port; directs the user to enum/name/UID. |
| Path is not occupied by any connected micro:bit | `ValueError` listing the micro:bit ports that *are* occupied, or `none`. |

If the path does resolve to a live UID, that UID must still be present in
the registry. If it is not, `deploy` refuses with "…which is not in the
registry. Run 'mbdeploy probe' first — deploy needs the registry entry to
know whether the board is a relay." The reason is exact: the entry is
where `role` comes from, and `role` is what the relay guard reads, so
flashing an unregistered UID would mean flashing with no relay guard at
all.

Behaviour pinned by the test suite: with the registry recording device1 on
`/dev/cu.device1` but the two boards having since traded paths, `deploy
/dev/cu.device1` flashes **device2** — the board actually on it. And when
the relay has moved onto the path the registry associates with an ordinary
board, the relay guard fires and nothing is flashed.

### 6.3 `connect` — a path is opened verbatim

`cli._connect_port(target, registry) -> port`:

- A path (`startswith("/dev/")` or containing `/`) is **returned
  unchanged**. No lookup of any kind, live or recorded. `connect` wants a
  port and the path *is* one, so there is nothing to translate. This also
  means a board that has never been probed can be reached by path.
- Any other token is resolved through `resolve_target`, then:
  - the board must be in `connected_uids()`, or
    `ValueError("device not connected: …")`;
  - the port is re-read live via `port_serial_map({uid})`, falling back to
    the entry's recorded `port` if the live map yields nothing;
  - if neither produces a port, `ValueError("no serial port for device: …")`.

The recorded-port fallback is `connect`-only and deliberate: opening the
wrong port is recoverable and immediately obvious, whereas flashing the
wrong board is not. `deploy` refuses in the same situation. See §12 D5.

`connect`'s `target` is **required** — there is no auto-pick.

### 6.4 Why `common_name` is never a target

`common_name` is a human label for a board's *role* in a classroom —
"robot", "Jane's robot" — assigned by whoever set the fleet up. It is not
an identity the board carries: two boards can wear the same one, it
changes when a class is reassigned, and it says nothing about which
hardware is in your hand. `list` shows it so a board can be found on a
desk; nothing resolves it, and this is enforced in three places:

- `NAME_FIELDS` deliberately omits it.
- The test suite asserts that a `common_name` raises `No device found` for
  both `deploy` and `connect`, and separately that two boards sharing one
  label cannot silently pick either.
- `cli._device_label(entry)` — which names a device in error messages —
  never uses it, preferring `device_name`, then `board_name`, then `enum`,
  then `uid`, then the literal `"unknown"`. Quoting a `common_name` back
  in an error would name the device by the one word that cannot address
  it.

### 6.5 Auto-pick (`deploy` with no target)

When `deploy` is given no target, it selects the unique **non-relay**
entry in the registry:

- Zero non-relay entries → error "no non-relay devices in registry. Run
  'mbdeploy probe' first." → exit 1.
- More than one → error "ambiguous — multiple non-relay devices: [names].
  Specify a target." (names via `_device_label`) → exit 1.
- Exactly one → that entry is the target.

Auto-pick considers the *registry*, not the live probe list; a uniquely
non-relay but unplugged board is auto-picked and then fails the live-probe
confirmation in §8.

---

## 7. Command surface

```
mbdeploy [--version] [--agent] <subcommand> [options]
```

A subcommand is required (`subparsers.required = True`), except that
`--version` and `--agent` are handled first and short-circuit it.

### 7.1 Top-level flags

| Flag | Effect |
|------|--------|
| `--version` | Print `mbdeploy <version>` and exit 0. Argparse `action="version"`. |
| `--agent` | Print the bundled agent manual to stdout and exit 0. |
| `-h`, `--help` | Print usage and exit 0. |

`--agent` is a custom `argparse.Action` (`cli._AgentManualAction`) with
`nargs=0` and `default=argparse.SUPPRESS`. It reads
`agent_manual.md` out of the installed package via
`importlib.resources.files("mbdeploy")`, appends a trailing newline if the
file lacks one, writes it to stdout, and calls `parser.exit()`. The
manual is shipped inside the wheel (`pyproject.toml` declares it under
`[tool.hatch.build.targets.wheel] artifacts`), so `--agent` works from a
`pipx` install with no source tree present.

### 7.2 `build`

Compile the micro:bit firmware. Delegates to `builder.run()`.

| Flag | Effect |
|------|--------|
| `--clean` | Append `--clean` to the build command. |
| `--verbose` | Append `--verbose` to the build command. |
| `-j N` | Append `-j N`. |
| `--build-cmd CMD` | Replace the entire build command. Split on whitespace. `build` only. |

`builder.run(clean, verbose, jobs, build_cmd) -> int`:

- With `build_cmd`: the command is `build_cmd.split()`.
- Without: requires `./build.py` to exist in the CWD. If it does not,
  print "Error: build.py not found in CWD. Use --build-cmd to override."
  to stderr and return 1. Otherwise the command is
  `[sys.executable, "build.py"]`.
- Flags are then appended in the order `--clean`, `--verbose`, `-j N`.
- Returns the subprocess exit code. Never raises on build failure.

### 7.3 `deploy`

Flash firmware to a micro:bit.

| Argument / flag | Effect |
|-----------------|--------|
| `target` (positional, optional) | Enum, `/dev/` path, UID, or the board's five-letter name. Omitted → auto-pick (§6.5). |
| `--build` | Build before deploying. |
| `--clean` | Clean before building. Implies a build. |
| `-j N` | Parallel build jobs. |
| `--force-relay` | Allow deploying to a board whose role is a relay. |
| `--hex PATH` | Path to a pre-built `.hex`. Default `MICROBIT.hex`. |
| `--target-mcu MCU` | Target MCU. Default `nrf52833`. |
| `--config PATH` | Registry path. |

Order of operations in `_cmd_deploy` — each stage can fail the command
before the next begins:

1. Load the registry.
2. Resolve the target (`_deploy_entry`) or auto-pick. Failure → message on
   stderr, exit 1.
3. **Relay guard**: if `is_relay(entry["role"])` and not `--force-relay`,
   print `Error: <label> is a relay. Use --force-relay to override.` and
   exit 1. This runs *before* the connection check, so a relay is refused
   whether or not it is plugged in.
4. **Live-probe confirmation**: the entry's UID must be in
   `flashable_probes()`. Otherwise `Error: device not connected: <uid>`,
   exit 1.
5. **Optional build**: if `--build` or `--clean`, run `builder.run()`. On
   non-zero, print `Error: build failed (exit N).` and return that code.
6. **Flash and reset** — §8.

### 7.4 `list`

List every known device, connected or not.

| Flag | Effect |
|------|--------|
| `--config PATH` | Registry path. |
| `--fast` | Skip reading board names over SWD for boards with no recorded name. |
| `--target-mcu MCU` | MCU used for those SWD reads. Default `nrf52833`. |

`list` reads `flashable_probes()` for the live UID set, calls
`port_serial_map(live_uids)` **only if any probe is connected**, loads the
registry, and merges the three into display rows. With no rows at all it
prints `no devices found` to stdout and returns 0.

### 7.5 `probe`

Actively probe every connected device and update the registry.

| Flag | Effect |
|------|--------|
| `--config PATH` | Registry path. |
| `--target-mcu MCU` | MCU used for the SWD name read. Default `nrf52833`. |
| `--clear` | Start from an empty registry, keeping only currently connected devices. |

`probe` calls `probe_all()` (§4.4), then prints the same table as `list`,
built from the returned entries with `connected_uids()` for the `CONN`
column, an empty live-port map (`probe_all` already refreshed every
entry's port), and `read_names=False` (`probe_all` already read every
missing name). With no entries it prints `no devices found` and returns 0.

`--clear` is the only way an entry ever leaves the registry, and it is not
a deletion: `probe_all` simply starts from `{}` instead of the loaded
file, so the saved result contains exactly the boards connected at that
moment. Enums are reassigned from 1 for the survivors.

### 7.6 `connect`

Open a serial session, or send one line and print the reply.

| Argument / flag | Effect |
|-----------------|--------|
| `target` (positional, **required**) | Enum, name, UID, or `/dev/` port path. |
| `message` (positional, `nargs="*"`) | Words to send, joined with single spaces and terminated with `\n`. Omit for an interactive session. |
| `--baud N` | Baud rate. Default 115200. |
| `--timeout SEC` | Whole-exchange budget for a one-shot reply. Default 2.0. Parsed as a float. |
| `--config PATH` | Registry path. |

Flow (`_cmd_connect`):

1. Load the registry; resolve the port with `_connect_port` (§6.3). On
   `ValueError`, print `Error: <message>. Run 'mbdeploy probe' first.` to
   stderr and return 1.
2. Open the port with `console.open_port(port, baud)`. On `ConsoleError`,
   print `Error: <message>` to stderr and return 1.
3. **No message** → print the banner
   `connected to <port> at <baud> baud — Ctrl-D or Ctrl-C to exit` to
   **stderr**, then run `console.interact(ser)` and return its code (always 0).
4. **Message present** → `console.send_command(ser, " ".join(message),
   timeout)`. Print each reply line to **stdout**. If there were no lines,
   print `Error: no response from <port> within <timeout>s.` to stderr and
   return 1. Otherwise return 0.
5. The port is closed in a `finally` in every case.

### 7.7 Argument parsing — interleaved positionals

`connect <target> --baud 9600 "HELLO"` interleaves an option between two
positional groups, which plain argparse cannot place: it matches
positionals greedily in one pass, gives `target` the leading chunk, and
then reports the trailing message as unrecognised.

`cli._IntermixedSubparser` overrides `parse_known_args` to route through
`parse_known_intermixed_args`, with a `_intermixing` re-entrancy guard
because the intermixed parser drives `parse_known_args` internally. It is
installed as `parser_class` on `add_subparsers`, so **every** subcommand
gets it, not just `connect`.

Forms pinned by the test suite:

| Command line | Result |
|--------------|--------|
| `connect tovez --baud 9600 HELLO` | target=`tovez`, message=`["HELLO"]`, baud=9600 |
| `connect tovez HELLO --baud 9600` | same |
| `connect --baud 9600 tovez HELLO` | same |
| `connect tovez` | message=`[]` → interactive, baud=115200 |
| `connect tovez SET SPEED 50` | message=`["SET","SPEED","50"]`, order preserved |
| `connect tovez --timeout 5.5` | timeout=`5.5` (float) |
| `connect` | `SystemExit` — target is required |
| `connect tovez --bogus` | `SystemExit` — a typo'd flag must not become message text |
| `deploy gutov --clean`, `list --fast`, `probe --clear` | still parse normally |

### 7.8 Table output

`list` and `probe` print the identical table. Row format
(`cli._ROW_FMT`):

```
{enum:<5} {conn:<5} {name:<12} {common:<12} {role:<13} {port:<24} {uid}
```

Header `ENUM CONN DEVICE NAME COMMON NAME ROLE PORT UID`, followed by a
rule of `-` the same width as the header line.

Row construction (`cli._device_rows`), for the registry's UIDs followed by
any live UID not in the registry:

- `enum` — `str(entry["enum"])`, or empty for an unregistered board.
- `conn` — `yes` if the UID is in the live probe set, else `no`.
- `name` — `device_name` or `board_name`; if both are absent **and** the
  board is connected **and** `read_names` is true, read live over SWD
  (`read_board_name`), else empty.
- `common` — `common_name` or empty.
- `role` — `role` or empty.
- `port` — for a connected board, the live port, else the recorded port,
  else empty. **For a disconnected board, always empty** — a remembered
  port is meaningless once the board is unplugged, and showing it would
  invite exactly the stale-port mistake §6.1 exists to prevent.
- `uid` — the UID.

Sort key: connected first, then ascending `enum` (unregistered boards sort
last via a sentinel of `1 << 30`), then UID.

---

## 8. Flash behaviour

`deploy` runs pyOCD as a subprocess in up to three steps.

**Flash:**

```
<python> -m pyocd flash -t <target_mcu> --uid <uid> <hex_path>
```

**If the flash returns non-zero — mass-erase recovery.** A locked or
protected nRF (APPROTECT set, or a protected SoftDevice region at address
`0x0`) rejects every erase the flash algorithm attempts, so the flash
fails before it can program — typically as
`flash erase sector failure (… result code 0x67)`. Neither sector erase
nor chip erase clears that state; **only a CTRL-AP mass erase
(`ERASEALL`)** does, and it also resets APPROTECT. So `deploy`:

1. Prints to stderr: "flash failed — attempting CTRL-AP mass erase to
   recover a locked device, then retrying."
2. Runs `<python> -m pyocd erase -t <mcu> --uid <uid> --mass`.
   If that returns non-zero, print `Error: mass erase failed (exit N).`
   and **return N immediately** — no blind retry.
3. Retries the flash **once**. If it still fails, print
   `Error: flash still failed after mass erase (exit N).` and return N.

**Reset**, on success:

```
<python> -m pyocd reset -t <target_mcu> --uid <uid>
```

`deploy`'s exit code is this reset command's return code. The final reset
is not optional — freshly flashed firmware must actually start.

Pinned by the test suite: a first-flash failure followed by a successful
erase leads to exactly two flash invocations and an overall exit 0; an
erase failure with code 5 yields exit 5 and exactly one flash invocation;
a successful first flash never runs an erase at all.

Manual equivalent, for recovering a board outside `deploy`:

```bash
pyocd erase -t nrf52833 -u <uid> --mass
pyocd flash -t nrf52833 -u <uid> MICROBIT.hex
pyocd reset -t nrf52833 -u <uid>
```

---

## 9. Serial session semantics

All of `console.py`. Two modes share one open port.

### 9.1 Opening a port — DTR/RTS held low

`console.open_port(port, baud, settle=OPEN_SETTLE)`:

- Raises `ConsoleError("pyserial is not installed, …")` if `serial` is
  `None`.
- Constructs `serial.Serial(baudrate=baud, timeout=READ_TIMEOUT,
  dsrdtr=False, rtscts=False)`, then sets `.port`, `.dtr = False`,
  `.rts = False`, and only then calls `.open()`.
- **The ordering is the point.** DAPLink resets the target when DTR is
  asserted, so the modem lines are cleared *before* the port is opened —
  connecting to a running robot must not reboot it. The test suite asserts
  that both `dtr=False` and `rts=False` are set before `open()`.
- A failure to open is wrapped as `ConsoleError(f"cannot open {port}: {exc}")`.
- Sleeps `settle` seconds after opening.

### 9.2 Timing constants — what each one bounds

| Constant | Value | What it bounds |
|----------|-------|----------------|
| `OPEN_SETTLE` | 0.3 s | How long a freshly opened port is left alone before anything is written to it. Matches what `probe_type` does for its HELLO handshake. |
| `IDLE_GAP` | 0.4 s | How long the board may stay silent *after it has already said something* before its reply is treated as finished. |
| `READ_TIMEOUT` | 0.1 s | The per-read block time. Bounds how quickly a session notices its deadline or a stop request. Not a user-visible timeout. |
| `EOF_DRAIN` | 0.4 s | Grace period after stdin ends in an interactive session, so a piped one-liner shows the board's reply instead of racing EOF. Skipped on Ctrl-C, which means "stop now". |

### 9.3 One-shot exchange — `send_command(ser, message, timeout, idle_gap=IDLE_GAP)`

1. `reset_input_buffer()` — clear anything the board said before the
   question was asked.
2. Write `message.encode("utf-8") + b"\n"` and flush. Exactly one
   newline-terminated line.
3. Read lines until the loop ends, decoding UTF-8 with `errors="replace"`
   and stripping trailing `\r\n`. Empty lines are dropped.

The loop is governed by a **whole-exchange budget**, not a per-read
timeout:

```
deadline    = now + timeout
quiet_after = deadline          # nothing heard yet: wait out the full budget
loop:
    if now >= deadline or (lines and now >= quiet_after): return lines
    read a line; if non-empty, append it
    quiet_after = now + idle_gap
```

Three consequences, all deliberate:

- **The board has the full `timeout` to say anything at all.** Until it
  does, `quiet_after` is the deadline, so the idle gap cannot end the read
  early on a slow board.
- **A multi-line reply comes back whole.** Each line pushes `quiet_after`
  out by `idle_gap`; the read ends `idle_gap` after the last line, without
  waiting out the timeout.
- **A chatty board cannot hang the command.** A board streaming telemetry
  or an `ack` loop never goes quiet, so it is cut off at `timeout`.
  Raising `--timeout` helps a slow board answer, and simultaneously
  raises how much of a chatty board's stream is captured.

Returns a list of reply lines — empty if the board said nothing.

### 9.4 Interactive session — `interact(ser)`

A daemon thread (`mbdeploy-serial-read`) reads `ser.read(max(1,
ser.in_waiting))` in a loop and writes the decoded text (UTF-8,
`errors="replace"`) to stdout, flushing each time; it breaks out if the
port goes away. The main loop reads stdin line by line and writes each
line to the port.

- **EOF (Ctrl-D, or the end of a pipe)** → sleep `EOF_DRAIN`, then stop.
- **Ctrl-C** → `KeyboardInterrupt` is caught and the drain is skipped.
- Either way the reader is signalled to stop and joined with a 1 s
  timeout.
- **Always returns 0.** Ending a session is not a failure.

### 9.5 One port, one owner

A serial port cannot be shared. `connect` fails if a monitor already holds
it, and while `connect` is running, `probe` cannot read that board's
announcement — its `probe_type` call times out and returns `None`, at
which point invariant 3 (§4.3) preserves the board's previously recorded
identity rather than blanking it.

---

## 10. Exit codes and output streams

### 10.1 Exit codes

The standard contract: **`0` = success, non-zero = failure.** Callers —
especially agents — should check the exit code rather than scraping
stdout.

Non-zero cases for `deploy`:

- The target token matched no registry entry.
- A `/dev/` path could not be resolved live (no board connected, no live
  map available, path unoccupied, or the live board is unregistered).
- Auto-pick found zero or more than one non-relay device.
- The resolved device is a relay and `--force-relay` was not given.
- The resolved device is not currently connected.
- The build step (`--build` / `--clean`) failed — its exit code is
  propagated.
- `pyocd flash` failed *and* the mass-erase recovery also failed — the
  erase's or the retry flash's exit code is propagated.
- `pyocd reset` returned non-zero — its code is the command's exit code.

Non-zero cases for `connect`:

- The target matched no registry entry, or the board is not connected, or
  it has no serial port.
- The serial port could not be opened — wrong path, or another program (a
  serial monitor, an editor's terminal) already holds it.
- A message was sent and **nothing came back** within `--timeout`
  (exit 1).

`build` returns the build subprocess's exit code, or 1 when `build.py` is
missing and no `--build-cmd` was given.

`list` and `probe` return 0, including when there are no devices at all.

### 10.2 stdout vs. stderr

- **stdout** carries data: the device table, the board's serial reply, the
  agent manual, the version string, `no devices found`.
- **stderr** carries status and errors: every `Error: …` line, the
  interactive-session banner, and the mass-erase notice.

For `connect` the split is a contract, not a convention: the board's reply
is the *only* thing on stdout, so `mbdeploy connect tovez STATUS` can be
piped or captured directly. The test suite asserts that a one-shot's
stdout is exactly the reply, and that an interactive session's banner is
on stderr with nothing on stdout.

---

## 11. Known limitations

### 11.1 `port_serial_map()` is macOS-only

`devices.port_serial_map()` shells out to macOS `ioreg` and **returns
`{}` on Linux** (and on any platform without `ioreg`, and whenever `ioreg`
fails or exits non-zero). This is stated plainly because several
behaviours follow from it and none of them are obvious:

- **`deploy /dev/…` does not work off macOS.** It errors, naming `ioreg`,
  and explicitly refuses to fall back to the registry's recorded port —
  that fallback is the wrong-board bug the design exists to prevent.
  Target by enum, name, or UID instead; those work everywhere.
- **`probe` records `port: null` for every board**, and therefore never
  calls `probe_type`. No announcement is ever captured, so `role`,
  `common_name`, `device_name` and `serial` are never populated off macOS.
  Only `uid`, `enum`, `port: null`, `board_name` and `device_id` are —
  the last two because the SWD read path is platform-independent.
  A registry built on Linux consequently has **no roles, so the relay
  guard has nothing to guard with**.
- **`list` shows an empty PORT column**, and falls back to the recorded
  port for connected boards (which will be `null` if the registry was
  built off macOS).
- **`connect <name>` still works** where a port was previously recorded,
  because it falls back to the entry's stored `port`.

### 11.2 Other bounded behaviours

- A board whose serial port is held by another program cannot be
  announcement-probed while that program runs.
- `read_device_id` returns `None` for a probe that is busy mid-flash or a
  target that refuses the connection; the name is then simply blank.
- `flashable_probes()` swallows all pyOCD API errors and falls back to
  scraping `pyocd list`; a total failure yields an empty fleet rather than
  an error.
- A corrupt or unreadable `devices.json` is treated as empty, silently.

---

## 12. Documentation discrepancies

Found while reverse-engineering this specification. **In every case the
code is normative and this document follows it**; the entries below record
where the README or the bundled `--agent` manual has drifted.

| # | Discrepancy | Code behaviour | Doc claim |
|---|-------------|----------------|-----------|
| D1 | Announcement dialects | `probe_type` accepts **two** dialects: colon (`DEVICE:…`) and space-delimited (`device …`), with different serial-field handling (§3.5). | README and manual §3/§4 describe only the `DEVICE:` colon form. |
| D2 | `connect --config` | `connect` defines `--config PATH` like every other registry-reading subcommand. | Manual §3: "Override it with `--config PATH` on the `deploy`, `list`, and `probe` subcommands" — omits `connect`. |
| D3 | `deploy --verbose` | The `deploy` subparser defines **no** `--verbose` flag. `_cmd_deploy` reads it as `getattr(args, "verbose", False)`, which is therefore always `False`; `deploy --verbose` is a parse error. Only `build` has `--verbose`. | Manual §7 heads its table "Build options (`build` and `deploy --build`)" and lists `--verbose` without restriction, while explicitly marking `--build-cmd` as build-only. |
| D4 | Where a path is resolved | Resolution is two-layer: `resolve_target` **refuses** a path outright with a `ValueError`; the live-`ioreg` lookup lives in `cli._deploy_entry`. | Manual §4 presents "contains `/` → matched against the live `ioreg` port map" as step 2 of one precedence list, which reads as though `resolve_target` does it. |
| D5 | `connect`'s port fallback | `_connect_port` falls back to the entry's recorded `port` when the live map yields nothing (pinned by `test_recorded_port_is_the_fallback`). `deploy` refuses in the same situation. | Manual §4: "`connect` re-reads the port live rather than trusting the registry, for the same staleness reason" — omits the fallback and the asymmetry with `deploy`. |
| D6 | `--target-mcu` on `list` / `probe` | Both accept `--target-mcu MCU`, used for the SWD name read. | Neither README nor manual documents it; the manual mentions `--target-mcu` only under `deploy`. |
| D7 | The shipped registry has no roles | `config/devices.json` records three boards (`gopiv`, `tovez`, `vevov`) with only `board_name`, `device_id`, `enum`, `port`, `uid` — no `role`, `common_name`, `device_name`, or `serial`. Consequence: `is_relay(None)` is `False` for all three, so auto-pick sees three non-relay devices and errors as ambiguous, and no relay guard can fire. | This is the field footprint of the D1 parser gap described in `probe_type`'s own comment; no document mentions that a registry can legitimately contain announcement-free entries. |
| D8 | Registry field list | `probe_all` writes an `announcement` field (the raw line). | The manual §3 field table lists nine fields and omits `announcement`. |
| D9 | When `list` reads a name live | The condition is "connected, `read_names`, and **no name recorded**" — `device_name` and `board_name` both absent. A registry entry that exists but has neither name still triggers a live read. | README and manual §3.1 say "boards the registry doesn't know yet" / "connected boards the registry does not know", which reads as "not in the registry". |
| D10 | Decode error policy | `probe_type` decodes with `errors="ignore"`; `console.send_command` and `interact` decode with `errors="replace"`. | Undocumented either way. Noted here only so the asymmetry is not mistaken for a bug later. |
