---
status: in-progress
sprint: '002'
tickets:
- 001-001
- 001-002
- 001-003
- 001-004
- 002-001
- 002-002
- 002-003
- 002-004
- 002-005
- 002-006
- 002-007
- 002-008
---

# `mbdeploy serve` — a network-facing micro:bit fleet daemon

## Repo and platform

**Implemented entirely in the `mbdeploy` repo**
(`/Volumes/Proj/proj/RobotProjects/mbdeploy`, `Busboombot/mbdeploy`), a
standalone tool with no CLASI process of its own. Nothing in
`radio-robot-lib` changes. All paths below are relative to the `mbdeploy`
checkout.

**The deployment target is a Raspberry Pi (Linux). This is mandatory, not
a nice-to-have** — the entire point is a always-on box with the fleet
plugged into it, so nobody has to run the daemon on their own laptop.
macOS remains supported for development and for the `--remote` client,
but **Linux is the platform `serve` must work on**, and a change that
only works on macOS is not done.

That has a consequence the rest of this issue is shaped around: `mbdeploy`
is currently macOS-only in one specific place, and that has to be fixed
first. See "Prerequisite" below.

## Description

`mbdeploy` only talks to boards plugged into the machine it runs on. A
classroom fleet should live on a Pi with the boards attached, reachable
from any laptop on the LAN.

Add a `serve` subcommand that turns the Pi into a network service. It
watches USB, and for every board that appears it publishes two mDNS
services named after that board's own five-letter name, so `vevov`
becomes reachable from anywhere on the LAN:

- **`_mbserial._tcp`** — a raw byte pipe to the board's serial port,
  equivalent to what `mbdeploy connect` gives you locally. Usable with
  bare `nc`, `socat`, or `telnet`; no handshake.
- **`_mbflash._tcp`** — send a `.hex` file, the daemon flashes the board.

Unplug the board and both advertisements disappear. Plug it into a
different machine running `serve` and they reappear there. No registry
sync, no configuration — the board's name *is* its network address.

## Prerequisite — make the device layer work on Linux

`devices.port_serial_map()` (`devices.py:93`) shells out to macOS `ioreg`
and parses `"USB Serial Number"` / `"IOCalloutDevice"` pairs out of the
output. **On Linux it returns `{}`.** Every downstream consequence is
fatal for a Pi deployment: `probe_all` records `port: null` for every
board, `connect` can find no port, and `deploy`'s `/dev/...` path branch
refuses outright. The agent manual already admits this in §4 ("Target by
enum, name, or UID on other platforms").

**Fix: reimplement `port_serial_map()` on `serial.tools.list_ports`,
which is cross-platform and already a declared dependency.** Verified on
the current fleet — it returns exactly the pyOCD UID as `serial_number`:

```
'/dev/cu.usbmodem2121402' serial_number='9906360200052820b8e12372c44f4f67000000006e052820'
                          vid:pid=0x0D28:0x0204  'BBC micro:bit CMSIS-DAP'
```

So the whole `ioreg` subprocess, both `_IOREG_*` regexes, and the macOS
caveat collapse into a `comports()` loop filtered on VID:PID
`0x0D28:0x0204` (ARM DAPLink). This is strictly better than what it
replaces: it is cross-platform, it needs no subprocess, and the VID:PID
filter makes the existing `known`-set guard against mis-attributing a
non-micro:bit serial port belt-and-braces rather than the only defence.

Keep `port_serial_map`'s signature and its `known` parameter — every
caller (`probe_all`, `_deploy_entry`, `_connect_port`) stays untouched,
and the existing tests in `tests/test_devices.py` pin the contract.

The rest of the device layer is already portable: `flashable_probes()`
and `read_device_id()` go through pyOCD/libusb, and `probe_type()`
through pyserial.

### Pi host setup this implies

- **udev rule for CMSIS-DAP**, or pyOCD needs root:
  `SUBSYSTEM=="usb", ATTR{idVendor}=="0d28", ATTR{idProduct}=="0204", MODE="0666"`
  in `/etc/udev/rules.d/50-cmsis-dap.rules`. pyOCD does not ship this in
  the wheel — check for it and write it as part of the documented setup.
- **The service user must be in `dialout`** to open `/dev/ttyACM*`.
- Ports are `/dev/ttyACM0`, not `/dev/cu.usbmodem*`. Nothing in the code
  assumes the macOS spelling (`resolve_target` only checks `/dev/`), but
  README and agent-manual examples do.

## Decisions taken

Settled with the stakeholder. A planner should treat these as given.

| Question | Decision |
|---|---|
| Service shape | **Two services per board.** Raw serial pipe; separate flash port. Rejected: one service with a mode-selecting handshake. |
| Client | **Explicit `--remote`** on `connect`, `deploy`, `list`. Rejected: silent fallback from local USB to network — the same command must never be able to hit two different boards. |
| Daemon | **Foreground process + service unit templates.** systemd on the Pi supervises it. Rejected: a self-daemonizing `--daemon` with its own pidfile. |
| Access | **Open on the LAN by default**, with `--token` and `--no-flash` to lock down. |
| mDNS backend | **`python-zeroconf`**, a new declared dependency. See below. |

### Why `python-zeroconf`

An earlier draft of this issue chose macOS `dns-sd` subprocesses,
reasoning that registering through the OS responder avoids running a
second mDNS responder on the box. **The Pi requirement reverses that
call.** `dns-sd` does not exist on Linux; its Avahi equivalent
(`avahi-publish`/`avahi-browse`) is a separate `avahi-utils` apt package,
and going that route means writing and testing *two* native backends —
Avahi for the platform that matters and dns-sd for the one that doesn't.

`python-zeroconf` is one implementation that runs identically on the Pi
and on a Mac, installs as a pip dependency with the tool (no apt step, no
"did you remember `avahi-utils`"), and offers a real browse API instead
of parsing CLI output — which also makes `browse()` unit-testable
in-process.

The stated risk, coexisting with `avahi-daemon` on port 5353, is
well-trodden: Home Assistant and ESPHome both run `python-zeroconf`
alongside Avahi on Raspberry Pis as their normal configuration. Keep the
`Advertiser` interface below thin enough that an `avahi-publish` backend
can be dropped in if a specific deployment does conflict.

## Wire protocols

### `_mbserial._tcp` — raw

No handshake. Bytes in go to the board, bytes out come back, until either
side closes. `--token` is the only thing that changes this: when set, the
client must send `AUTH <token>\n` and wait for `OK\n` first. Default is
off, so the pipe stays genuinely raw.

**Exclusive.** One session per board. A second connection gets
`ERR busy\n` and is closed.

### `_mbflash._tcp` — line header, then payload

```
C: FLASH <bytes> [sha256=<hex>] [force-relay]
S: OK send
C: <exactly <bytes> raw bytes of Intel hex>
S: LOG <line>                 (zero or more; pyocd progress)
S: OK flashed                 or   ERR <message>
```

Also served on this port:

```
C: INFO
S: OK {"uid":…,"board_name":"vevov","role":"NEZHA2","port":"/dev/ttyACM0","connected":true}
```

`ERR` cases: `ERR busy`, `ERR relay refused — send force-relay`,
`ERR flash disabled` (`--no-flash`), `ERR sha256 mismatch`,
`ERR short payload`, `ERR auth required`.

### Board exclusivity

One `threading.Lock` per board covers everything that touches it: a
flash, a serial session, and the watcher's SWD/HELLO probing.

A `FLASH` arriving while a serial session is live **wins**: the session's
socket is shut down, then the flash proceeds. Reflashing is the more
intentional act, and pyocd's post-flash reset would corrupt that session
anyway. This is a deliberate choice over `ERR busy` — log it clearly at
the point it happens.

## Proposed fix

### 1. Port `port_serial_map()` off `ioreg`

Per "Prerequisite" above. Do this first and independently: it is a
self-contained change with existing test coverage, and nothing else in
this issue can be validated on the Pi until it lands.

### 2. Extract the flash sequence

The flash → mass-erase-recovery → retry → reset logic is inline in
`_cmd_deploy` at `cli.py:336-377`. Move it verbatim to a new
`src/mbdeploy/flash.py`:

```python
def flash_hex(uid, hex_path, target_mcu=DEFAULT_MCU, log=None) -> int
```

`log` is a callable so the server can turn pyocd's chatter into `LOG`
lines while the CLI keeps printing to stderr. `_cmd_deploy` becomes a
call to it, with no behaviour change. Without this the server grows a
second, divergent copy of the locked-part recovery path.

### 3. `src/mbdeploy/mdns.py`

```python
class Advertiser:
    def register(self, name, service_type, port, txt: dict) -> handle
    def unregister(self, handle) -> None
    def close(self) -> None          # unregister everything

def browse(service_type, timeout=2.0) -> list[dict]   # name, host, port, txt
```

Backed by `python-zeroconf` (`Zeroconf`, `ServiceInfo`,
`ServiceBrowser`). Keep the interface free of any zeroconf type so an
`avahi-publish` backend remains a drop-in.

TXT records carry `uid`, `role`, `common_name`, `enum`, and `port` (the
board's local `/dev/ttyACM*`), so `list --remote` can print the familiar
table without opening a connection.

Note for the implementer: give `ServiceInfo` the Pi's real `.local`
hostname and let zeroconf handle instance-name collisions (two machines
each holding a board that hashed to the same five-letter name) with its
normal `name (2)` rename.

### 4. `src/mbdeploy/server.py`

**`Supervisor`** — the USB watcher.

- Ticks every `--poll-interval` (default 2.0 s), calling
  `devices.flashable_probes()` (`devices.py:56`) and diffing the UID set
  against the previous tick. That call is a cheap libusb enumeration;
  nothing expensive happens on a quiet tick.
- On any change, call `devices.probe_all(config_path)` (`devices.py:324`)
  to refresh the same `config/devices.json` that `list` and `probe` use,
  then read board identity out of the returned entries. Full reuse of the
  existing, tested discovery path — no parallel implementation.
- mDNS instance name for a board: `board_name` (read from silicon over
  SWD, so it works on unflashed and silent boards) → `device_name` (from
  the announcement) → `mb-<last 8 of uid>` if both are unavailable.
- On arrival: bind two listeners on ephemeral ports (`--base-port N` for
  deterministic, firewall-friendly ports instead), register both services.
- On departure: unregister, close listeners, kill live sessions.
- Per-board expensive work uses a **non-blocking** lock acquire — a board
  mid-flash is skipped and picked up on a later tick rather than stalling
  the whole fleet's tick.
- `_tick(probes)` must be callable directly with a supplied probe list.
  That is what makes the watcher testable without sleeping or hardware.

**`Board`** — uid, name, registry entry, the two listener sockets, the
lock, the live session (if any).

**Accept loop** — a single `selectors.DefaultSelector` over all listener
sockets, spawning a thread per accepted connection. One selector rather
than an accept thread per board, so hotplug doesn't churn threads.
Matches the repo's existing plain-threading style (`console.py:101`) —
no asyncio, no `pyserial-asyncio`.

**Session handlers** — `serve_serial(board, conn)` and
`serve_flash(board, conn)`. The serial one reuses `console.open_port()`
(`console.py:45`), which already holds DTR/RTS low so connecting doesn't
reset the board, plus a new `console.relay_socket(ser, conn, stop)` built
on the same two-thread shape as the existing `console.interact()`. The
flash one writes the payload to a temp file and calls `flash.flash_hex()`.

### 5. `serve` subcommand

```
mbdeploy serve [--config PATH] [--poll-interval SEC] [--base-port N]
               [--bind ADDR] [--token SECRET] [--no-flash]
               [--target-mcu MCU] [--service-name NAME]
               [--print-service | --install-service]
```

Foreground, logs to stdout — systemd captures it into the journal.
`SIGINT`/`SIGTERM` → unregister everything, close listeners, exit 0.
`SIGTERM` handling is not optional here: it is how `systemctl stop`
reaches the process, and a daemon that dies without unregistering leaves
stale advertisements until they time out.

`--install-service` writes a **systemd unit** on Linux
(`~/.config/systemd/user/mbdeploy.service`, or `/etc/systemd/system/` with
`--system`), and a launchd plist on macOS, baking in the current CWD and
`--config` path: `serve` is CWD-relative like every other subcommand, and
a service manager gives a process no useful CWD, so `WorkingDirectory`
must be explicit. `--print-service` emits the same file to stdout without
installing.

Templates live in `src/mbdeploy/service/` and must be added to the
`[tool.hatch.build.targets.wheel] artifacts` list in `pyproject.toml`,
the same way `agent_manual.md` is shipped.

### 6. `--remote` on the client side

- `list --remote` — `mdns.browse()` both service types; print the usual
  table with an added HOST column.
- `connect --remote <name> [message…]` — resolve via mDNS, open a TCP
  socket instead of a serial port. Both the interactive and one-shot
  paths in `_cmd_connect` need the socket usable where `ser` is today:
  give it a small `readline`/`write`/`flush`/`close` adapter so
  `console.send_command()` and `console.interact()` (`console.py:71`)
  work unchanged. The adapter is much cheaper than forking those functions.
- `deploy --remote <name>` — resolve; `--build`/`--clean` still run
  locally; then stream the `.hex` over `_mbflash._tcp` and relay `LOG`
  lines to stderr. Exit code mirrors the server's `OK`/`ERR`, preserving
  the 0-is-success contract documented in agent manual §5.

`--remote` is mutually exclusive with a `/dev/…` target — reject it in
argparse.

## Files

**New:** `src/mbdeploy/flash.py`, `src/mbdeploy/mdns.py`,
`src/mbdeploy/server.py`, `src/mbdeploy/service/*.service|*.plist`,
`tests/test_flash.py`, `tests/test_mdns.py`, `tests/test_server.py`,
`tests/test_remote.py`

**Modified:** `src/mbdeploy/devices.py` (port `port_serial_map` off
`ioreg`; drop both `_IOREG_*` regexes), `src/mbdeploy/cli.py` (extract
flash, add `serve`, add `--remote`), `src/mbdeploy/console.py` (add
`relay_socket`), `pyproject.toml` (`zeroconf` dependency, service-template
artifacts, version bump), `README.md`, `src/mbdeploy/agent_manual.md`
(new §9 "Serving a fleet over the network", including Pi setup; the
subcommand tables in manual §2 and in the README both need a `serve` row;
§4's "other platforms" caveat is now false and must go).

## Verification

**Unit — no hardware. Must be run on Linux as well as macOS**, since the
whole point of the prerequisite is portability:

- `test_devices.py` — the existing `port_serial_map` tests must pass
  against the new implementation, plus a new one asserting a
  non-micro:bit VID:PID is filtered out.
- `test_flash.py` — `flash_hex` issues the right pyocd argv, and the
  mass-erase-recovery path fires on first-flash failure (monkeypatch
  `subprocess.run`). The existing deploy tests in `tests/test_devices.py`
  must pass **unchanged** — that is the proof the extraction was
  behaviour-preserving.
- `test_mdns.py` — register/unregister against a fake `Zeroconf`, and
  TXT-record round-tripping (zeroconf TXT values are bytes, names are
  bytes — an easy place to get `str` confusion wrong).
- `test_server.py` — real listeners on `127.0.0.1`, real client sockets,
  a fake serial (reuse `FakeSerial` from `tests/test_connect.py:37`) and
  a stubbed `flash_hex`. Cover: raw pipe in both directions; `ERR busy`
  on a second serial connection; `FLASH` header parse; short payload;
  sha256 mismatch; relay guard (`is_relay`, `devices.py:278`);
  `--no-flash`; `--token` on both services; a flash killing a live serial
  session; `SIGTERM` unregisters everything.
- `test_server.py::Supervisor` — drive `_tick(probes)` directly with
  stubbed probe lists; assert register/unregister calls on a fake
  `Advertiser`, that arrival and removal are idempotent, and that a
  locked board is skipped rather than blocking the tick.
- `test_remote.py` — `--remote` argument shapes, and the socket adapter
  against a loopback server.

**Manual, on the Pi with hardware attached — the acceptance test:**

```bash
# on the Pi, boards plugged in
mbdeploy probe                                       # ports must be /dev/ttyACM*,
                                                     # NOT null — this is the
                                                     # prerequisite's real check
mbdeploy serve --config config/devices.json

# from a laptop on the same LAN
avahi-browse -rt _mbserial._tcp                      # plug/unplug a board on the
                                                     # Pi, watch it appear/vanish
(echo HELLO; cat) | nc vevov.local <port>            # raw pipe answers
mbdeploy list --remote                               # table shows vevov + Pi host
mbdeploy connect --remote vevov "HELLO"              # exit 0, announcement line
mbdeploy deploy --remote vevov --hex MICROBIT.hex    # flashes, exit 0
mbdeploy connect --remote vevov "HELLO"              # still answers after flash
```

Then, still against the Pi: unplug mid-serial-session (client sees a
clean close, advertisement disappears); flash while a serial session is
open (session drops, flash succeeds); two clients race the serial port
(second gets `ERR busy`); `mbdeploy serve --install-service` then
`systemctl --user enable --now mbdeploy`, reboot the Pi, confirm boards
advertise with no login; `journalctl --user -u mbdeploy` shows the log.

The LAN crossing is the point of the feature — running the client on the
Pi itself proves nothing.

## Related

- Board naming, the registry, and the relay guard this issue reuses are
  documented in `mbdeploy`'s own `src/mbdeploy/agent_manual.md` §3 and §4.
- The `DEVICE:` announcement that `probe_type` parses to fill `role` and
  `device_name` — the fields this issue puts into mDNS TXT records — is
  the subject of `hello-banner-emit-the-specified-colon-announcement-format`
  in this repo. A board whose announcement does not parse still advertises
  correctly here, because the mDNS instance name falls back to
  `board_name` read over SWD, but its `role` TXT record will be stale or
  empty and the flash-side relay guard has nothing to read.
