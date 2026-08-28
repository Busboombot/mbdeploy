---
title: Building and flashing
blurb: What deploy actually runs, how a locked nRF is recovered automatically, and why pyOCD's output is streamed.
order: 40
updated: 2026-08-27
tags: [pyocd, flash, approtect]
---

# Building and flashing

## What `deploy` does, in order

1. **Resolve the target** — enum, live port, UID, or five-letter name; or
   auto-pick the unique non-relay registry entry. See
   [The device model](/subsystems/mbdeploy/device-model/).
2. **Check the relay guard.** If the resolved board's `role` contains `RELAY` or
   `BRIDGE` and `--force-relay` was not given, stop with a non-zero exit.
3. **Confirm the board is live.** Its UID must appear in the current pyOCD probe
   list; otherwise stop.
4. **Build, if asked.** `--build` or `--clean` runs the project's build script
   first, and a failed build propagates its exit code without touching the board.
5. **Flash, recover if necessary, and reset.**

Steps 1–3 all happen before anything is built or written, so a mistargeted
command costs nothing.

## The pyOCD invocations

`mbdeploy` runs pyOCD as a subprocess through the running interpreter — never as
a bare `pyocd` on `PATH`, which a pipx install would not have:

```
<python> -m pyocd flash -t <target-mcu> --uid <uid> <hex>
<python> -m pyocd erase -t <target-mcu> --uid <uid> --mass     # only on failure
<python> -m pyocd reset -t <target-mcu> --uid <uid>
```

The final reset is not optional: freshly flashed firmware has to actually start.
Its return code is `deploy`'s exit code on the success path.

## Locked and protected boards recover automatically

A locked or protected nRF52833 — `APPROTECT` set, or a protected SoftDevice
region at address `0x0` — rejects every erase the flash algorithm attempts, so
the flash fails before it can program. The signature is a line like:

```
flash erase sector failure (address 0x00000000; result code 0x67)
```

Neither a sector erase nor a chip erase clears that state. **Only a CTRL-AP mass
erase (`ERASEALL`) does**, and it also resets `APPROTECT`. So when the first
flash fails, `deploy` handles it without being asked:

1. Print to stderr: `flash failed — attempting CTRL-AP mass erase to recover a
   locked device, then retrying.`
2. Run `pyocd erase --mass`.
3. Retry the flash **once**.

The return codes are precise, and scripts can rely on them:

| Outcome | Exit code |
|---|---|
| Mass erase itself failed | The **erase**'s return code — no blind retry follows. |
| Retried flash still failed | The **flash**'s return code. |
| Success | The **reset**'s return code (0). |

This is pinned by the test suite: a first-flash failure followed by a successful
erase produces exactly two flash invocations and an overall exit 0; an erase
failure with code 5 yields exit 5 and exactly one flash invocation; a successful
first flash never runs an erase at all.

Some firmware images re-lock the part every time they are written, so the
recovery path runs on *every* flash of that image. That is expected, and simply
makes each flash slower.

To recover a board by hand, outside `deploy`:

```bash
pyocd erase -t nrf52833 -u <uid> --mass
pyocd flash -t nrf52833 -u <uid> MICROBIT.hex
pyocd reset -t nrf52833 -u <uid>
```

## pyOCD's output is streamed, line by line

Each pyOCD subprocess is run with its stdout and stderr merged and relayed **line
by line as it arrives**, rather than captured and reported at the end. Locally
that just means you see progress. Over the network it is load-bearing:
`serve_flash` forwards every one of those lines to the client as a `LOG` line,
and the client's read timeout resets on each one.

Before this, the only things a remote client ever heard from a flash were three
fixed status messages, so a long flash was genuinely silent on the wire for its
whole duration. A real production image — about 1.2 MB of Intel HEX text,
~464 KB actually programmed — took ~48 s to flash on typical Raspberry Pi SWD
hardware (measured throughput around 14 kB/s), and up to ~78 s through the
mass-erase recovery path. Both comfortably exceeded the client's old 30-second
read timeout, so `deploy --remote` reported failure, reproducibly, on flashes
that had **succeeded** on the board.

The fix was streaming; the client's per-line read timeout was also raised to 90 s
as defence in depth, not as a substitute. Re-verified on real hardware: the same
hex, the same board, both the plain-flash and the mass-erase-recovery paths, exit
0 both times with continuous progress on stderr throughout — including a run that
took 78 s, more than 2.6× the old timeout.

Note that this is a *per-line* budget, not an overall deadline: it bounds how
long a connection may go completely silent, not how long a flash may take.

## Building

`build`, and `deploy --build` / `deploy --clean`, shell out to the project's own
build script — `./build.py` unless `--build-cmd` overrides it (on `build` only).
The flags map straight through:

| Option | Passed to the build script as | Available on |
|---|---|---|
| `--clean` | `--clean` | `build`, `deploy` (implies a build) |
| `--verbose` | `--verbose` | `build` only |
| `-j N` | `-j N` | `build`, `deploy` |
| `--build-cmd CMD` | replaces the command entirely | `build` only |

Under `deploy --remote` the build still runs **locally**, unchanged, before
anything touches the network — you flash the hex your own tree just produced.
