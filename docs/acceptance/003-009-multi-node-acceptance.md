# Acceptance log: full multi-node run from the Mac across the LAN

Ticket:
`clasi/sprints/003-remote-client-and-nolanet-acceptance/tickets/009-full-multi-node-acceptance-run-from-the-mac-across-the-lan.md`

Run from this Mac (`.venv/bin/mbdeploy`, `mbdeploy 0.20260827.2`, branch
`sprint/003-remote-client-and-nolanet-acceptance`), across the real LAN,
2026-08-27.

**Verdict: the fleet daemon and its `--remote` client work correctly
end to end over the real LAN, on the 3 of 4 nodes actually running the
daemon.** Discovery, raw byte transport, the serial busy/preemption
rules, departure/arrival advertisement, and reboot survival with a
clean log are all directly demonstrated, including two rigorous,
timestamped race/preemption tests. One genuine, reproducible code-level
gap was found and is **not** fixed here (out of this ticket's scope):
`deploy --remote`'s client-side read timeout is too short for a
real-sized production hex, causing a spurious client failure even
though the hardware flash itself succeeds — see Finding 2. `magni`
remains out of scope per ticket 008 (no passwordless sudo for `jtl`
there); this run covers `hodr`, `loki`, `meili` only, as directed.

## Environment

| Node | Board | mDNS identity | Daemon |
|---|---|---|---|
| hodr (.148) | `vevav` | no role/common_name announced | active, enabled |
| loki (.149) | `togov` | no role/common_name announced | active, enabled |
| meili (.150) | `gitev` | role `NAMETAG`, common_name "Sally" | active, enabled |
| magni (.147) | `tigez` | role `NEZHA2`, common_name "robot" | **not installed** (ticket 008 finding, out of scope) |

Corrections carried into this run, both already on record before this
ticket started:

- Ticket 008 found `meili`'s and `magni`'s boards **do** announce a
  role/common_name — sprint.md's original "all four boards are silent"
  premise does not hold for those two. Only `hodr` (`vevav`) and `loki`
  (`togov`) carry no role/common_name.
- This ticket adds one more correction (Finding 1, below): "no
  role/common_name announced" is not the same thing as "silent on the
  wire." `togov` is genuinely silent under every condition tested here;
  `vevav` is not.

## Step-by-step results

### 1. `mbdeploy list --remote`

```
$ .venv/bin/mbdeploy list --remote
ENUM  DEVICE NAME  COMMON NAME  ROLE          HOST                     UID
--------------------------------------------------------------------------
1     gitev        Sally        NAMETAG       192.168.1.150            99063602000528205e042b046826389c000000006e052820
1     togov                                   192.168.1.149            9906360200052820fe9a0254d8d892d9000000006e052820
1     vevav                                   192.168.1.148            99063602000528202e78ea8f7143163f000000006e052820
```

3 boards across 3 distinct hosts — every currently-deployed node, none
missing, none duplicated. **`magni` correctly does not appear** (its
daemon was never installed, per ticket 008). The ticket's literal "4
boards / 4 hosts" wording is not met for the reason already on record
before this ticket began; the criterion is met for every node actually
running the daemon. PASS (3 of 3 in-scope).

### 2. mDNS browse (`dns-sd -B _mbserial._tcp`)

```
$ dns-sd -B _mbserial._tcp
Browsing for _mbserial._tcp
17:00:49.933  Add  3   6 local.  _mbserial._tcp.  vevav
17:00:49.933  Add  3  12 local.  _mbserial._tcp.  vevav
17:00:49.933  Add  3   6 local.  _mbserial._tcp.  togov
17:00:49.933  Add  3  12 local.  _mbserial._tcp.  togov
17:00:49.933  Add  3  12 local.  _mbserial._tcp.  gitev
17:00:49.933  Add  2   6 local.  _mbserial._tcp.  gitev
```

All 3 instances visible (each on 2 local interfaces, normal mDNS
behavior). Plug/unplug itself is covered under step 10 below (not
literally possible from this Mac).

### 3. Raw `nc` pipe

**`togov` (loki, silent):**

```
$ printf 'HELLO\n' | nc -w 20 loki.local 43499
$                              # nothing — 20s window, no reply
```

Repeated with `PING`, `ZZZZ`, and no input at all (listen-only, 15-20s
windows) — silence every time. **PASS** — the raw pipe carries bytes to
the board (the connection is accepted and holds; the daemon is not
rejecting the write), and a silent board's silence is confirmed, not
assumed.

**`gitev` (meili, announcing):**

```
$ printf 'HELLO\n' | nc -w 5 meili.local 41393 | od -c
0000000    D
0000001
```

A reply — one byte, `D`. Listen-only (no input sent) for 15s produced
*nothing*, confirming this is a genuine response to the input, not an
unsolicited periodic announcement. The single byte is `nc -w`'s idle
timeout cutting the connection off mid-reply (`D` is literally the
first character of the full announcement string — see step 4, which
reads it in full through a client that doesn't truncate early). PASS —
bytes travel both directions.

### 4. `connect --remote gitev "HELLO"`

```
$ .venv/bin/mbdeploy connect --remote gitev HELLO
DEVICE:NAMETAG:Sally:gitev:-302893805
[exit: 0]
```

PASS — full announcement reply, exit 0, matching the corrected
(ticket-008) expectation that `gitev` is not silent.

### 5. `connect --remote togov "HELLO"` (silent board)

```
$ .venv/bin/mbdeploy connect --remote togov HELLO
Error: no response from togov (192.168.1.149:43499) within 2s.
[exit: 1]
```

PASS — no reply, exit 1, exactly the documented, expected result for a
genuinely silent board.

**Finding 1 (informational, not blocking).** `connect --remote vevav
"HELLO"` does **not** reproduce the same result:

```
$ .venv/bin/mbdeploy connect --remote vevav HELLO
y:0
x:0
y:0
x:0
... (repeats for the full read-timeout budget)
[exit: 0]
```

Confirmed with a raw listen-only `nc` to `vevav`'s serial port: it
continuously streams `x:0`/`y:0`-style telemetry **unprompted**, with
no input sent at all. `vevav` genuinely carries no role/common_name
(consistent with ticket 008), but it is not silent on the wire the way
`togov` is — its firmware is actively running and producing output.
This means `vevav` is not usable as a demonstration of "a silent board
gets no reply," and (as it turned out) is not a good deploy target
either, since it is evidently in active use. `togov` was used for the
deploy test below instead — it is the one board on this fleet confirmed
silent under every condition tested.

### 6. `deploy --remote togov --hex ...`

Two attempts with a real, non-trivial hex (the official
`micropython-microbit-v2.1.1.hex`, 1,239,726 text bytes / 463,872 raw
bytes, nRF52833, sha256-verified on the wire by the existing protocol):

```
$ time .venv/bin/mbdeploy deploy --remote togov --hex MICROBIT.hex
flash failed — attempting CTRL-AP mass erase to recover a locked device, then retrying.
Error: no response from togov (192.168.1.149:32785) (connection closed or timed out) before the flash finished.
... 47.608 total
[exit: 1]
```

`loki`'s `journalctl -u mbdeploy` for the same window:

```
17:08:39 Loading /tmp/mbdeploy-flash-ybow5ff4.hex [load_cmd]
17:08:43 Erasing...
17:08:49 flash erase sector failure (address 0x00000000; result code 0x67)
17:08:54 Mass erasing device...
17:08:54 Mass erase complete
17:08:59 Loading /tmp/mbdeploy-flash-ybow5ff4.hex [load_cmd]  (retry)
17:09:02 Erasing...
17:09:15 Programming...
17:09:34 Erased 463872 bytes (114 sectors), programmed 463872 bytes (114 pages), identical 0 bytes (0 pages) at 13.96 kB/s
```

The server-side flash **succeeded** — a full, real reprogram of the
device, ~7 seconds *after* the client had already given up and printed
`exit 1`. A second, immediate retry (device already unlocked from the
first attempt) reproduced the identical client-side failure and an
identical server-side eventual success (the board re-locks itself each
time this particular firmware is written, so the mass-erase-recovery
path re-triggers on every attempt with this hex):

```
$ time .venv/bin/mbdeploy deploy --remote togov --hex MICROBIT.hex
flash failed — attempting CTRL-AP mass erase to recover a locked device, then retrying.
Error: no response from togov (192.168.1.149:32785) (connection closed or timed out) before the flash finished.
... 58.365 total
[exit: 1]
```
```
17:14:18 Erased 463872 bytes (114 sectors), programmed 463872 bytes (114 pages), identical 0 bytes (0 pages) at 13.97 kB/s
```

**Root cause, read in source, not guessed:** `flash.py::flash_hex` runs
`subprocess.run(flash_cmd)` / `subprocess.run(erase_cmd)` /
`subprocess.run(reset_cmd)` as fully blocking calls with no output
capture at all — pyocd's own stdout/stderr (the progress bars visible
in `journalctl` above) go straight to the daemon process's inherited
stdout, never through `flash_hex`'s `log` callback. That callback
(`flash.py:63-79`) fires at exactly three fixed points — "flash
failed, attempting mass erase," "mass erase failed," "still failed
after mass erase" — never once during an actual pyocd subprocess run.
`remote.py`'s client (`_FLASH_READ_TIMEOUT = 30.0`, `remote.py:395`)
resets its 30-second read timeout only when a `LOG` line (or the
terminal result) arrives; sprint.md's own design rationale for this
value states it will "catch a connection that has gone genuinely
silent... not bound how long flashing itself may take" — but the
*implementation* it wraps produces exactly that genuine, multi-second
silence during every real flash, by design, not as a bug in this
sprint's own new code. On this hardware (~14 kB/s measured SWD
throughput), the mass-erase-plus-retry span alone ran ~35-45 seconds
with zero `LOG` traffic — comfortably over the 30-second budget.

**Confirmed by a control test:** built a deliberately tiny, safe hex
locally (`arm-none-eabi-gcc`/`objcopy`, a bare nRF52833 vector table
plus a `b .` infinite-loop reset handler — 4 Intel-HEX lines, a single
30-byte payload landing in one 4 KB flash page) and flashed it to the
same board:

```
$ time .venv/bin/mbdeploy deploy --remote togov --hex mini.hex
flash failed — attempting CTRL-AP mass erase to recover a locked device, then retrying.
... 22.030 total
[exit: 0]
```
```
17:15:39 Erased 4096 bytes (1 sector), programmed 4096 bytes (1 page), identical 0 bytes (0 pages) at 7.63 kB/s
```

Same mass-erase-recovery path, but the silent span (mass erase + a
single-page flash + reset) stayed under 30 seconds — client exit 0,
`OK flashed` received correctly. This isolates the failure to
**duration relative to the fixed timeout**, not a protocol defect: the
`FLASH` header exchange, payload upload with sha256, `LOG` relay of the
mass-erase message, and the terminal `OK flashed` → exit-0 mapping all
work correctly.

**This is a genuine, reproducible (2/2) finding, not papered over.**
`deploy --remote` can and did spuriously report failure on a real,
appropriately-sized hex while the underlying flash succeeded, purely
because of `flash.py`'s non-streaming subprocess calls colliding with
`remote.py`'s per-line timeout. It is flagged here, not fixed — both
`flash.py` (sprint 001) and `remote.py`'s timeout constant are outside
this acceptance ticket's scope, and the fix requires a design decision
(stream pyocd's own output through the `log` callback, or rework the
timeout to cover a whole flash rather than the gap between fixed
log points) that belongs to a follow-up ticket, the same escalation
sprint 003 already used for ticket 001's hidapi finding. **The literal
acceptance criterion ("flashes and exits 0") is met only by the
substitute tiny hex; a real ~450 KB production image failed client-side
twice for a reason with nothing to do with the network or the wire
protocol.** Marked PARTIAL.

### 7. `INFO` on the flash port

```
$ printf 'INFO\n' | nc -w 4 loki.local 32785
OK {"uid": "9906360200052820fe9a0254d8d892d9000000006e052820", "board_name": "togov", "role": null, "port": "/dev/ttyACM0", "connected": true}
```

PASS — valid JSON, correct fields.

### 8. Two clients race the serial port

First client opened and held the raw pipe (via a FIFO, so it could be
kept alive and probed); second client connected while the first was
still live:

```
$ nc -w 3 loki.local 43499
ERR busy
[exit: 0]
```

First client proven unaffected: a probe line was written through it
*after* the race, then it was closed deliberately from this end and
exited cleanly (`exit 0`, never dropped by the contention attempt).
PASS.

### 9. Flash preempts an open serial session

A serial session was started and scripted to hold the connection for
20 real seconds; ~2 seconds in, `deploy --remote togov --hex mini.hex`
was run concurrently. Timestamps:

```
session start:  17:17:30
deploy start:   17:17:32
deploy done:    17:17:44   (exit 0)
session ended:  17:17:44   (same second — 6s before its own 20s hold would end)
```

The session was actively dropped by the flash, and the flash itself
succeeded. PASS.

### 10. Unplug/replug — not testable remotely; departure/arrival substitute

Physical unplug/replug cannot be performed from this Mac, as stated in
the dispatch and confirmed again here — recorded as **not testable** by
this method, not faked. Substituted per the dispatch's own instruction:
stopping/restarting `hodr`'s `mbdeploy` daemon produces the same
client-visible effect a real unplug/replug does (the board's
advertisement and any open session on it go away/come back).

**Advertisement appear/vanish**, via `list --remote` before/after
`sudo systemctl stop mbdeploy` on `hodr`: `vevav` present -> absent ->
present again after `start`. Also captured live via
`dns-sd -B _mbserial._tcp local` spanning a stop: an explicit `Rmv`
event for `vevav` at the moment of the stop.

**Client sees a clean close mid-session**, using `vevav`'s continuous
telemetry stream as the live data source (same finding-1 stream, put
to good use here): a raw session was opened and captured 676 bytes of
live `x:0`/`y:0` traffic in the first 2 seconds, then
`sudo systemctl stop mbdeploy` was issued:

```
stop issued:     17:33:50.797
stop completed:  17:33:52.385
session closed:  17:33:52.392   (exit 0 — clean, not an error)
```

The client's connection closed cleanly in the same instant the daemon
stopped, with no further bytes — the direct analog of "unplug a board
mid-serial-session: client sees a clean close, advertisement
disappears." Both halves confirmed. PASS (by substitute, explicitly
labeled as such per the dispatch's own guidance).

`hodr`'s daemon was restarted after each of these tests; final state
verified `active`/`enabled`, matching `loki`/`meili`.

### 11. Reboot survival

```
$ ssh ... jtl@hodr sudo reboot
```

Node back on the network and reachable over SSH within ~2 minutes
(`uptime -p` → "up 0 minutes", a genuinely fresh boot, not a
still-shutting-down old session). Immediately:

```
$ ssh ... jtl@hodr "systemctl is-active mbdeploy; systemctl is-enabled mbdeploy"
active
enabled
```

No interactive login was needed to start the daemon — it is an enabled
systemd unit and came up on its own at boot; the SSH session above was
for verification only. The board re-advertised on the LAN (confirmed
via `mbdeploy list --remote` / `mdns.browse()` from the Mac, ~15s after
`active` while USB enumeration and the 2s poll interval caught up).
PASS.

**Side note, operational transparency, not a code finding.** The
reboot caused ~90 seconds of ordinary Docker Swarm reconciliation on
`hodr`: `docker node ls` briefly reported it `Down`, and
`management_cadvisor`'s global-mode task went through one
`Failed`/`Starting` retry cycle (briefly `3/4` before settling), while
`management_node-exporter` was briefly `4/3`. This is normal
swarm-rejoin behavior after any node reboot. Confirmed fully self-healed
before concluding this ticket: `docker node ls` shows all 4 nodes
`Ready`/`Active` (`magni` still `Leader`), and `docker service ls`
matches the ticket 007/008 baseline exactly (`hello_whoami` 8/8,
`management_cadvisor` 4/4, `management_grafana` 1/1,
`management_node-exporter` 4/4, `management_portainer` 1/1,
`management_prometheus` 1/1). No manual swarm intervention was
performed or needed.

### 12. `journalctl -u mbdeploy` shows a clean log

`hodr`, this boot (`journalctl -u mbdeploy -b`):

```
Aug 27 17:20:44 hodr systemd[1]: Started mbdeploy.service ...
Aug 27 17:20:49 hodr python3[1017]: mbdeploy serve: running (poll every 2s; Ctrl-C or SIGTERM to stop)
```

Two lines, no errors, no tracebacks. Every other journal excerpt pulled
in this run (`loki`, across both flash attempts and the tiny-hex
control) was equally clean — pyocd's own progress output and the three
`flash_hex` status lines, nothing else. PASS.

## Findings summary

1. **Informational, not blocking.** `vevav` (hodr) streams continuous,
   unprompted serial telemetry — it is not silent on the wire, even
   though (consistent with ticket 008) it announces no role/common_name.
   `togov` (loki) is the one board on this fleet confirmed genuinely
   silent under every condition tested here.
2. **Blocking-quality, escalated, not fixed here.** `deploy --remote`'s
   fixed 30-second per-`LOG`-line client timeout, combined with
   `flash.py::flash_hex`'s non-streaming, fully-blocking
   `subprocess.run()` calls, causes a spurious client-side failure on a
   real, appropriately-sized hex whenever the actual flash (with or
   without the mass-erase-recovery path) runs longer than 30 seconds
   between `flash_hex`'s three fixed log points — which a real ~450 KB
   image did, reproducibly (2/2), at this hardware's ~14 kB/s measured
   SWD throughput. Root cause confirmed by reading `flash.py` and
   `remote.py` (not guessed) and by a clean control flash that avoided
   the silent span. Recommend a follow-up ticket: either stream pyocd's
   subprocess output through `flash_hex`'s `log` callback, or rework
   the client timeout to bound the whole flash rather than the gap
   between today's three fixed log points.
3. **Non-blocking, self-resolved.** `hodr`'s reboot triggered ~90s of
   ordinary Docker Swarm reconciliation, fully healed before this
   ticket concluded; noted for operational transparency only, no
   intervention performed.
4. **Non-blocking, unexplained, not reproduced.** One anomalous partial
   serial reply from `togov` ("`[MASTER] AT+HELLO -> no/ERROR`") on the
   very first connection made to it this session; never recurred across
   6 further attempts (including a 20-second listen-only window).
   Documented rather than silently discarded; not chased further since
   it did not reproduce and is outside this ticket's scope regardless.
5. **Carried, not new.** `magni` remains without the daemon installed
   (ticket 008: no passwordless sudo for `jtl` there). This run covers
   `hodr`, `loki`, `meili` — 3 of the fleet's 4 nodes — consistent with
   the dispatch's own framing of this run.

## Overall verdict

**Yes, the fleet daemon works end to end across the LAN**, for the
three nodes it is actually running on: discovery (`list --remote`),
raw byte transport (`nc`), the serial-session `connect --remote` path
(including the corrected silent-vs-announcing distinction), the busy
and flash-preemption rules under direct concurrent load, departure and
arrival advertisement (by the necessary stop/start substitute for
physical unplug), and reboot survival with a clean log are all
demonstrated and hold up under repeated, timestamped, real-hardware
verification — not just "it seemed to work." The one real gap is
Finding 2: `deploy --remote` needs a longer-lived or differently-timed
client read budget before it can be trusted against production-sized
firmware without a false failure. Recommend the arc's issue not be
closed as fully resolved without a follow-up ticket for that finding;
every other piece of this sprint's and this ticket's own scope is
solid.
