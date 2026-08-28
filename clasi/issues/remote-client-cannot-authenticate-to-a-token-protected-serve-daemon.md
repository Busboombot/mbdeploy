---
status: pending
---

# `--remote` clients cannot authenticate to a `serve --token` daemon

## What is wrong

`mbdeploy serve` implements token auth on both services: with `--token` or
`--token-file` set, a client must send `AUTH <token>\n` and receive `OK\n`
before anything else. The server side is complete and tested
(`server.py`, sprint 002 ticket 007; constant-time comparison via
`hmac.compare_digest`).

**No client can satisfy it.** `list`, `connect`, and `deploy` have no
`--token`/`--token-file` flag, and `remote.py` never sends an `AUTH` line.
`connect --remote` and `deploy --remote` against a token-protected daemon
therefore fail with `ERR auth required`, which `remote.py` treats as an
ordinary error string. `list --remote` is unaffected because it never
opens a socket — it reads mDNS TXT records only.

Found during sprint 003 ticket 006 (documentation), by reading the code
rather than the plan. Sprint 003's own architecture section describes
client-side token forwarding as though it shipped; it did not. No ticket
in the arc ever claimed it — tickets 004 and 005 both recorded it as out
of scope, and nothing picked it up.

## Why it matters

`--token` is one of the two controls the deployment actually relies on.
The relay guard reads `role`, which is empty on any board that has never
announced, so `is_relay()` returns `False` and the guard is inert on such
a fleet. That leaves `--no-flash` and `--token` as the real protections —
and `--token` currently makes the daemon unusable rather than protected.

Nolanet runs open on the LAN today (the stakeholder's settled decision),
so nothing is broken in the current deployment. This blocks the moment
anyone wants to lock a fleet down.

## Fix

- Add `--token SECRET` / `--token-file PATH` to `connect` and `deploy`
  (mutually exclusive, same shape as `serve`'s).
- In `remote.py`, send `AUTH <token>\n` and require `OK\n` before handing
  the socket to `console` (serial service) or sending the `FLASH` header
  (flash service).
- Surface a missing/incorrect token as a clear error, not as a protocol
  desync.
- Document it in agent manual §9.2 and §9.5, replacing the current text
  that records the limitation.

## Verification

- Unit: loopback server demanding `AUTH`, for both services; wrong token
  rejected; no token supplied against a token-demanding server produces a
  clear error.
- Real hardware: run a Nolanet node's daemon with `--token-file`, confirm
  `connect --remote` and `deploy --remote` succeed with the token and fail
  cleanly without it.

## Related

- `docs/design/specification.md` and agent manual §9.2 currently document
  the limitation; both need updating when this lands.
