# Acceptance log: real-hardware regression run for sprint 004

Ticket:
`clasi/sprints/004-deploy-failure-handling/tickets/005-real-hardware-acceptance-truncated-hex-over-deploy-remote-on-a-spare-board.md`

Run from this Mac (`.venv/bin/mbdeploy`, `mbdeploy 0.20260827.4`, branch
`sprint/004-deploy-failure-handling`), across the real LAN, 2026-08-27,
~20:59-21:10 PDT.

**Verdict: the sprint 004 fix is shipped and running on `loki`, and the
repo's own 344-test unit suite (which exercises every branch of the new
`flash_hex` control flow, including the mass-erase gate, against a
faked `subprocess.Popen`) passes clean — but the live truncated-hex
regression against real hardware could NOT be executed this session.**
`togov`, the designated spare board, is not physically connected to
`loki` or to any node on the fleet right now; the only micro:bit
anywhere on the LAN is `tovez` (a NEZHA2 robot), which the dispatch
explicitly forbids using as a test target. This is a hardware
availability gap, not a code finding — it is reported plainly rather
than substituted or faked.

## Environment

| Node | Board present | mDNS identity | Daemon |
|---|---|---|---|
| loki (.149) | `tovez` (NEZHA2 robot — off-limits) | role `NEZHA2`, common_name "robot" | active, enabled, sprint 004 code |
| hodr (.148) | none plugged in | n/a | not checked (out of scope for this ticket) |
| meili (.150) | none plugged in | n/a | not checked (out of scope for this ticket) |
| magni (.147) | none plugged in | n/a | not checked (out of scope for this ticket) |

`togov` was present on `loki` earlier the same day, per
`docs/acceptance/003-009-multi-node-acceptance.md` — it has since been
unplugged or swapped by someone with physical access to the lab. No
non-robot spare exists anywhere on the fleet at the time of this run.

## Step-by-step results

### 0. Repo test baseline

```
$ .venv/bin/python -m pytest -q
........................................................................ [ 20%]
........................................................................ [ 41%]
........................................................................ [ 62%]
........................................................................ [ 83%]
........................................................                 [100%]
344 passed in 9.84s
```

PASS — matches the sprint's documented 344-test baseline exactly. No
source changes were made in this ticket, so this is confirmation, not
a new result.

### 1. Ship the branch to `loki` and restart the daemon

```
$ git archive --format=tar HEAD | ssh -i <key> jtl@loki 'tar -x -C ~/mbdeploy'
$ ssh -i <key> jtl@loki 'grep -n "_looks_transient\|_looks_locked\|_validate_hex\|board_name" ~/mbdeploy/src/mbdeploy/flash.py | head -10'
38:def _validate_hex(hex_path: str) -> str | None:
87:def _looks_transient(output: str) -> bool:
116:def _looks_locked(output: str) -> bool:
171:    board_name: str | None = None,
189:    (:func:`_validate_hex`). A missing, unreadable, or malformed hex file
196:    :func:`_looks_transient`) is retried exactly once, logged visibly,
202:    *locked* (:func:`_looks_locked`) -- a `0x67` sector-erase failure, or
215:    ``board_name`` (falling back to ``uid`` when not given), so a remote
219:    hex_error = _validate_hex(hex_path)
$ ssh -i <key> jtl@loki '~/mbdeploy/.venv/bin/pip install --quiet --no-cache-dir ~/mbdeploy'
$ ssh -i <key> jtl@loki 'sudo systemctl restart mbdeploy'
$ ssh -i <key> jtl@loki 'systemctl is-active mbdeploy; systemctl is-enabled mbdeploy'
active
enabled
$ ssh -i <key> jtl@loki 'journalctl -u mbdeploy -n 15 --no-pager'
Aug 27 21:02:22 loki systemd[1]: Stopping mbdeploy.service ...
Aug 27 21:02:23 loki systemd[1]: mbdeploy.service: Deactivated successfully.
Aug 27 21:02:23 loki systemd[1]: Stopped mbdeploy.service ...
Aug 27 21:02:23 loki systemd[1]: Started mbdeploy.service ...
Aug 27 21:02:24 loki python3[51162]: mbdeploy serve: running (poll every 2s; Ctrl-C or SIGTERM to stop)
```

Version match confirmed both sides:

```
$ .venv/bin/mbdeploy --version
mbdeploy 0.20260827.4
$ ssh -i <key> jtl@loki '~/mbdeploy/.venv/bin/pip show mbdeploy | head -2'
Name: mbdeploy
Version: 0.20260827.4
```

PASS — `loki` is running the exact sprint 004 code (all four fix
functions present in the deployed `flash.py`), daemon restarted
cleanly, no errors in the journal.

### 2. Baseline: confirm the target board and its behavior

```
$ .venv/bin/mbdeploy list --remote
ENUM  DEVICE NAME  COMMON NAME  ROLE          HOST                     UID
--------------------------------------------------------------------------
2     tovez        robot        NEZHA2        192.168.1.149            9906360200052820a8fdb5e413abb276000000006e052820
```

**`togov` does not appear.** Only `tovez` is listed, on `loki`'s own
address. Confirmed this is not a discovery/caching artifact:

```
$ ( dns-sd -B _mbserial._tcp local. & DNSPID=$!; sleep 8; kill $DNSPID )
Browsing for _mbserial._tcp.local.
21:03:32.396  Add        3   6 local.               _mbserial._tcp.      tovez
21:03:32.396  Add        2  12 local.               _mbserial._tcp.      tovez
```

Confirmed at the USB layer directly on `loki` (bypasses mbdeploy
entirely):

```
$ ssh -i <key> jtl@loki 'lsusb; ls /dev/ttyACM*'
Bus 001 Device 006: ID 0d28:0204 NXP ARM mbed
Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub
Bus 001 Device 002: ID 0424:9514 Microchip Technology, Inc. ... Hub
Bus 001 Device 003: ID 0424:ec00 Microchip Technology, Inc. ... Fast Ethernet Adapter
/dev/ttyACM1
```

Exactly one micro:bit-class device is attached to `loki`, and it is
the one mbdeploy correctly identifies as `tovez`. Checked the rest of
the fleet too, in case `togov` had moved to a different node:

```
$ for h in hodr meili magni; do ssh -i <key> jtl@$h 'lsusb; ls /dev/ttyACM* 2>&1'; done
(each host: only the built-in USB hub/ethernet adapter, no micro:bit,
 "ls: cannot access '/dev/ttyACM*': No such file or directory")
```

**BLOCKED.** `togov` is not present anywhere on the fleet. The only
board present anywhere is `tovez`, a NEZHA2 robot, which the dispatch
explicitly forbids using as a test target ("Do NOT use a NEZHA2 robot
as a test target ... `tovez` on loki are robots — leave them alone").
No baseline behavior for `togov` could be captured this session because
`togov` itself is absent.

### 3. The regression test that matters: truncated hex over `deploy --remote`

A malformed hex was prepared locally by truncating a real MicroPython
hex mid-record, with no EOF record — reproducing the field-report
scenario (an incomplete/corrupted file) rather than a hand-crafted
error:

```
$ head -c 500000 micropython-microbit-v2.1.1.hex > truncated.hex
$ wc -c truncated.hex
500000 truncated.hex
$ .venv/bin/python -c "
import intelhex
try:
    intelhex.IntelHex().loadhex('truncated.hex')
    print('UNEXPECTED: parsed OK')
except Exception as e:
    print(f'{type(e).__name__}: {e}')
"
HexRecordError: Hex file contains invalid record at line 11365
```

This confirms `truncated.hex` fails exactly the same `intelhex`
validation `flash.py::_validate_hex` performs — the same library, same
call (`IntelHex().loadhex(path)`), so the pre-flight check that ships
on `loki` will reject this file before constructing any pyocd command.

**Could not be run against `togov` over `deploy --remote`** — see the
Blocker above; there is no `togov` to target. Running it against
`tovez` instead was not done, per the dispatch's explicit instruction
not to use a robot as a test subject.

**Recorded as BLOCKED, not skipped or faked.** The client-side
behavior (`mbdeploy deploy --remote togov --hex truncated.hex` failing
fast, with no `erase --mass` in `loki`'s `journalctl -u mbdeploy`
output, and `togov` still answering identically afterward) is the
headline scenario this whole sprint exists to prove on real hardware,
and it remains unverified live pending `togov`'s reconnection.

### 4. Confirm the good path still works

**Not run**, for the same reason as step 3 — there is no `togov` to
flash, and `tovez` must not be used. The valid hex
(`micropython-microbit-v2.1.1.hex`, the same file sprint 003's
acceptance run used, still present locally) is ready and unchanged,
so this step needs only `togov`'s reconnection to run, not any further
preparation.

### 5. Locked-signature recovery

Per the dispatch's own guidance, this is not induced on healthy real
hardware. Covered by the unit suite only:
`tests/test_flash.py::TestMassEraseRecovery` and the corresponding
cases in `tests/test_devices.py`, both included and passing in the
344-test baseline (step 0 above). Stated plainly rather than
manufactured.

## Findings summary

1. **Blocking, environmental, not a code defect.** `togov` is not
   connected to `loki` or to any node on the fleet at the time of this
   run — confirmed independently via `mbdeploy list --remote`, a live
   `dns-sd` browse, and direct `lsusb`/`/dev/ttyACM*` checks on all
   four Raspberry Pi nodes. The only micro:bit anywhere on the LAN is
   `tovez`, a NEZHA2 robot explicitly off-limits as a test target. This
   prevented steps 2-4 (baseline, truncated-hex regression, valid-hex
   regression) from running live this session.
2. **Non-blocking, confirmed.** The fix itself is shipped and running
   correctly on `loki` (version- and source-matched), and the 344-test
   unit suite — which does exercise the mass-erase gate, the
   transient-retry path, and the blank-board message against a faked
   pyocd subprocess — passes clean.
3. **Non-blocking, confirmed offline.** The truncated hex prepared for
   step 3 does fail `intelhex` validation with the identical call
   `flash.py::_validate_hex` makes, so once `togov` is available again
   the live run needs no further preparation.
4. **No action taken against `tovez`.** No flash, erase, or reset
   command was issued to `tovez` at any point in this session; the only
   interaction with it was the passive `list --remote`/mDNS discovery
   already shown above. `loki` was left exactly as found aside from
   the daemon restart in step 1 (which does not touch board firmware).

## Overall verdict

**Not proven on real hardware in this session.** The sprint 004 code
fix is deployed, running, and passing its full unit suite, but the one
live demonstration this ticket exists to produce — a truncated hex
over `deploy --remote` against `togov` failing safely with no mass
erase, followed by a successful valid-hex flash — could not be run
because `togov` is not currently connected anywhere on the fleet, and
the only board present (`tovez`) is a robot that must not be used as a
substitute. This is recorded as a hardware-availability blocker, not a
code finding: nothing observed here contradicts the fix, but nothing
observed here proves it live either. Recommend reconnecting `togov` (or
any other non-robot board) to `loki` or any fleet node and re-running
this ticket's steps 2-4 before treating the sprint's headline
real-hardware success criterion as met. `loki` is left healthy: daemon
`active`/`enabled` running sprint 004 code, `tovez` unaffected and
still advertising normally.
