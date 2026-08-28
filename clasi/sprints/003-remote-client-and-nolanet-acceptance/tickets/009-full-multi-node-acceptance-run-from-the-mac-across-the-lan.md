---
id: 009
title: Full multi-node acceptance run from the Mac across the LAN
status: in-progress
use-cases:
- SUC-010
- SUC-011
- SUC-012
- SUC-013
- SUC-014
depends-on:
- 008
github-issue: ''
issue: mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# Full multi-node acceptance run from the Mac across the LAN

## Description

From this Mac, across the real LAN (not from a Nolanet node itself —
the LAN crossing is the point of the whole feature):

```
mbdeploy list --remote                     # 4 boards, 4 hosts
avahi-browse -rt _mbserial._tcp            # plug/unplug, watch appear/vanish
(echo HELLO; cat) | nc togov.local <port>  # raw pipe answers
mbdeploy connect --remote togov "HELLO"
mbdeploy deploy --remote togov --hex MICROBIT.hex
```

Then, still against real hardware:

- unplug a board mid-serial-session — client sees a clean close,
  advertisement disappears;
- flash while a serial session is open — session drops, flash succeeds;
- two clients race the same board's serial port — the second gets
  `ERR busy`;
- reboot a node and confirm its board(s) advertise again with no one
  logged in;
- `journalctl -u mbdeploy` shows the daemon's log on the rebooted node.

**All four cluster boards are silent** (no announcing firmware) — this
is expected, verified, and not to be treated as a bug when encountered
here: `connect --remote togov "HELLO"` gets **no reply and exits 1**.
The demonstrable pieces on this hardware are the raw byte pipe (`nc`),
`deploy --remote`'s flash success, and `INFO`'s JSON reply — not an
announcement round-trip. Record this explicitly in the results so a
future reader doesn't mistake documented silence for a regression.

> **Superseded before this ticket ran** (ticket 008's own finding,
> carried forward): the "all four boards are silent" premise above does
> **not** hold — `meili` (`gitev`) and `magni` (`tigez`) both announce a
> real role/common_name. Only `hodr` (`vevav`) and `loki` (`togov`)
> carry no role/common_name. `magni` is also not running the daemon at
> all (no passwordless sudo for `jtl` there) and is out of scope for
> this run — this ticket covers `hodr`, `loki`, `meili` (3 of 4).

## Results (measured 2026-08-27)

Full step-by-step evidence (commands, raw output, `journalctl`
excerpts, timestamps) is recorded in
`docs/acceptance/003-009-multi-node-acceptance.md` — this section is
the summary; that file is the record required by the last acceptance
criterion below.

**Verdict: the fleet daemon and its `--remote` client work correctly
end to end across the real LAN, on the 3 of 4 nodes actually running
it.** One genuine, reproducible finding was made and is **not** fixed
here (out of this ticket's/sprint's scope) — see Finding 2 below and
the acceptance table.

| # | Step | Result |
|---|---|---|
| 1 | `list --remote` | PASS (3 boards/3 hosts — `vevav`/hodr, `togov`/loki, `gitev`/meili; `magni` correctly absent, out of scope) |
| 2 | mDNS browse, plug/unplug | PASS by substitute (daemon stop/start on `hodr`; live `dns-sd -B` captured the `Rmv` event) |
| 3 | raw `nc` pipe | PASS (`togov` silent under every test; `gitev` replied) |
| 4 | `connect --remote gitev HELLO` | PASS — reply `DEVICE:NAMETAG:Sally:gitev:-302893805`, exit 0 |
| 5 | `connect --remote togov HELLO` | PASS — no reply, exit 1, as documented |
| 6 | `deploy --remote togov --hex ...` | **PARTIAL** — real ~450 KB hex failed client-side twice (exit 1) despite genuine server-side flash success both times; a tiny control hex succeeded (exit 0), isolating the cause to a client timeout duration bug, not the wire protocol. See Finding 2. |
| 7 | `INFO` on flash port | PASS — valid JSON |
| 8 | two-client busy race | PASS — second gets `ERR busy`, first proven unaffected |
| 9 | flash preempts serial session | PASS — timestamped: session dropped the same second the flash (exit 0) completed |
| 10 | unplug mid-session (substitute) | PASS by substitute — live telemetry stream on `vevav` closed cleanly (exit 0) the instant `hodr`'s daemon was stopped; advertisement vanished/reappeared |
| 11 | reboot survival | PASS — `hodr` rebooted, daemon back `active`/`enabled` with no login needed, board re-advertised |
| 12 | `journalctl -u mbdeploy` clean | PASS — 2 clean lines on `hodr`'s post-reboot boot, no errors anywhere else pulled |

**Finding 1 (informational, not blocking):** `vevav` (hodr) is not
behaviorally silent — it streams continuous, unprompted `x:0`/`y:0`
telemetry over serial, even though it announces no role/common_name.
`connect --remote vevav HELLO` exits 0 with a stream of that telemetry,
not "no reply, exit 1" as originally assumed for "the other silent
board." `togov` is the one board on this fleet confirmed genuinely
silent; it was used for the deploy test and the silent-board `connect`
demonstration instead.

**Finding 2 (blocking-quality, escalated, not fixed here):**
`deploy --remote`'s client-side read timeout (`remote.py`'s
`_FLASH_READ_TIMEOUT = 30.0`, reset only on a `LOG` line) is too short
for a real, appropriately-sized hex, because `flash.py::flash_hex`
never streams pyocd's own subprocess output through its `log`
callback — that callback fires at only three fixed transition points,
so a real flash's actual erase/program time (and the mass-erase-recovery
path, which this board needed on every attempt) produces long stretches
of genuine silence the client can't distinguish from a hung connection.
Reproduced 2/2 with the real ~450 KB `micropython-microbit-v2.1.1.hex`
at this hardware's ~14 kB/s measured SWD throughput (server-side flash
succeeded both times, ~7-19s after the client had already given up);
confirmed as a timeout-duration issue and not a protocol defect via a
control flash of a deliberately tiny hex, which completed within the
30s budget and exited 0. Fixing `flash.py`'s output streaming or
`remote.py`'s timeout policy is outside this ticket's/sprint's stated
scope (mirrors ticket 001's hidapi-finding escalation pattern) —
recorded and flagged for a follow-up ticket, not silently patched or
papered over. **Recommend the arc's issue not be closed as fully
resolved without that follow-up.**

Swarm discipline: `hodr`'s stop/starts (daemon-level, not `docker`) had
no effect on swarm state. `hodr`'s reboot caused ~90s of ordinary Docker
Swarm reconciliation (node briefly `Down`, `cadvisor`'s global task one
retry cycle) — confirmed fully self-healed to the ticket 007/008
baseline (`docker node ls` all 4 `Ready`, `docker service ls` matching
exactly) before this ticket concluded; no manual intervention performed
or needed. All three live nodes' daemons verified `active`/`enabled` at
the end, matching their state at the start.

Repo test suite: `327 passed` (`.venv/bin/python -m pytest -q`),
unchanged from ticket 008's baseline — no source code was touched by
this ticket.

## Acceptance Criteria

- [ ] **PARTIAL** — `mbdeploy list --remote` shows exactly 4 boards
      across 4 distinct hosts. Shows 3 of 4 (`magni` not deployed, per
      ticket 008's finding, out of scope for this ticket) — every node
      actually running the daemon is shown correctly, none missing or
      duplicated.
- [x] `avahi-browse -rt _mbserial._tcp` shows a board's advertisement
      appear on plug-in and vanish on unplug. — PASS by the necessary
      substitute (physical unplug is not possible remotely; daemon
      stop/start on `hodr` produces the same client-visible effect, per
      dispatch instruction). See Results.
- [x] The raw `nc` pipe against a board's `_mbserial._tcp` port carries
      bytes both directions. — PASS (`togov` accepts writes and stays
      silent as documented; `gitev` replies). See Results.
- [x] `connect --remote togov "HELLO"` against a silent board gets no
      reply and exits 1 — recorded as the expected, correct result. —
      PASS. See Results.
- [ ] **PARTIAL** — `deploy --remote togov --hex MICROBIT.hex` flashes
      and exits 0. A real ~450 KB hex failed client-side (exit 1) on
      both attempts despite genuine server-side flash success each
      time; a tiny control hex succeeded end to end (exit 0). See
      Finding 2 — a real client-timeout bug, not fixed in this ticket's
      scope.
- [x] Unplugging a board mid-serial-session closes the client cleanly
      and its advertisement disappears. — PASS by substitute: a live
      telemetry session on `vevav` closed cleanly (exit 0) the instant
      `hodr`'s daemon was stopped, and its advertisement vanished. See
      Results.
- [x] Flashing a board with an open serial session drops that session
      and the flash still succeeds. — PASS, timestamped. See Results.
- [x] Two simultaneous serial clients to the same board: the second
      gets `ERR busy`, the first is unaffected. — PASS. See Results.
- [x] Rebooting a node brings its board back up and advertising with no
      login required. — PASS (`hodr`). See Results.
- [x] `journalctl -u mbdeploy` on the rebooted node shows the daemon's
      log. — PASS, clean. See Results.
- [x] Every result above is recorded in this ticket (pass/fail plus
      brief evidence), not just summarized as "acceptance passed." —
      see Results table/Findings above and the full evidence log in
      `docs/acceptance/003-009-multi-node-acceptance.md`.

## Testing

- **Existing tests to run**: `uv run pytest` (full automated suite)
  should already be green from tickets 001-008; this ticket is the
  manual, real-hardware gate the issue's Verification section calls
  for, not a replacement for it.
- **New tests to write**: none in the automated suite — this is the
  manual acceptance run itself.
- **Verification command**: manual, from this Mac across the LAN, per
  the script above.
