---
id: '002'
title: 'remote.py: SocketSerial adapter and resolve_board() mDNS resolution'
status: open
use-cases: [SUC-011]
depends-on: []
github-issue: ''
issue: mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# remote.py: SocketSerial adapter and resolve_board() mDNS resolution

## Description

Foundation ticket for the whole client side (sprint.md Architecture
Step 3). Create `src/mbdeploy/remote.py` with:

- `SocketSerial` — an adapter around a connected `socket.socket` that
  exposes exactly the **six** members `console.py` actually calls, not
  the four the source issue lists: `reset_input_buffer()` (used by
  `console.send_command`), `write(data)`, `flush()`, `readline()`
  (used by both `send_command` and, indirectly, nothing else),
  `read(size)`, and the `in_waiting` property (used by
  `console.interact`'s `ser.read(max(1, ser.in_waiting))`). Missing
  `in_waiting` breaks only the interactive path — a one-shot-only test
  suite would not catch it, so both paths must be tested here (see
  Testing).
- `resolve_board(name, service_type, timeout=2.0) -> dict` — one
  `mdns.browse(service_type, timeout)` call, recovering each result's
  short board name by stripping the trailing `.{service_type}` from
  `browse()`'s own `name` field (TXT records do not carry the short
  name — confirmed by reading `server.py::_service_txt`, which only
  sets `uid`/`role`/`common_name`/`enum`/`port`). Raises `ValueError`
  with a clear message on zero matches ("no board named '{name}' found
  advertising {service_type}") or 2+ matches (lists the ambiguous
  hosts) — never silently picks one, matching the issue's own
  "must never hit two different boards" guarantee.

`console.py` itself must not change — that is the point of the
adapter. Do not fork or wrap `send_command`/`interact`.

## Acceptance Criteria

- [ ] `SocketSerial` implements `reset_input_buffer`, `write`, `flush`,
      `readline`, `read`, and `in_waiting` against a real (loopback)
      `socket.socket`.
- [ ] `console.send_command(SocketSerial(...), ...)` works unchanged
      against a real loopback server that behaves like `serve_serial`.
- [ ] `console.interact(SocketSerial(...))` works unchanged against the
      same kind of loopback server, exercising the `in_waiting`-driven
      read path specifically (a test that only calls `readline` would
      not catch a missing/broken `in_waiting`).
- [ ] `resolve_board` returns the single matching `{name, host, port,
      txt}`-shaped dict when exactly one board matches.
- [ ] `resolve_board` raises `ValueError` with a clear message on zero
      matches.
- [ ] `resolve_board` raises `ValueError` listing the candidates on 2+
      matches — it never silently returns the first one.
- [ ] `console.py` has zero lines changed by this ticket.

## Testing

- **Existing tests to run**: `tests/test_connect.py`, `tests/test_mdns.py`
  (both must pass unchanged — this ticket touches neither `console.py`
  nor `mdns.py`).
- **New tests to write**: `tests/test_remote.py` —
  - a real loopback TCP server thread standing in for `serve_serial`
    (raw byte echo/scripted replies), driving `SocketSerial` through
    both `console.send_command` (one-shot) and `console.interact`
    (interactive, with scripted stdin) in both directions;
  - `resolve_board` against a monkeypatched `mdns.browse` returning 0,
    1, and 2+ matching entries (varying `name` so the short-name-strip
    logic is exercised, not just the count).
- **Verification command**: `uv run pytest tests/test_remote.py
  tests/test_connect.py tests/test_mdns.py`
