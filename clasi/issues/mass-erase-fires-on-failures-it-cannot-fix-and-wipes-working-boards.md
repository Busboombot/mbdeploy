---
status: pending
---

# Mass-erase recovery fires on failures it cannot fix, wiping working boards

**Severity: high — this destroys firmware on a healthy board in response to a
client-side mistake.** Reported from the field, reproduced deliberately by the
reporter, and confirmed in the code.

## What happens

`flash.flash_hex` gates its CTRL-AP mass erase on `if rc != 0` — *any* non-zero
exit from `pyocd flash`. A malformed hex file exits non-zero, so it triggers a
full chip erase of a working board:

```
C Error: Hex file contains invalid record at line 1
flash failed — attempting CTRL-AP mass erase to recover a locked device, then retrying.
I Mass erasing device...
I Mass erase complete
C Error: Hex file contains invalid record at line 1
Error: flash still failed after mass erase (exit 1)
```

The erase **cannot** help here, by construction: the retry re-reads the same
invalid file and fails identically. The board is wiped to recover from a problem
that lives entirely on the operator's laptop.

## Where

[`src/mbdeploy/flash.py`](../../src/mbdeploy/flash.py), `flash_hex`:

```python
rc = _run_streamed(flash_cmd, log)
if rc != 0:            # <-- any failure at all
    ... mass erase ...
```

The comment directly above it states the correct, narrow rationale — APPROTECT or
a protected SoftDevice region making the flash algorithm's erase fail — but the
condition never checks for it.

This predates the `serve` work: sprint 001 ticket 002 moved the block out of
`cli._cmd_deploy` under an explicit "verbatim, no behaviour change" constraint,
which faithfully preserved the latent bug. It was never introduced; it was never
questioned either.

## Fix

Two independent layers, both worth having:

1. **Validate the hex before touching the board.** `deploy` already rejects a
   missing file cleanly, so the file is in hand well before any pyocd call — parse
   it (`intelhex` is already an installed pyocd dependency) and fail with a clear
   message. This removes the whole class of "operator-side problem erases
   hardware".
2. **Gate the mass erase on failures a locked device actually explains.** Match
   the signatures that mean "locked/protected": the `0x67` sector-erase failure,
   and pyocd's auth/lock/APPROTECT errors. Anything else — an invalid hex, a file
   permission error, a bad `--target-mcu` — must fail without erasing.

Default to *not* erasing when the failure is unrecognised. An unnecessary erase
destroys work; a missed recovery just means the operator runs
`pyocd erase --mass` by hand, which the manual already documents.

## Verification

- Unit: a simulated `pyocd flash` failure with an invalid-hex message must **not**
  invoke `erase --mass`; a `0x67` sector-erase failure must; an unrecognised
  failure must not.
- Unit: a malformed hex is rejected before any pyocd subprocess runs at all.
- The existing `TestMassEraseRecovery` tests must keep passing — their simulated
  failure needs to carry a lock-like signature so the recovery path still fires.
- Real hardware: feed `deploy --remote` a truncated hex against a spare board and
  confirm the board still answers afterwards.

## Related

- Issue: `deploy-leaves-a-board-blank-without-saying-so`
- Issue: `retry-once-on-transient-probe-errors`
