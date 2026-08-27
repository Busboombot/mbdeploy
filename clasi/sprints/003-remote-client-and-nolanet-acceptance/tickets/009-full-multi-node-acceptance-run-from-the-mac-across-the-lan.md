---
id: 009
title: Full multi-node acceptance run from the Mac across the LAN
status: open
use-cases: [SUC-010, SUC-011, SUC-012, SUC-013, SUC-014]
depends-on: ['008']
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

## Acceptance Criteria

- [ ] `mbdeploy list --remote` shows exactly 4 boards across 4 distinct
      hosts.
- [ ] `avahi-browse -rt _mbserial._tcp` shows a board's advertisement
      appear on plug-in and vanish on unplug.
- [ ] The raw `nc` pipe against a board's `_mbserial._tcp` port carries
      bytes both directions.
- [ ] `connect --remote togov "HELLO"` against a silent board gets no
      reply and exits 1 — recorded as the expected, correct result.
- [ ] `deploy --remote togov --hex MICROBIT.hex` flashes and exits 0.
- [ ] Unplugging a board mid-serial-session closes the client cleanly
      and its advertisement disappears.
- [ ] Flashing a board with an open serial session drops that session
      and the flash still succeeds.
- [ ] Two simultaneous serial clients to the same board: the second
      gets `ERR busy`, the first is unaffected.
- [ ] Rebooting a node brings its board back up and advertising with no
      login required.
- [ ] `journalctl -u mbdeploy` on the rebooted node shows the daemon's
      log.
- [ ] Every result above is recorded in this ticket (pass/fail plus
      brief evidence), not just summarized as "acceptance passed."

## Testing

- **Existing tests to run**: `uv run pytest` (full automated suite)
  should already be green from tickets 001-008; this ticket is the
  manual, real-hardware gate the issue's Verification section calls
  for, not a replacement for it.
- **New tests to write**: none in the automated suite — this is the
  manual acceptance run itself.
- **Verification command**: manual, from this Mac across the LAN, per
  the script above.
