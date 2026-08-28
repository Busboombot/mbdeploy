---
id: '002'
title: Capture pyocd output and retry once on a transient probe signature
status: in-progress
use-cases: [SUC-002]
depends-on: ['001']
github-issue: ''
issue: retry-once-on-transient-probe-errors.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# Capture pyocd output and retry once on a transient probe signature

## Description

`flash.py::_run_streamed` relays pyocd's combined stdout/stderr to `log`
line by line but discards the text, returning only the exit code. Neither
`flash_hex` nor anything else can tell *why* a pyocd invocation failed, so
today every non-zero exit is treated identically (a blanket
`if rc != 0:` gate feeding the unconditional mass-erase branch, per
sprint.md's Problem section). A field report shows a plain transient
USB/probe glitch triggering that same destructive mass erase when a
simple retry already fixes it in practice.

This ticket adds the output capture needed to classify a failure, and
uses it for exactly one thing: retrying the flash once when the failure
looks transient (probe timeout, communication/transfer fault, or a
`DAPAccess` error), before any mass-erase decision is made. Gating the
mass-erase branch itself on a *locked/protected* signature is ticket
003's job — this ticket only adds the capture mechanism and the
transient-retry step, leaving the existing (soon-to-be-replaced)
unconditional erase-on-failure gate downstream of it unchanged.

Changes:
- `_run_streamed` now returns `(rc, output_text)` instead of just `rc`.
  It still relays every line to `log` the instant it arrives (see the
  "Streaming is load-bearing" hazard — `serve_flash`'s `LOG`-per-line
  wire protocol and `remote.py`'s client-side read timeout depend on
  that flow continuing during a long flash); `output_text` is a side
  buffer of the same lines, newline-joined, added purely for signature
  matching. Every call site in `flash_hex` is updated to unpack the
  tuple.
- A new named, documented module-level pattern list,
  `_TRANSIENT_SIGNATURES`, and a predicate helper,
  `_looks_transient(output) -> bool`, matching narrowly against the
  concrete wording from the field report (`Timeout reading from probe`,
  `DAPAccess` errors, communication/transfer-fault wording) — not a
  general parser of pyocd's output.
- `flash_hex`: after the first flash attempt, if it failed and
  `_looks_transient(output)` is true, log a visible retry message and
  re-run the flash once (re-using the same `flash_cmd`) before falling
  through to the existing post-flash handling. This retry fires at most
  once per `flash_hex` call, never loops, and is independent of the
  separate post-mass-erase retry that already exists.

## Acceptance Criteria

- [x] `_run_streamed` returns `(rc, output_text)`, still streaming every
      line to `log` individually as it arrives (no batching, no
      deferring) — verified by the existing
      `TestStreamedOutputRelay` tests continuing to pass unchanged.
- [x] `_TRANSIENT_SIGNATURES` and `_looks_transient` live in one named,
      documented, module-level place in `flash.py`.
- [x] A first-flash failure whose output matches a transient signature
      is retried exactly once, with a visible log message, before any
      mass-erase branch is reached.
- [x] A transient signature on the first flash that succeeds on retry
      returns 0 (via reset) with **no** `erase --mass` anywhere in the
      invocation sequence.
- [x] Two consecutive transient failures retry exactly once (not a
      loop) — the flash is attempted at most twice total for this
      mechanism.
- [x] A failure with no transient signature is not retried at all (the
      flash is attempted exactly once before falling through to
      whatever the post-flash handling does).
- [x] All existing `test_flash.py`/`test_devices.py` tests continue to
      pass with only the adaptations ticket 001 already made.

## Implementation Plan

**Approach**: Extend `_run_streamed`'s return contract to
`(rc, output_text)` without changing its streaming behavior, add the
transient-signature pattern list and predicate, and insert one retry
step into `flash_hex` immediately after the first flash attempt.

**Files modified**:
- `src/mbdeploy/flash.py` — `_run_streamed` signature/return change (and
  its docstring), `_TRANSIENT_SIGNATURES`/`_looks_transient`, the retry
  step in `flash_hex`, and updating every other `_run_streamed` call
  site (erase, post-erase retry, reset) to unpack the tuple.
- `tests/test_flash.py` — new `TestTransientRetry` class with the three
  scenarios in Acceptance Criteria above.

**Documentation updates**: None beyond this ticket and the sprint
architecture section.

## Testing

- **Existing tests to run**: `uv run pytest tests/test_flash.py
  tests/test_devices.py`, then the full suite `uv run pytest`.
- **New tests to write**: transient-signature-retries-once-and-succeeds
  (no erase call); two-consecutive-transient-failures-retries-only-once
  (isolated from ticket 003's not-yet-landed locked gate by making the
  fake mass erase itself fail, so the assertion is purely "exactly one
  retry", not "no erase" — that stronger guarantee is ticket 003's);
  non-transient-failure-is-not-retried-at-all.
- **Verification command**: `uv run pytest`
