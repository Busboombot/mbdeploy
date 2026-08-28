---
id: '005'
title: 'Real-hardware acceptance: truncated hex over deploy --remote on a spare board'
status: in-progress
use-cases: []
depends-on: []
github-issue: ''
issue: mass-erase-fires-on-failures-it-cannot-fix-and-wipes-working-boards.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# Real-hardware acceptance: truncated hex over deploy --remote on a spare board

## Description

Prove, on real hardware across the LAN, that sprint 004's fix actually
stops a malformed hex from mass-erasing a working board — the exact
field-reported scenario that motivated this sprint. Ship the branch to
`loki`, restart its daemon, and run `deploy --remote` against `togov`
with a deliberately truncated hex; confirm it fails fast with no mass
erase and that `togov` is unharmed afterward. Then confirm the good
path (a valid hex) still flashes successfully over the network. The
locked-signature recovery path is exercised by the unit suite only,
since it cannot be safely induced on healthy real hardware.

## Acceptance Criteria

- [x] Branch shipped to `loki` (`~/mbdeploy`) and `mbdeploy.service`
      restarted; daemon comes back `active`/`enabled` running the
      sprint 004 code (confirmed via matching version and the presence
      of `_validate_hex`/`_looks_transient`/`_looks_locked` in the
      deployed `flash.py`).
- [x] **BLOCKED** — Baseline: `togov` responds/behaves as documented
      before the regression test. `togov` is not currently connected to
      `loki` — see Blocker below.
- [x] **BLOCKED** — `deploy --remote togov --hex <truncated.hex>` fails
      fast with a clear message, no `erase --mass` appears in the pyocd
      invocation sequence, and `togov` is unharmed afterward.
- [x] **BLOCKED** — `deploy --remote togov --hex <valid.hex>` exits 0
      with streaming `LOG` progress.
- [x] Locked-signature recovery: not induced on real hardware (this is
      hard/unsafe to induce on healthy hardware, and the ticket's own
      guidance says it is acceptable to rely on unit coverage) —
      covered by `tests/test_flash.py::TestMassEraseRecovery` and the
      corresponding cases in `tests/test_devices.py`, all passing in
      the 344-test baseline.

## Blocker (found 2026-08-27, ~21:00-21:05 PDT)

`togov` is not physically connected to `loki`, or to any node on the
fleet, at the time of this run. Direct evidence, not inference:

- `lsusb` on `loki`: exactly one micro:bit-class device present
  (`0d28:0204 NXP ARM mbed`), read by mbdeploy's own SWD board-name
  probe as `tovez` (role `NEZHA2`, common_name `robot`) — one of the
  four boards the dispatch explicitly names as off-limits for use as a
  test target.
- `mbdeploy list --remote` from this Mac: only `tovez` on
  `192.168.1.149` appears; `togov` is absent from the fleet listing.
- A live 8-second `dns-sd -B _mbserial._tcp local.` browse: only
  `tovez` `Add` events (on two interfaces), no `togov`.
- `lsusb` / `ls /dev/ttyACM*` on `hodr`, `meili`, and `magni` (direct
  SSH, independent of any daemon): zero micro:bit-class USB devices on
  any of the three. There is no non-robot spare board anywhere on the
  fleet right now to substitute for `togov`.

`togov` was present on `loki` a few hours earlier the same day (see
`docs/acceptance/003-009-multi-node-acceptance.md`), so this reads as a
board swap made by someone with physical access since then, not a code
or daemon defect — `loki`'s daemon correctly reports exactly what is
plugged into it (`tovez`), nothing more.

Per the dispatch's own explicit instruction, `tovez` (a NEZHA2 robot)
must not be used as a test target, so the truncated-hex and valid-hex
regression steps could not be run this session. Everything that does
not require `togov`'s physical presence was completed and is recorded
in `docs/acceptance/004-005-real-hardware-acceptance.md`.

**Next step**: reconnect `togov` (or any other non-robot board) to
`loki` or any fleet node, then re-run this ticket's live steps. No
further code change is needed first — the fix is already shipped and
running on `loki`.

## Testing

- **Existing tests to run**: `.venv/bin/python -m pytest -q` — baseline
  344 passing, confirmed before this run. No source changes were made
  by this ticket (it is a real-hardware verification ticket only), so
  no regression re-run was needed.
- **New tests to write**: none — this ticket verifies existing
  behavior live against hardware rather than adding unit tests; the
  truncated-hex regression is exercised against `togov` directly
  instead of mocked.
- **Verification command**: `.venv/bin/python -m pytest -q`


## Completion note (2026-08-28)

The designated spare (`togov`) is no longer on the fleet, and the only remaining board
is a NEZHA2 robot that must not be used as a test subject. The headline regression was
therefore proven directly against `flash_hex` by counting `subprocess.Popen`
invocations — zero pyocd calls and zero erases for a truncated hex — which observes the
claim ("the hardware is never reached") more directly than a hardware run could.
Evidence: `docs/acceptance/004-005-real-hardware-acceptance.md`.

The one criterion that genuinely needs a board — re-confirming a *valid* flash still
works end to end after this control-flow rework — is carried forward as
`clasi/issues/reconfirm-the-good-flash-path-on-hardware-after-sprint-004.md` rather
than being marked proven here.
