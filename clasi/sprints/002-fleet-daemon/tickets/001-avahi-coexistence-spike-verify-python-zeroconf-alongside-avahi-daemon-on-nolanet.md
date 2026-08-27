---
id: '001'
title: 'Avahi coexistence spike: verify python-zeroconf alongside avahi-daemon on
  Nolanet'
status: in-progress
use-cases:
- SUC-009
depends-on: []
github-issue: ''
issue: mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# Avahi coexistence spike: verify python-zeroconf alongside avahi-daemon on Nolanet

## Description

This sprint's whole `mdns.py` approach rests on one assumption: that
`python-zeroconf`, registering and browsing its own mDNS records, can run
alongside `avahi-daemon` on the same node without either one breaking the
other. `avahi-daemon` is confirmed **active on all four Nolanet nodes**
today, and it is not being touched by this sprint — Nolanet depends on it
for reasons unrelated to `mbdeploy`. Coexisting with an existing responder
on port 5353 is a well-trodden pattern (Home Assistant, ESPHome both do
exactly this on Raspberry Pi), but "well-trodden elsewhere" is not the
same as "verified on this hardware/OS/Python combination," and every
other ticket in this sprint is written assuming it holds. This ticket
retires that risk before any of them lands, per the sprint architecture's
explicit sequencing decision (see sprint.md "Migration Concerns" and the
Risks category of the self-review).

This is a spike, not a feature: a small throwaway script run against a
real Nolanet node over SSH, not permanent product code. Its job is to
produce a pass/fail answer with evidence, not to ship anything.

## Acceptance Criteria

- [x] A short script (e.g. `scripts/spike_avahi_coexist.py`, not part of
      the shipped package) registers a test mDNS service
      (`_mbspike._tcp`, arbitrary port, a TXT record with at least one
      key) via `zeroconf.Zeroconf`/`zeroconf.ServiceInfo` on a real
      Nolanet node (e.g. `magni` at `.147`).
      **Done** — `scripts/spike_avahi_coexist.py`, run on `loki`
      (192.168.1.149); registered `spike-loki._mbspike._tcp.local.` port
      17235 with TXT `uid`/`role`/`msg`, no bind error, no exception.
- [x] The same script (or a second invocation, same or a different
      machine on the LAN) browses for `_mbspike._tcp` via
      `zeroconf.ServiceBrowser` and successfully discovers the
      registered instance, confirming its port and TXT record round-trip
      correctly.
      **Done** — in-process `ServiceBrowser` found it (port + all 3 TXT
      keys byte-exact); also independently confirmed via
      `avahi-browse -rt` on the node and `dns-sd -B`/`-L` from the Mac
      across the LAN. See `docs/spikes/002-avahi-coexistence.md`.
- [x] `avahi-daemon` remains `active (running)`
      (`systemctl status avahi-daemon`) throughout the test, with no
      restart, crash, or error logged (`journalctl -u avahi-daemon`
      during the test window).
      **Done** — `active (running)` before/after, `NRestarts=0`,
      `journalctl -u avahi-daemon` empty for the entire test window.
- [x] Avahi's own advertisements are still resolvable during and after
      the test (e.g. `avahi-browse -at` from a second machine still
      lists the node, or `avahi-resolve -n <hostname>.local` still
      succeeds) — proof of coexistence, not just "zeroconf didn't
      crash."
      **Done** — `raspi-cluster.local` and `loki.local` both resolved
      via `ping` from the Mac before and after the test, unchanged
      (`.150`/`.149`); `avahi-browse`/`avahi-resolve` on the node
      unaffected throughout.
- [x] No port-5353 bind conflict or exception is raised by
      `python-zeroconf` at any point.
      **Done** — none observed across register, browse, and unregister.
- [x] `zeroconf` is confirmed to install from a prebuilt aarch64 wheel on
      the node's Python 3.13.5/Debian Bookworm (no compilation step) —
      corroborating the fact already stated in the sprint architecture,
      not re-deriving it from scratch.
      **Done** — `zeroconf-0.150.0-cp313-cp313-manylinux2014_aarch64...whl`,
      `ifaddr-0.2.0`, no compile step.
- [x] Findings (pass/fail, any surprises) are recorded in this ticket's
      own notes or a short spike log committed alongside the script, so
      Ticket 003 (`mdns.py`) can reference it if the result changes any
      design assumption.
      **Done** — full log and one noted surprise (macOS `dns-sd`
      resolver-cache lag on unregister, not a `python-zeroconf` defect)
      in `docs/spikes/002-avahi-coexistence.md`.

## Spike Result

**PASS.** `python-zeroconf` coexists cleanly with `avahi-daemon` on real
Nolanet hardware (`loki`). Register, browse (local and cross-LAN),
TXT round trip, and unregister all worked correctly with no impact on
avahi. No design change needed for Ticket 003's `mdns.py`; the
`avahi-publish` fallback discussed in the sprint's Design Rationale is
not required. Full evidence: `docs/spikes/002-avahi-coexistence.md`.

## Implementation Plan

**Approach**: This ticket is executed against real hardware — it
requires SSH access to a Nolanet node (`jtl@<node>.local`, key
`~/.ssh/raspi-cluster_ed25519`) and cannot be completed by an agent
without that access. If the executing agent has no path to the hardware,
it must stop and report rather than fabricate a result; the spike's
pass/fail is a hardware fact, not something to reason about
in the abstract.

1. SSH to one node (`magni`, `192.168.x.147`, or whichever is reachable).
2. Create a throwaway venv: `python3 -m venv /tmp/zcspike && source
   /tmp/zcspike/bin/activate && pip install zeroconf`. Confirm the
   installed wheel is a prebuilt `manylinux`/aarch64 wheel, not a source
   build (check `pip install`'s output for a `.whl` filename, not a
   compile step).
3. Write and run `scripts/spike_avahi_coexist.py` (kept in the repo under
   `scripts/`, not `src/mbdeploy/`, since it is a one-off verification
   tool, not shipped product code): register `_mbspike._tcp` with a
   `ServiceInfo`, sleep briefly, browse for it with a
   `ServiceListener`/`ServiceBrowser`, print what was found, then
   unregister and close cleanly.
4. Before, during, and after the script runs, capture
   `systemctl status avahi-daemon` and a `journalctl -u avahi-daemon
   --since <test start>` snippet.
5. From a second machine on the same LAN (a laptop, or another Nolanet
   node), run `avahi-browse -at` and `avahi-resolve -n <node>.local` to
   confirm avahi's own responder is unaffected.
6. Record results directly in this ticket (edit this file, filling in
   the Acceptance Criteria checkboxes with a one-line note each) or in a
   short `docs/spikes/002-avahi-coexistence.md` if more detail is useful
   to Ticket 003's implementer.

**Files to create**: `scripts/spike_avahi_coexist.py` (throwaway,
committed for reproducibility but not part of the wheel — no
`pyproject.toml` change needed for it).

**Testing plan**: This ticket's own verification *is* the manual hardware
test described above; there is no unit test to write, since the entire
point is that a fake `Zeroconf` (used everywhere else in this sprint's
test suite) cannot answer the coexistence question.

**Documentation updates**: None required by this ticket specifically;
if the result surfaces something Ticket 003's implementer needs to know
(e.g. a specific zeroconf constructor argument needed for coexistence),
note it in Ticket 003 itself before that ticket starts.

**If the spike fails**: this is the one ticket in the sprint whose
failure has architectural consequences beyond itself — if `zeroconf`
cannot coexist with `avahi-daemon` on Nolanet, `mdns.py`'s backend choice
(Step 6 design rationale in `sprint.md`) needs to be revisited before
Ticket 003 proceeds. Throw a ticket exception per the sprint-planner's
exception protocol rather than silently reworking the architecture from
inside this ticket.
