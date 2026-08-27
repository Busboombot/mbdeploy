---
id: '004'
title: 'console.relay_socket: bidirectional serial-socket pump for network sessions'
status: done
use-cases:
- SUC-004
depends-on: []
github-issue: ''
issue: mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# console.relay_socket: bidirectional serial-socket pump for network sessions

## Description

`console.py` already has `interact(ser)` (`console.py:101-134`): a
two-thread pump relaying `ser` to stdout and stdin to `ser`. Sprint 002's
`_mbserial._tcp` service needs the network-facing sibling of exactly
that behavior — relay `ser` to a connected socket and the socket to
`ser` — so `serve_serial` (Ticket 005) gets a byte-for-byte raw pipe with
no new I/O pattern introduced into the codebase, matching
`sprint.md`'s Step 3 module description for `console.py`.

```python
def relay_socket(ser, conn, stop: threading.Event) -> None:
```

Two threads, same shape as `interact`'s reader thread: one reads from
`ser` and writes to `conn`; the other reads from `conn` and writes to
`ser`. Either side closing (socket EOF, serial read error, or `stop`
being set externally — this is exactly the hook `serve_flash`'s
preemption in Ticket 005 calls to tear a session down per Design Problem
1) causes both threads to exit and the function to return. Unlike
`interact`, there's no stdin/stdout, no `EOF_DRAIN` grace period (a
network client's disconnect is unambiguous — no piped-command-race
concern the way stdin EOF has), and it must be preemptible from another
thread via `stop`, not just Ctrl-C.

## Acceptance Criteria

- [x] `relay_socket(ser, conn, stop)` relays bytes from `ser` to `conn`
      and from `conn` to `ser`, concurrently, until either side closes
      or `stop.is_set()`.
- [x] Setting `stop` from another thread causes `relay_socket` to return
      within one `READ_TIMEOUT`-scale interval (matches the existing
      `console.py` per-read polling convention — see `READ_TIMEOUT` at
      `console.py:34`), without needing either `ser` or `conn` to
      produce data first.
- [x] The connected socket closing (client disconnect / EOF) causes
      `relay_socket` to return promptly, without requiring `stop` to be
      set externally.
- [x] A serial read/write error (fake serial raising, simulating a
      physical unplug) causes `relay_socket` to return promptly rather
      than hanging or raising out of the function.
- [x] Both of `relay_socket`'s internal threads are joined (or otherwise
      confirmed stopped) before the function returns — no leaked thread
      after a session ends, so a long-running daemon's thread count
      doesn't grow with connection churn.
- [x] Existing `console.py` behavior (`open_port`, `send_command`,
      `interact`) is completely unchanged; `tests/test_connect.py`
      passes unmodified.

## Implementation Plan

**Approach**: Mirror `interact()`'s `_pump` reader-thread pattern
(`console.py:109-117`) for the `ser`→`conn` direction (read
`ser.in_waiting`-sized chunks, write to `conn`, breaking the loop on any
exception — a closed socket on write raises `OSError`/`BrokenPipeError`,
which the loop should treat as "stop", not propagate). Run the
`conn`→`ser` direction as the "main" loop of `relay_socket` itself
(analogous to `interact`'s stdin-read main loop), but replace stdin's
blocking `readline()` with a short-timeout `conn.recv()` (set
`conn.settimeout(READ_TIMEOUT)` or equivalent) so the loop can also
observe `stop.is_set()` between reads — stdin has no such polling need
in `interact` because Ctrl-C is the only external stop signal there,
but `relay_socket` must be stoppable by another thread (`serve_flash`'s
preemption). On loop exit for any reason, set `stop` (so the reader
thread also unwinds even if the trigger was a socket error rather than
an explicit `stop.set()` from outside), join the reader thread with a
bounded timeout, and return — do not close `ser` or `conn` here; that
remains the caller's responsibility (`serve_serial` owns the socket's
lifecycle, `Board` owns the serial port's), matching `interact()`'s own
convention of not closing `ser`.

**Files to modify**: `src/mbdeploy/console.py` (add `relay_socket`;
no changes to any existing function).

**Files to create**: none (tests can live in the existing
`tests/test_connect.py` or a new `tests/test_console_relay.py` —
implementer's choice, but keep `relay_socket`'s tests separate from the
`connect`-subcommand-specific tests already in `test_connect.py` if a
new file reads more clearly).

**Testing plan**: Reuse `FakeSerial` from `tests/test_connect.py:37`
(scripted `readline`/`write` recording) paired with a real loopback
`socket.socketpair()` (no actual TCP needed — a socket pair is enough to
exercise both read and write paths without a real network listener,
which Ticket 005 will test separately at the `serve_serial` level).
Cover: bytes written by the fake serial arrive on the socket; bytes sent
on the socket arrive at the fake serial's `write`; setting `stop`
externally unblocks and returns; closing the socket half from the "peer"
side causes a prompt return with `stop` observed set on return; a fake
serial that raises from `read`/`write` causes a prompt return rather
than a hang; no thread is left running (`threading.enumerate()` before
and after, or an explicit join with a short timeout that must succeed).

**Documentation updates**: None required by this ticket — `relay_socket`
has no CLI-visible surface of its own; `serve_serial`'s behavior
(Ticket 005) is what the daemon's user-facing docs (deferred to sprint
003 per sprint scope) will eventually describe.
