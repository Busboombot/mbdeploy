# Spike log: python-zeroconf + avahi-daemon coexistence

Ticket:
`clasi/sprints/002-fleet-daemon/tickets/001-avahi-coexistence-spike-verify-python-zeroconf-alongside-avahi-daemon-on-nolanet.md`

Script: `scripts/spike_avahi_coexist.py` (throwaway, not shipped — see the
script's own docstring for usage).

**Verdict: PASS.** `python-zeroconf` coexists cleanly with `avahi-daemon`
on real Nolanet hardware. No design change needed for `mdns.py` (Ticket
003); the `avahi-publish`-backed fallback discussed in the sprint's
Design Rationale is not required.

## Environment

- Node: `loki` (192.168.1.149), Debian Bookworm, aarch64, Python 3.13.5.
- Checkout: `~/mbdeploy-test` on loki, refreshed to this branch's HEAD via
  `git archive`.
- `avahi-daemon` confirmed `active (running)` before the test began
  (`systemctl status avahi-daemon`, up 3+ weeks, `NRestarts=0`).
- `zeroconf` installed into the existing venv (`~/mbdeploy-test/.venv`):

  ```
  Collecting zeroconf
    Using cached zeroconf-0.150.0-cp313-cp313-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl (2.1 MB)
  Collecting ifaddr>=0.1.7 (from zeroconf)
    Using cached ifaddr-0.2.0-py3-none-any.whl (12 kB)
  Successfully installed ifaddr-0.2.0 zeroconf-0.150.0
  ```

  A prebuilt `manylinux`/aarch64 `cp313` wheel — no compilation step.
  Confirms the sprint architecture's stated fact rather than re-deriving
  it.

## Test sequence and results

1. **Register.** `scripts/spike_avahi_coexist.py selftest` registered
   `spike-loki._mbspike._tcp.local.` (port 17235, TXT `uid`, `role`,
   `msg` — `msg`'s value includes a non-ASCII character to exercise the
   UTF-8 byte round trip) via `zeroconf.Zeroconf().register_service()`.
   No bind error, no exception:

   ```
   [selftest] registering spike-loki._mbspike._tcp.local. on port 17235 ...
   [selftest] registered OK -- no bind error, no exception.
   ```

2. **Browse — same process (`ServiceBrowser`).**

   ```
   [selftest] browse discovered the registered instance.
   [found] spike-loki._mbspike._tcp.local.
           addresses: ['192.168.1.149']
           port: 17235
           server: loki.local.
           txt[uid] = b'spike-uid-0001' expected b'spike-uid-0001' [OK]
           txt[role] = b'spike' expected b'spike' [OK]
           txt[msg] = b'hello-mbdeploy-\xe2\x98\x83' expected b'hello-mbdeploy-\xe2\x98\x83' [OK]
   ```

3. **Browse — `avahi-browse -rt` on the node itself**, run concurrently
   with a `register --hold 40` invocation. Avahi's own resolver found the
   zeroconf-registered instance on every interface it manages (`docker0`,
   `docker_gwbridge`, `wlan0`, `lo`), TXT intact:

   ```
   =  wlan0 IPv4 spike-loki                                    _mbspike._tcp        local
      hostname = [loki.local]
      address = [192.168.1.149]
      port = [17235]
      txt = ["msg=hello-mbdeploy-☃" "role=spike" "uid=spike-uid-0001"]
   ```

   This is the stronger coexistence proof: not just "zeroconf didn't
   crash next to avahi," but "avahi's own responder correctly sees and
   resolves the record zeroconf put on the wire."

4. **Browse — from the Mac, across the LAN**, same `register --hold 40`
   window, using `dns-sd` (backgrounded with a manual `sleep`/`kill`
   since `dns-sd` does not exit on its own):

   ```
   $ dns-sd -B _mbspike._tcp .
   13:30:31.975  Add   3   6 local.  _mbspike._tcp.  spike-loki
   13:30:31.975  Add   2  12 local.  _mbspike._tcp.  spike-loki

   $ dns-sd -L spike-loki _mbspike._tcp local
   13:30:45.055  spike-loki._mbspike._tcp.local. can be reached at loki.local.:17235 (interface 12)
    uid=spike-uid-0001 role=spike msg=hello-mbdeploy-☃
   ```

   Full cross-LAN discovery and resolution, TXT intact including the
   non-ASCII byte.

5. **TXT round trip.** Confirmed byte-for-byte identical on both the
   in-process `ServiceBrowser` path and the Mac's `dns-sd -L` path,
   including the UTF-8-encoded non-ASCII test value
   (`hello-mbdeploy-\xe2\x98\x83` / `hello-mbdeploy-☃`). No mangling in
   either direction.

6. **Unregister.**

   ```
   [selftest] unregistering...
   [selftest] unregister confirmed: advertisement disappeared.
   ```

   Confirmed authoritatively on the node itself: a fresh `avahi-browse
   -rt _mbspike._tcp` run immediately after unregistering the
   `register --hold 40` instance returned **zero** entries (exit 0, no
   output) — avahi's own view shows the record gone.

   **Surprise, noted for completeness:** on the Mac, `dns-sd -B`/`-L`
   run ~20s after unregister still returned a cached, resolvable answer
   once; a second `dns-sd -B` run about a minute later did observe a live
   `Rmv` event partway through its window. This is standard mDNS
   resolver-cache behavior on macOS (`mDNSResponder` is a long-running
   system daemon that can serve a still-live-TTL cached answer before a
   goodbye/re-query cycle catches up) — not a defect in
   `python-zeroconf`'s unregister, which the node-side `avahi-browse`
   check confirms fired correctly and immediately. Worth knowing for
   Ticket 003/007 if a client-side `--remote list` ever needs to react to
   a board's departure quickly: expect eventual, not instant, consistency
   from a macOS/Bonjour client's cache, even though the origin node's own
   avahi and any Linux `avahi-browse` client converge immediately.

7. **Avahi health, before/during/after.**
   - `systemctl status avahi-daemon`: `active (running)` throughout,
     `NRestarts=0` at the end.
   - `journalctl -u avahi-daemon` over the full test window (and a wider
     60-minute sanity window): **no entries** — avahi logged nothing,
     i.e. no conflict, no error, no restart worth mentioning.
   - `raspi-cluster.local` and `loki.local` both resolved via `ping` from
     the Mac before and after the entire test, unchanged (`.150` and
     `.149` respectively) — the cluster's own naming was undisturbed.

## Acceptance criteria checklist

- [x] Register `_mbspike._tcp` with TXT via `zeroconf.ServiceInfo` on a
      real node (`loki`) — no bind error, no exception.
- [x] Browse and discover it, port + TXT round-trip correct — both
      in-process (`ServiceBrowser`) and via `avahi-browse -rt` on the
      node.
- [x] `avahi-daemon` stayed `active (running)` throughout, `NRestarts=0`,
      no journal entries.
- [x] Avahi's own advertisements still resolvable during/after:
      `raspi-cluster.local`/`loki.local` still resolve via `ping` from
      the Mac; `avahi-browse`/`avahi-resolve` on the node unaffected.
- [x] No port-5353 bind conflict or exception raised by `python-zeroconf`
      at any point.
- [x] `zeroconf` installs from a prebuilt aarch64 (`cp313`, manylinux)
      wheel on Python 3.13.5/Bookworm — no compilation. Confirmed above.
- [x] Findings recorded here and in the ticket's own checklist.

## Cleanup performed

- `register --hold` process completed and exited on its own (unregister
  + close observed in its log); confirmed no `spike_avahi_coexist.py`
  process left running on `loki` afterward.
- `/tmp/spike_register.log` removed from `loki`.
- Final `avahi-browse -rt _mbspike._tcp` on `loki` returned nothing.
