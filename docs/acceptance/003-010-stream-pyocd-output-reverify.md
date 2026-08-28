# Acceptance log: re-verification of `deploy --remote` against real hardware

Ticket:
`clasi/sprints/003-remote-client-and-nolanet-acceptance/tickets/010-stream-pyocd-output-through-flash-hex-so-deploy-remote-survives-a-real-flash.md`

Follows directly on `docs/acceptance/003-009-multi-node-acceptance.md`,
Finding 2: ticket 009 found `deploy --remote togov --hex
micropython-microbit-v2.1.1.hex` (the official 1,239,726-text-byte /
463,872-raw-byte hex) exiting 1 on the client while the server-side flash
actually succeeded, reproduced 2/2, because `flash.py::flash_hex` ran
pyocd via blocking `subprocess.run()` with no output streaming, so
`remote.py`'s 30s per-`LOG`-line client read timeout expired during the
silent erase/program/verify span.

This ticket fixed it by switching `flash_hex` to `subprocess.Popen`,
streaming pyocd's combined stdout/stderr line by line through the `log`
callback (`flash.py::_run_streamed`), so `server.py::serve_flash` relays
a steady stream of `LOG` lines to the client for the whole duration of a
flash. `remote.py`'s `_FLASH_READ_TIMEOUT` was also bumped 30s -> 90s as
defence in depth, per the ticket's own guidance that this is not a
substitute for streaming.

This log records the required real-hardware re-run, from this Mac,
across the LAN, 2026-08-27.

## Node update, confirmed before testing

The node's installed copy at `~/mbdeploy` on `loki` (192.168.1.149) was
updated with this ticket's fix before any re-test, not tested against a
stale pre-fix copy:

```
$ git archive --format=tar HEAD | ssh -i /Users/eric/.ssh/raspi-cluster_ed25519 jtl@loki 'tar -x -C ~/mbdeploy && echo ARCHIVE_EXTRACTED_OK'
ARCHIVE_EXTRACTED_OK
```

Confirmed the fix actually landed in the extracted tree before
reinstalling:

```
$ ssh ... jtl@loki 'grep -n "_FLASH_READ_TIMEOUT" ~/mbdeploy/src/mbdeploy/remote.py | tail -3'
406:_FLASH_READ_TIMEOUT = 90.0
513:        sock.settimeout(_FLASH_READ_TIMEOUT)

$ ssh ... jtl@loki 'grep -n "_run_streamed" ~/mbdeploy/src/mbdeploy/flash.py | head -3'
36:def _run_streamed(cmd: list[str], log: Callable[[str], None] | None) -> int:
83:    arrives (see :func:`_run_streamed`) rather than captured and
95:    rc = _run_streamed(flash_cmd, log)
```

Reinstalled into the node's own venv and restarted the systemd unit:

```
$ ssh ... jtl@loki '.venv/bin/python -m pip install -e . --quiet'
(exit 0, no output)

$ ssh ... jtl@loki 'sudo systemctl restart mbdeploy; sleep 2; systemctl is-active mbdeploy; systemctl is-enabled mbdeploy'
active
enabled
```

`journalctl -u mbdeploy -n 15` after the restart showed a clean
start line (`mbdeploy serve: running (poll every 2s; ...)`) with no
errors, immediately followed by the two flash runs below.

## Re-run: `deploy --remote togov --hex micropython-microbit-v2.1.1.hex`

Same command, same hex file (freshly downloaded from the official
`microbit-foundation/micropython-microbit-v2` v2.1.1 GitHub release;
`wc -c` confirmed 1,239,726 bytes, matching ticket 009's evidence
exactly), same target (`togov` on `loki`, the confirmed genuinely-silent
board, nothing in use).

### Attempt 1 — direct flash (no mass-erase path this time)

```
$ time .venv/bin/mbdeploy deploy --remote togov --hex micropython-microbit-v2.1.1.hex
0003810 I Loading /tmp/mbdeploy-flash-7bztbdjk.hex [load_cmd]
0006786 I Erasing... [loader]
[---|---|---|---|---|---|---|---|---|----]
[========================================]
0019701 I Programming... [loader]
[---|---|---|---|---|---|---|---|---|----]
[========================================]
0039208 I Erased 463872 bytes (114 sectors), programmed 463872 bytes (114 pages), identical 0 bytes (0 pages) at 13.97 kB/s [loader]
.venv/bin/mbdeploy deploy --remote togov --hex   0.10s user 0.09s system 0% cpu 47.589 total
$ echo $?
0
```

**Exit 0.** Visible `LOG` progress throughout the entire ~47.6s flash —
load, erase progress bars, program progress bars, and the final
erase/program summary line all streamed to stderr live, not silence
followed by one result line. This is the same total duration class as
ticket 009's first, failing attempt (47.608s there, exit 1) — the
duration did not shrink; what changed is that the client's timeout now
resets on every one of these lines instead of expiring in the gap
between three fixed messages.

### Attempt 2 — mass-erase-recovery path, the exact failure mode from ticket 009

```
$ time .venv/bin/mbdeploy deploy --remote togov --hex micropython-microbit-v2.1.1.hex
0003788 I Loading /tmp/mbdeploy-flash-n22913cr.hex [load_cmd]
0006780 I Erasing... [loader]
[---|---|---|---|---|---|---|---|---|----]
[===========================0024797 C flash erase sector failure (address 0x00000000; result code 0x67) [__main__]
flash failed — attempting CTRL-AP mass erase to recover a locked device, then retrying.
0003929 I Mass erasing device... [eraser]
0004137 I Mass erase complete [eraser]
0003782 I Loading /tmp/mbdeploy-flash-n22913cr.hex [load_cmd]
0006762 I Erasing... [loader]
[---|---|---|---|---|---|---|---|---|----]
[========================================]
0019643 I Programming... [loader]
[---|---|---|---|---|---|---|---|---|----]
[========================================]
0039214 I Erased 463872 bytes (114 sectors), programmed 463872 bytes (114 pages), identical 0 bytes (0 pages) at 13.96 kB/s [loader]
.venv/bin/mbdeploy deploy --remote togov --hex   0.09s user 0.07s system 0% cpu 1:18.07 total
$ echo $?
0
```

**Exit 0.** This is the precise scenario ticket 009 reproduced 2/2 as a
client-side failure (the board re-locks itself on every write of this
firmware, so the mass-erase-recovery path re-triggers every time) — and
this run took **78.07 seconds total, more than 2.6x the old 30-second
timeout** — yet exited 0 with continuous `LOG` progress the entire time:
the initial erase failure, the "attempting CTRL-AP mass erase" message,
mass-erase progress, the retried load/erase/program cycle, and the final
summary line all arrived live on stderr.

**Reproduced 2/2, both exit 0** (the inverse of ticket 009's 2/2 exit-1
reproduction) — the fix holds under the exact conditions, hex, and board
that exposed the original bug, including the slower, more failure-prone
mass-erase-recovery path.

## Node left in a good state

```
$ .venv/bin/mbdeploy list --remote
ENUM  DEVICE NAME  COMMON NAME  ROLE          HOST                     UID
--------------------------------------------------------------------------
1     gitev        Sally        NAMETAG       192.168.1.150            99063602000528205e042b046826389c000000006e052820
2     tigez                                   192.168.1.148            99063602000528203b43773cab0210ea000000006e052820
1     togov                                   192.168.1.149            9906360200052820fe9a0254d8d892d9000000006e052820
1     vevav                                   192.168.1.148            99063602000528202e78ea8f7143163f000000006e052820
```

`togov` still advertising normally after both flashes. `loki`'s daemon
confirmed `active`/`enabled` post-test, with a clean `journalctl`
covering both runs (no tracebacks, no unexpected errors — only pyocd's
own progress output and the one expected recovery message). `togov` was
left running the same official MicroPython v2.1.1 firmware ticket 009
already established as its baseline for this board; no further recovery
action was needed. Neither `gitev` (meili, a NAMETAG in use) nor `tigez`
(a robot) was touched.

## Verdict

**PASS.** `deploy --remote` now completes correctly against a real,
appropriately-sized production hex on real Nolanet hardware, in both the
plain-flash and mass-erase-recovery cases, with live progress visible on
stderr throughout — closing the one gap ticket 009 left open (Finding 2)
and, with it, the full 3-sprint arc this issue tracked.
