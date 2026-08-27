---
id: '005'
title: 'server.py: Board/Session model, accept loop, serve_serial, serve_flash'
status: done
use-cases:
- SUC-004
- SUC-005
- SUC-006
- SUC-008
depends-on:
- '004'
github-issue: ''
issue: mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# server.py: Board/Session model, accept loop, serve_serial, serve_flash

## Description

This is the sprint's central ticket: new `src/mbdeploy/server.py`
providing `Board`, the accept loop, `serve_serial`, and `serve_flash`.
It implements the wire protocol from the source issue's "Wire protocols"
section (`_mbserial._tcp` raw pipe with optional `AUTH`; `_mbflash._tcp`
`FLASH`/`INFO` header lines) **and** Design Problem 1's concurrency
model from `sprint.md`'s Architecture — read that section in full before
starting; the lock/occupant discipline below is not optional detail, it
is the thing that makes `serve_flash` not deadlock against a live
session.

**`Board`** owns one board's live network state: its two listener
sockets, its two mDNS handles (set later by `Supervisor`, Ticket 006 —
`Board` just holds them), a `threading.Lock`, and an `occupant`
reference that is `None`, a `SerialSession`, or a `FlashSession`.

**The lock/occupant discipline** (per `sprint.md` Design Problem 1 —
summarized here, but the sprint doc is the source of truth):
- The lock is held only for the instructions that read or write
  `Board.occupant` — never for a session's or a flash's duration.
- `serve_serial`: acquire lock; if `occupant is not None`, release and
  reply `ERR busy`; a serial session **never** preempts anything.
  Otherwise install `self` as `occupant`, release, then run
  `console.relay_socket(ser, conn, stop)` (Ticket 004) with the lock
  held by no one. On exit (any reason), acquire lock, clear `occupant`
  only if it is still `self`, release.
- `serve_flash`: acquire lock; if `occupant` is a `FlashSession`,
  release and reply `ERR busy` (two flashes never race). If `occupant`
  is a `SerialSession`, atomically swap in a new `FlashSession` as
  `occupant` (so a concurrent second `FLASH` sees the `FlashSession` and
  also gets `ERR busy`), release holding a reference to the *old*
  session, then — **outside the lock** — call that session's
  `terminate()` (set its stop event; shut down and close its socket) and
  `join()` its thread with a bounded timeout (recommend 2s; log a
  warning if it doesn't join in time, but proceed anyway rather than
  hanging the flash indefinitely). Only after that does the flash
  proceed. If `occupant` was `None`, just install the new `FlashSession`
  and proceed directly. On completion (success or error), acquire lock,
  clear `occupant` if still `self`, release.

**`serve_flash` wire protocol** (issue's exact grammar):
```
C: FLASH <bytes> [sha256=<hex>] [force-relay]
S: OK send
C: <exactly <bytes> raw bytes of Intel hex>
S: LOG <line>                 (zero or more; pyocd progress)
S: OK flashed                 or   ERR <message>
C: INFO
S: OK {"uid":…,"board_name":…,"role":…,"port":…,"connected":true}
```
Error cases: `ERR busy`, `ERR relay refused — send force-relay` (checked
via `devices.is_relay(board.entry.get("role"))`, unless `force-relay` is
present in the header), `ERR flash disabled` (when `--no-flash` is
active — checked before anything else, `flash_hex` must never be called
in this case), `ERR sha256 mismatch` (only checked if a `sha256=` was
given in the header — compute over the received payload before writing
it to a temp file), `ERR short payload` (fewer than `<bytes>` bytes
arrived before the client closed or a read timeout elapsed), `ERR auth
required` (see below). On success, write the payload to a temp file and
call `flash.flash_hex(uid, tmp_path, target_mcu, log=lambda line: send
"LOG " + line)` — this is the exact seam sprint 001 built for this.

**Token auth** (both services, when a token is configured — the token
string itself is resolved by Ticket 007's CLI layer and passed in): the
client must send `AUTH <token>\n` and wait for `OK\n` before anything
else is accepted; compare with `hmac.compare_digest`, never `==`. Missing
or wrong token → `ERR auth required`, then close. When no token is
configured, the pipe/protocol behaves exactly as if `AUTH` were never
required (default off, so `_mbserial._tcp` stays genuinely raw per the
issue).

**Accept loop**: one `selectors.DefaultSelector` registered with every
board's listener sockets (both raw and flash, across every `Board`);
`select()` in a loop, and for each ready listener, `accept()` and spawn
one new thread per accepted connection running the appropriate handler.
One selector, not one thread per listening socket — hotplug must not
churn threads. `Board`'s listener sockets are added to the selector on
arrival and removed on departure by `Supervisor` (Ticket 006); this
ticket provides the loop mechanism and the two per-connection handlers,
not the add/remove-on-hotplug logic itself.

## Acceptance Criteria

- [x] `Board` exposes `occupant`/`lock` semantics exactly as described
      above; nothing outside `Board` reads or writes `Board.occupant`
      directly without going through the documented claim/preempt/release
      sequence (verified by code review, not just tests — this is a
      design invariant, not just a behavior).
- [x] Raw pipe (`_mbserial._tcp`, no token configured): bytes written by
      a connected client arrive at the (fake) serial port; bytes from
      the fake serial port arrive at the client. Both directions tested
      against a real loopback TCP connection, per `sprint.md` SUC-004.
- [x] Second connection to an already-occupied `_mbserial._tcp` gets
      exactly `ERR busy\n` and is closed; the first session's traffic is
      unaffected (SUC-005).
- [x] `FLASH` against a board with a live serial session: the serial
      session's socket observably closes on the client side, the flash
      proceeds using a stubbed `flash_hex`, and `OK flashed` is
      returned — with a bounded test timeout proving this isn't
      deadlocking on the lock (SUC-006, and this is the test that
      exercises Design Problem 1's fix directly).
- [x] A second, concurrent `FLASH` while a flash is already in progress
      on the same board gets `ERR busy`.
- [x] `FLASH` header parsing: valid header with byte count only; with
      `sha256=`; with `force-relay`; malformed header (missing byte
      count) rejected with an `ERR` before any payload is read.
- [x] Short payload (client sends fewer bytes than declared, then closes
      or times out) → `ERR short payload`, `flash_hex` never called.
- [x] `sha256=` mismatch → `ERR sha256 mismatch`, `flash_hex` never
      called; payload matching the declared hash proceeds.
- [x] Relay guard: `is_relay(role)` True and no `force-relay` → `ERR
      relay refused — send force-relay`, `flash_hex` never called; with
      `force-relay` present, the flash proceeds despite `is_relay`.
- [x] `--no-flash` (a flag threaded into `serve_flash`, wired by Ticket
      007): every `FLASH` gets `ERR flash disabled`; `flash_hex` is
      never called, and this check happens before relay-guard/sha256/
      payload-read logic, not after.
- [x] `INFO` returns the documented JSON shape on both a flash-service
      connection and independent of any in-progress session state.
- [x] Token auth: with a token configured, `AUTH <correct-token>\n` →
      `OK\n`, then normal protocol proceeds; `AUTH <wrong-token>\n` or no
      `AUTH` at all before another command → `ERR auth required`, then
      the connection is closed; verified on **both** `_mbserial._tcp`
      and `_mbflash._tcp`. Comparison uses `hmac.compare_digest`
      (verify by code inspection, not just behavior, since a timing
      side-channel wouldn't show up in a functional test).
- [x] The accept loop uses one `selectors.DefaultSelector` shared across
      all registered listener sockets; accepting a connection spawns
      exactly one new thread for that connection (verify via
      `threading.enumerate()` count deltas in a test, not just that
      the behavior works).
- [x] Every existing test suite (`test_devices.py`, `test_connect.py`,
      `test_flash.py`) is unaffected — this ticket adds a new module and
      does not modify any of sprint 001's code.

## Implementation Plan

**Approach**: Build `Board`/`SerialSession`/`FlashSession` first
(simple dataclasses/small classes — no I/O in their own definitions,
just state + a `terminate()` method on the session types that sets a
`threading.Event` and shuts down/closes the owned socket), with unit
tests for the claim/preempt/release state machine in isolation *before*
wiring in real sockets — this is the highest-risk piece in the sprint
and deserves its own narrow tests separate from the full
socket-plus-serial integration tests. Then build `serve_serial` and
`serve_flash` as functions taking `(board, conn, *, token=None,
no_flash=False, target_mcu=...)`, and the accept loop as a small
class or function wrapping `selectors.DefaultSelector`. Reuse
`console.open_port` (`console.py:45`) for opening the serial port inside
`serve_serial` — do not reimplement port-opening.

**Files to create**: `src/mbdeploy/server.py` (this ticket's portion:
`Board`, `SerialSession`, `FlashSession`, the accept-loop helper,
`serve_serial`, `serve_flash` — `Supervisor` is Ticket 006, added to the
same file), `tests/test_server.py`.

**Testing plan**: `tests/test_server.py`, real listeners on `127.0.0.1`,
real client sockets, `FakeSerial` (reuse from `tests/test_connect.py:37`,
extend if it needs a way to simulate a mid-session read error for the
"serial error tears down session" case), and a stubbed `flash_hex` (a
function recording its call args and returning a controllable exit
code, with a way to simulate calling its own `log` callback with a few
lines). Cover every item in Acceptance Criteria above. Structure tests
so the lock/occupant state machine has its own fast, hardware-free unit
tests (constructing `Board` directly, calling the claim/preempt
sequence without real sockets) in addition to the full end-to-end
socket tests — a regression in the state machine should fail a test in
milliseconds, not only show up as a flaky timing-dependent integration
failure.

**Documentation updates**: None in this ticket — the `serve` subcommand
itself (Ticket 007) is the user-facing surface; deferred docs
(`agent_manual.md` §9) are out of scope per sprint.md.
