---
title: The remote client
blurb: --remote on list, connect, and deploy — how a board is found on the LAN, and which flags stop meaning anything.
order: 60
updated: 2026-08-27
tags: [remote, mdns, client]
---

# The remote client (`--remote`)

`--remote` is a plain flag on `list`, `connect`, and `deploy`. There is no host
argument and no client config file: the existing `target` positional is reused as
the board's **mDNS instance name**, and the client browses the LAN to find it.

```bash
mbdeploy list --remote                                  # every board advertising right now
mbdeploy connect --remote gopiv "HELLO"                 # one-shot, over _mbserial._tcp
mbdeploy connect --remote gopiv                         # interactive session
mbdeploy deploy  --remote gopiv --hex build/MICROBIT.hex
mbdeploy deploy  --remote gopiv --force-relay
```

The name to use is whatever the daemon advertised — usually the board's
five-letter name. See
[How a board is named on the network](/subsystems/mbdeploy/fleet-daemon/).

## `list --remote`

Browses both service types for 2 s, groups the results into one row per board by
their shared `uid`, and prints a table with the local-only `CONN` and `PORT`
columns replaced by `HOST`:

```
ENUM  DEVICE NAME  COMMON NAME  ROLE          HOST                     UID
1     gopiv        robot        NEZHA2        <host-address>           9906360200052820…
```

`CONN` is dropped because a board reached over mDNS is, by construction, live
right now. `PORT` is dropped because a board carries two different network ports
— one per service type — and neither is "the" right value for a single column.

A board that answers on only one of the two service types (its daemon has one
listener up, or the browse caught it mid-registration) still produces exactly one
row, filled in from whichever registrations were seen. Rows are sorted by name,
then UID, for a stable listing.

**Nothing found prints an empty table**, header and all, not `no devices found`.
That is deliberate: no boards currently advertising is an unremarkable, momentary
state on a network, not the same thing as a local board that was probed once and
should still be in the registry.

`list --remote` uses **no local registry at all** and takes no target argument.

## How a name is resolved

`connect --remote` and `deploy --remote` browse their own service type for 2 s
and match the requested name against the leading label of each advertised
instance name. A trailing zeroconf collision suffix (`gopiv (2)`) is stripped
before matching, so a renamed duplicate is still recognised as sharing its base
name.

The client never silently picks one:

- **No match** → `no board named '<name>' found advertising <service type>`.
  Possibly the board is not there; possibly its daemon has not finished
  registering.
- **Two or more matches** → an error listing every candidate's host and port
  rather than guessing. The same command must never be able to hit two different
  boards.

## `connect --remote`

Resolves against `_mbserial._tcp`, opens a TCP connection, and then behaves
exactly like a local `connect` — the same one-shot and interactive modes, the
same reply bounding, the same exit codes. A socket is substituted for the serial
port; nothing else changes.

Immediately after connecting, the client peeks (without consuming) for about
0.3 s to see whether the daemon sent an immediate `ERR …` line — `ERR busy` from
a second client racing an already-claimed board, or `ERR auth required`. If it
did, the client reports it and exits 1. Otherwise the bytes stay in the stream
for the session to read, because the relay is unframed and a real board's first
bytes are not the client's to swallow.

`--timeout` doubles as the connect timeout here, as well as bounding the reply.

## `deploy --remote`

1. `--build`/`--clean` run **locally and unchanged**, before anything touches the
   network — you flash the hex your own tree just produced.
2. The hex is read and hashed client-side.
3. `FLASH <nbytes> sha256=<hex>[ force-relay]` goes out; the server answers
   `OK send`; the payload follows.
4. Every `LOG` line the daemon relays is printed to stderr as it arrives, so a
   multi-second flash shows continuous progress rather than silence followed by a
   verdict.
5. `OK flashed` → exit 0. Any `ERR …` → the message on stderr, exit 1.

The client's read timeout is **per line**, not an overall deadline: it bounds how
long the connection may go completely silent, not how long the flash may take.
See [Building and flashing](/subsystems/mbdeploy/flashing/) for why that
distinction was expensive to learn.

There is **no local registry lookup, no local relay guard, and no local
live-probe confirmation** on this path — the client has no registry for a remote
board. The relay guard still applies, enforced by the daemon, which is the only
side that knows the board's `role`.

## Flags that are ignored rather than rejected

| Flag | Under | Why it does nothing |
|---|---|---|
| `--baud N` | `connect --remote` | The daemon already has the board's local port open, at the fixed baud it started with. There is nothing for a client to change. |
| `--fast` | `list --remote` | `list --remote` never reads a board name over SWD — names come from mDNS, not a debug probe — so there is no read to skip. |
| `--target-mcu MCU` | `list --remote` | Same reason: it only ever fed that SWD read. |
| `--target-mcu MCU` | `deploy --remote` | Accepted for symmetry with the local path, but never sent on the wire. The daemon flashes with its own `--target-mcu`. |

## Rejected combinations

Both are checked before any mDNS lookup or socket I/O:

```
$ mbdeploy connect --remote /dev/ttyACM0
Error: --remote cannot be combined with a device path ('/dev/ttyACM0').

$ mbdeploy deploy --remote
Error: --remote requires a target board name -- unlike local deploy, there is
no local registry of remote boards to auto-pick from.
```

`--remote` names a board on the network; a `/dev/…` path names a local device.
Combining them cannot mean anything coherent.

## A silent board answers nothing — that is not a `--remote` bug

A board running no announcing firmware answers nothing on its serial port, so
`mbdeploy connect --remote <name> "HELLO"` gets no reply and exits 1. A local
`connect` to the same board behaves identically. Its `role` and `common_name`
stay empty for the same reason.

The mDNS instance name still works, because it comes from the SWD name read
rather than any firmware announcement — so `list --remote` and `deploy --remote`
are fully usable against a fleet that never says a word. Use `INFO` on the flash
service to confirm such a board is present and connected without expecting it to
talk.

## Authentication is not available to clients

Against a daemon started with `--token`/`--token-file`, `connect --remote` and
`deploy --remote` fail immediately with `auth required`. There is no client-side
`--token` flag and no `AUTH` line is ever sent. `list --remote` is unaffected —
it never opens a socket. This is a known gap, recorded in
[Open tasks](/subsystems/mbdeploy/) and on
[The fleet daemon](/subsystems/mbdeploy/fleet-daemon/).
