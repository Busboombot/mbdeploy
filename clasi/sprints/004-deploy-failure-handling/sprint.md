---
id: '004'
title: Deploy failure handling
status: executing
branch: sprint/004-deploy-failure-handling
use-cases: []
issues:
- mass-erase-fires-on-failures-it-cannot-fix-and-wipes-working-boards.md
- deploy-leaves-a-board-blank-without-saying-so.md
- retry-once-on-transient-probe-errors.md
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# Sprint 004: Deploy failure handling

## Goals

Make `flash.flash_hex`'s failure handling match what its own comments already
claim: recover a genuinely locked/protected board, retry a genuinely flaky
probe, and never mass-erase a board to "fix" a problem that lives on the
operator's laptop. Say plainly when a board has actually been left blank.

## Problem

`flash_hex` (`src/mbdeploy/flash.py`) gates its CTRL-AP mass erase on
`if rc != 0` — any non-zero exit from `pyocd flash` — even though the comment
directly above the branch names a narrow rationale (APPROTECT / a protected
SoftDevice region) that the condition never actually checks for. A field
report confirms the consequence directly: a malformed hex file exits
non-zero, mass-erases a working board, then the identical retry fails
against the same bad file. A second field report shows the companion gap —
a genuine lock-recovery that mass-erased successfully and then failed to
reflash left the board blank with only `Error: flash still failed after mass
erase (exit 1)` to go on; nothing said the board was now empty. A third
report shows the same gate causing a transient USB/probe glitch to trigger
an unnecessary and destructive mass erase, when a plain retry already fixes
it in practice.

All three defects live in the same ~130-line function and interact — the
fix is one coherent change to `flash_hex`'s control flow, not three
independent patches.

## Solution

Rework `flash_hex`'s failure handling into an explicit, ordered escalation,
plus a validation layer ahead of it:

0. **Validate the hex file before touching the board at all** — parse it
   with `intelhex` (already an installed pyocd dependency) and fail with a
   clear message if it's missing, unreadable, or malformed. No pyocd
   subprocess runs at all in this case.
1. **Flash.** If it fails with a *transient* signature (probe timeout,
   communication/transfer fault, `DAPAccess` error), retry the flash once,
   logging the retry visibly.
2. **Still failing, and the signature says *locked*** (a `0x67` sector-erase
   failure, or an auth/lock/APPROTECT error) — mass erase, then retry the
   flash once.
3. **Any other failure** (invalid hex that slipped past validation, a bad
   `--target-mcu`, anything unrecognized) — fail without erasing. Default to
   *not* erasing when a signature doesn't match; an unnecessary erase
   destroys work, a missed recovery just means the operator runs
   `pyocd erase --mass` by hand.
4. **If the erase succeeded and the reflash then failed**, say explicitly
   and unmissably that the board is now blank, naming it, routed through
   `log` so a remote client sees it too.

## Success Criteria

- A malformed hex fails without any `erase --mass` appearing in the pyocd
  invocation sequence.
- A `0x67` sector-erase failure still triggers mass-erase recovery exactly
  as before.
- A transient probe timeout retries the flash once and never reaches the
  mass-erase branch.
- An erase-then-failed-reflash emits an explicit "this board is now blank"
  message through `log` (so it reaches a remote client too), naming the
  board.
- The existing 330-test suite still passes (with the specific, expected
  adaptations noted in ticket 001 and ticket 003 for tests whose simulated
  failures need a recognizable signature under the new gating).
- Real hardware: `deploy --remote` against a deliberately truncated hex on
  a spare board (`togov` on `loki`) leaves the board answering afterward.

## Scope

### In Scope

- `src/mbdeploy/flash.py`: hex validation, transient-retry, signature-gated
  mass-erase, blank-board messaging — all within `flash_hex` and its
  private helpers.
- Minimal call-site plumbing in `src/mbdeploy/cli.py` (`_cmd_deploy`) and
  `src/mbdeploy/server.py` (`serve_flash`) to pass a friendly board name
  into `flash_hex` for the blank-board message.
- Adapting `tests/test_flash.py` and
  `tests/test_devices.py::TestMassEraseRecovery` (and any other
  `test_devices.py` test that reaches the flash step) to the new
  validation and gating behavior, per the hazards below.
- One real-hardware acceptance check on a spare board.

### Out of Scope

- Any change to `_run_streamed`'s external streaming contract (the
  line-by-line relay to `log` that `serve_flash`/`remote.py`'s read timeout
  depends on) — it gets extended to also retain the text for signature
  matching, not replaced.
- Switching pyocd invocation from subprocess to pyocd's importable Python
  API (would resolve the string-matching brittleness noted below, but is a
  module redesign, not a compact fix — see Design Rationale).
- Any change to `remote.py`'s client-side missing-file check, or to the
  wire protocol between `remote.py` and `server.py::serve_flash`.
- Retrying more than once for any failure class, or looping.

## Test Strategy

Unit tests only exercise `flash_hex` (and, for the existing
`TestMassEraseRecovery` suite in `test_devices.py`, `_cmd_deploy`) against a
faked `subprocess.Popen`, per the existing pattern in both test files — no
real pyocd invocation. New tests are added per ticket for: hex validation
short-circuiting before any subprocess call; transient-signature retry with
no erase; locked-signature gating still firing; non-matching signatures
failing without erasing; and the blank-board message appearing through
`log` exactly when erase-then-reflash fails. One real-hardware step (ticket
005) verifies the headline scenario end-to-end on a spare board over
`deploy --remote`, which is the only way to confirm the fix actually
prevents wiping working hardware rather than just satisfying a mock.

## Architecture

**Sizing: Compact.** This sprint changes exactly one module
(`src/mbdeploy/flash.py`), and within it effectively one function
(`flash_hex`, plus its private helpers). No new cross-module dependency is
introduced — `flash.py` already imports nothing new in kind, only a
library (`intelhex`) already present transitively as a pyocd dependency.
No dependency-direction change. No data-model change. No new component or
file. Diagrams are omitted per the compact variant: there is nothing new
being composed between modules for a diagram to clarify — the entire
change is internal control flow inside one function.

### Architecture Overview

**What changed.** `flash.py::flash_hex` gains, in this order:

1. A pre-flight validation step (`_validate_hex`, new private helper) that
   parses the hex file with `intelhex` before constructing any pyocd
   command. A missing, unreadable, or malformed file returns immediately
   with a clear message — no `pyocd` subprocess is ever invoked for it.
2. Output capture alongside streaming. `_run_streamed` currently relays
   pyocd's combined stdout/stderr to `log` line-by-line and returns only
   the exit code, discarding the text. It now also accumulates the lines it
   already streams and returns `(rc, output_text)` instead of just `rc`.
   The line-by-line relay to `log` — the behavior `serve_flash`'s
   `LOG`-per-line wire protocol and `remote.py`'s client-side read timeout
   depend on (sprint 003 ticket 010) — is unchanged; this only adds a side
   buffer, it does not defer or batch anything already streamed.
   `_run_streamed` is a module-private helper with exactly one caller
   (`flash_hex`), so this is a same-module signature change, not a
   cross-module contract change.
3. Two named pattern lists, `_TRANSIENT_SIGNATURES` and
   `_LOCKED_SIGNATURES`, plus two small predicate helpers
   (`_looks_transient(output)`, `_looks_locked(output)`) that test
   `output_text` against them. This is the "one named, documented place"
   the patterns live, per the hazard below.
4. `flash_hex`'s control flow becomes a linear escalation driven by the
   *current* failure's signature, not a blanket `if rc != 0`:
   - flash → success → reset (unchanged fast path).
   - flash → transient signature → retry the flash once (logged
     visibly) → re-evaluate.
   - still failing → locked signature → mass erase → if erase fails,
     return its rc (unchanged) → else retry the flash once → if that still
     fails, emit the explicit blank-board message (naming the board) and
     return its rc; else reset.
   - still failing → signature matches neither list → fail immediately,
     returning that rc, without ever calling `erase --mass`.
5. `flash_hex` gains one new optional parameter, `board_name: str | None =
   None` (falls back to `uid` when not given), used only in the
   blank-board message. `cli.py::_cmd_deploy` passes
   `_device_label(entry)` (already computed for other error messages) and
   `server.py::serve_flash` passes `board.name` (already held by `Board`).
   Both are one-line additions to existing call sites — no new
   cross-module dependency, since both call sites already depend on
   `flash_hex`.

**Why.** The three defects share one root cause (a failure-blind erase
gate) and one shared fix surface (`flash_hex`'s control flow), so they are
one sprint, not three. Validation and signature-matching both live inside
`flash_hex` rather than in `cli.py`/`server.py`/`remote.py` deliberately:
`flash.py`'s own module docstring records that it was extracted from
`cli._cmd_deploy` in sprint 001 specifically so the daemon
(`server.py::serve_flash`) drives "the exact same locked-part recovery
path over the network, instead of growing a second, divergent copy of
it." Putting validation or signature logic anywhere else would recreate
exactly that divergence risk for the local-CLI and remote-serve paths.

**Impact on existing components.**
- `cli.py::_cmd_deploy` — one new argument at its existing `flash_hex`
  call site (`board_name=_device_label(entry)`); no other change.
- `server.py::serve_flash` — one new argument at its existing `flash_hex`
  call site (`board_name=board.name`); no other change.
- `remote.py` — none. It does not call `flash_hex` and its existing
  client-side missing-file check (`Path(hex_path).read_bytes()` wrapped in
  a try/except) is untouched; hex *content* validation stays server-side
  in `flash_hex` for the reason above.
- `tests/test_flash.py`, `tests/test_devices.py::TestMassEraseRecovery`,
  and any other `test_devices.py` test that reaches the flash step —
  affected, and adaptation is expected and legitimate (see Hazards
  below), not a regression. The mass-erase recovery *semantics* and the
  three existing return-code contracts (erase failure → erase's rc;
  still-failing flash → flash's rc; success → reset's rc) do not change.

**Hazards carried into the tickets:**
- *Test fixture blast radius.* `flash_hex`'s new pre-flight validation
  means any test that expects `_cmd_deploy`/`flash_hex` to reach the
  faked-`Popen` pyocd step must now supply a real, valid, on-disk hex file
  — today's tests pass a literal, non-existent path (`"MICROBIT.hex"`,
  `args.hex=None` defaulting to the same). This is confirmed by direct
  reproduction: `intelhex.IntelHex().loadhex("MICROBIT.hex")` against a
  nonexistent file raises `FileNotFoundError` immediately. Ticket 001 must
  introduce a small real (temp-file or fixture) valid hex used wherever a
  test expects the flash step to be reached, before any later ticket's
  tests can pass.
- *Signature matching is on pyocd's human-readable stdout.* `_run_streamed`
  streams lines to `log`, it does not retain them today — the capture in
  item 2 above must not change what is streamed or when, only add a side
  buffer, or it risks the exact regression ticket 010 fixed (a real flash
  going silent long enough to trip `remote.py`'s client-side read
  timeout).
- *String matching is brittle by nature.* Match narrowly against the
  concrete signatures already observed in the three field reports
  (`flash erase sector failure (0x67)`, `Timeout reading from probe`,
  `DAPAccess` errors, APPROTECT/auth/lock wording); treat anything
  unmatched as "not recoverable," never as "assume locked." Keep the
  patterns in the two named lists (item 3) so they can be corrected as
  pyocd's own wording changes, without touching the control-flow logic
  around them.
- *Existing assertions on exact message text will need updating.*
  `tests/test_flash.py`'s
  `TestMassEraseRecovery::test_flash_still_failing_after_mass_erase_returns_flash_rc`
  currently asserts the substring `"flash still failed after mass erase"`;
  the new blank-board message changes this wording (see ticket 004).
  Updating the assertion is expected; the return code it's paired with
  (`rc == 7` in that test, generally "the retried flash's own rc") must
  not change.

### Design Rationale

**Decision: default to *not* erasing when a failure signature is
unrecognized.**
- Context: the whole sprint exists because an unnecessary mass erase
  destroyed a working board. Any signature-matching scheme will
  eventually meet an error string it doesn't recognize (a pyocd version
  bump, a probe-specific error, a typo in the pattern list).
- Alternatives considered: (a) default to erasing on anything unmatched,
  as today — rejected, it's the exact bug being fixed; (b) maintain an
  allowlist of every non-locked failure instead of a denylist-style
  "unrecognized = don't erase" — rejected as strictly more maintenance for
  the same outcome, since new non-locked pyocd failures are far more
  varied than the small, stable set of locked-device signatures.
- Why this choice: an unrecognized failure that *was* actually a lock
  costs the operator one manual `pyocd erase --mass` (already documented
  in the manual per UC-009a). An unrecognized failure that *wasn't* a lock
  and gets erased anyway costs a board's firmware. The asymmetry is not
  close.
- Consequences: a future pyocd wording change that shifts a genuinely
  locked-device message outside the pattern list will present as "recovery
  stopped working," not as data loss — the safer failure mode.

**Decision: match on subprocess stdout text rather than switching
`flash_hex` to invoke pyocd's importable Python API for structured
exceptions.**
- Context: pyocd's Python API would raise typed exceptions
  (transfer/timeout/access-fault classes) instead of leaving `flash_hex`
  to pattern-match human-readable CLI output, removing the brittleness
  noted above at the source.
- Alternatives considered: rewriting `flash_hex` around pyocd's API
  instead of `subprocess` + `-m pyocd`.
- Why this choice: `flash_hex`'s subprocess-based design (including the
  `sys.executable -m pyocd` invocation and the ticket-010 streaming
  rework) is an established, deliberate module boundary from prior
  sprints, not something this compact fix is scoped to touch. Rewriting
  the invocation mechanism is a separate, larger change with its own
  review, not a defect this sprint needs to fix to meet its acceptance
  criteria.
- Consequences: the brittleness named in the Hazards section is accepted,
  not eliminated, and is mitigated only by keeping the patterns narrow,
  named, and documented in one place.

### Migration Concerns

None — additive, no data model, no schema, no public CLI flag or config
change. `flash_hex`'s existing positional/keyword parameters are
unchanged; the one new parameter (`board_name`) is optional and
appended last, so no existing caller (including any out-of-tree script
importing `flash_hex` directly) needs to change to keep compiling. Two
existing stderr/log message strings change wording (the post-mass-erase
failure message gains the blank-board notice); anything downstream
scraping exact log text for those two messages needs to tolerate the new
wording — there is no other consumer of that text within this repository
besides the tests called out in Hazards above.

## Use Cases

Compact tier — four short sprint-level use cases, each tracing to one of
the three field-reported issues (one issue splits across two SUCs since it
names two independent fixes). Three update `UC-009` ("Recover a locked or
protected board" — `docs/design/usecases.md`) in place once consolidated;
one is a new error flow shared by every deploy use case.

### SUC-001: Reject an invalid hex file before touching the board
Parent: UC-005, UC-006

- **Actor**: Operator, Agent.
- **Preconditions**: A hex path has been resolved (by CLI default, `--hex`,
  or a network payload written to a temp file server-side).
- **Main Flow**: `flash_hex` parses the file with `intelhex` before
  constructing any pyocd command; a missing, unreadable, or malformed file
  fails immediately with a clear message.
- **Postconditions**: No pyocd subprocess ran; the board's state is
  unchanged.
- **Acceptance Criteria**:
  - [ ] A malformed hex fails without any `erase --mass` (or `flash`, or
        `reset`) call appearing in the invocation sequence.
  - [ ] A missing/unreadable hex fails the same way.

### SUC-002: Retry once on a transient probe error, before any erase decision
Parent: UC-009

- **Actor**: Operator, Agent.
- **Preconditions**: Hex file is valid (SUC-001 passed). The first
  `pyocd flash` fails with a transient signature (probe timeout,
  communication/transfer fault, `DAPAccess` error).
- **Main Flow**: `flash_hex` logs the retry visibly and re-runs the flash
  once before any mass-erase consideration.
- **Postconditions**: On success, reset proceeds normally; the erase branch
  was never entered.
- **Acceptance Criteria**:
  - [ ] A simulated probe timeout on the first flash retries once and
        succeeds, with no mass erase anywhere in the invocation sequence.
  - [ ] Two consecutive transient failures give up after the one retry
        (not a loop) and proceed to signature evaluation on the second
        failure.

### SUC-003: Gate mass-erase recovery on a locked/protected signature only
Parent: UC-009

- **Actor**: Operator, Agent.
- **Preconditions**: The flash still fails after SUC-002's retry (or failed
  immediately with a non-transient signature).
- **Main Flow**: `flash_hex` inspects the failure output; a locked/protected
  signature (`0x67` sector-erase failure, auth/lock/APPROTECT wording)
  triggers mass erase and one retry, exactly as UC-009 already documents.
  Any other signature fails immediately without erasing.
- **Postconditions**: A locked board is recovered and flashed, as before.
  A board failing for an unrelated reason keeps its existing firmware.
- **Acceptance Criteria**:
  - [ ] A `0x67` sector-erase failure still triggers mass-erase recovery,
        matching UC-009's existing main flow and both existing error
        flows (erase failure → erase's rc, no retry; still-failing flash
        after erase → its rc).
  - [ ] An unrecognized failure signature never invokes `erase --mass`.

### SUC-004: Report explicitly when an erase-then-failed-reflash leaves a board blank
Parent: UC-009

- **Actor**: Operator, Agent (including a remote operator over
  `deploy --remote`).
- **Preconditions**: Mass erase succeeded (SUC-003) and the retried flash
  still failed.
- **Main Flow**: `flash_hex` emits an explicit, unmissable message stating
  the named board has been erased and has no firmware, routed through
  `log` (not printed only to local stderr) so a remote client sees it as a
  `LOG`/`ERR` line too.
- **Postconditions**: The operator knows unambiguously that the board is
  blank and needs a re-run, not merely that "flashing failed."
- **Acceptance Criteria**:
  - [ ] Erase succeeds, reflash fails → emitted lines say the board was
        erased and is now empty, and name the board; the return code is
        unchanged from today's behavior.
  - [ ] Erase itself fails → no such claim is made (firmware is generally
        still intact in that case).
  - [ ] The message reaches a remote client through `serve_flash`'s `LOG`
        line relay, not just local stderr.

## GitHub Issues

(GitHub issues linked to this sprint's tickets. Format: `owner/repo#N`.)

## Definition of Ready

Before tickets can be created, all of the following must be true:

- [ ] Sprint planning document is complete (sprint.md, including its
      Architecture and Use Cases sections)
- [ ] Architecture review passed (or skipped, for changes with no
      architectural impact)
- [ ] Stakeholder has approved the sprint plan

## Tickets

| # | Title | Depends On |
|---|-------|------------|

Tickets execute serially in the order listed.
