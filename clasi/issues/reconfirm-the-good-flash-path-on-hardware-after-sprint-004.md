---
status: pending
---

# Re-confirm the good flash path on real hardware after sprint 004

Sprint 004 reworked `flash_hex`'s control flow: hex validation before any pyocd call,
a transient-failure retry, mass erase gated on a locked signature, and a new
`board_name` parameter. 344 unit tests pass, and the headline regression (a malformed
hex no longer erases a board) is proven conclusively — `flash_hex` invokes pyocd zero
times for an invalid file. See `docs/acceptance/004-005-real-hardware-acceptance.md`.

**What is not yet re-confirmed on hardware: that a genuine, valid flash still works
end to end.** Sprint 003 ticket 010 proved it — a real 1.2 MB MicroPython hex over
`deploy --remote`, exit 0, with streaming `LOG` progress — but that predates these
changes.

## Blocker

No suitable board. `togov`, the designated spare, is no longer on the fleet; the only
board present is `tovez` on `loki`, a NEZHA2 robot that should not be used as a test
subject (flashing robots is mbdeploy's purpose, but not for exercising the flash path —
a failure would destroy firmware we have no copy of).

**Needs: any non-robot board — one with no announcing firmware, empty ROLE — plugged
into any fleet node.** No code change is required first: the fix is already installed
and running on `loki` at `0.20260827.4`.

## Steps once a board is available

1. `mbdeploy list --remote` — confirm the spare is advertising.
2. `mbdeploy deploy --remote <spare> --hex <valid MicroPython hex>` from a laptop on
   the LAN — expect exit 0 and continuous `LOG` progress on stderr.
3. `mbdeploy connect --remote <spare> --timeout 3 "HELLO"` afterwards to confirm the
   board came back up.
4. Re-run the truncated-hex case against the same board and confirm it is unharmed —
   the on-hardware version of the proof already established in unit form.
5. Record the output in `docs/acceptance/`.
