---
id: '003'
title: Gate mass-erase recovery on a locked/protected signature
status: done
use-cases:
- SUC-003
depends-on:
- '001'
- '002'
github-issue: ''
issue: mass-erase-fires-on-failures-it-cannot-fix-and-wipes-working-boards.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# Gate mass-erase recovery on a locked/protected signature

## Description

This is the headline fix for sprint 004. `flash_hex` currently gates its
CTRL-AP mass erase on `if rc != 0:` — any non-zero exit from `pyocd flash`
at all — even though the comment directly above the branch names a narrow
rationale (APPROTECT / a protected SoftDevice region) that the condition
never actually checks for. A field report confirms the consequence
directly: a failure the mass erase cannot fix (in the field report, a
malformed hex file — now caught earlier by ticket 001, but any other
non-locked failure shape has the identical problem) mass-erases a working
board, then the identical retry fails against the same underlying problem.

Ticket 002 added `_run_streamed`'s output capture and a transient-signature
retry ahead of any erase decision. This ticket finishes the fix by
replacing the unconditional `if rc != 0:` erase gate with a signature check:
mass erase now fires **only** when the (post-transient-retry) failure output
matches a locked/protected signature. Anything else — an invalid hex that
slipped past validation, a bad `--target-mcu`, or any pyocd failure this
module doesn't recognize — fails immediately, without ever calling
`erase --mass`.

Changes:
- A new named, documented module-level pattern list, `_LOCKED_SIGNATURES`
  (a `0x67` sector-erase failure, APPROTECT/auth/lock wording), and a
  predicate helper, `_looks_locked(output) -> bool`, placed alongside
  `_TRANSIENT_SIGNATURES`/`_looks_transient` from ticket 002 as the one
  named, documented place these signatures live.
- `flash_hex`'s erase gate changes from `if rc != 0:` to
  `if rc != 0 and _looks_locked(output):`, with a new `elif rc != 0:`
  branch that logs a clear "not mass-erasing, signature not recognized"
  message and returns the failing rc immediately — no subprocess call at
  all in that branch.
- Per the sprint's Design Rationale: default to **not** erasing when a
  signature is unrecognized. An unrecognized failure that was actually a
  lock costs the operator one manual `pyocd erase --mass` (already
  documented in the manual); an unrecognized failure that wasn't a lock
  and gets erased anyway costs a board's firmware. The asymmetry is not
  close, so "unrecognized = don't erase" is not a fallback heuristic, it's
  the point of this ticket.
- The three existing return-code contracts are preserved exactly: erase
  failure → erase's own rc (no retry); still-failing flash after a
  successful mass erase → its own rc; success → reset's rc.

**Existing test adaptation (expected, not a regression):** every existing
test in `tests/test_flash.py`/`tests/test_devices.py::TestMassEraseRecovery`
that simulates "flash fails, therefore mass erase happens" previously did
so with a signature-free fake failure (a bare non-zero exit code, no
output text). Under the new gating those fakes no longer trigger the
mass-erase branch. Each such fake now includes a locked-style output line
(`"flash erase sector failure (0x67)"`) so it continues to exercise the
recovery path it was written to test. Two tests in ticket 002's
`TestTransientRetry` class that used an erase-always-fails fake purely to
short-circuit the sequence after a retry are updated to assert the
now-correct final behavior directly: two consecutive transient failures
(or one non-transient failure) never reach the erase branch at all, since
neither signature is locked.

## Acceptance Criteria

- [x] `_LOCKED_SIGNATURES` and `_looks_locked` live in one named,
      documented, module-level place in `flash.py`, alongside
      `_TRANSIENT_SIGNATURES`/`_looks_transient`.
- [x] A `0x67` sector-erase failure still triggers mass-erase recovery,
      matching the pre-sprint main flow and both existing error flows
      (erase failure → erase's rc, no retry; still-failing flash after
      erase → its rc).
- [x] APPROTECT/auth/lock wording is also recognized as a locked
      signature and triggers the same recovery.
- [x] An unrecognized failure signature never invokes `erase --mass` —
      asserted directly, not just implied by return code.
- [x] A malformed hex (ticket 001) still results in zero
      `erase --mass` calls, verified directly at the `flash_hex` level in
      this ticket's own test (independent of how ticket 001 implements
      its guard).
- [x] A simulated "bad `--target-mcu`"-style pyocd failure fails without
      erasing.
- [x] Two consecutive transient failures (ticket 002) still give up after
      one retry and now correctly never reach the erase branch, since a
      merely-transient signature is not a locked one.
- [x] All three return-code contracts are unchanged: erase failure →
      erase's rc; still-failing flash after erase → its rc; success →
      reset's rc.
- [x] Full suite passes with the expected test adaptations described
      above.

## Implementation Plan

**Approach**: Add `_LOCKED_SIGNATURES`/`_looks_locked` next to ticket
002's transient pair, change the erase gate's condition, add the
`elif rc != 0: return rc` fail-without-erasing branch, and adapt every
existing fake that relied on the old unconditional gate.

**Files modified**:
- `src/mbdeploy/flash.py` — `_LOCKED_SIGNATURES`/`_looks_locked`, the
  gate rewrite in `flash_hex`, updated docstring.
- `tests/test_flash.py` — a shared `_LOCKED_SIGNATURE_LINES` constant;
  updated fakes in `TestArgvConstruction::test_erase_argv_on_recovery`,
  all of `TestMassEraseRecovery`, and `TestLogRouting`; corrected
  assertions in `TestTransientRetry`'s two multi-failure tests; a new
  `TestSignatureGating` class with the ticket's own regression tests.
- `tests/test_devices.py` — a shared `_LOCKED_SIGNATURE_LINES` constant;
  updated fakes in `TestMassEraseRecovery::test_flash_retries_after_mass_erase`
  and `::test_mass_erase_failure_aborts_without_retry`.

**Documentation updates**: None beyond this ticket and the sprint
architecture section.

## Testing

- **Existing tests to run**: `uv run pytest tests/test_flash.py
  tests/test_devices.py`, then the full suite `uv run pytest`.
- **New tests to write** (in `TestSignatureGating`):
  `test_0x67_sector_erase_failure_triggers_mass_erase`,
  `test_approtect_signature_triggers_mass_erase`,
  `test_malformed_hex_never_reaches_erase_mass`,
  `test_bad_target_mcu_style_failure_never_erases`.
- **Verification command**: `uv run pytest`
