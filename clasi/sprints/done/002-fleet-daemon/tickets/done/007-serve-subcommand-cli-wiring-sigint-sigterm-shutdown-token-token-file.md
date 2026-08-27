---
id: '007'
title: 'serve subcommand: CLI wiring, SIGINT/SIGTERM shutdown, --token/--token-file'
status: done
use-cases:
- SUC-003
- SUC-004
- SUC-005
- SUC-006
- SUC-008
depends-on:
- '005'
- '006'
github-issue: ''
issue: mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# serve subcommand: CLI wiring, SIGINT/SIGTERM shutdown, --token/--token-file

## Description

Add the `serve` subcommand to `cli.py`'s parser (alongside `build`,
`deploy`, `list`, `probe`, `connect`), wiring together everything the
prior tickets built: `mdns.Advertiser`, `server.Supervisor`, and the
accept loop, into one runnable foreground process.

```
mbdeploy serve [--config PATH] [--poll-interval SEC] [--base-port N]
               [--bind ADDR] [--token SECRET | --token-file PATH]
               [--no-flash] [--target-mcu MCU] [--service-name NAME]
```
(`--print-service`/`--install-service`/`--system`/`--user` are
Ticket 008's flags, added to this same subparser — sequence this ticket
first so Ticket 008 has a working `serve` entry point to extend.)

**Foreground, logs to stdout** — systemd captures it into the journal;
no self-daemonizing, no pidfile (issue's own "Decisions taken" table).

**`--token` / `--token-file`** are mutually exclusive
(`argparse`'s `add_mutually_exclusive_group`). Resolve whichever is
given to a single secret string before constructing the `Supervisor`/
session handlers: `--token` is used verbatim; `--token-file` reads and
strips the file's contents (reject empty). Neither given → no auth
required (today's fully-open default, unchanged). This resolved string
is what Ticket 005's `serve_serial`/`serve_flash` compare via
`hmac.compare_digest` — this ticket does not reimplement that
comparison, it only resolves the secret and passes it down.

**`SIGINT`/`SIGTERM` handling is not optional**: install handlers (or
use a `threading.Event` checked in the main loop, whichever fits the
existing accept-loop shape from Ticket 005 more naturally) that, on
either signal, unregister every mDNS advertisement
(`Advertiser.close()`), close every listener socket, and exit 0. This is
how `systemctl stop` reaches the process — a daemon that dies without
unregistering leaves stale advertisements sitting until they time out on
their own, which is a real, visible bug on a LAN with several boards.

**`--bind ADDR`** (default: all interfaces) is threaded to both the
listener sockets' bind address and `Advertiser(bind_addr=...)`
(Ticket 003), so the advertised address matches what's actually
listening.

## Acceptance Criteria

- [x] `serve` subcommand exists with all listed flags; `--target-mcu`
      defaults matching the existing `_DEFAULT_MCU` convention used by
      `deploy`/`list`/`probe`.
- [x] `--token` and `--token-file` are mutually exclusive at the
      argparse level (passing both is a parse error, not a runtime one).
- [x] `--token-file PATH` reads the file, strips trailing whitespace/
      newline, and uses the result as the token; a missing file or an
      empty-after-strip file is a clear startup error (non-zero exit,
      message to stderr), not a silent "no auth."
- [x] With neither `--token` nor `--token-file`, the resolved token is
      `None` and `serve_serial`/`serve_flash` require no `AUTH` — matches
      today's fully-open default described in the issue.
- [x] `SIGTERM` (simulated in a test — send the signal to the running
      process/thread under test, or call the handler function directly)
      triggers `Advertiser.close()` (verified via a fake `Advertiser`'s
      recorded calls) and every listener socket is closed, before
      process exit; exit code 0.
- [x] `SIGINT` produces the same shutdown behavior as `SIGTERM`.
- [x] `--bind ADDR` is passed through to both the listener sockets' bind
      address and the `Advertiser` constructor — verified by inspecting
      what address a bound listener actually reports
      (`socket.getsockname()`) and what `bind_addr` the fake `Advertiser`
      was constructed with.
- [x] `mbdeploy serve --help` documents every flag with a clear
      one-line description, consistent with the existing subcommands'
      help text style (`cli.py`'s existing `add_argument(..., help=...)`
      calls).
- [x] Full existing CLI test coverage (`tests/test_devices.py`'s CLI-
      adjacent tests, `tests/test_connect.py`) is unaffected — `serve` is
      a new leaf in the subparser tree, no existing subcommand's argparse
      wiring changes.

## Implementation Plan

**Approach**: Add `_cmd_serve(args)` following the existing `_cmd_*`
pattern in `cli.py` (local imports of `mbdeploy.server`/`mbdeploy.mdns`
inside the function, matching how `_cmd_deploy` imports `flash` locally
— keeps CLI startup fast for subcommands that don't need pyocd/zeroconf
at all). Resolve `--token`/`--token-file` to a single string (or `None`)
right at the top of `_cmd_serve`, before constructing anything. Build
the `Advertiser`, the `Supervisor` (passing the resolved token,
`--no-flash`, `--target-mcu`, `--base-port`, `--bind`,
`--poll-interval`, `--service-name`), start the accept loop and the
Supervisor's poll loop (likely each on its own thread, with the main
thread blocking on a `threading.Event` that the signal handlers set),
and register `signal.signal(signal.SIGINT, ...)` /
`signal.signal(signal.SIGTERM, ...)` before entering that wait.

**Files to modify**: `src/mbdeploy/cli.py` (new `_cmd_serve`, new
`serve` subparser registration in `_build_parser`).

**Files to modify (tests)**: `tests/test_server.py` or a new
`tests/test_serve_cli.py` — implementer's choice, but keep
argument-parsing/token-resolution tests separate from the heavier
socket-level tests already in `test_server.py` if a new file reads more
clearly.

**Testing plan**: Argparse-level tests (mutually-exclusive
`--token`/`--token-file`, help text presence, default values) using
`_build_parser()` directly, matching the style of existing CLI arg-shape
tests elsewhere in the suite. `--token-file` resolution tested against a
real temp file (correct content, missing file, empty file). Signal
handling tested by calling the registered handler function directly with
a fake `Advertiser`/fake listener list rather than actually sending a
process signal in the test process (safer and more deterministic than
`os.kill`). `--bind` propagation tested by inspecting a real bound
socket's local address.

**Documentation updates**: Deferred per sprint scope —
`agent_manual.md` §9 and the README/manual subcommand-table `serve` row
land in sprint 003 alongside `--remote` (see `sprint.md` Step 7 Open
Questions). If the stakeholder wants a one-line subcommand-table entry
landed now instead, that is a small addition to this ticket, not a new
one — confirm before adding it.
