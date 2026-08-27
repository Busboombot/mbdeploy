---
id: '006'
title: 'server.py: Supervisor USB watcher (_tick, arrival/departure, mDNS lifecycle)'
status: in-progress
use-cases:
- SUC-003
depends-on:
- '002'
- '003'
- '005'
github-issue: ''
issue: mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# server.py: Supervisor USB watcher (_tick, arrival/departure, mDNS lifecycle)

## Description

`Supervisor`, added to `src/mbdeploy/server.py` alongside Ticket 005's
`Board`/session code. Purpose (per `sprint.md` Step 3): keeps the live
`Board` set in sync with which boards are physically connected. This is
where Design Problem 2 (scoped hotplug probing, Ticket 002) is put to
use, and where Design Problem 1's lock-free departure detection (see
`sprint.md`'s Architecture) is implemented.

**Polling**: on a loop at `--poll-interval` (default 2.0s, threaded
through from Ticket 007's CLI), call `devices.flashable_probes()` — a
cheap libusb enumeration — and diff the returned UID set against the
previous tick's. `_tick(probes)` must be **directly callable** with a
supplied probe list, independent of the loop/sleep — this is what makes
the watcher testable without hardware or timing.

**On arrival** (a UID appears that wasn't in the previous tick):
1. Call `devices.probe_all(config_path, only_uids={the arrived UID(s)})`
   (Ticket 002's parameter) — refreshes exactly this board's registry
   entry (port, announcement, board name), touching no other board.
2. Determine the mDNS instance name: `board_name` (read over SWD, works
   on unflashed/silent boards) → `device_name` (from the announcement) →
   `mb-<last 8 of uid>` if both are unavailable. This order matters:
   silent Nolanet boards (verified: all four currently announce
   nothing) still get a usable name from the SWD read.
3. Bind two listener sockets (raw + flash) on ports allocated from
   `--base-port` (see Open Question in `sprint.md` — this ticket
   implements sequential allocation from `--base-port` with a free-list
   reclaiming a departed board's ports for reuse; when `--base-port` is
   0/unset, bind on OS-assigned ephemeral ports instead).
4. Register both mDNS services via the `Advertiser` (Ticket 003),
   storing the returned handles on the `Board`.
5. Add both listener sockets to the shared accept-loop selector
   (Ticket 005).

**On departure** (a previously-seen UID disappears): acquire the
board's lock just long enough to grab any live `occupant` reference,
release, and — outside the lock — call that session's `terminate()` (the
same pattern `serve_flash`'s preemption uses in Ticket 005; do not
duplicate the logic, factor it into a shared helper both call). Then
unregister both mDNS handles, remove and close both listener sockets
from the selector, and reclaim the board's two ports into the
`--base-port` free-list.

**Lock-free detection, non-blocking identity refresh**: `_tick`'s
arrival/departure diff must never acquire any `Board.lock` — it works
purely off the UID set from `flashable_probes()`, per `sprint.md` Design
Problem 1. A board mid-flash or mid-session must still be detected as
departed the instant it disappears from that UID set. The **only** place
this ticket uses a non-blocking lock acquire is around the optional
identity-refresh step for an already-known, still-connected board (if
this sprint's design calls for periodic re-probing beyond arrival — if
not needed, state explicitly in the implementation that only arrival
triggers a probe, and skip this non-blocking-acquire step entirely
rather than adding speculative unused code).

## Acceptance Criteria

- [x] `Supervisor._tick(probes)` is callable directly with a plain list
      of probe dicts (`[{"uid": ...}, ...]`), with no sleep and no real
      hardware involved.
- [x] Arrival: a UID appearing in `_tick`'s probe list that wasn't
      present before triggers exactly one `probe_all(only_uids={uid})`
      call, one pair of `Advertiser.register` calls (raw + flash), and
      binds two listener sockets — verified against fakes/mocks for
      `devices.probe_all` and `Advertiser`.
- [x] Departure: a UID present in the previous tick but absent from the
      current one triggers exactly one pair of `Advertiser.unregister`
      calls and closes both listener sockets — even when that board's
      `occupant` is a live (faked) session; the departure path does not
      skip a board because it's "locked" (this is the direct regression
      test for the Supervisor half of Design Problem 1).
- [x] Repeating the exact same tick twice (same UID set both times) is
      idempotent: no double-register, no double-unregister, no error.
- [x] A board's own arrival probe never touches another, already-known,
      still-connected board's registry entry (regression coverage
      overlapping Ticket 002's own tests, exercised here at the
      `Supervisor` level with two simultaneously-connected fake boards).
- [x] mDNS instance naming follows the documented fallback order
      (`board_name` → `device_name` → `mb-<uid8>`), tested with fakes
      that supply each combination (both present, only one, neither).
- [x] Port allocation from `--base-port`: two sequential ports per
      arriving board; a departed board's ports are available for reuse
      by a later arrival (free-list behavior) rather than growing
      unboundedly across repeated hotplug cycles in a single test run.
- [x] `--base-port` unset/0: listeners bind on OS-assigned ephemeral
      ports without error.
- [x] Full `test_server.py` suite (Ticket 005's tests plus this
      ticket's) passes together — `Supervisor` and the session/accept-loop
      code from Ticket 005 are exercised as one integrated module.

## Implementation Plan

**Approach**: `Supervisor.__init__` takes the `Advertiser`, the accept
loop's selector (or owns/creates it and exposes it — coordinate with how
Ticket 005 structured the accept loop so there is exactly one selector
shared by everything, not one per ticket's code), `config_path`,
`base_port`, and any other CLI-sourced settings threaded through by
Ticket 007. `_tick(probes)` computes `current = {p["uid"] for p in
probes}`, diffs against `self._known` (the previous tick's set), calls
`_on_arrival(uid)`/`_on_departure(uid)` for each delta, then sets
`self._known = current`. Factor the "grab occupant, terminate outside
lock" sequence into a small shared function/method both `_on_departure`
here and `serve_flash`'s preemption (Ticket 005) call, rather than
duplicating the pattern — this is exactly the kind of near-duplicate
logic the architecture's Design Quality review should catch if it
diverges.

**Files to modify**: `src/mbdeploy/server.py` (add `Supervisor` to the
module Ticket 005 created).

**Files to modify (tests)**: `tests/test_server.py` (add a
`TestSupervisor`-style section; extend fakes for `devices.probe_all` and
`mdns.Advertiser` as needed — these should be lightweight fakes local to
the test module, not real zeroconf or real hardware).

**Testing plan**: Drive `_tick` directly with hand-built probe-list
sequences (empty → one board → two boards → back to one → empty) and
assert the fake `Advertiser`/fake `probe_all`/selector-registration call
sequence at each step. Separately, a smaller set of tests targeting just
port allocation/reclamation logic in isolation (no need to go through
`_tick` for every case). One test specifically constructs a `Board` with
a live fake `occupant` session, then drives a departure tick, and
asserts the occupant's `terminate()` was called and the tick completed
without blocking — this is the ticket's proof of Design Problem 1's
Supervisor-side fix.

**Documentation updates**: None in this ticket — see Ticket 007 for the
`serve` subcommand's own documentation surface (deferred per sprint
scope) and Ticket 008 for service-template documentation.
