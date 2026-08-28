# mbdeploy — Use Cases

Derived from the shipped command surface and the recipes in the bundled
agent manual. Each use case describes behaviour that exists today; nothing
here is aspirational.

Behavioural detail (exact error text, precedence rules, timing constants)
lives in `specification.md`; this document records *who does what, in what
order, and what happens when it goes wrong*.

## Actors

- **Operator** — the classroom operator: a teacher or student running a
  lab of micro:bit robots from a terminal, with the boards physically in
  front of them.
- **Agent** — an AI coding agent driving `mbdeploy` non-interactively. It
  cannot see the desk, so it relies on the registry, the table output, and
  above all the exit code.

Where both actors perform a use case identically, both are listed.

## Global preconditions

Unless a use case says otherwise, these hold for all of them:

- `mbdeploy` is installed (typically via `pipx`) and on `PATH`.
- The command is run from the root of the firmware project, so
  `./config/devices.json`, `./MICROBIT.hex` and `./build.py` resolve.
- Any board being acted on is attached over USB.

---

## UC-001 — Discover the fleet

**Actor**: Operator, Agent

**Preconditions**: One or more micro:bits are plugged in. The registry may
be absent, stale, or empty.

**Main flow**:

1. The actor runs `mbdeploy probe`.
2. `mbdeploy` enumerates every connected CMSIS-DAP probe and collects
   their UIDs.
3. It builds a live UID → `/dev/cu.*` map from macOS `ioreg`, restricted
   to those UIDs so no unrelated USB serial device can be mis-attributed.
4. For each probe: the entry's `port` is refreshed (possibly to `null`); a
   new UID is assigned the next enum; if a port exists, the board is sent
   `HELLO` and its `DEVICE:` announcement parsed into `role`,
   `common_name`, `device_name`, `serial` and `announcement`; and if no
   `board_name` is recorded yet, `FICR.DEVICEID[1]` is read over SWD and
   decoded into the five-letter name.
5. The merged registry is written to `config/devices.json`.
6. The device table is printed, connected boards first.
7. Exit 0.

**Postconditions**: Every connected board has a registry entry with a
current `port` and an `enum`. Boards that were already known keep their
enum and — if this probe read nothing from them — their previously
recorded identity. No entry was removed.

**Error flows**:

- *No probes connected* → `no devices found` on stdout, exit 0. This is
  not an error condition.
- *A board's serial port is busy* (a monitor or an open `connect` session
  holds it) → the announcement read times out, and that board's existing
  `role`/`common_name`/`device_name`/`serial` are preserved unchanged
  rather than cleared.
- *A board is locked, or its probe is busy mid-flash* → the SWD name read
  returns nothing; `board_name` stays absent and nothing else about the
  entry changes.
- *Running off macOS* → the live port map is empty, every entry records
  `port: null`, and no announcement is attempted for any board. Names
  still appear, because the SWD read is platform-independent.

---

## UC-002 — List the known fleet, connected or not

**Actor**: Operator, Agent

**Preconditions**: None. A missing or corrupt registry is treated as
empty.

**Main flow**:

1. The actor runs `mbdeploy list`.
2. `mbdeploy` reads the live probe list, and — only if something is
   connected — the live port map.
3. It loads the registry and merges: every registry entry, plus any
   connected UID the registry does not contain.
4. Each row gets `CONN=yes` or `CONN=no`. A connected board with no
   recorded name has its five-letter name read live over SWD.
5. A disconnected board's PORT column is left blank, because a remembered
   port is meaningless once the board is unplugged.
6. Rows are sorted connected-first, then by enum (unregistered boards
   last), then by UID, and printed.
7. Exit 0.

**Postconditions**: The registry is unchanged — `list` never writes. The
actor knows which boards exist, which are plugged in right now, and what
each is called.

**Error flows**:

- *Nothing known and nothing connected* → `no devices found` on stdout,
  exit 0.
- *A live SWD name read fails* → that row's DEVICE NAME is blank; the rest
  of the row still prints.

**Variation — UC-002a, skip the SWD reads**: the actor runs
`mbdeploy list --fast`. Step 4's live read is skipped entirely, so a
connected but never-probed board appears with a blank name. Used when the
debug probes are busy, or when the command must not touch hardware at all.

---

## UC-003 — Rebuild the registry from the live fleet

**Actor**: Operator

**Preconditions**: The registry has accumulated entries for boards that
have left the fleet, or holds identity fields the operator no longer
trusts. Every board that should survive is plugged in **now**.

**Main flow**:

1. The operator runs `mbdeploy probe --clear`.
2. `mbdeploy` starts from an empty registry rather than loading the file.
3. It then performs UC-001's discovery against that empty starting point.
4. The saved registry contains exactly the boards connected at that
   moment, with enums reassigned from 1.
5. The table is printed; exit 0.

**Postconditions**: Departed boards are gone from the file. Surviving
boards have fresh ports, fresh announcements, and **new enum numbers**.

**Error flows**:

- *A board that should survive is unplugged at the moment of the probe* →
  it is silently absent from the rebuilt registry. Recovering it means
  plugging it in and probing again, which will assign it a new enum.
- *Scripts or notes that referenced the old enums* → those enums are now
  wrong. This is the cost of `--clear` and the reason it is opt-in;
  ordinary `probe` never renumbers anything.

---

## UC-004 — Build the firmware

**Actor**: Operator, Agent

**Preconditions**: The CWD contains `build.py`, or the actor supplies
`--build-cmd`.

**Main flow**:

1. The actor runs `mbdeploy build` (optionally with `--clean`,
   `--verbose`, `-j N`).
2. `mbdeploy` constructs `<python> build.py`, appending `--clean`,
   `--verbose`, and `-j N` as requested.
3. The build runs as a subprocess with its output inherited.
4. `mbdeploy` exits with the build's exit code.

**Postconditions**: On success, the project's hex artifact (by default
`MICROBIT.hex`) is current. No board was touched and the registry was not
read.

**Error flows**:

- *`build.py` is absent and no `--build-cmd` was given* →
  `Error: build.py not found in CWD. Use --build-cmd to override.` on
  stderr, exit 1.
- *The build script fails* → its non-zero exit code is `mbdeploy`'s exit
  code. `mbdeploy` never raises on a build failure.

**Variation — UC-004a, a different build command**: the actor runs
`mbdeploy build --build-cmd "make firmware"`. The string is split on
whitespace and replaces the command entirely; the `build.py` existence
check is skipped. `--build-cmd` exists only on `build`, not on `deploy`.

---

## UC-005 — Deploy to the only robot on the bench

**Actor**: Operator, Agent

**Preconditions**: The registry contains exactly one non-relay device, and
that board is connected. A hex file exists at `MICROBIT.hex`.

**Main flow**:

1. The actor runs `mbdeploy deploy` with no target.
2. `mbdeploy` loads the registry and selects the unique entry whose `role`
   is not a relay.
3. The relay guard passes trivially.
4. The board's UID is confirmed present in the live probe list.
5. `pyocd flash` writes the hex; `pyocd reset` starts it.
6. Exit 0.

**Postconditions**: The board is running the new firmware. The registry is
unchanged.

**Error flows**:

- *No non-relay entries* → `Error: no non-relay devices in registry. Run
  'mbdeploy probe' first.`, exit 1.
- *More than one non-relay entry* → `Error: ambiguous — multiple non-relay
  devices: [names]. Specify a target.`, exit 1. The correct response is to
  pass an explicit target (UC-006), never to retry the bare command. Note
  that boards with no recorded `role` count as non-relay, so a registry
  built without announcements (for example off macOS) is ambiguous as soon
  as it holds two boards.
- *The auto-picked board is not plugged in* → `Error: device not
  connected: <uid>`, exit 1. Auto-pick reads the registry, not the live
  fleet, so it can select an absent board.

**Variation — UC-005a, build and deploy in one step**: the actor runs
`mbdeploy deploy --build` (or `--clean`, which implies a build). The build
runs *after* target resolution, the relay guard, and the connection check,
and *before* the flash — so a bad target or a guarded relay fails fast
without spending a build. A failing build propagates its exit code and
nothing is flashed.

---

## UC-006 — Deploy to an explicitly named board

**Actor**: Operator, Agent

**Preconditions**: The target board is in the registry and connected.

**Main flow**:

1. The actor runs `mbdeploy deploy <target>`, where `<target>` is one of:

   | Form | Example | Resolved against |
   |------|---------|------------------|
   | Enum | `mbdeploy deploy 2` | The `enum` field |
   | Five-letter name | `mbdeploy deploy tovez` | `device_name`, then `board_name`, case-insensitively |
   | UID | `mbdeploy deploy 9906…2820` | The `uid` field (40–52 hex chars) |

2. `mbdeploy` resolves the token through the registry by that precedence.
3. The relay guard, the connection check, the optional build, the flash
   and the reset proceed as in UC-005.
4. Exit 0.

**Postconditions**: That specific board — and no other — is running the
new firmware.

**Error flows**:

- *The token matches nothing* → `No device found with enum N` /
  `No device found with uid '…'` / `No device found matching '…'` on
  stderr, exit 1.
- *The token is a `common_name`* → treated as an unmatched name and
  refused. A `common_name` ("robot", "Jane's robot") is a classroom label
  for a role, not an address: two boards can share one, and it changes
  when a class is reassigned. `list` shows it so a board can be found on a
  desk; nothing resolves it.
- *The board is registered but unplugged* → `Error: device not connected:
  <uid>`, exit 1.
- *The board's `role` is a relay* → refused; see UC-008.

---

## UC-007 — Deploy to whichever board is on a given port

**Actor**: Operator

**Preconditions**: macOS (the live port map comes from `ioreg`). A
micro:bit occupies the named path, and that board is already in the
registry.

**Main flow**:

1. The operator runs `mbdeploy deploy /dev/cu.usbmodem1234`.
2. `mbdeploy` recognises the token as a path and does **not** look it up
   in the registry.
3. It enumerates connected probes, builds the live `ioreg` port map
   restricted to them, and inverts it to find the UID on that path *right
   now*.
4. That UID's registry entry is fetched — the entry is where `role` comes
   from, and `role` is what the relay guard reads.
5. The relay guard, connection check, flash and reset proceed as in
   UC-005.
6. Exit 0.

**Postconditions**: The board physically on that port was flashed —
even if the registry still records a different board there.

**Error flows**:

- *No micro:bit is connected at all* → `cannot resolve port '…': no
  micro:bit is connected.`, exit 1.
- *The live map is unavailable* (off macOS, or `ioreg` failed) →
  an error naming `ioreg` and explicitly refusing to fall back to the
  registry's recorded port; the operator is directed to target by enum,
  name, or UID. Exit 1. **This refusal is the feature.** Falling back
  would flash the board that *used to* be on that path — a wrong-board
  flash that has actually happened.
- *No micro:bit occupies that path* → an error listing the micro:bit ports
  that are occupied (or `none`), exit 1.
- *The board on the path is not in the registry* → `port '…' is device
  <uid>, which is not in the registry. Run 'mbdeploy probe' first —
  deploy needs the registry entry to know whether the board is a relay.`,
  exit 1. Flashing it would mean flashing with no relay guard at all.
- *The board on the path is a relay* → the guard fires on the **live**
  board's role, not the one the registry associates with that path, and
  nothing is flashed.

**Note**: scripts should prefer an enum or a name. A path is resolved
live, so it is never *wrong* — but it may name a different board than it
did an hour ago.

---

## UC-008 — Deliberately flash a relay

**Actor**: Operator

**Preconditions**: The target board's registry entry has a `role`
containing `RELAY` or `BRIDGE` (case-insensitively) — e.g. `RADIOBRIDGE`.
The operator genuinely intends to reflash the radio gateway.

**Main flow**:

1. The operator runs `mbdeploy deploy bridge1 --force-relay`.
2. The target resolves as in UC-006 or UC-007.
3. The relay guard sees `--force-relay` and permits the deploy.
4. The connection check, optional build, flash and reset proceed.
5. Exit 0.

**Postconditions**: The relay is running the new firmware. Until it comes
back up, the fleet's radio link is down.

**Error flows**:

- *`--force-relay` omitted* → `Error: <label> is a relay. Use
  --force-relay to override.`, exit 1, and nothing is flashed. The guard
  runs **before** the connection check, so a relay is refused whether or
  not it is plugged in. The device is named by the word that would
  actually address it (`device_name`, else `board_name`, else `enum`, else
  `uid`) — never by its `common_name`.
- *The board's `role` is unknown because no announcement was ever
  captured* → the guard cannot fire and the board is treated as an
  ordinary device. Run `mbdeploy probe` on macOS with the port free to
  populate `role`.

---

## UC-009 — Recover a locked or protected board

**Actor**: Operator, Agent

**Preconditions**: The target board's nRF52833 is locked or protected —
APPROTECT set, or a protected SoftDevice region at address `0x0`. The
board is connected and resolvable.

**Main flow**:

1. The actor runs an ordinary `mbdeploy deploy <target>`. **No special
   flag is needed; recovery is automatic.**
2. `pyocd flash` fails, because the flash algorithm's erase is rejected —
   typically `flash erase sector failure (… result code 0x67)`.
3. `mbdeploy` prints to stderr that it is attempting a CTRL-AP mass erase
   to recover a locked device, then retrying.
4. It runs `pyocd erase --mass` (`ERASEALL`) — the only operation that
   clears APPROTECT.
5. It retries the flash **once**.
6. On success, `pyocd reset` starts the firmware; exit 0.

**Postconditions**: The board is unlocked, flashed, and running. Its
previous flash contents are gone — a mass erase is not selective.

**Error flows**:

- *The mass erase itself fails* → `Error: mass erase failed (exit N).`,
  and `mbdeploy` returns N **without** retrying the flash. Blind retries
  on a board that refuses ERASEALL achieve nothing.
- *The retried flash fails too* → `Error: flash still failed after mass
  erase (exit N).`, exit N.
- *The board was never locked and the first flash succeeded* → no erase is
  attempted at all. Recovery is strictly a failure path.

**Variation — UC-009a, manual recovery outside `mbdeploy`**:

```bash
pyocd erase -t nrf52833 -u <uid> --mass
pyocd flash -t nrf52833 -u <uid> MICROBIT.hex
pyocd reset -t nrf52833 -u <uid>
```

---

## UC-010 — Ask a board one question and read its answer

**Actor**: Agent, Operator

**Preconditions**: The board is connected, resolvable, and its serial port
is free. Its firmware answers on serial.

**Main flow**:

1. The actor runs `mbdeploy connect tovez "STATUS"` (or, without quotes,
   `mbdeploy connect tovez SET SPEED 50` — everything after the target is
   joined with single spaces).
2. `mbdeploy` resolves the target through the registry, confirms the board
   is connected, and re-reads its port live.
3. The port is opened at `--baud` (default 115200) with DTR and RTS
   cleared **before** the open, so the board is not reset, and is left to
   settle briefly.
4. The input buffer is cleared; the message is written as one
   newline-terminated line.
5. Replies are collected under a whole-exchange budget of `--timeout`
   (default 2 s): the board has that long to say anything at all, and once
   it has answered and then stayed quiet for ~0.4 s the read ends early,
   so a multi-line reply comes back whole without waiting out the timeout.
6. Each reply line is printed to **stdout**; every status line went to
   stderr.
7. The port is closed. Exit 0.

**Postconditions**: The board is unchanged and still running whatever it
was running. Nothing was written to the registry. The command's stdout is
exactly the board's reply, so it pipes cleanly.

**Error flows**:

- *The board answers nothing within `--timeout`* → nothing on stdout,
  `Error: no response from <port> within <timeout>s.` on stderr, **exit
  1**. This is the whole point of the exit-code contract: an agent checks
  the code, not the text.
- *The target cannot be resolved, or the board is not connected, or it has
  no known port* → `Error: <reason>. Run 'mbdeploy probe' first.`, exit 1.
  No port is opened.
- *The port cannot be opened* — wrong path, or another program (a serial
  monitor, an editor's terminal) already holds it → `Error: cannot open
  <port>: <reason>`, exit 1. A serial port has exactly one owner.
- *`pyserial` is not installed* → a clean `ConsoleError` about pyserial,
  exit 1.
- *The board streams continuously* (telemetry, an `ack` loop) → it never
  goes quiet, so the read is cut off at `--timeout` rather than hanging
  the command. Raising `--timeout` both helps a slow board answer and
  captures more of a chatty board's stream.

**Note for agents**: use `connect`, not `probe`, to check that firmware
came up after a deploy. `probe` rewrites the registry; `connect` only
reads.

---

## UC-011 — Hold an interactive serial session

**Actor**: Operator

**Preconditions**: As UC-010, and the operator has a terminal.

**Main flow**:

1. The operator runs `mbdeploy connect tovez` with **no** message.
2. The port is resolved and opened exactly as in UC-010 — DTR held low, so
   connecting does not reboot the board.
3. A banner (`connected to <port> at <baud> baud — Ctrl-D or Ctrl-C to
   exit`) is printed to **stderr**, keeping stdout clean for the board's
   output.
4. A background reader relays everything the board says to stdout; the
   main loop relays each line the operator types to the board.
5. The operator ends the session with Ctrl-D (or EOF on a pipe), which
   allows a short drain so a piped one-liner still shows the reply, or
   with Ctrl-C, which stops immediately and skips the drain.
6. The reader is stopped, the port closed. **Exit 0 always** — ending a
   session is not a failure.

**Postconditions**: The board is unchanged and still running. The registry
was not written.

**Error flows**:

- *Target resolution or port opening fails* → as UC-010; the session never
  starts and the exit code is 1.
- *The port disappears mid-session* (the board is unplugged) → the reader
  thread stops; the session ends without an error code.

---

## UC-012 — Talk to a board that has never been probed

**Actor**: Operator, Agent

**Preconditions**: The board is plugged in and its `/dev/cu.*` path is
known (from `ls /dev/cu.*`, or from another tool). It need not be in the
registry, and need not run announcing firmware.

**Main flow**:

1. The actor runs `mbdeploy connect /dev/cu.usbmodem99 "HELLO"` — or with
   no message, for an interactive session.
2. `mbdeploy` recognises the token as a path and uses it **verbatim**: no
   registry lookup, no live port map, no connection check.
3. The exchange proceeds exactly as in UC-010 or UC-011.

**Postconditions**: The board was talked to without ever being registered.

**Error flows**:

- *The path does not exist or cannot be opened* → `Error: cannot open
  <path>: <reason>`, exit 1.
- *The path names a board the registry also knows* → it is still opened
  verbatim and never re-resolved. Matching the recorded port and then
  re-resolving that board's *current* port would quietly open a different
  board than the one the actor named.

**Contrast with `deploy`**: `deploy` cannot do this. pyOCD addresses a
board by UID, so a path must be translated — and translating it demands
both a live port map and a registry entry (for the relay guard). `connect`
wants a port, and the path already is one.

---

## UC-013 — Read the manual, or the installed version

**Actor**: Agent, Operator

**Preconditions**: `mbdeploy` is installed. No hardware, registry, or
project directory is required.

**Main flow**:

1. The actor runs `mbdeploy --agent`.
2. The flag is handled before any subcommand is required, so no subcommand
   need be named.
3. The bundled `agent_manual.md` is read out of the installed package —
   it ships inside the wheel, so this works from a `pipx` install with no
   source tree present.
4. The full manual is written to stdout, newline-terminated, and the
   process exits 0.

**Postconditions**: The actor has the complete reference: command surface,
device model, exit-code contract, and copy-paste recipes.

**Error flows**:

- *The packaged resource is missing* (a broken or partial install) → the
  resource read raises rather than printing a partial manual.

**Variation — UC-013a, version check**: `mbdeploy --version` prints
`mbdeploy <version>` and exits 0. The version comes from installed package
metadata, so it is correct for editable installs too; it is never a
hardcoded placeholder.

---

## UC-014 — Point every command at a different registry

**Actor**: Operator, Agent

**Preconditions**: A second fleet, or a registry kept somewhere other than
`./config/devices.json`.

**Main flow**:

1. The actor passes `--config /path/to/devices.json` to `probe`, `list`,
   `deploy`, or `connect`.
2. That path replaces the CWD-relative default for the whole invocation.
3. `probe` creates the file's parent directories if needed and writes the
   registry there.

**Postconditions**: The named registry is the one read and — for `probe` —
written. The default `config/devices.json` is untouched.

**Error flows**:

- *The path does not exist, or holds invalid JSON* → it is treated as an
  empty registry, silently. `list` then reports only connected boards;
  `deploy` cannot resolve any name and refuses.
- *The path is not writable* → `probe` fails when saving.

**Related overrides**: `--hex PATH` selects a different firmware image and
`--target-mcu MCU` a different part for `deploy` (e.g.
`mbdeploy deploy 2 --hex build/MICROBIT.hex --target-mcu nrf52833`);
`--target-mcu` on `list` and `probe` selects the part used for the SWD
name read.
