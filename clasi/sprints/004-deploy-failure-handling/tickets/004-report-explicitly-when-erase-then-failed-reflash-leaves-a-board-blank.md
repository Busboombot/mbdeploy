---
id: '004'
title: Report explicitly when erase-then-failed-reflash leaves a board blank
status: in-progress
use-cases: [SUC-004]
depends-on: ['003']
github-issue: ''
issue: deploy-leaves-a-board-blank-without-saying-so.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# Report explicitly when erase-then-failed-reflash leaves a board blank

## Description

A second field report shows a companion gap to the mass-erase-gating bug
(tickets 001/003): a genuine lock-recovery mass-erased a board
successfully, and the retried flash then failed — leaving the board with
no firmware at all — with only `Error: flash still failed after mass
erase (exit 1)` to go on. Nothing said the board was now empty, so the
operator had no way to distinguish "flashing failed, firmware unchanged"
from "the board that used to work now has nothing on it."

This ticket makes that state explicit and unmissable, wherever it
happens: local CLI deploy or remote `deploy --remote` against the daemon.

Changes:
- `flash_hex` gains one new optional parameter, `board_name: str | None =
  None`, used only in this message (falls back to `uid` when not given).
- The message emitted when the retried flash still fails after a
  successful mass erase now states plainly that `{board_name or uid}` was
  erased and has no firmware, in addition to (not instead of) the
  existing exit-code detail. It is routed through `_log`/`log` exactly
  like every other message in this module — never printed only to local
  stderr — so `server.py::serve_flash`'s `LOG`-per-line relay carries it
  to a remote client too.
- `cli.py::_cmd_deploy` passes `board_name=_device_label(entry)` at its
  existing `flash_hex` call site — `_device_label` is already computed
  for other error messages in that function, so this is a one-line
  addition, no new cross-module dependency.
- `server.py::serve_flash` passes `board_name=board.name` at its existing
  `flash_hex` call site — `Board.name` is already held by the class, so
  this is also a one-line addition.
- The erase-failure branch (mass erase itself fails) is deliberately
  **unchanged** — no blank-board claim is made there, since the board's
  prior firmware is generally still intact when the erase never
  completed.

**Test fixture note:** `flash_hex` is monkeypatched out entirely in
`tests/test_server.py` and `tests/test_remote.py` (a `FakeFlash`/inline
fake stands in for it, per those files' own testing conventions). Adding
`board_name` as a new keyword-only-by-convention parameter to the real
`flash_hex` means every one of those fakes' call signatures needs the
same new parameter (`board_name=None`) or a real `serve_flash` call
raises `TypeError: ... unexpected keyword argument 'board_name'` — this
surfaced immediately as concrete `TestServeFlash`/`TestRemote` failures
when the call sites were updated, and is fixed in this ticket alongside
the feature itself, not deferred.

## Acceptance Criteria

- [x] `flash_hex` accepts an optional `board_name: str | None = None`,
      documented, defaulting to `uid` when not given.
- [x] Erase succeeds, reflash fails → the emitted message names the
      board and says explicitly it has no firmware; the return code is
      unchanged from the pre-ticket behavior (the retried flash's own
      rc).
- [x] Erase itself fails → no blank-board claim is made (only the
      existing "mass erase failed" message).
- [x] The message is routed through `log`, not only local stderr —
      verified with a supplied `log` callable and an empty captured
      stderr.
- [x] `cli.py::_cmd_deploy` passes `board_name=_device_label(entry)`.
- [x] `server.py::serve_flash` passes `board_name=board.name`.
- [x] Every test-suite fake standing in for `flash_hex` (in
      `tests/test_server.py` and `tests/test_remote.py`) accepts the new
      `board_name` keyword without raising.
- [x] Full suite passes with the new tests added, no regressions.

## Implementation Plan

**Approach**: Add the parameter and use it only in the one message that
needed it; thread it through both existing call sites; fix every
test-suite fake's signature that the new keyword argument would
otherwise break.

**Files modified**:
- `src/mbdeploy/flash.py` — `board_name` parameter, docstring update,
  the blank-board message.
- `src/mbdeploy/cli.py` — `_cmd_deploy`'s `flash_hex` call site.
- `src/mbdeploy/server.py` — `serve_flash`'s `flash_hex` call site.
- `tests/test_flash.py` — new `TestBlankBoardMessage` class.
- `tests/test_devices.py` — one new test asserting `_cmd_deploy` threads
  `_device_label(entry)` through to the message.
- `tests/test_server.py` — `FakeFlash`/`SlowFakeFlash.__call__` and the
  inline `slow_flash_hex` fake all gain `board_name=None`; one assertion
  added that `serve_flash` passes `board.name` through.
- `tests/test_remote.py` — the inline `fake_flash_hex` fake gains
  `board_name=None`.

**Documentation updates**: None beyond this ticket and the sprint
architecture section.

## Testing

- **Existing tests to run**: `uv run pytest tests/test_flash.py
  tests/test_devices.py tests/test_server.py tests/test_remote.py`, then
  the full suite `uv run pytest`.
- **New tests to write**: `TestBlankBoardMessage` in `test_flash.py`
  (names the board via `log`; falls back to `uid` without `board_name`;
  reaches `log` with stderr staying clean; erase failure makes no
  blank-board claim); one `test_devices.py` test confirming
  `_cmd_deploy` threads the device's label through end to end; one
  `test_server.py` assertion that `serve_flash` passes `board.name`.
- **Verification command**: `uv run pytest`
