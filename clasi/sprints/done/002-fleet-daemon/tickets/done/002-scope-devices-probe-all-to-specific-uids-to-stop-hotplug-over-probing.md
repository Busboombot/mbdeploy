---
id: '002'
title: Scope devices.probe_all to specific UIDs to stop hotplug over-probing
status: done
use-cases:
- SUC-003
depends-on: []
github-issue: ''
issue: mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# Scope devices.probe_all to specific UIDs to stop hotplug over-probing

## Description

`devices.probe_all()` (`src/mbdeploy/devices.py:323-369`), as written,
enumerates and probes **every** currently connected board on every call —
`probes = flashable_probes()` is unfiltered, and the loop then calls
`probe_type(port)` (a `HELLO\n` write) for every one of them. This is
fine for the CLI's `probe`/`list` commands, which want a full refresh.
It is wrong for Sprint 002's `Supervisor` watcher (Ticket 006), which
must react to a single board arriving without disturbing every *other*
board already plugged in — on a multi-board host, hotplugging one board
would otherwise inject a stray `HELLO` into every already-running board,
possibly mid-session or mid-relay, and stall the watcher's tick for the
full timeout of every board's probe rather than just the new one's. This
is Design Problem 2 from `sprint.md`'s Architecture section, and it's a
`devices.py` change the source issue's own Files list omits — sequenced
first in this sprint, ahead of the `Supervisor` ticket that needs it,
because nothing about the fix requires any other sprint code to exist.

Add `only_uids: set[str] | None = None` to `probe_all`'s signature. When
given, restrict the set of probes actually touched (port refresh,
`probe_type`'s `HELLO` write, and the SWD board-name read) to exactly
the UIDs in that set — every UID not in `only_uids` is left completely
untouched by this call, not even re-enumerated. When omitted (the
default, and every existing caller's behavior), nothing changes: this is
purely additive.

## Acceptance Criteria

- [x] `probe_all(config_path, clear=False, target_mcu=DEFAULT_MCU,
      only_uids=None)` — new keyword-only-by-convention parameter,
      default `None`.
- [x] With `only_uids=None`: behavior is byte-for-byte identical to
      today — every existing test in `tests/test_devices.py` covering
      `probe_all` passes unchanged.
- [x] With `only_uids={<uid>}` given a set of currently connected UIDs:
      only those UIDs' entries are refreshed (port, announcement fields,
      board name); a currently-connected UID **not** in the set is never
      passed to `port_serial_map`, `probe_type`, or `read_device_id` by
      this call, and its existing registry entry (if any) is left
      byte-for-byte unchanged.
- [x] With `only_uids=set()` (empty set, not `None`): no board is probed
      at all — a no-op refresh. (Distinguishing "narrow to nothing" from
      "don't narrow" is exactly why the parameter defaults to `None`
      rather than an empty set.)
- [x] Every currently-connected UID *not* passed a `HELLO` is
      **provably** never opened: a test using a fake `serial.Serial`
      asserts `open`/`write` was never called for the excluded UID's
      port.
- [x] `only_uids` combined with `clear=True` raises `ValueError` before
      touching the registry file — `clear` wipes the registry down to
      what this call sees, which would silently delete every
      non-`only_uids` board's entry if the guard weren't there. No
      existing caller combines the two, so this is a new, deliberate
      restriction, not a behavior change.
- [x] Every existing caller (`cli._cmd_probe`, `cli._cmd_list` via
      `port_serial_map` — note `_cmd_list` doesn't call `probe_all`
      directly, verify this before assuming it needs a change) is
      confirmed to pass no `only_uids` argument and therefore be
      unaffected.
- [x] Full existing `tests/test_devices.py` suite passes unchanged.

## Implementation Plan

**Approach**: In `probe_all`, immediately after `probes =
flashable_probes()`, filter: `if only_uids is not None: probes = [p for p
in probes if p["uid"] in only_uids]`. Raise `ValueError("only_uids
cannot be combined with clear=True")` at the top of the function if both
`clear` and `only_uids is not None` are truthy, before `devices = {} if
clear else load_devices(...)` runs. No other line in the function
changes — `uids = {p["uid"] for p in probes}` and everything downstream
already only iterates `probes`, so narrowing that list upstream is
sufficient; do not add a second filter deeper in the loop.

**Files to modify**:
- `src/mbdeploy/devices.py` — `probe_all`'s signature and the two lines
  described above. Update the function's docstring to document
  `only_uids` and the `clear` interaction.
- `tests/test_devices.py` — new tests for `only_uids` (narrowing,
  empty-set no-op, non-narrowed entries untouched, `HELLO` never sent to
  excluded UIDs, `ValueError` on `only_uids` + `clear=True`).

**Testing plan**:
- Reuse the existing `probe_all` test fixtures/fakes (fake
  `flashable_probes`, fake `port_serial_map`, fake `probe_type`) already
  present in `tests/test_devices.py`; add a fake for a second,
  simultaneously-connected board so narrowing has something to prove it
  excludes.
- Assert: calling with `only_uids={uid_a}` when both `uid_a` and `uid_b`
  are connected updates `uid_a`'s entry and leaves `uid_b`'s prior entry
  (or absence of one) untouched; the fake `probe_type`/`serial.Serial`
  records that it was invoked for `uid_a`'s port only.
- Assert `only_uids=set()` touches neither board.
- Assert `ValueError` is raised, and the registry file is **not**
  written, when `clear=True` and `only_uids` is not `None`.
- Run the full existing `tests/test_devices.py` suite to confirm no
  regression to the unscoped default path.

**Documentation updates**: None required by this ticket — `only_uids` is
an internal parameter consumed by Ticket 006's `Supervisor`, not a
user-facing CLI flag.
