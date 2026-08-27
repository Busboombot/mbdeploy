---
id: '002'
title: Extract flash_hex into src/mbdeploy/flash.py
status: done
use-cases:
- UC-005
- UC-006
- SUC-002
depends-on: []
github-issue: ''
issue: mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
completes_issue: false
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# Extract flash_hex into src/mbdeploy/flash.py

## Description

The flash → mass-erase-recovery → retry → reset sequence lives inline in
`cli._cmd_deploy` (`src/mbdeploy/cli.py:336-377`). Sprint 002's `serve`
daemon needs to run this exact same sequence when it receives a `FLASH`
request over the network; without this extraction it would grow a second,
divergent copy of the locked-part recovery path — the issue this sprint
implements calls that out explicitly as a thing to avoid.

Move the block **verbatim** into a new `src/mbdeploy/flash.py`:

```python
def flash_hex(uid: str, hex_path: str, target_mcu: str = DEFAULT_MCU, log=None) -> int
```

`_cmd_deploy` becomes a thin call into it, with no behavior change.
`log` is a callable so a future caller (sprint 002's server) can turn
pyocd's chatter into structured lines instead of stderr text; **when
`log=None`, the function must still print to stderr** — this is a hard
constraint, not a nicety, because `test_mass_erase_failure_aborts_without_retry`
asserts `"mass erase failed"` appears on stderr with no `log` argument
passed. `_cmd_deploy` calls `flash_hex` without passing `log`, so its
current behavior is exercised through the default path.

The three existing tests that exercise this logic
(`test_flash_retries_after_mass_erase`,
`test_mass_erase_failure_aborts_without_retry`,
`test_successful_flash_skips_mass_erase`, all in
`tests/test_devices.py`) patch `subprocess.run` on the shared, global
`subprocess` module — not a name imported into `cli` — via `import
subprocess; monkeypatch.setattr(subprocess, "run", fake_run)`. That means
the same monkeypatch follows the code into `flash.py` as long as
`flash.py` also does a plain `import subprocess` and calls
`subprocess.run(...)` (not `from subprocess import run`). **These three
tests must pass completely unchanged** — that is the mechanical proof the
extraction was behavior-preserving, and the acceptance criteria below say
so explicitly rather than just "tests pass."

## Acceptance Criteria

- [x] `src/mbdeploy/flash.py` exists with
      `flash_hex(uid, hex_path, target_mcu=DEFAULT_MCU, log=None) -> int`,
      containing the flash/mass-erase-recovery/retry/reset logic moved
      verbatim (same pyocd argv construction, same stderr messages, same
      control flow) from `cli.py:336-377`.
- [x] `flash.py` does `import subprocess` and calls `subprocess.run(...)`
      directly (not `from subprocess import run`), so a test that patches
      the shared `subprocess` module's `run` attribute still intercepts
      calls made from inside `flash.py`.
- [x] `log=None` (the default) still prints every status/error line to
      stderr, exactly as `_cmd_deploy` does today. When a caller supplies
      a `log` callable, those same lines are routed through it instead
      (exact routing/format is an implementation choice — the constraint
      is that `log=None` must not go silent).
- [x] `cli._cmd_deploy` no longer contains the flash/erase/reset argv
      construction; it calls `flash.flash_hex(uid, hex_path, target_mcu)`
      and returns its result, preserving every other step of
      `_cmd_deploy` (relay guard, connection check, optional build) as-is.
- [x] `test_flash_retries_after_mass_erase`,
      `test_mass_erase_failure_aborts_without_retry`, and
      `test_successful_flash_skips_mass_erase` in `tests/test_devices.py`
      pass **with no changes to the test file**.
- [x] New `tests/test_flash.py` covers, calling `flash_hex` directly
      (not through the CLI): the exact pyocd argv used for `flash`,
      `erase --mass`, and `reset`; the mass-erase recovery path firing on
      first-flash failure and succeeding on retry; a mass-erase failure
      aborting without a retry; a successful first flash skipping erase
      entirely; and that `log=None` prints to stderr while a supplied
      `log` callable receives the same messages instead.

## Implementation Plan

**Approach**: Cut-and-paste the block at `cli.py:336-377` into
`flash.py`, parameterizing `target_mcu` (already a parameter in scope)
and adding the `log` parameter with a stderr-printing default. Introduce
a small internal helper, e.g. `_log(log, message)` that does `print(message,
file=sys.stderr)` when `log is None` else `log(message)`, and route every
existing `print(..., file=sys.stderr)` call in the moved block through it.
Keep `DEFAULT_MCU`'s definition wherever it already lives (`devices.py`)
and import it into `flash.py` rather than duplicating it.

**Files to create/modify**:
- `src/mbdeploy/flash.py` (new) — `flash_hex` and its `_log` helper.
- `src/mbdeploy/cli.py` — remove the extracted block from `_cmd_deploy`;
  replace with a call to `flash.flash_hex`; add the import.
- `tests/test_flash.py` (new) — direct tests per Acceptance Criteria.

**Testing plan**: Run `tests/test_devices.py`'s three mass-erase tests
unmodified against the new code path first — that is the regression
gate. Then add `tests/test_flash.py` with its own fake `subprocess.run`
(same shared-module patching technique) to test `flash_hex` directly,
independent of the CLI/argparse layer.

**Documentation updates**: None — this is an internal refactor with no
user-visible behavior change; nothing in README or the agent manual
describes flash.py as an implementation detail.
