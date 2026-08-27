---
id: '001'
title: 'Hardware risk spike: verify hidapi-exit crash risk on Nolanet (Linux)'
status: in-progress
use-cases:
- SUC-015
depends-on: []
github-issue: ''
issue: mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# Hardware risk spike: verify hidapi-exit crash risk on Nolanet (Linux)

## Description

Sprint 002 ticket 007 isolated a pre-existing hidapi/IOKit thread-safety
bug: `mbdeploy serve` crashes at process exit on macOS with an
`NSInvalidArgumentException` inside hidapi's `hid_exit()`, reproducible
with `devices.flashable_probes()` alone run on a background thread — no
mbdeploy server code in the path. It only triggers with real HID
hardware attached, and "may not reproduce on Linux, which uses a
different hidapi backend" — genuinely unknown, not assumed either way.

This ticket answers that question on the actual deployment target
(Nolanet, Linux/aarch64) **before** the four-node rollout (tickets
007-009) proceeds, because a crash-at-exit loop on every `systemctl
stop`/reboot would silently defeat the "survives a reboot" acceptance
criterion. `loki` already has a working checkout+venv at
`~/mbdeploy-test`, so this needs no new install — use it.

No code change is expected from this ticket in the ordinary case. This
sprint's Scope explicitly excludes changes to `server.py`/`mdns.py`
internals beyond what the client adapter needs, so this ticket does
**not** attempt a fix if the crash reproduces — see Acceptance
Criteria for what happens instead.

## Acceptance Criteria

- [x] On `loki`, with its real micro:bit attached, run
      `devices.flashable_probes()` on a background thread (mirroring
      `Supervisor.run`'s shape) followed by interpreter exit, several
      times in a row, and separately run `mbdeploy serve` itself and
      stop it via SIGINT and via SIGTERM, several times each.
      **Done** — `scripts/spike_hidapi_exit.py`, run on `loki`
      (192.168.1.149) against its attached micro:bit V2
      (`9906360200052820fe9a0254d8d892d9000000006e052820`). 15 trials of
      the minimal `flashable_probes()`-on-a-background-thread repro,
      plus 32 `mbdeploy serve --no-flash` runs (8 SIGINT + 8 SIGTERM
      under aggressive timing chosen to race an in-flight probe tick,
      then the same 16 again under realistic timing matching `serve`'s
      actual default poll interval). See
      `docs/spikes/003-hidapi-exit-linux.md`.
- [x] Each run's exit status and any traceback/core dump is recorded.
      **Done** — all 47 runs exit 0; zero tracebacks; zero core dumps.
      Full table in `docs/spikes/003-hidapi-exit-linux.md`.
- [x] **If no crash reproduces**: record this finding in the ticket and
      in sprint.md's ticket-completion notes; tickets 007-009 proceed
      without further gating.
      **Done — no crash reproduces.** PASS. Recorded here, in
      `docs/spikes/003-hidapi-exit-linux.md`, and in sprint.md's Step 7
      / Tickets section. Tickets 007-009 may proceed without further
      gating on this risk.
- [ ] **If a crash reproduces**: do NOT attempt to patch `server.py` —
      that is out of this sprint's stated Scope. Instead, record the
      exact reproduction steps, the crash signature (exception type,
      traceback, or signal), and flag it explicitly as a blocking
      finding requiring a team-lead/stakeholder decision (a
      separately-scoped fix to `server.py`'s shutdown path). Tickets
      007-009 must not proceed until that decision is made.
      **N/A** — no crash reproduced; this branch was not taken.
- [x] Either outcome is recorded before this ticket is marked done —
      "inconclusive" or "didn't get to it" is not an acceptable close.
      **Done** — PASS recorded above and in `docs/spikes/003-hidapi-exit-linux.md`.

## Testing

- **Existing tests to run**: none — this is a hardware spike, not a
  code change to the automated suite. `uv run pytest` should still pass
  unchanged on the branch (no source edits expected).
- **New tests to write**: none in the automated suite; the deliverable
  is the recorded finding (reproduces / does not reproduce) plus
  reproduction steps, written into this ticket file.
- **Verification command**: manual, on `loki` over SSH. No
  `uv run pytest` step is meaningful for this ticket's core work.
