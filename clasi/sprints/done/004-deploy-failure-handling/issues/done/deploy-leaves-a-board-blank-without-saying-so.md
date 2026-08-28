---
status: done
sprint: '004'
tickets:
- 004-004
---

# A failed deploy can leave a board unbootable without saying so

Reported from the field, alongside
`mass-erase-fires-on-failures-it-cannot-fix-and-wipes-working-boards`.

## What happens

A deploy hit `flash erase sector failure (0x67)`, mass-erased successfully, then
failed on `Timeout reading from probe`. The board was left blank. The only output
was:

```
Error: flash still failed after mass erase (exit 1)
```

Nothing said the board is now **empty**. The operator has to infer it from the
board not answering on the next `connect`. "The flash didn't work" and "the board
has been erased and has no firmware on it" are very different states, and the tool
reports them identically.

## Where

[`src/mbdeploy/flash.py`](../../src/mbdeploy/flash.py), `flash_hex` — the
`erase_rc == 0` then `rc != 0` path. The message describes the *operation* that
failed rather than the *state the board is in*.

## Fix

When the mass erase succeeded and the subsequent flash did not, say so explicitly
and unmissably — the board is blank and needs reflashing, and name it so the
message is actionable on a fleet:

```
Error: flash failed after mass erase (exit 1).
  *** <name> HAS BEEN ERASED and currently has no firmware. ***
  Re-run the deploy to restore it.
```

Route it through `log` like every other line, so a remote client sees it as a
`LOG`/`ERR` line too — an operator flashing over the network is *more* likely to
misread silence, not less.

Note the same wording problem exists in the mass-erase-failure branch, but that
one is benign: if the erase itself failed, the board's firmware is generally
still intact.

## Verification

- Unit: erase succeeds, reflash fails → the emitted lines say the board was erased
  and is now empty; the exit code is unchanged.
- Unit: erase fails → no such claim is made.
- The message reaches a remote client through `serve_flash`, not just local stderr.
