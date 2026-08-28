---
status: pending
---

# Retry the flash once on a transient probe error

Reported from the field: two genuine deploys each failed once with a probe
timeout, then succeeded unchanged on the next run — same board, same hex, same
node. The reporter notes `mbflash` on the Pi Zero shows the same characteristic
and that "always retry once" has been the right answer there too.

## Why it matters

Right now the operator retries by hand, and cannot distinguish a flaky probe from
a real fault. Worse, in combination with
`mass-erase-fires-on-failures-it-cannot-fix-and-wipes-working-boards`, a transient
`Timeout reading from probe` on the *first* flash currently triggers a mass erase
— so a glitch on a USB link can wipe a working board.

## Fix

Retry the flash once when the failure looks transient — a probe timeout, a
communication/transfer fault, a `DAPAccess` error — before declaring failure and
**before** considering any mass erase. Ordering matters:

1. flash → transient failure → **retry the flash**
2. still failing, and the signature says locked → mass erase → retry
3. otherwise → fail without erasing

Log the retry so it is visible rather than hidden ("probe timed out — retrying
once"); a board that needs a retry on every deploy is telling you something about
the hardware, and silently absorbing it hides that.

Keep it to a single retry. This is transient-glitch smoothing, not a loop that
hammers unhealthy hardware.

## Verification

- Unit: a simulated probe timeout on the first flash retries once and succeeds,
  with no mass erase anywhere in the invocation sequence.
- Unit: two consecutive transient failures give up (one retry, not a loop).
- Unit: a transient failure never reaches the mass-erase branch.
