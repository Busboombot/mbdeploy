---
id: '003'
title: 'Regression tests for both DEVICE: announcement dialects'
status: done
use-cases:
- UC-001
depends-on: []
github-issue: ''
issue: mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
completes_issue: false
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# Regression tests for both DEVICE: announcement dialects

## Description

`devices.probe_type()` (`src/mbdeploy/devices.py:125`) parses a board's
`HELLO` reply into `{role, common_name, device_name, serial, raw}`.
Commit `2e19088` taught it a **second** announcement dialect —
space-delimited (`device <role> <common_name> <device_name> <serial>`,
used by the robot firmware since the v6 wire protocol dropped `:` as a
field separator) — alongside the original colon form
(`DEVICE:<role>:<common_name>:<device_name>:<serial>`). That commit
**shipped with zero tests**: the suite was 92 tests before and 92 after.
Every current reference to `probe_type` in `tests/test_devices.py`
monkeypatches it away, so neither dialect has ever actually been
exercised by a test.

This ticket is pure test debt — `probe_type` itself does not change. Add
direct tests, driving `probe_type` against a fake `serial.Serial`
(monkeypatch `devices_mod.serial.Serial` to return a scripted double
exposing `readline()`, `write()`, `flush()`, `reset_input_buffer()`,
`open()`, `.port`, `.dtr`, `.rts`, `.is_open`, `.close()` — the existing
`FakeSerial` in `tests/test_connect.py:37` is close but lacks `open()`
and the modem-line attributes `probe_type` sets before opening; extend it
or add a second small fake local to `test_devices.py` rather than forking
console.py's version).

Use these two real announcement lines as fixtures, since they're the
concrete banners named in the review that found this gap:
- Colon dialect: `DEVICE:RADIOBRIDGE:relay:getez:1779042496`
- Space dialect: `device NEZHA2 robot vevov 1198504156`

## Acceptance Criteria

- [x] Both dialects parse to the same five fields (`role`, `common_name`,
      `device_name`, `serial`, `raw`), using the two fixture lines above.
- [x] The colon form's serial is rejoined on `:` when the serial value
      itself contains a colon (i.e. a serial with 6+ colon-delimited
      parts still ends up as one `serial` string, matching the existing
      `":".join(parts[4:])` behavior at `devices.py:182`).
- [x] The space form ignores extra trailing tokens beyond the fifth field
      (matching `devices.py:189`'s "any extra trailing tokens are not
      part of it" comment) — a line with a 6th space-delimited token
      still parses to the same four announcement fields plus `raw`.
- [x] A truncated/incomplete banner (fewer than 5 fields in either
      dialect) returns `None`.
- [x] A `ver` reply and a `status` reply (i.e., any line that isn't a
      `DEVICE:`/`device ` announcement) return `None`.
- [x] `probe_all` preserves a board's prior `role`/`common_name`/
      `device_name`/`serial`/`announcement` fields unchanged when
      `probe_type` returns `None` for that probe (this is the existing
      invariant in `devices.py`'s merge logic — add a regression test
      confirming it holds for *both* the "no reply at all" and "reply
      doesn't parse" cases, since the field gap this ticket closes is
      exactly what let a robot announcement silently fail to update
      `role` in the past).
- [x] No production code in `devices.py` changes — this ticket is test
      coverage only.

## Implementation Plan

**Approach**: Add a `TestProbeType` (or similar) section to
`tests/test_devices.py`. Build a minimal fake `serial.Serial`-alike whose
`readline()` yields the fixture line(s) then empty bytes, monkeypatched
in place of `devices_mod.serial.Serial` so `probe_type` opens and reads
from it exactly as it would a real port. For the `probe_all`-preservation
case, monkeypatch `probe_type` to return `None` (as already done
elsewhere in the file) and assert the registry entry's announcement
fields are untouched from a pre-seeded value — that pattern already
exists for other `probe_all` tests in the file and should be followed
rather than reinvented.

**Files to modify**:
- `tests/test_devices.py` — new test class(es) for `probe_type` dialect
  parsing and the `probe_all` preservation regression.

**Testing plan**: This ticket *is* the testing plan. Verification is
`pytest tests/test_devices.py -k probe_type or announcement` plus the
full suite to confirm no existing test's behavior shifted.

**Documentation updates**: None. (Documenting the second dialect in
README/agent_manual is noted as an open question in this sprint's
Architecture section, §12 discrepancy D1 in `specification.md` — out of
this sprint's stated doc scope, left for whichever sprint next touches
those files.)
