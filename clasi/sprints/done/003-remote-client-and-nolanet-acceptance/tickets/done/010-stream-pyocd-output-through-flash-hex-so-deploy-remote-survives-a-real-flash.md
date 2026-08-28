---
id: '010'
title: Stream pyocd output through flash_hex so deploy --remote survives a real flash
status: done
use-cases:
- SUC-012
- SUC-014
depends-on: []
github-issue: ''
issue: mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# Stream pyocd output through flash_hex so deploy --remote survives a real flash

## Description

Ticket 009's real-hardware acceptance run found that `deploy --remote
<board> --hex <file>` **exits 1 on a real ~450 KB hex even though the
flash actually succeeds** (server-side success confirmed in
`journalctl`). Reproduced 2/2 against real Nolanet hardware (`togov` on
`loki`). Full evidence: `docs/acceptance/003-009-multi-node-acceptance.md`,
Finding 2.

Root cause, established by reading the source during acceptance:

- `src/mbdeploy/flash.py::flash_hex` runs pyocd via three fully blocking
  `subprocess.run()` calls (flash, optional mass-erase, reset) with no
  output streaming. Its `log` callback (routed to stderr when
  `log=None`, per `_log()`) therefore fires at only three fixed
  transition points and never during the actual flash — the entire
  erase/program/verify duration is silent from `log`'s point of view.
- `src/mbdeploy/remote.py`'s client-side read timeout
  (`_FLASH_READ_TIMEOUT = 30.0`, reset only on receipt of a `LOG` line)
  expires during that silence, because `serve_flash` (which drives
  `flash_hex` with its own `log` callback to emit `LOG` lines back to
  the client) has nothing to relay for long stretches.
- Confirmed as a timeout/streaming problem, not a protocol defect: a
  tiny control hex that flashes within 30s completes and exits 0 on the
  same hardware.

This defeats the issue's own stated requirement — "relay `LOG` lines to
stderr as they arrive, so a long flash shows progress rather than going
silent" — and breaks `deploy --remote`'s correctness on realistic
firmware sizes, the sprint's headline client feature. It must be fixed
before the sprint (and the 3-sprint arc) can be considered done; ticket
009 explicitly recommended the issue not be closed as fully resolved
without this follow-up.

## Preferred Fix

Make `flash_hex` **stream** pyocd's output line by line instead of
blocking on a single `subprocess.run()` per pyocd invocation:

- Replace each `subprocess.run(cmd)` call with `subprocess.Popen(cmd,
  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)` (or
  equivalent), iterating the process's stdout line by line as it
  arrives and passing each line to `_log(log, line)` — so `serve_flash`
  (which already forwards every `log(...)` call to the client as a
  `LOG` line, per `remote.py`'s own docstring) emits a steady stream of
  `LOG` lines for the whole duration of a flash, not just at the three
  fixed transition messages that exist today. This keeps `remote.py`'s
  client-side timeout meaningful, because it resets on every `LOG`
  line — a real flash's normal erase/program/verify cadence will keep
  producing them.
- Simply raising `_FLASH_READ_TIMEOUT` is the inferior fallback — it
  hides the silence rather than fixing it, and picking a "big enough"
  number is guesswork against unknown future hardware/hex-size
  combinations. Still, a sensible timeout floor on the client side is
  worth keeping as defence in depth (e.g. bump it modestly, or make it
  configurable) — but it is not a substitute for streaming, and this
  ticket's acceptance does not pass on the timeout bump alone.

## Constraints and Hazards

- **`log=None` must still print to stderr**, exactly as `_log()`
  documents today (existing callers, including local, non-remote
  `deploy`, rely on this). Streamed lines must go through the same
  `_log()` routing, not bypass it.
- **The main hazard: switching `subprocess.run()` to `Popen()` will
  break the existing test suite's monkeypatching unless handled
  deliberately.** `tests/test_flash.py` (all of `TestArgvConstruction`,
  `TestMassEraseRecovery`, `TestLogRouting`) and the three
  `TestMassEraseRecovery` tests in `tests/test_devices.py`
  (`test_flash_retries_after_mass_erase`,
  `test_mass_erase_failure_aborts_without_retry`, and the
  still-failing-after-mass-erase case) all monkeypatch module-level
  `subprocess.run` with a `fake_run(cmd, **kw)` that returns a
  fake-`returncode` object — they never call the real pyocd. A rewrite
  to `Popen` must keep all of these passing. Acceptable approaches
  include: fully switching to `Popen`-based streaming and adapting the
  test fakes to patch `subprocess.Popen` instead (a fake context
  manager/process object yielding scripted stdout lines and a
  `returncode`) where doing so is genuinely behavior-preserving (same
  argv construction, same return codes, same mass-erase-recovery
  control flow — only the process-invocation mechanics and the
  streaming of stdout change); or any other implementation that
  achieves line-by-line streaming while keeping these tests' intent
  (argv shape, retry/erase control flow, return codes, log routing)
  intact. Do not change what these tests are actually verifying — only
  adapt their patch target/fixture shape if the switch to `Popen`
  requires it.
- **Return codes must not change**: mass-erase failure returns the
  erase subprocess's return code, a flash that still fails after mass
  erase returns that flash's return code, and success returns the
  reset subprocess's return code — exactly as today's three
  `subprocess.run(...).returncode` values are threaded through
  `flash_hex`'s existing control flow.
- **Mass-erase recovery semantics must not change**: first flash fails
  → mass erase attempted → mass-erase failure aborts without retrying
  the flash → mass-erase success retries the flash once → a still-failing
  retried flash returns its own rc. This is the exact behavior
  `tests/test_devices.py::TestMassEraseRecovery` and
  `tests/test_flash.py::TestMassEraseRecovery` pin down; none of it
  changes, only how each pyocd subprocess's output is captured.

## Acceptance Criteria

- [x] `flash_hex` streams pyocd's stdout line by line (not a single
      blocking call per subprocess) and routes each line through
      `_log()`, so a caller-supplied `log` callback receives progress
      lines throughout the flash, not just at the three fixed
      transition messages.
- [x] `log=None` still prints every line — transition messages and
      streamed pyocd output alike — to stderr.
- [x] `tests/test_flash.py` (`TestArgvConstruction`,
      `TestMassEraseRecovery`, `TestLogRouting`) passes, adapted only as
      needed for the `Popen` switch, with no change to what each test
      actually verifies (argv shape, retry/erase control flow, return
      codes, log routing).
- [x] `tests/test_devices.py::TestMassEraseRecovery`'s three tests pass
      unchanged in intent (adapted only as needed for the `Popen`
      switch).
- [x] Return codes are unchanged: mass-erase failure returns the erase
      rc, a still-failing flash after mass erase returns the flash rc,
      success returns the reset rc.
- [x] `serve_flash` (server side, unchanged by this ticket) is confirmed
      to relay each `LOG` line to the client as it arrives — i.e. the
      streaming actually reaches `remote.py`'s client, not just
      `flash_hex`'s own `log` parameter in isolation. Add or extend a
      `test_server.py`/`test_flash.py` case if the current suite doesn't
      already cover a multi-line, time-spread `log` sequence being
      relayed as multiple `LOG` lines rather than coalesced or dropped.
- [x] Full automated suite passes: `uv run pytest` (or
      `.venv/bin/python -m pytest -q`).
- [x] **Re-run against real Nolanet hardware**: from this Mac, across
      the LAN, `deploy --remote togov --hex <the same ~450 KB hex that
      failed in ticket 009>` (e.g. `micropython-microbit-v2.1.1.hex`)
      against `togov` on `loki` (192.168.1.149 — genuinely silent
      firmware, nothing in use, the safe target) exits 0, with visible
      `LOG` progress lines on stderr throughout the flash (not silence
      followed by a single result line). SSH to `loki` as `jtl` with
      `~/.ssh/raspi-cluster_ed25519`. The node's installed copy at
      `~/mbdeploy` must be updated with this ticket's fix (pulled/
      redeployed and the `mbdeploy` systemd service restarted, or
      however this project's existing update mechanism works) before
      re-testing — testing against the node's stale pre-fix copy would
      not exercise the fix at all.
- [x] The re-run's result (commands, raw output/timestamps, exit code,
      confirmation that `~/mbdeploy` on `loki` was updated first) is
      recorded — either as a new section in
      `docs/acceptance/003-009-multi-node-acceptance.md` or a new
      `docs/acceptance/003-010-*.md` file — not just summarized as "it
      worked."

## Testing

- **Existing tests to run**: `uv run pytest` (full suite; must stay at
  or above ticket 009's `327 passed` baseline, with these specific
  files confirmed green: `tests/test_flash.py`,
  `tests/test_devices.py::TestMassEraseRecovery`, `tests/test_remote.py`,
  `tests/test_server.py`).
- **New tests to write**: a `test_flash.py` (or `test_server.py`) case
  proving multiple `log(...)` calls emitted over the course of one
  `flash_hex` invocation (simulating a real flash's multi-line pyocd
  output, spread across the call rather than all at once) are each
  routed individually — not batched into one call or lost — so a
  regression back to "log only fires at the three fixed transitions"
  would be caught without needing real hardware.
- **Verification command**: `uv run pytest`, then the real-hardware
  re-run against `togov`/`loki` described above.
