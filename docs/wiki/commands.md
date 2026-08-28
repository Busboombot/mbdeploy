---
title: Command reference
blurb: Every subcommand and flag, the exit-code contract, and the stdout/stderr split.
order: 30
updated: 2026-08-27
tags: [cli, reference, exit-codes]
---

# Command reference

```
mbdeploy [--version] [--agent] <subcommand> [options]
```

`mbdeploy --help` and `mbdeploy <subcommand> --help` are always authoritative;
this page is the same surface written out with the reasoning attached.

## Top-level flags

| Flag | Effect |
|---|---|
| `--version` | Print the installed version and exit. |
| `--agent` | Print the bundled agent manual to stdout and exit. |
| `-h`, `--help` | Print usage and exit. |

`--version` and `--agent` are handled before a subcommand is required, so both
work on their own.

## Conventions that apply everywhere

- **Exit code `0` means success.** Check it; never infer success from stdout
  text. Full contract [below](#exit-codes).
- **stdout carries data, stderr carries status.** See
  [Output streams](#output-streams).
- **`--config PATH`** overrides the registry location on every subcommand that
  reads or writes it: `deploy`, `list`, `probe`, `connect`, and `serve`.
- **Options may appear anywhere**, including between two positional arguments.
  `mbdeploy connect tovez --baud 9600 "HELLO"` parses the way it reads;
  plain `argparse` cannot do this, so every subparser opts into intermixed
  parsing explicitly.
- **All project paths are relative to the current working directory.**

---

## `build`

Compile the firmware by shelling out to the project's build script.

| Flag | Effect |
|---|---|
| `--clean` | Clean before building (passes `--clean` to the build script). |
| `--verbose` | Show full build output (passes `--verbose`). |
| `-j N` | Build with N parallel jobs (passes `-j N`). |
| `--build-cmd CMD` | Replace the whole build command. Split on whitespace. |

Without `--build-cmd`, the command is `<python> build.py` in the current
directory. If `build.py` is not there and no `--build-cmd` was given, `build`
prints an error and exits 1 rather than guessing. The build subprocess's exit
code is `build`'s exit code.

---

## `deploy`

Flash firmware to one board.

```
mbdeploy deploy [target] [options]
```

| Flag | Effect |
|---|---|
| `--build` | Build before flashing. |
| `--clean` | Clean before building; implies `--build`. |
| `-j N` | Parallel jobs for that build. |
| `--force-relay` | Allow flashing a board whose `role` marks it a relay. |
| `--hex PATH` | Flash this hex instead of `./MICROBIT.hex`. |
| `--target-mcu MCU` | Target MCU for pyOCD (default `nrf52833`). |
| `--config PATH` | Registry location. |
| `--remote` | Flash a board advertising on the LAN instead of a local one. See [The remote client](/subsystems/mbdeploy/remote-client/). |

**`deploy` has no `--verbose`.** Only `build` does; `deploy --verbose` is a parse
error. `--build-cmd` is likewise `build`-only — a `deploy --build` always runs
the default build command.

`target` is optional; omitted, it auto-picks the unique non-relay registry entry
and errors if that is ambiguous. Targeting rules, and why a `/dev/…` path is
handled the way it is, are on [The device model](/subsystems/mbdeploy/device-model/).
What actually gets run against the board is on
[Building and flashing](/subsystems/mbdeploy/flashing/).

```bash
mbdeploy deploy                     # the only robot on the bench
mbdeploy deploy 2                   # by enum
mbdeploy deploy gopiv               # by five-letter name
mbdeploy deploy /dev/ttyACM0        # whichever board is on that port right now
mbdeploy deploy --build             # build first, then flash
mbdeploy deploy gopiv --clean       # clean build, then flash
mbdeploy deploy 2 --hex build/MICROBIT.hex --target-mcu nrf52833
mbdeploy deploy bridge1 --force-relay
```

---

## `list`

Print every board the registry knows, plus anything newly attached.

| Flag | Effect |
|---|---|
| `--config PATH` | Registry location. |
| `--fast` | Skip the live SWD name read for connected boards that have no recorded name. |
| `--target-mcu MCU` | MCU used for that name read (default `nrf52833`). |
| `--remote` | List boards advertising on the LAN instead. `--fast` and `--target-mcu` are then ignored — no SWD read happens remotely. |

`list` merges the saved registry with the current live probe list. It reads a
name over SWD only for a board that is **connected and has no name recorded at
all** (neither `device_name` nor `board_name`) — an entry that already has one
is never re-read. `--fast` skips even those reads, at the cost of showing a
blank name for a board nothing has ever recorded.

With no boards known and none attached, `list` prints `no devices found` and
exits 0.

## `probe`

Actively probe every connected board and update the registry.

| Flag | Effect |
|---|---|
| `--config PATH` | Registry location. |
| `--target-mcu MCU` | MCU used for the SWD name read (default `nrf52833`). |
| `--clear` | Empty the registry first, keeping only currently connected boards. |

`probe` refreshes each connected board's port, opens that port and sends `HELLO`
to capture an announcement, and reads the five-letter name over SWD for any
board that has none recorded. It then saves the registry and prints the same
table `list` does. Run it whenever ports may have changed — after replugging, or
before first use of a board `mbdeploy` has never seen.

`probe --clear` is the only operation that removes registry entries.

### The table

`list` and `probe` print the same table:

```
ENUM  CONN  DEVICE NAME  COMMON NAME  ROLE          PORT                     UID
1     yes   gopiv        robot        NEZHA2        /dev/cu.usbmodem2121102  9906360200052820…
2     no    tovez                                                            9906360200052820…
```

- `CONN` is `yes` when the board is attached right now, `no` when it is only
  remembered from an earlier probe. Connected boards sort first, then by enum,
  then by UID; a board with no enum sorts last.
- `PORT` is blank for a disconnected board — its remembered port no longer
  refers to anything.
- `DEVICE NAME` is the board's own five-letter name.
- `COMMON NAME` is a human label, shown so you can find the board on a desk. It
  is never a target.

---

## `connect`

Open a serial session with a board, or send it one line and print the reply.

```
mbdeploy connect <target> [word ...] [options]
```

| Flag | Effect |
|---|---|
| `--baud N` | Baud rate (default 115200). Ignored under `--remote`. |
| `--timeout SEC` | Budget for the whole exchange (default 2). |
| `--config PATH` | Registry location. |
| `--remote` | Talk to a board advertising on the LAN instead. |

`target` is **required** — there is no auto-pick. Everything after it is joined
with spaces and sent as a single newline-terminated line, so quoting is
optional:

```bash
mbdeploy connect tovez "HELLO"
mbdeploy connect tovez SET SPEED 50      # sends "SET SPEED 50\n"
mbdeploy connect tovez --baud 9600 "HELLO"
mbdeploy connect tovez "HELLO" --timeout 5
mbdeploy connect tovez                   # interactive; Ctrl-D or Ctrl-C to exit
```

Opening the port holds DTR and RTS low, so connecting does **not** reset the
board; whatever it was doing keeps running.

### How the reply is bounded

`--timeout` is the budget for the *whole* exchange, not a per-read timeout:

- The board has that long to say anything at all.
- Once it has answered and then stayed quiet for about 0.4 s, the read stops
  early — so a multi-line reply comes back whole without waiting out the
  timeout.
- A board that streams continuously (telemetry, an `ack` loop) is cut off at
  `--timeout` rather than hanging the command.

Raise `--timeout` for a board that is slow to answer; the same flag caps how
much of a chatty board's stream you capture.

### One port, one owner

A serial port cannot be shared. `connect` fails if a monitor, an editor terminal,
or another `connect` already holds the port — and while `connect` is running,
`probe` cannot read that board's announcement.

---

## `serve`

Run the fleet daemon. Fully documented on
[The fleet daemon](/subsystems/mbdeploy/fleet-daemon/); the flags are:

| Flag | Effect |
|---|---|
| `--config PATH` | Registry location. |
| `--poll-interval SEC` | Seconds between USB polls (default 2). |
| `--base-port N` | First of a sequential TCP port pair handed to each board (default 0 — OS-assigned ephemeral ports). |
| `--bind ADDR` | Address to bind listeners to and advertise (default: all interfaces). |
| `--token SECRET` | Shared secret clients must send as `AUTH <token>`. Mutually exclusive with `--token-file`. |
| `--token-file PATH` | Read that secret from a file instead, so it never appears in a process listing or in `systemctl cat`. |
| `--no-flash` | Reject every `FLASH` request with `ERR flash disabled`. |
| `--target-mcu MCU` | Target MCU for flashes (default `nrf52833`). |
| `--service-name NAME` | Override the mDNS instance name for every board this process manages. Only meaningful on a single-board host. |
| `--print-service` | Render the systemd unit for this exact invocation to stdout and exit, touching nothing. |
| `--install-service` | Write that unit to disk and exit without running the daemon. |
| `--system` / `--user` | Which scope `--print-service`/`--install-service` targets. `--system` is the default. |

`serve` has no `--baud`: the daemon always opens a board's port at 115200.

---

## Exit codes

**`0` = success, non-zero = failure.** Always check the exit code rather than
scraping stdout.

| Command | Exit code |
|---|---|
| `build` | The build subprocess's exit code; 1 if `build.py` is missing and no `--build-cmd` was given. |
| `deploy` | `0` on success — specifically, the exit code of the final `pyocd reset`. See below for the failure cases. |
| `list`, `probe` | `0`, including when there are no devices at all. |
| `connect` | `0` when the board answered, or when an interactive session ends. `1` on the failures below. |
| `serve` | `0` after a clean SIGINT/SIGTERM shutdown; `1` on a startup error such as an unreadable or empty `--token-file`, or a failed `--install-service` write. |

`deploy` exits non-zero when:

- the target token matched no registry entry;
- a `/dev/…` path could not be resolved live — nothing on the path, no live map,
  or the board on it is not in the registry;
- auto-pick found zero or more than one non-relay entry;
- the resolved board is a relay and `--force-relay` was not given;
- the resolved board is not currently connected;
- the build step failed — its exit code is propagated;
- `pyocd flash` failed *and* the automatic mass-erase recovery also failed — the
  erase's or the retried flash's exit code is propagated;
- `pyocd reset` returned non-zero.

`connect` exits non-zero when:

- the target matched no registry entry, the board is not connected, or it has no
  serial port;
- the port could not be opened — wrong path, or another program holds it;
- a message was sent and **nothing came back** within `--timeout`.

That last one is worth internalising: a board running no announcing firmware
answers nothing, so `connect <board> "HELLO"` exits 1 against it. That is correct
behaviour, not a fault. See
[Troubleshooting](/subsystems/mbdeploy/troubleshooting/).

## Output streams

- **stdout** carries data: the device table, the board's serial reply, the agent
  manual, the version string, `no devices found`.
- **stderr** carries status: every `Error: …` line, the interactive-session
  banner, the mass-erase notice, and relayed flash progress.

For `connect` the split is a contract, not a convention: the board's reply is the
*only* thing on stdout, so

```bash
mbdeploy connect tovez STATUS | grep -q OK
```

works without the banner contaminating the data.

## Notes for scripting

- **Probe before deploying** to any board `mbdeploy` has not seen. The registry
  entry is what supplies `role`, and without one `deploy` refuses a path target
  rather than flash a board it cannot relay-check.
- **Prefer an enum or the five-letter name over a port path.** Ports shift
  across reconnects; enums and names do not. A path is resolved live, so it is
  never *wrong*, but it may name a different board than it did an hour ago.
- **Use `connect`, not `probe`, to check that a board is alive.** `probe`
  rewrites the registry; `connect` only reads. After a deploy,
  `mbdeploy connect <target> HELLO` and check the exit code.
- **Never reflash a relay implicitly.** If you mean to, say `--force-relay`.
