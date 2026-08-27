---
id: '004'
title: 'cli.py: --remote on connect (network serial session, both paths)'
status: in-progress
use-cases:
- SUC-011
- SUC-013
depends-on:
- '002'
github-issue: ''
issue: mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# cli.py: --remote on connect (network serial session, both paths)

## Description

Add `--remote` (`store_true`) to `connect`'s subparser. In `_cmd_connect`:

- If `args.remote` and `args.target` starts with `/dev/` (or contains
  `/`, matching `_connect_port`'s own existing test), reject before any
  mDNS lookup or socket I/O: print `Error: --remote cannot be combined
  with a device path ('{target}').` to stderr, return 1. This is the
  first check performed — sprint.md Architecture Step 3 settles that
  argparse's declarative `mutually_exclusive_group` cannot express this
  (it tests presence, not a sibling positional's value), so the check
  is a plain early-return in the handler, matching this file's existing
  style (`_deploy_entry`, `_connect_port`).
- If `args.remote` (and the path above didn't reject), call
  `remote.resolve_board(args.target, "_mbserial._tcp.local.")`, open a
  TCP socket to the resolved `{host, port}`, wrap it in
  `remote.SocketSerial`, and pass that to `console.send_command`/
  `console.interact` exactly as the local path passes `ser` — **no
  branching inside `console.py`**, only in `_cmd_connect`'s own
  port-vs-socket setup.
- A `resolve_board` `ValueError` (board not found, or ambiguous) is
  caught and printed the same way `console.ConsoleError` already is:
  `Error: {exc}`, return 1.

## Acceptance Criteria

- [x] `connect --remote /dev/ttyACM0` is rejected before any network
      call — assert via a monkeypatch that `mdns.browse` is never
      invoked.
- [x] `connect --remote <name>` with no message runs the interactive
      path (`console.interact`) against a `SocketSerial`-wrapped socket;
      Ctrl-D/EOF on stdin ends the session the same way it does locally.
- [x] `connect --remote <name> "HELLO"` runs the one-shot path
      (`console.send_command`) against the same adapter and prints the
      reply lines to stdout, status to stderr — matching local
      `connect`'s existing stdout/stderr split.
- [x] A `resolve_board` failure (board not found / ambiguous) surfaces
      as `Error: ...` on stderr with exit 1, never a raw traceback.
- [x] Local `connect <target> [message]` (no `--remote`) is unaffected
      — same code path, same output, as before this ticket.
- [ ] On real Nolanet hardware (validated in ticket 009): against a
      silent board, `connect --remote togov "HELLO"` gets no reply and
      exits 1 — this is documented in the ticket/manual as correct
      behavior for a silent board, not treated as a bug found here.

## Testing

- **Existing tests to run**: `tests/test_connect.py` must pass
  unchanged.
- **New tests to write**: `tests/test_remote.py` — the `/dev/` +
  `--remote` rejection (no network touched); `_cmd_connect`'s
  interactive path against a real loopback socket server standing in
  for `serve_serial` (scripted stdin, asserting `in_waiting`-driven
  reads work — this is the interactive-path test ticket 002's adapter
  work made possible, exercised here through the actual CLI handler);
  the one-shot path against the same kind of server; a `resolve_board`
  failure surfacing as `Error: ...`/exit 1.
- **Verification command**: `uv run pytest tests/test_remote.py
  tests/test_connect.py`

## Implementation Notes

- `--baud` is documented (its own `--help` text) as ignored under
  `--remote`, not rejected — the daemon already owns the local port at
  whatever baud `serve` was started with, so `--remote --baud N` is a
  harmless no-op rather than an error a script combining flags would
  have to special-case.
- Client-side `--token`/`AUTH` support is **not** in this ticket's
  scope — the ticket's own Description/Acceptance Criteria never
  mention it, unlike ticket 005's `deploy --remote`. Not implemented
  here.
- `serve_serial`'s one pre-relay message (`ERR busy`, sent synchronously
  on accept when a second client races an already-claimed board, then
  the connection is closed) is peeked for with `socket.MSG_PEEK` in
  `cli._peek_remote_err` right after connecting, before the socket is
  handed to `console.py` — so it surfaces as a clean `Error: ... busy`
  and exit 1 instead of flowing into `send_command`/`interact` as
  ordinary board bytes. `MSG_PEEK` means a false alarm (a real board
  whose first output doesn't start with `ERR `) leaves the bytes
  exactly where `console.py`'s own reads will find them — no data is
  ever discarded. This lives entirely in `cli.py`
  (`_peek_remote_err`/`_REMOTE_ERR_PEEK_TIMEOUT`), keeping `console.py`
  and `remote.py` exactly as sprint.md's Step 3 specified.
