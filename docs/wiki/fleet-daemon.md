---
title: The fleet daemon
blurb: serve, its two mDNS services, both wire protocols verbatim, and the exclusivity rules between them.
order: 50
updated: 2026-08-27
tags: [serve, mdns, protocol, daemon]
---

# The fleet daemon (`serve`)

`mbdeploy serve` turns a host into a network-facing daemon for every micro:bit
plugged into it. A USB watcher keeps per-board state in sync with what is
actually connected, and each connected board gets two independent
mDNS-advertised TCP services — a raw serial relay and a flash-over-the-network
protocol. `list --remote`, `connect --remote`, and `deploy --remote` on another
machine can then reach that board without the client having any registry entry,
or any USB connection, for it at all.

```bash
mbdeploy serve
```

runs in the **foreground** until SIGINT or SIGTERM. It never self-daemonizes and
never writes a pidfile — running it as a background service is systemd's job,
covered on [Deploying the daemon](/subsystems/mbdeploy/deployment/). On either
signal it lets any in-flight USB poll finish, unregisters every mDNS
advertisement, and closes every listener socket before exiting. The shutdown is
idempotent: a second signal during a slow shutdown is a no-op, not a traceback.

## The USB watcher

Every `--poll-interval` seconds (default 2), the supervisor lists connected
CMSIS-DAP probes and diffs the UID set against the previous tick.

**On arrival** it:

1. probes **that board only** — never sending a stray `HELLO` into a board that
   is already connected and possibly mid-session;
2. picks the board's mDNS instance name (below);
3. binds two TCP listeners;
4. registers both mDNS services.

**On departure** it tears down whatever occupies the board — idle, a live serial
session, or a flash in flight — then unregisters both advertisements, closes both
listeners, and returns the port pair to a free list for reuse. A client
mid-session sees its connection drop; a client mid-flash sees the connection drop
with no terminal `OK`/`ERR` line at all.

The diff is computed purely from the probe list. It never takes a board's
occupancy lock, which is precisely why a board that is mid-flash or mid-session
is still detected as departed the instant it disappears from the bus, instead of
leaking its advertisement.

There is no periodic re-probing of an already-known, still-connected board. A
board's identity is refreshed on arrival, and a replug is what re-reads it.

### TCP ports

By default (`--base-port 0`) each listener binds an OS-assigned ephemeral port,
and clients find them through mDNS. Pass `--base-port N` to hand out sequential
pairs starting at N instead — useful when a firewall rule has to name the range.
Departed boards' pairs go back on a free list and are reused before the counter
advances.

`--bind ADDR` restricts both listeners, and what is advertised, to one address.
The default binds all interfaces.

## How a board is named on the network

Both of a board's services are registered under the same instance name — the
thing you pass as `target` to a `--remote` command. It is chosen once, when the
board arrives, from this chain:

1. **`board_name`** — the five-letter name read over SWD during the arrival
   probe. This is the normal path in practice, not a rare fallback: it works on a
   board that runs no announcing firmware at all.
2. **`device_name`** — from a serial announcement, used if the SWD read failed.
3. **`mb-<last 8 of uid>`** — last resort, so a board is always nameable even
   with neither of the above.

`--service-name NAME` overrides the chain entirely, for every board that process
manages. It is only meaningful on a single-board host: on a multi-board host
every board would get the same name and collide, leaving them to be told apart
by zeroconf's own `name (2)` rename.

## The two services

| Service type | Purpose | Protocol |
|---|---|---|
| `_mbserial._tcp.local.` | Raw serial pass-through | A raw byte pipe — see below |
| `_mbflash._tcp.local.` | Flash over the network, plus `INFO` | A line protocol — see below |

Each service's TXT record carries `uid`, `role`, `common_name`, `enum`, and
`port`.

> **`port` in a TXT record is the network TCP port of that specific service** —
> what a client connects to. It is never the board's local `/dev/…` path, which
> is not exposed on the wire at all. (Confusingly, `port` in an `INFO` **reply**
> *is* the local device path. They are different fields with the same name;
> see below.)

The board's short name is **not** a TXT field. It is the leading label of the
mDNS instance name.

### `_mbserial._tcp`: the serial relay

Connect to the board's serial-service port and — only if the daemon was started
with `--token`/`--token-file` — send `AUTH <token>\n` first. What you get back is
a **raw, unframed byte pipe** to the board's local serial port, opened at 115200
baud. There is no other handshake: the first bytes you see are the board's own
output, exactly as if you had run a local `connect`.

`serve` has no `--baud` flag. The daemon always opens the port the same fixed
way, regardless of who connects.

| Line the daemon may send first | When |
|---|---|
| `OK` | A correct `AUTH` line (only when a token is configured). |
| `ERR auth required` | Missing, malformed, or wrong `AUTH` line. Checked before anything else, including the busy check. Five seconds to send it. |
| `ERR busy` | Another session or a flash already occupies this board. |
| `ERR <reason>` | The board's local serial port could not be opened. |

**Exclusive, and never preempting.** A second connection while one is live gets
`ERR busy` and is dropped immediately. A serial session can never displace
anything else occupying the board — only an incoming `FLASH` can do that.

### `_mbflash._tcp`: `INFO` and `FLASH`

One command line, then — for `FLASH` — a binary payload:

```
INFO
→ OK {"uid": "...", "board_name": "gopiv", "role": null, "port": "/dev/ttyACM0", "connected": true}

FLASH <nbytes> [sha256=<hex>] [force-relay]
→ OK send
  <exactly nbytes of raw hex-file bytes>
→ LOG <pyocd output line>          (zero or more, as flashing proceeds)
→ OK flashed                       -- or, on failure --
→ ERR <reason>
```

`INFO` deliberately never touches the board's occupancy state: it answers
identically whether the board is idle, mid-session, or mid-flash. Its `port`
field is the board's **local** serial device path (or `null`), and `role` is
`null` on a board whose registry entry has none — this is the one place a client
can see a board's local path.

The two optional `FLASH` tokens may appear in either order or not at all.
`deploy --remote` always sends `sha256=<hex>`, computed over exactly the bytes it
is about to send; the daemon verifies it whenever it is present.

Every `ERR` line the flash service can send, **verbatim**, in the order the
daemon checks them:

| Line | When |
|---|---|
| `ERR auth required` | Missing or wrong `AUTH` — only checked when `--token`/`--token-file` is set. |
| `ERR unknown command` | The command line is neither `INFO` nor `FLASH`. |
| `ERR flash disabled` | `--no-flash` is set. Checked before the header is even parsed, so nothing can reach the flash path. |
| `ERR bad header` | The byte count is missing or non-numeric. |
| `ERR relay refused — send force-relay` | The board's `role` matches the relay guard and `force-relay` was not sent. |
| `ERR busy` | A flash is already in flight against this board — two flashes never race. |
| `ERR short payload` | Fewer than the declared byte count arrived within 30 s. |
| `ERR sha256 mismatch` | The declared hash does not match what arrived. |
| `ERR flash failed (exit N)` | `pyocd flash`, including its mass-erase recovery, still failed. |

`deploy --remote` maps `OK flashed` to exit 0 and any `ERR …` to exit 1,
relaying each `LOG` line to stderr as it arrives.

## Board exclusivity and preemption

A board has exactly one occupant at a time: idle, a live serial session, or an
in-flight flash.

- A `FLASH` against an **idle** board claims it. A `FLASH` against a board that
  already has a flash in flight gets `ERR busy`.
- A `FLASH` against a board with a **live serial session preempts it**. The
  daemon tears the session down — closes its socket, releases the local serial
  port — and waits up to 2 s for it to actually exit before flashing. The serial
  client simply sees its connection close; it is not told why. This is
  deliberate: a live `connect --remote` session must never be able to block a
  `deploy --remote` to the same board indefinitely.
- **Unplugging** the board tears down whatever occupies it the same way, and
  additionally unregisters both advertisements and closes both listeners.

The occupancy lock is only ever held for the handful of instructions that read or
write "who has this board" — never for the duration of a session or a flash.
That is what makes preemption possible at all: the flash swaps itself in under
the lock, releases it, and only then tears down the session it displaced.

## Access controls

| Control | What it does | Caveat |
|---|---|---|
| `--token SECRET` / `--token-file PATH` | Requires `AUTH <token>` on **both** services before anything else. Comparison is constant-time. | **No client can send it today** — see below. |
| `--no-flash` | Every `FLASH` is refused with `ERR flash disabled`, before the header is parsed. `INFO` and the serial relay still work. | The most reliable control currently available. |
| Relay guard | `FLASH` is refused for a board whose `role` names a relay or bridge, unless `force-relay` is sent. | **Inert on any board that never announced** — see below. |
| `--bind ADDR` | Binds and advertises on one address only. | Not authentication. |

### Two limitations you must not assume away

**`--remote` clients cannot authenticate.** The server side of `AUTH` is complete
and tested, but there is no `--token` flag on `list`, `connect`, or `deploy`, and
the client never sends an `AUTH` line. A daemon started with
`--token`/`--token-file` is therefore currently **unreachable** by
`connect --remote` and `deploy --remote`: both fail immediately with
`auth required`. `list --remote` still works, because it only reads mDNS TXT
records and never opens a socket — which also means the token gates neither
discovery nor the information in those TXT records.

**The relay guard reads `role`, which is empty on a board that never
announced.** `is_relay("")` and `is_relay(None)` are both `False`, so on a fleet
of boards running no announcing firmware the guard never fires and
`--force-relay` is irrelevant. This is security-relevant and stated plainly:
on such a fleet, **`--no-flash` is the actual access control**, and `--token`
would be the other one if a client could use it.

## Timeouts

| Bound | Value | Applies to |
|---|---|---|
| `AUTH` line | 5 s | Both services, when a token is configured. |
| Command line (`INFO`/`FLASH`) | 5 s | Flash service, after auth. |
| Payload arrival | 30 s | The declared `FLASH` byte count. |
| Preemption join | 2 s | Waiting for a displaced serial session to exit. |
| Shutdown join | 5 s | Waiting for the watcher thread on SIGINT/SIGTERM. |
| USB poll | 2 s | `--poll-interval`. |

## Running it for real

For systemd units, tokens on disk, group membership, and where the actual
machines are recorded, see
[Deploying the daemon](/subsystems/mbdeploy/deployment/).
