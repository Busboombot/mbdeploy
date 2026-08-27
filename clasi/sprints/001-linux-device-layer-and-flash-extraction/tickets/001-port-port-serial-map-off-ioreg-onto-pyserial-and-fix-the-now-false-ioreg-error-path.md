---
id: '001'
title: Port port_serial_map off ioreg onto pyserial, and fix the now-false ioreg error
  path
status: in-progress
use-cases:
- UC-001
- UC-002
- UC-007
- SUC-001
depends-on: []
github-issue: ''
issue: mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
completes_issue: false
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# Port port_serial_map off ioreg onto pyserial, and fix the now-false ioreg error path

## Description

`devices.port_serial_map()` (`src/mbdeploy/devices.py:93`) shells out to
macOS `ioreg -r -c IOUSBHostDevice -l` and regex-parses `"USB Serial
Number"` / `"IOCalloutDevice"` pairs. It returns `{}` on every other
platform, which cascades: `probe_all` records `port: null` fleet-wide,
`deploy` refuses every `/dev/…` target, and `cli._deploy_entry`'s error
message for that refusal names `ioreg` explicitly — a claim that becomes
false the moment this function no longer uses it.

Reimplement `port_serial_map()` on `serial.tools.list_ports.comports()`,
filtered to VID:PID `0x0D28:0x0204` (ARM DAPLink). This has been verified
on real hardware: `comports()` returns the pyOCD UID verbatim as
`serial_number` on both macOS (4 boards) and Linux/aarch64
(`/dev/ttyACM0` → `9906360200052820fe9a...`). Keep the function's
signature and `known` parameter exactly as they are — `probe_all`,
`cli._deploy_entry`, and `cli._connect_port` must not need to change.
Delete the `ioreg` subprocess call and both `_IOREG_SERIAL_RE` /
`_IOREG_CALLOUT_RE` regexes.

There is currently **no direct test** of `port_serial_map` — every one of
the ~10 references to it in `tests/test_devices.py` monkeypatches it
away — so this ticket also writes the first-ever tests against it,
against fake `comports()` objects (simple namespace/namedtuple stand-ins
exposing `.device`, `.vid`, `.pid`, `.serial_number`; no real hardware or
real `pyserial` internals needed).

Finally, fix the one place downstream whose wording depends on the old
implementation: `cli._deploy_entry`'s "no live port mapping is available"
message (`src/mbdeploy/cli.py:242-247`) says the map "is read from macOS
'ioreg'". Reframe it platform-neutrally — an empty map now means "no
micro:bit serial port found," and since the likeliest cause on a Pi is
the service user not being in `plugdev`/`dialout`, say that. Update
`tests/test_devices.py:695` (`test_no_live_map_is_refused_not_guessed`),
which currently asserts `"ioreg" in stderr`, to match the new message —
its other assertions (`rc != 0`, `calls == []`) are unaffected and must
keep passing.

## Acceptance Criteria

- [x] `port_serial_map(known=None)` is implemented on
      `serial.tools.list_ports.comports()`; the `ioreg` subprocess call
      and both `_IOREG_*` regexes are deleted from `devices.py`.
- [x] The function's signature and `known`-parameter semantics are
      unchanged: with `known` given, only UIDs in that set are recorded;
      with `known=None`, every discovered micro:bit port is recorded.
- [x] Ports are filtered to VID:PID `0x0D28:0x0204`; a port with a
      different VID:PID is never included, even if its `serial_number`
      collides with a real UID.
- [x] A port whose `serial_number` is `None` is skipped without raising
      (the dev Mac has three such ports: Bluetooth-Incoming-Port,
      debug-console, wlan-debug — a naive dict build maps `None` as a
      key, which must not happen).
- [x] `comports()` returning nothing that matches yields `{}`, not an
      exception.
- [x] Every existing caller (`probe_all`, `cli._deploy_entry`,
      `cli._connect_port`) is unmodified in this ticket except for the
      one error-message string described below.
- [x] `cli._deploy_entry`'s "no live port mapping" message no longer
      mentions `ioreg` or macOS; it states that no micro:bit serial port
      was found and suggests checking `plugdev`/`dialout` group
      membership. The "no board connected at all" branch (`known` is
      empty) is unaffected — only the "map is unexpectedly empty despite
      known probes" branch changes wording.
- [x] `tests/test_devices.py:695` no longer asserts `"ioreg" in
      capsys.readouterr().err`; it asserts against the new message text.
      `rc != 0` and `calls == []` in that same test still pass.
- [x] Full existing suite (`tests/test_devices.py`,
      `tests/test_connect.py`) passes unchanged apart from the one
      updated assertion above.

## Implementation Plan

**Approach**: Replace the body of `port_serial_map` with a loop over
`serial.tools.list_ports.comports()`, building `{uid: device}` the same
way the current function builds `{serial: callout_device}` — `setdefault`
per UID so the first port seen for a given serial wins, matching current
behavior. Import `serial.tools.list_ports` inside the existing
`try/except` guard at the top of the module (pyserial is optional in
CI-without-hardware) rather than adding a second import path.

**Files to modify**:
- `src/mbdeploy/devices.py` — reimplement `port_serial_map`; delete
  `_IOREG_SERIAL_RE`, `_IOREG_CALLOUT_RE`, and the `ioreg` subprocess
  call; update the module's `import subprocess` usage if `flashable_probes`
  is the only remaining subprocess caller (verify before removing the
  import — `_flashable_probes_cli` still uses it).
- `src/mbdeploy/cli.py` — reword the "no live port mapping" branch of
  `_deploy_entry` (around line 242-247).
- `tests/test_devices.py` — update the one `"ioreg" in stderr` assertion;
  add a new test class/section for `port_serial_map` directly.

**Testing plan**: New tests (likely a new `TestPortSerialMap` class in
`tests/test_devices.py`, monkeypatching
`devices_mod.serial.tools.list_ports.comports`):
- UID → port mapping for a plausible fake `comports()` result.
- `known` set filtering: an unlisted UID is excluded even if VID:PID
  matches.
- `known=None` returns every matching port.
- A non-micro:bit VID:PID is excluded even with a colliding
  `serial_number`.
- A port with `serial_number is None` is skipped, no exception.
- Empty `comports()` result → `{}`.
Existing tests to run unchanged: the full `tests/test_devices.py` and
`tests/test_connect.py` suites (both monkeypatch `port_serial_map` away
for everything except the two updated/new pieces above).

**Documentation updates**: None in this ticket — covered in ticket 004,
which depends on this one landing first so the docs describe the actual
post-fix behavior.
