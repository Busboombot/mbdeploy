---
title: Troubleshooting
blurb: The failures that look like bugs and are not, and the ones that are.
order: 80
updated: 2026-08-27
tags: [troubleshooting, operations]
---

# Troubleshooting

Symptom first, then what it actually means. Several entries here describe
**correct** behaviour that is routinely mistaken for a defect.

## Local USB and the registry

### The PORT column is empty, and `deploy /dev/…` refuses

```
Error: cannot resolve port '/dev/ttyACM0': no micro:bit serial port was found,
even though a probe is connected. On Linux, check that this user is in the
'plugdev'/'dialout' group. Refusing to fall back to the registry's recorded
port, which may name a different board. Target by enum, name, or UID instead.
```

An empty live port map means **no micro:bit serial port was found**, even though
pyOCD can see a probe. On Linux this is almost always group membership: the user
needs both `plugdev` (raw USB, for pyOCD) and `dialout` (the serial port).

```bash
sudo usermod -aG plugdev,dialout <user>
```

then log out and back in. Other causes: `pyserial` missing from the environment,
or the device is not a micro:bit — the scan only accepts USB `0d28:0204`, the
DAPLink interface every micro:bit enumerates as.

`deploy` refuses rather than falling back to the registry's recorded port on
purpose: that fallback is the wrong-board flash the whole design exists to
prevent. Enum, name, and UID targeting still work in this state.

### `no micro:bit is on port 'X' right now`

The path is not occupied by a micro:bit at this moment. The error lists the
micro:bit ports that *are* occupied. Ports get re-issued on every reconnect, so a
path that worked an hour ago may now name a different board or nothing at all.
Target by enum or name instead.

### `port 'X' is device <uid>, which is not in the registry`

The board on that path has never been probed. `deploy` needs the registry entry
because that is where `role` comes from, and `role` is what the relay guard
reads — flashing an unregistered UID would mean flashing with no guard at all.
Run `mbdeploy probe` once and retry.

### `deploy` says a board "is a relay" and it is not

Its registry entry holds a stale `role`. Before 2026-08-27 only one of the two
announcement dialects was parsed, so a board running robot firmware never
updated its announcement fields and kept whatever role it last had — including
`RADIOBRIDGE` on a board reflashed from relay firmware to robot firmware. The
five-letter name still filled in from the independent SWD read, which hid the
problem.

Re-run `mbdeploy probe` with a current build. If the entry is still wrong,
`mbdeploy probe --clear` rebuilds the registry from the connected boards only.

### `ambiguous — multiple non-relay devices`

`deploy` with no target auto-picks the unique non-relay entry — counting
**registry entries, not connected boards**. Three remembered boards with no roles
are three non-relay entries, so auto-pick is ambiguous even when only one is
plugged in. Pass an explicit target rather than retrying the bare command.

### `device not connected: <uid>`

The resolved board is not in the current probe list. It is unplugged, its cable
is charge-only, or the probe is temporarily unavailable (another pyOCD operation
holds it).

### A board's DEVICE NAME column is blank

The SWD name read did not succeed, or `list --fast` skipped it. The read fails
when the probe is busy — mid-flash, mid-erase — or the part refuses the
connection. Nothing else about the entry changes; re-run `probe` when the board
is idle. A blank name is never fatal: target the board by enum or UID meanwhile.

### `probe` records ports but no role or announcement

The board's serial port was busy, or the board says nothing. **One port, one
owner**: while a `connect` session, a serial monitor, or an editor terminal holds
the port, `probe` cannot read that board's announcement — it preserves the
previously known identity fields rather than clearing them. Close the other
program and re-probe.

A board with no announcing firmware never produces an announcement at all; see
the next section.

### `build.py not found in CWD`

`mbdeploy` resolves all project paths against the current directory. Run it from
the root of the firmware project, or pass `--build-cmd`.

## Serial

### `connect <board> "HELLO"` exits 1 with no output — expected on a silent board

```
Error: no response from /dev/ttyACM0 within 2s.
```

A board running no announcing firmware answers nothing. `connect` exits 1
because nothing came back within `--timeout`, which is the documented contract,
not a failure of the tool — and it is why such a board's `role`, `common_name`,
and `device_name` stay empty in the registry forever. The board is still
perfectly flashable, and still has a five-letter name, because that name is read
over SWD rather than asked for over serial.

Before concluding the board is silent, rule out: the wrong baud (`--baud`), too
short a `--timeout` for slow firmware, and another program holding the port.

### `cannot open <port>`

Something else already has it. A serial port cannot be shared.

### An interactive session prints nothing

Check that you are talking to the right board (`list`), that the firmware
actually emits anything unprompted, and remember that connecting holds DTR low
deliberately — the board is **not** reset when you connect, so it will not
re-print a startup banner.

## Flashing

### `flash erase sector failure (address 0x00000000; result code 0x67)`

The part is locked or protected (`APPROTECT`, or a protected SoftDevice region at
`0x0`). **This recovers automatically**: `deploy` runs a CTRL-AP mass erase — the
only operation that clears `APPROTECT` — and retries the flash once. You will see
the notice on stderr and then a normal flash. Some firmware images re-lock on
every write, so the recovery path runs on every flash of that image; that is
expected and simply slower.

If the mass erase itself fails, `deploy` stops and returns the erase's exit code
rather than retrying blindly. That points at the probe or the target connection,
not at protection.

### A flash takes a long time and looks stuck

It probably is not. pyOCD's output is streamed line by line, so erase and program
progress appear as they happen, locally and over the network alike. A real
production image (~1.2 MB of Intel HEX, ~464 KB programmed) takes roughly 48 s on
Raspberry Pi-class SWD hardware, and up to ~78 s through the mass-erase recovery
path.

## Network and `--remote`

### `list --remote` shows nothing, or a board is missing

In order of likelihood: the node with that board is not running the daemon; the
board is not plugged in; the client and the node are not on the same LAN segment
(mDNS does not cross subnets); or the board has only just been plugged in and the
watcher's next poll has not run yet (up to `--poll-interval`, default 2 s, plus
USB enumeration). The browse itself waits about 2 s — a heavily loaded network
can occasionally need a second attempt.

Which nodes are supposed to be running the daemon is recorded on the internal
Robot Garage wiki: <http://robot-garage.home/doku.php?id=mbdeploy> (garage LAN
only).

### `no board named 'X' found advertising _mbserial._tcp.local.`

Either nothing by that name is on the LAN, or the daemon has not finished
registering it. Run `mbdeploy list --remote` to see the names actually being
advertised — the name is the mDNS instance name, which is usually the board's
five-letter name but falls back to `mb-<last 8 of uid>` when neither identity
source was available.

### `multiple boards named 'X' found advertising …`

Two boards really do share a name, or a duplicate registration was caught in
flight. The client refuses to guess and lists every candidate's host and port.
This is also what a misused `serve --service-name` on a multi-board host looks
like — that flag renames *every* board the process manages.

### A board advertises as `mb-<something>`, or two boards share that name

The daemon could name it neither from the SWD read nor from an announcement, so
it fell back to `mb-<last 8 of uid>`. That suffix is **the same on every
micro:bit**, so every board in this state advertises the same name; a second one
is renamed by zeroconf to `mb-… (2)`, and neither can be reliably addressed.

Two things to do. First, treat it as a signal that the SWD name read is failing
for that board — a blank DEVICE NAME in a local `mbdeploy list` on that node
confirms it — and find out why (busy probe, locked part, cabling). Second, until
the underlying defect is fixed, `serve --service-name NAME` can give a
single-board host an unambiguous name. Tracked in
[Open tasks](/subsystems/mbdeploy/).

### `refused the connection: busy`

Another serial session or a flash already occupies that board. Serial sessions
are exclusive and never preempt anything.

### A `connect --remote` session drops without explanation

Most likely a `FLASH` preempted it — a flash always wins over a live serial
session, and the displaced client is deliberately not told why. The other causes
are the board being unplugged and the daemon being restarted; all three look
identical from the client, which simply sees a clean close.

### `deploy --remote` reports "no response … before the flash finished"

The connection went completely silent for longer than the client's 90-second
per-line read budget. Since pyOCD's output is streamed as `LOG` lines throughout
a flash, this now indicates a genuinely stalled connection rather than a slow
flash — but **check the node's journal before assuming the board was not
written**: this exact message was historically produced by flashes that had
actually succeeded, and the server side does not abandon a flash because a client
went away.

### `auth required` from `connect --remote` or `deploy --remote`

The daemon was started with `--token`/`--token-file`, and **no client can
satisfy it** — there is no client-side token flag and no `AUTH` line is ever
sent. Restart the daemon without a token, or use `--no-flash` if the goal was to
prevent remote flashing. `list --remote` keeps working either way, because it
never opens a socket. Tracked in [Open tasks](/subsystems/mbdeploy/).

## The daemon

### `serve` exits 1 immediately at startup

The usual cause is `--token-file`: an unreadable path, or a file that is empty
after trailing whitespace is stripped. `serve` refuses to start rather than
silently running with no authentication.

### `mbdeploy` service is enabled but boards never appear

Check the service user's group membership on that node (`plugdev` and
`dialout`) — the daemon starts happily without them and then finds no ports.
`journalctl -u mbdeploy -b` and a local `mbdeploy list` on the node itself will
show whether the boards are visible at all.

### macOS only: `serve` throws `NSInvalidArgumentException` at exit

With hardware attached, `mbdeploy serve` on macOS can raise an
`NSInvalidArgumentException` from hidapi's `hid_exit()` during interpreter
shutdown. This is a **pre-existing hidapi/IOKit thread-safety bug, not an
mbdeploy defect**, and it happens after the daemon has already unregistered its
mDNS advertisements and closed its sockets — nothing is left in a bad state.

It was explicitly checked for on Linux/aarch64 and does **not** reproduce there:
47 consecutive process exits with real hardware attached, every one exit 0 with
no traceback and no core dump. Production nodes are unaffected.
