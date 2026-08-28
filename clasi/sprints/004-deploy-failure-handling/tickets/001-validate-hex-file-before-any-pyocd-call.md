---
id: '001'
title: Validate hex file before any pyocd call
status: in-progress
use-cases:
- SUC-001
depends-on: []
github-issue: ''
issue: mass-erase-fires-on-failures-it-cannot-fix-and-wipes-working-boards.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# Validate hex file before any pyocd call

## Description

`flash.flash_hex` currently hands `hex_path` straight to `pyocd flash`
without ever looking at it. A missing, unreadable, or malformed hex file
therefore fails only after pyocd has already tried to touch the board —
and, before tickets 002/003 land, would still trigger the old unconditional
mass erase. This ticket adds a pre-flight validation step so the whole
class of "operator-side file problem reaches the board at all" is removed
independently of anything the later tickets do with pyocd's failure
signatures.

Add a private helper, e.g. `_validate_hex(hex_path: str) -> str | None`,
that:
- Opens `hex_path` and parses it with `intelhex.IntelHex()` (already an
  installed pyocd dependency — confirmed present in this project's `.venv`
  at `intelhex==2.3.0`, so no new dependency needs adding to
  `pyproject.toml`).
- Returns `None` on success, or a short human-readable error string on
  failure (missing file, permission error, parse error — `intelhex` raises
  distinct exception types for these; catch broadly enough to cover a
  missing file, e.g. catch `OSError` and whatever `intelhex` itself raises
  for a malformed file, and fold both into one clear message rather than
  leaking a raw traceback).

Call this at the very top of `flash_hex`, before `flash_cmd` is even
constructed. On a non-`None` result, route the message through `log` (same
`_log` helper already used elsewhere in the module) and return a non-zero
exit code — no `_PYOCD` subprocess of any kind runs.

**Test-fixture hazard (do this as part of this ticket, not deferred):**
Both `tests/test_flash.py` (module constant `_HEX_PATH = "MICROBIT.hex"`)
and `tests/test_devices.py` (`_make_args(hex_path=None)` defaults through
`_cmd_deploy` to the literal string `"MICROBIT.hex"`) currently pass a hex
path that does not exist on disk. Confirmed by direct reproduction:
`intelhex.IntelHex().loadhex("MICROBIT.hex")` raises `FileNotFoundError`
immediately against a nonexistent file. Once this ticket lands, every
existing test that expects `flash_hex`/`_cmd_deploy` to reach the faked
`subprocess.Popen` step will fail at the new validation step instead,
unless it supplies a real, valid, on-disk hex file. Fix this in the same
ticket:
- Add a small shared helper/fixture in both test files that writes a
  minimal valid Intel HEX file to `tmp_path` (a single EOF record,
  `:00000001FF`, is a complete valid file) and use its path everywhere a
  test expects the flash step to be reached.
- Do **not** solve this by monkeypatching `_validate_hex` away in the
  general case — the fixture approach means the validation code path is
  itself exercised by the existing suite, not bypassed by it. (A targeted
  monkeypatch is fine only in the new invalid-hex test below, where the
  point is to prove validation fires without needing a hand-crafted
  malformed file — though a real malformed file is preferred if it's no
  more effort.)

## Acceptance Criteria

- [x] `flash_hex` validates `hex_path` before constructing any pyocd
      command.
- [x] A malformed hex file fails `flash_hex` with a clear message and
      **zero** `subprocess.Popen` calls (asserted directly — not just "no
      erase call", but no flash/reset/erase call at all).
- [x] A missing or unreadable hex file fails the same way, with zero
      subprocess calls.
- [x] The message reaches a caller-supplied `log` (not only stderr) —
      consistent with the module's existing `_log` convention.
- [x] `tests/test_flash.py` and `tests/test_devices.py` are updated so
      every test that expects the flash step to be reached uses a real,
      valid, on-disk hex file; the full suite (330 tests today) passes
      after this ticket, with the two new tests above added.
- [x] `intelhex` is imported directly in `flash.py` (not just relied upon
      transitively via pyocd); if `pyproject.toml`'s `dependencies` list
      does not already declare it explicitly, add it there rather than
      relying on pyocd's own dependency resolution to keep providing it.

## Implementation Plan

**Approach**: Add `_validate_hex` as a new private function in
`src/mbdeploy/flash.py`, call it as the first statement in `flash_hex`,
and thread a real on-disk hex fixture through the two existing test files
so the pre-existing suite keeps exercising the real flash path rather than
short-circuiting on the new check.

**Files to modify**:
- `src/mbdeploy/flash.py` — add `_validate_hex`, add `import intelhex` (or
  `from intelhex import IntelHex`), call at the top of `flash_hex`.
- `pyproject.toml` — add `intelhex` to `dependencies` if not already
  present explicitly (check `uv.lock` first; it is present transitively
  today).
- `tests/test_flash.py` — add a valid-hex-file fixture/helper; update
  `_HEX_PATH` usage (or add a second constant) so tests that expect the
  flash step to run use a real file; add the two new validation tests.
- `tests/test_devices.py` — same fixture pattern for `_make_args`'s
  `hex_path`, applied to every test under `TestMassEraseRecovery` and any
  other test that currently relies on the default/fake hex path reaching
  the mocked `Popen`.

**Testing plan**: Run `uv run pytest tests/test_flash.py
tests/test_devices.py` first in isolation, then the full suite
(`uv run pytest`), confirming 330 + new-tests all pass with zero skips.

**Documentation updates**: None beyond this ticket and the sprint
architecture section — no public-facing docs describe `flash_hex`'s
internals.

## Testing

- **Existing tests to run**: `uv run pytest tests/test_flash.py
  tests/test_devices.py`, then the full suite `uv run pytest`.
- **New tests to write**: malformed-hex-rejected-with-zero-subprocess-calls;
  missing-file-rejected-with-zero-subprocess-calls; both asserting the
  message reaches a supplied `log` callable.
- **Verification command**: `uv run pytest`
