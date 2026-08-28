---
status: pending
---

# The `mb-<uid8>` mDNS fallback name is identical for every micro:bit

## What is wrong

`server.Supervisor`'s instance-name fallback chain is `board_name` →
`device_name` → `mb-<last 8 of uid>`. The third rung does not distinguish
boards: **every micro:bit UID ends in the same eight hex characters.**

Measured across the five boards currently on Nolanet:

```
99063602000528205e042b046826389c000000006e052820   gitev
9906360200052820312bde85515a72e6000000006e052820   (fallback)
99063602000528203b43773cab0210ea000000006e052820   tigez
9906360200052820fe9a0254d8d892d9000000006e052820   togov
99063602000528202e78ea8f7143163f000000006e052820   vevav
```

Last 8 for all five: `6e052820`. It is a DAPLink product/firmware suffix,
not a per-board value. The per-board entropy lives in the middle — UID
characters 16–32 (`5e042b04…`, `312bde85…`, `3b43773c…`, `fe9a0254…`,
`2e78ea8f…`).

Observed live: `mbdeploy list --remote` shows a board on `hodr` advertising
as `mb-6e052820`. A second nameless board would advertise as the *same*
name; zeroconf would rename one to `mb-6e052820 (2)`, and across two hosts
they would be indistinguishable.

## Why it matters

The fallback exists precisely for the boards that are hardest to identify —
one whose SWD name read failed and which has no announcing firmware. Those
are exactly the boards an operator most needs to tell apart, and the
fallback currently gives them all one name. Since the board name **is** the
network address, a colliding name means `connect --remote` and
`deploy --remote` cannot reliably reach a specific board.

Not hypothetical: the second board on `hodr` (`/dev/ttyACM1`,
UID `…312bde85515a72e6…`) is in this state right now. `mbdeploy list` on
that node shows a blank DEVICE NAME for it, so the SWD read is failing
there too — worth understanding as part of this.

## Fix

Use a slice of the UID that actually varies. UID characters 16–24 give
`5e042b04`, `312bde85`, `3b43773c`, `fe9a0254`, `2e78ea8f` — distinct
across the fleet. Do not assume a fixed offset without checking: derive it,
or hash the whole UID and take a short digest, which is robust to a
different DAPLink layout.

Also worth doing:
- Log a warning when the fallback fires. It means a board could not be
  named by either route, which is a condition an operator should see.
- Consider why the SWD read fails for the `hodr` board — if the probe is
  busy or the target is locked, `read_device_id` returns `None` and the
  board silently falls back.

## Verification

- Unit: two probes whose UIDs differ only outside the last 8 characters
  must produce two different instance names.
- Real hardware: with two nameless boards on one node, confirm two
  distinct advertisements and that `connect --remote <name>` reaches the
  intended one.

## Related

- `server.py` `_instance_name`; sprint 002 ticket 006 built the fallback
  chain, and its tests covered the three-way fallback but used synthetic
  UIDs that differed in the last 8 characters, so the collision was not
  visible.
