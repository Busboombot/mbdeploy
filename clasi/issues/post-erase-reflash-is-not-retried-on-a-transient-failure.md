---
status: pending
---

# The post-erase reflash is not retried on a transient failure, leaving the board blank

Found during sprint 004's hardware acceptance on `vevav` (2026-08-28), on the
very first real flash after the sprint landed.

## What happened

```
flash erase sector failure (address 0x00000000; result code 0x67)
flash failed — attempting CTRL-AP mass erase to recover a locked device, then retrying.
Mass erasing device... / Mass erase complete
Error reading AP#0 IDR: Timeout reading from probe 9906...163f
Error: flash still failed after mass erase (exit 1) — vevav WAS ERASED AND NOW HAS
NO FIRMWARE. It will not run until it is successfully reflashed.
```

Everything sprint 004 built worked correctly here:
- the `0x67` signature is a genuine locked-device failure, so gating the mass erase
  on it was right — it fired when it should;
- the new blank-board message fired, named the board, and was accurate.

**But the board was left blank for no good reason.** Re-running the identical
command immediately afterwards succeeded and restored it. The post-erase reflash
failed on `Timeout reading from probe` — precisely the transient signature sprint
004 already recognises and retries.

## The gap

`retry-once-on-transient-probe-errors` was implemented for the **first** flash
attempt only. The reflash *after* a mass erase gets no such retry, so a transient
probe glitch at exactly that moment is unrecoverable in one command — and it is the
worst possible moment, because the erase has already happened and the board is bare.

This is the field reporter's original observation ("the first attempt fails and the
second succeeds, with no retry") reappearing one layer down.

## Fix

Apply the same transient-retry policy to the post-erase reflash: if it fails and
`_looks_transient(output)`, retry it once before declaring failure and emitting the
blank-board message.

Consider factoring the "run the flash, retrying once on a transient signature" step
into a single helper used by both call sites, so the two paths cannot drift again.

Keep it at one retry per site — this is glitch smoothing, not a loop.

## Verification

- Unit: erase succeeds, reflash fails transiently, retry succeeds → overall success,
  no blank-board message emitted.
- Unit: erase succeeds, reflash fails transiently twice → gives up, blank-board
  message emitted, return code unchanged.
- Unit: erase succeeds, reflash fails non-transiently → no retry (current behaviour).

## Related

- `retry-once-on-transient-probe-errors` (the first-flash half, shipped in sprint 004)
- `docs/acceptance/004-005-real-hardware-acceptance.md`
