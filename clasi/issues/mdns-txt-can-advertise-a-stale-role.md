---
status: pending
---

# mDNS TXT can advertise a board's stale role after its firmware changes

Found while investigating a field report about `list --remote`'s ROLE column.

## What happens

The registry deliberately **preserves** announcement fields when a probe yields
nothing — a documented invariant, and the right behaviour for a board whose port
is momentarily busy. But `serve` publishes those same fields as mDNS TXT records,
so a board that has been erased or reflashed keeps advertising its *previous*
`role` and `common_name` until something successfully re-probes it.

Observed: a board was wiped, and `list --remote` continued to show
`role=NEZHA2, common_name=robot` from the node's retained registry entry.

## Why it matters

`role` is not cosmetic — `is_relay()` reads it, and it is the field the flash-side
relay guard consults. A stale `role` means the guard can act on an identity the
board no longer has. It is a weaker version of the already-documented problem that
the guard is inert on boards which never announced.

It also undercuts `list --remote` as a diagnostic: it reads as live fleet state,
but an identity field can be arbitrarily old.

## Fix — needs a judgement call, not just code

Options, roughly in order of appeal:

1. **Mark staleness in the TXT** — publish an `identity_probed_at` (or a
   `stale=true` flag when the most recent probe failed to re-read the
   announcement) and have `list --remote` show it. Keeps the preserve-on-failure
   invariant, stops the data from silently masquerading as current.
2. **Re-probe on a successful flash.** A flash is the one moment the tool *knows*
   the board's firmware changed; refreshing identity right after is cheap and
   targeted.
3. Leave it, and document the caveat where the relay guard is discussed.

Do not simply clear the fields on a failed probe — that breaks the registry
invariant for the common busy-port case and would make the listing worse.

## Related

- Issue: `remote-client-cannot-authenticate-to-a-token-protected-serve-daemon`
  (the other reason the guard is weaker than it looks)
