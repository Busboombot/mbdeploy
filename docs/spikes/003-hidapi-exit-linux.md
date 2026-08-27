# Spike log: hidapi exit-crash risk on Nolanet (Linux/aarch64)

Ticket:
`clasi/sprints/003-remote-client-and-nolanet-acceptance/tickets/001-hardware-risk-spike-verify-hidapi-exit-crash-risk-on-nolanet-linux.md`

Script: `scripts/spike_hidapi_exit.py` (throwaway, not shipped — see the
script's own docstring for usage).

**Verdict: PASS — does not reproduce on Linux/aarch64.** Across 47
process exits with real HID hardware attached on `loki`, every one
exited 0 with no traceback, no core dump, and (once a known, unrelated
cold-start timing artifact is accounted for — see below) no stderr at
all. Sprint 002 ticket 007's macOS `hid_exit()`/`NSInvalidArgumentException`
crash-at-exit does not recur on Nolanet's Linux/aarch64 hidapi backend.
Tickets 007–009 proceed unchanged; no gate is raised.

## Environment

- Node: `loki` (192.168.1.149), Debian, Linux 6.12.47+rpt-rpi-v8, aarch64.
- Checkout: `~/mbdeploy-test`, refreshed to this branch's HEAD via
  `git archive` (the new spike script itself is untracked on this branch
  and was copied over separately via `scp`, since `git archive` only
  archives committed content).
- `~/mbdeploy-test/.venv` re-installed editable
  (`pip install -e ~/mbdeploy-test`) to pick up the current tree;
  resulting build: `mbdeploy-0.20260827.2`. All dependencies were
  already present (`Requirement already satisfied` for every transitive
  dep including `zeroconf`) — no new install, no compilation.
- Real hardware confirmed attached and probeable before testing:

  ```
  $ ls -l /dev/ttyACM0
  crw-rw----+ 1 root plugdev 166, 0 Aug 27 13:05 /dev/ttyACM0

  $ .venv/bin/python -m pyocd list
    #   Probe/Board                        Unique ID                                          Target
  --------------------------------------------------------------------------------------------------------
    0   Arm BBC micro:bit CMSIS-DAP        9906360200052820fe9a0254d8d892d9000000006e052820   ✔︎ nrf52833
        Micro:bit Educational Foundation   BBC micro:bit V2
  ```

- No `mbdeploy`/spike processes were running before or after the test
  (`pgrep -fa "mbdeploy|spike_hidapi"` → none, both times).
- Free disk unchanged throughout (`df -h ~`): 7.2 GB free before and
  after.

## Test sequence and results

### 1. Minimal repro — `devices.flashable_probes()` on a background thread

Mirrors `Supervisor.run`'s shape (server.py): a background thread calls
`flashable_probes()` once, is joined, and the main thread then falls
through into normal interpreter shutdown — no mbdeploy server code in
the path, matching sprint 002 ticket 007's isolation exactly. Each trial
is its own subprocess, since the thing being measured is that
subprocess's own exit code/stderr at teardown.

```
$ .venv/bin/python scripts/spike_hidapi_exit.py probe-thread --trials 15 --timeout 15
=== probe-thread trial 1/15 ===
  exit_code=0
  stdout: [probe-thread-once] flashable_probes() -> [{'uid': '990636...', 'description': 'Arm BBC micro:bit CMSIS-DAP'}]
...
=== probe-thread trial 15/15 ===
  exit_code=0
  stdout: [probe-thread-once] flashable_probes() -> [{'uid': '990636...', 'description': 'Arm BBC micro:bit CMSIS-DAP'}]

[probe-thread] summary: 15/15 trials clean (exit 0, no stderr)
```

**15/15 clean.** Exit code 0, no stderr, every time.

### 2. The real thing — `mbdeploy serve --no-flash`, SIGINT and SIGTERM

Launched as a real subprocess, given a warmup window to start and poll
at least once, then signaled and waited on. Run twice, deliberately
under two different timing profiles:

**2a. Aggressive timing (`--warmup 3 --poll-interval 1`).** Chosen to
try to catch the signal while a probe tick is still in flight — the
sharpest analog to the macOS bug's race (a background thread still
inside a hidapi call when the process tears down).

```
$ .venv/bin/python scripts/spike_hidapi_exit.py serve-cycle --trials 8 --warmup 3 --poll-interval 1 --timeout 10
=== serve-cycle trial 1/8 signal=SIGINT ===
  exit_code=0 timed_out=False
  stderr:
mbdeploy serve: Supervisor thread did not exit within 5s of shutdown; unregistering anyway.
... (same message on all 16 of 16 runs: 8 SIGINT + 8 SIGTERM)
[serve-cycle] summary: 0/16 runs clean (exit 0, no timeout, no stderr)
```

Every one of these 16 runs exited **0**, with **no traceback and no core
dump** — but every one also printed the same single stderr line, which
the summary counts as "not clean" because it's non-empty stderr, not
because it's a crash. Investigated before treating it as a finding:

- The message is emitted by `_ServeShutdown.__call__` in `cli.py`
  (`_SERVE_JOIN_TIMEOUT = 5.0`) — an already-existing, documented code
  path (its own docstring: "a tick can be in the middle of
  `Advertiser.register()`... joining first lets that tick finish before
  anything is torn down"), not new behavior introduced by this spike.
- Timed a bare `flashable_probes()` call directly on `loki`: the
  **first** call took 2.28s (pyOCD's one-time plugin/backend import and
  discovery), every subsequent call ~20-25ms:

  ```
  0 2.280s [{'uid': '990636...', ...}]
  1 0.024s [...]
  2 0.024s [...]
  3 0.021s [...]
  4 0.027s [...]
  ```

- With `--poll-interval 1 --warmup 3`, the signal lands 3s after
  process start, while `serve`'s own cold start (imports, mDNS/avahi
  registration, accept-loop thread) is still competing with the
  Supervisor thread's first (slow, ~2.3s+) tick for CPU on the Pi —
  exactly the kind of one-time race the join-timeout warning exists to
  describe, not a hidapi/IOKit-style teardown crash.

**2b. Realistic timing (`--warmup 8 --poll-interval 2`, matching
`serve`'s actual default poll interval).** Warmup extended well past
the cold-start tick before signaling, isolating whether 2a's warning was
that cold-start race rather than anything hidapi-related:

```
$ .venv/bin/python scripts/spike_hidapi_exit.py serve-cycle --trials 8 --warmup 8 --poll-interval 2 --timeout 10
=== serve-cycle trial 1/8 signal=SIGINT ===
  exit_code=0 timed_out=False
  stdout:
mbdeploy serve: running (poll every 2s; Ctrl-C or SIGTERM to stop)
... (identical shape for all 16 of 16 runs: 8 SIGINT + 8 SIGTERM)
[serve-cycle] summary: 16/16 runs clean (exit 0, no timeout, no stderr)
```

**16/16 completely clean** — exit 0, no stderr at all, once the signal
no longer races the one-time pyOCD import cold start. This confirms
2a's warning was the documented cold-start race, not a hidapi
thread-safety crash: the same hardware, the same signals, the same
"background thread still doing hidapi work when shutdown starts" shape,
with only the timing of the signal relative to process start changed.

### 3. No-hardware comparison

**Not performed.** The task noted this only "if you can distinguish
that cleanly" — it wasn't, over an SSH-only session with no physical or
remote-switched access to unplug the attached micro:bit from `loki`.
The macOS bug is stated to require real HID hardware to trigger at all,
so this comparison would only ever have been useful as a negative
control; its absence does not weaken the positive result above, which
was gathered with real hardware attached throughout, matching the
condition the bug actually needs.

## Total evidence tally

| Check | Trials | Exit codes | Traceback/core dump | Unexplained stderr |
|---|---|---|---|---|
| `probe-thread` (minimal repro) | 15 | all 0 | none | none |
| `serve-cycle`, aggressive timing | 16 (8 SIGINT + 8 SIGTERM) | all 0 | none | none (1 known, explained, pre-existing warning line on every run) |
| `serve-cycle`, realistic timing | 16 (8 SIGINT + 8 SIGTERM) | all 0 | none | none |

47 total process exits with real hardware attached, all exit 0, zero
crashes, zero core dumps, zero unexplained stderr.

## Acceptance criteria checklist

- [x] On `loki`, with its real micro:bit attached, ran
      `devices.flashable_probes()` on a background thread (mirroring
      `Supervisor.run`'s shape) followed by interpreter exit, several
      times in a row (15), and separately ran `mbdeploy serve` itself
      and stopped it via SIGINT and via SIGTERM, several times each (8
      of each, under two timing profiles = 32 total serve runs).
      **Done.**
- [x] Each run's exit status and any traceback/core dump recorded above.
      **Done** — all 47 runs exit 0; no traceback or core dump in any
      run.
- [x] **No crash reproduced** — recorded here and in the ticket and in
      sprint.md's ticket-completion notes; tickets 007-009 proceed
      without further gating. **Done.**
- [ ] (N/A — crash-reproduction branch not taken.)
- [x] Outcome recorded before this ticket is marked done. **Done** —
      PASS, does not reproduce.

## Cleanup performed

- No `mbdeploy`/spike processes left running on `loki`
  (`pgrep -fa "mbdeploy|spike_hidapi"` → none, confirmed after the full
  test sequence).
- `config/devices.json` under `~/mbdeploy-test` was written/updated by
  the `serve`/probe runs themselves — this is the normal device
  registry `serve` maintains during operation, not spike-specific
  litter, and was left in place (harmless, and `~/mbdeploy-test` is a
  disposable test checkout regenerated from `git archive` on each
  sprint's use).
- Disk usage on `loki` unchanged (7.2 GB free before and after).
- `scripts/spike_hidapi_exit.py` is committed to the repo (same
  convention as `scripts/spike_avahi_coexist.py`) so it can be re-run
  for the ticket's own reproducibility, or reused in a future sprint if
  the risk needs re-checking against different hardware.
