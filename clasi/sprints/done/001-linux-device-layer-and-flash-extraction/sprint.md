---
id: '001'
title: Linux device layer and flash extraction
status: done
branch: sprint/001-linux-device-layer-and-flash-extraction
use-cases:
- SUC-001
- SUC-002
issues:
- mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# Sprint 001: Linux device layer and flash extraction

## Goals

- Make `port_serial_map()` — and everything downstream of it — work on
  Linux, on `serial.tools.list_ports` filtered to VID:PID `0x0D28:0x0204`,
  instead of shelling out to macOS `ioreg`.
- Extract the flash → mass-erase-recovery → retry → reset sequence out of
  `cli.py`'s `_cmd_deploy` into `src/mbdeploy/flash.py`, so Sprint 002's
  daemon has one flash implementation to call rather than a second copy.
- Write the device-layer and announcement-dialect tests that don't exist
  today (see Problem).
- Correct docs that currently claim Linux is unsupported.

## Problem

Sprint 1 of a 3-sprint arc implementing
`clasi/issues/mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md`.
Nothing else in the arc is verifiable until the device layer works on
Linux — the real deployment target is Nolanet, a 4-node Raspberry Pi
Docker Swarm. Review also found: no direct test of `port_serial_map`
exists (all ~10 references in `tests/test_devices.py` monkeypatch it
away, so the issue's claim that "existing tests pin the contract" is
false); `tests/test_devices.py:695` asserts `"ioreg" in stderr` and will
break; and the announcement-parser fix for both `DEVICE:` dialects
(commit `2e19088`) shipped with zero tests.

## Solution

Reimplement `port_serial_map()` on `serial.tools.list_ports.comports()`,
keeping its signature and `known` parameter untouched so every caller
(`probe_all`, `_deploy_entry`, `_connect_port`) is unaffected. Delete the
`ioreg` subprocess and both `_IOREG_*` regexes. Extract
`flash_hex(uid, hex_path, target_mcu=DEFAULT_MCU, log=None)` verbatim
from `cli.py:336-377`, `log=None` still printing to stderr (required —
`test_mass_erase_failure_aborts_without_retry` asserts on it).

## Success Criteria

- `pytest` green on aarch64, run on a Nolanet node.
- `mbdeploy probe` on a Nolanet node reports `/dev/ttyACM0` (not `null`)
  and correctly types the board.
- Existing macOS deploy tests in `tests/test_devices.py` pass unchanged
  — proof the flash extraction was behavior-preserving.

## Scope

### In Scope

- `devices.py`: reimplement `port_serial_map()`; delete `ioreg`
  subprocess and both `_IOREG_*` regexes.
- New `port_serial_map` tests: `serial_number=None` ports (three exist
  on the dev machine), non-micro:bit VID:PID exclusion, `known`-set
  behavior.
- Fix `tests/test_devices.py:695` and the message at `cli.py:244`: an
  empty port map on Linux means "no micro:bit CDC port found," not
  "wrong platform."
- Regression tests for both `DEVICE:` announcement dialects (colon and
  the robot's space-separated form).
- Extract `src/mbdeploy/flash.py::flash_hex`; `_cmd_deploy` becomes a
  call to it. New `tests/test_flash.py`.
- README / `agent_manual.md` §4: drop the false "other platforms"
  caveat; document Nolanet/Pi setup. Note Nolanet's `jtl` is already in
  `plugdev`/`dialout` and Raspberry Pi OS ships `70-microbit.rules` for
  VID `0d28` — **no new udev rule needed there**, correcting the
  issue's proposed `MODE="0666"` rule as unnecessary in this
  environment.

### Out of Scope

- mDNS/daemon (`mdns.py`, `server.py`, `serve`) — Sprint 002.
- `--remote`, and the Nolanet multi-node install/acceptance run —
  Sprint 003.
- Any change to the `raspi-cluster` Ansible repo.
- Reconciling `config/devices.json` entries with empty `role`
  (pre-dating the announcement fix) — fixed by the next `probe`,
  naturally, during Sprint 003's Nolanet setup.

## Test Strategy

Unit tests only, run on both macOS and Linux (a Nolanet node) — the
point of this sprint is portability. New: `port_serial_map` tests,
announcement-dialect tests, `test_flash.py`. The existing `test_devices.py`
deploy tests must pass unchanged.

## Architecture

**Sizing: Substantial** — three source modules are touched (`devices.py`
reimplemented, `cli.py` trimmed, new `flash.py` extracted), which crosses
the tier-3 threshold by module count alone even though no new subsystem
is *composed*: `port_serial_map()` is a like-for-like reimplementation
behind its unchanged interface, and `flash.py` is a mechanical extraction
of an existing inline block to a single new call site. Per the sprint 020
precedent (substantial by module count, no diagram because nothing new is
being composed), Step 4 below states why a component diagram is omitted
rather than including one. No use case changes user-visibly — this sprint
makes UC-001/UC-002/UC-005/UC-006/UC-007 work correctly on a second
platform; it does not add or alter behavior any of them describe. There is
no prior consolidated architecture document to reconcile against — this
is the project's first sprint (`design_docs: disabled` in
`.clasi/config.yaml`; architecture lives only in sprint sections) — so
`docs/design/overview.md` and `docs/design/specification.md` §§5.2, 8,
11–12 stand in as the current-state reference below.

### Step 1 — Understand the problem

`devices.port_serial_map()` shells out to macOS `ioreg` and returns `{}`
on every other platform (`specification.md` §11.1). Every downstream
consumer — `probe_all`, `cli._deploy_entry`, `cli._connect_port` —
degrades the same way off macOS: `probe` records `port: null` fleet-wide,
`deploy` refuses any `/dev/…` target, and no `DEVICE:` announcement is
ever captured because it has no port to open. Nothing in the 3-sprint
`serve` arc (sprints 002–003) is verifiable until this is fixed, because
the real deployment target is a Raspberry Pi. Two pieces of test debt ride
along: `port_serial_map` itself has never had a direct test (all ~10
references in `tests/test_devices.py` monkeypatch it away), and the
two-dialect `DEVICE:` announcement parser (commit `2e19088`) shipped with
zero tests of its own. Separately, sprint 002's daemon needs the flash →
mass-erase-recovery → retry → reset sequence available as a function it
can call, not as logic inlined in `cli._cmd_deploy`.

### Step 2 — Identify responsibilities

- **R1 — Live USB→UID port discovery.** Currently macOS-only
  (`ioreg` parsing). Must become cross-platform.
- **R2 — Error messaging when no live port map is available.** Currently
  worded around R1's old implementation (names `ioreg` explicitly);
  changes for the same reason R1 does, in the same commit, but lives in a
  different file (`cli.py` vs `devices.py`).
- **R3 — The flash-with-recovery sequence.** Currently inlined in
  `cli._cmd_deploy`; needs to become independently callable so sprint
  002's daemon doesn't fork it.
- **R4 — Announcement-dialect parsing test coverage.** Pure test debt;
  `probe_type` itself does not change.
- **R5 — User-facing platform documentation.** Depends on R1/R2's real
  post-fix behavior, so it must follow them, not precede them.

R1 and R2 change together (same cause, same commit) but live in different
modules, which is exactly why they show up as two module-level changes
rather than one. R3 is independent of R1/R2 — it touches the flash path,
not the discovery path. R4 is test-only and independent of everything
else. R5 depends on R1/R2's shipped behavior.

### Step 3 — Define subsystems and modules

- **`devices.py`** (existing). Purpose: discover, identify, and persist
  the fleet's device registry. Boundary: owns UID discovery
  (`flashable_probes`), port discovery (`port_serial_map`), announcement
  parsing (`probe_type`), the SWD name read, and the registry merge
  (`probe_all`); nothing outside this module talks to USB/serial hardware
  directly. Change this sprint: `port_serial_map`'s *implementation*
  only — its signature, its `known` parameter, and every caller are
  untouched. Serves UC-001, UC-002, UC-006, UC-007.
- **`cli.py`** (existing). Purpose: parse arguments and translate them
  into device-layer and flash calls. Boundary: owns argparse wiring,
  table formatting, and target-resolution glue (`_deploy_entry`,
  `_connect_port`); after this sprint it no longer owns flash mechanics.
  Change this sprint: `_cmd_deploy` delegates to `flash.flash_hex`, and
  `_deploy_entry`'s "no live port mapping" message drops its `ioreg`
  reference. Serves UC-005, UC-006, UC-007.
- **`flash.py`** (new). Purpose: run the flash-verify-recover-reset
  sequence for one board. Boundary: owns the three pyOCD subprocess
  invocations and the mass-erase retry decision; takes a UID, a hex path,
  and an optional log callback, returns an exit code, and knows nothing
  about argparse, the registry, or which board a user typed. Serves
  UC-005, UC-006 today; drawn this narrow specifically so sprint 002's
  `server.py` can call it unchanged instead of growing a second,
  divergent recovery path (the issue calls this out explicitly).

### Step 4 — Diagrams

**None included, deliberately.** This sprint adds exactly one new edge to
the module graph — `cli.py → flash.py` — and that edge is a decomposition
of a call `cli.py` already made to `pyocd` inline; it is not a new
dependency on an external system, and it composes nothing new with
anything else. `port_serial_map`'s reimplementation changes what happens
*inside* an existing, unchanged interface (`devices.py`'s public
contract), so it produces no edge at all. A diagram here would show
exactly the shape already stated in one sentence — "cli calls devices and
flash" — the same exception sprint 020 recorded: substantial by module
count, no diagram because there is no new composition to draw. No data
model changes, so no ERD. No dependency direction changes (cli already
sat above devices; the new cli→flash edge is the same direction), so no
dependency graph.

### Step 5 — What Changed / Why / Impact / Migration

**What Changed**

- `devices.port_serial_map(known=None)` reimplemented on
  `serial.tools.list_ports.comports()`, filtered to VID:PID
  `0x0D28:0x0204` and skipping any port with `serial_number is None`
  (three such ports exist on the dev Mac alone: Bluetooth-Incoming-Port,
  debug-console, wlan-debug). Signature, parameter name, and return shape
  (`dict[uid, port]`) unchanged; every caller (`probe_all`,
  `cli._deploy_entry`, `cli._connect_port`) is untouched. The `ioreg`
  subprocess call and both `_IOREG_*` regexes are deleted.
- `cli._deploy_entry`'s "no live port mapping" error no longer names
  `ioreg`; it states the platform-neutral fact (no micro:bit serial port
  found) and, since the likeliest cause on a Pi is a permissions problem,
  points at `plugdev`/`dialout` group membership.
- New `src/mbdeploy/flash.py` with
  `flash_hex(uid, hex_path, target_mcu=DEFAULT_MCU, log=None) -> int`,
  moved verbatim from `cli.py:336-377`. `_cmd_deploy` becomes a call to
  it. `log=None` still prints to stderr, so CLI behavior is unchanged;
  the parameter exists because sprint 002's daemon needs to turn pyocd's
  chatter into `LOG` protocol lines instead.
- First-ever direct tests for `port_serial_map` (against fake `comports()`
  objects) and for both `probe_type` announcement dialects (colon and
  space-delimited), neither of which existed before this sprint.
- `README.md` and `agent_manual.md` updated: every `ioreg` reference and
  the "other platforms" targeting caveat removed; a Raspberry Pi/Nolanet
  setup section added, grounded in facts verified on real hardware (no
  new udev rule needed — Raspberry Pi OS already ships
  `70-microbit.rules`; `jtl` is already in `plugdev`/`dialout`; ports are
  `/dev/ttyACM0`).

**Why**

Sprint 002's daemon and sprint 003's Nolanet acceptance run cannot be
validated at all until `probe` produces real ports on Linux — everything
else in the arc sits downstream of this one function. Extracting
`flash.py` now, rather than during sprint 002, means the daemon gets one
tested flash implementation to call instead of growing a second copy of
the mass-erase recovery path — a divergence the issue calls out by name
as a thing to avoid.

**Impact on Existing Components**

- `devices.py` callers (`probe_all`, `_deploy_entry`, `_connect_port`) —
  no change required; `known` and the return shape are preserved exactly.
- `cli._cmd_deploy` — behavior-preserving by construction. The three
  existing tests that patch `subprocess.run` on the shared `subprocess`
  module (`test_flash_retries_after_mass_erase`,
  `test_mass_erase_failure_aborts_without_retry`,
  `test_successful_flash_skips_mass_erase`) must pass **unchanged**
  against the new module, because they patch the module-global
  `subprocess.run`, not a name imported into `cli` — that is the
  mechanical proof the extraction was verbatim.
- `tests/test_devices.py:695` (`test_no_live_map_is_refused_not_guessed`)
  currently asserts `"ioreg" in stderr`; that one assertion changes to
  match the new message. The test's other assertions (`rc != 0`,
  `calls == []`) are unaffected.
- No change to `console.py`, `builder.py`, or the registry JSON schema.

**Migration Concerns**

- None for the registry file format — `port` stays a plain string
  regardless of which platform produced it.
- Operational, not code: `config/devices.json` entries probed before the
  announcement-dialect fix (commit `2e19088`) can still hold an empty
  `role`, which makes `is_relay()` False for that board until its next
  `probe`. This sprint does not touch that data — a fresh `probe` fixes
  it naturally — but sprint 002's daemon leans on `is_relay()` as its
  flash guard, so a stale, empty-role entry is a live gap the moment
  `serve` starts polling a registry nobody has re-probed. Flagging this
  as a precondition sprint 002/003 planning should account for, not a
  defect owed here.
- Code and docs ship together, so there is no window where a "Linux
  unsupported" claim outlives a working Linux implementation.

### Step 6 — Design Rationale

**Decision: reimplement on `serial.tools.list_ports.comports()`.**
*Context*: need one UID→port map that behaves identically on macOS and
Linux, without a second platform-specific code path.
*Alternatives considered*: (a) keep `ioreg` for macOS and add a
Linux-specific scraper (`udevadm`, `/sys/class/tty`) — rejected, doubles
the maintenance surface for exactly the two-native-backends trap the
issue's own mDNS section independently steers away from; (b) enumerate
via `pyusb`/`libusb` directly — rejected, `pyserial` is already a
declared dependency and its `comports()` already exposes `serial_number`
and `vid`/`pid` cross-platform.
*Why this choice*: one implementation, no new dependency, and the
`serial_number == pyOCD UID` equivalence was verified on real hardware on
both target platforms (4 macOS boards, one Linux/aarch64 board).
*Consequences*: no subprocess at all — a latency and reliability win over
the code it replaces — and the VID:PID filter becomes a second,
independent guard alongside the pre-existing `known`-set filter.

**Decision: extract `flash_hex` verbatim rather than restructuring it.**
*Context*: sprint 002 needs one flash implementation both the CLI and the
daemon call; the safest way to prove that before daemon code exists is a
diff-minimal move.
*Alternatives considered*: clean up the internal structure while
extracting — rejected for this sprint; the three tests pinning the
mass-erase recovery path give an unusually strong regression net
specifically because they patch the shared `subprocess` module rather
than a name imported into `cli`, and a verbatim move is what keeps that
net valid without touching the tests themselves. A restructuring pass, if
wanted, is better done as its own later change.
*Why this choice*: minimizes risk to a path with expensive-to-reproduce
hardware failure modes (locked/protected parts).
*Consequences*: `flash.py`'s first version inherits `cli.py`'s current
style rather than an idealized one — an explicit, accepted trade of
polish for safety.

### Step 7 — Open Questions

- The issue's Pi host-setup section proposes a udev
  `MODE="0666"` rule; this sprint's own verification found Raspberry Pi
  OS already ships `70-microbit.rules` (`TAG+="uaccess"`) for VID `0d28`,
  making that rule unnecessary on Nolanet. Open question for the
  stakeholder: keep the `MODE="0666"` rule documented as a fallback for a
  non-Raspberry-Pi-OS Linux target, or state the Pi-specific fact only?
  This plan takes the latter (docs describe what Nolanet actually needs)
  unless told otherwise.
- Stale, empty-`role` registry entries (specification.md §12, D7) are a
  known pre-existing gap this sprint does not fix, only surfaces as a
  dependency for sprint 002/003 (see Migration Concerns).
- Documenting the second `DEVICE:` announcement dialect (specification.md
  §12, D1) in README/`agent_manual.md` is adjacent to this sprint's test
  fix but outside its stated doc scope (ioreg/platform caveat + Pi setup
  only); flagging it for whichever sprint next touches those docs.

### Architecture Self-Review

Full five-category review, run because this sprint is substantial.

- **Consistency** — Sprint Changes above match the module descriptions in
  Step 3; both design decisions in Step 6 are the ones the "What Changed"
  list actually depends on.
- **Codebase Alignment** — Verified directly against source: the
  extraction boundaries (`cli.py:336-377`), the three subprocess-patching
  tests, and the `tests/test_devices.py:695` assertion were all read from
  the current tree, not assumed.
- **Design Quality** — Cohesion: `flash.py`'s purpose states in one
  sentence, no "and" ("runs the flash-verify-recover-reset sequence for
  one board"). Coupling: the only new edge is `cli.py → flash.py`, fan-out
  unchanged, no cycle. Boundaries: `flash_hex`'s interface (uid, hex_path,
  target_mcu, log) is narrow and explicit. `devices.py` itself still
  bundles discovery and registry persistence in one module — a
  pre-existing condition this sprint does not worsen or attempt to fix.
- **Anti-Pattern Detection** — No god component introduced (the
  extraction *reduces* `cli.py`'s scope, undoing a mild shotgun-surgery
  risk where flash logic and argument parsing were tangled). No feature
  envy, shared mutable state, or circular dependency introduced. One
  borderline case considered: `flash_hex`'s `log` parameter has no
  non-`None` caller until sprint 002. Judged acceptable, not speculative
  generality, because it is a single default-valued parameter (not a
  plugin surface), its consumer and call site are already specified
  concretely in the issue (`server.py`'s `serve_flash`), and the default
  behavior it must not disturb is pinned by an existing test
  (`test_mass_erase_failure_aborts_without_retry` asserts on stderr).
- **Risks** — No data migration (registry schema unchanged). Breaking
  changes are limited to two test assertions updated on purpose (the
  `ioreg` string check, and nothing else). Performance: dropping a
  subprocess spawn is a mild improvement, not a regression. Security:
  Linux port access depends on `plugdev`/`dialout` group membership,
  which is now documented rather than silently assumed. Deployment
  sequencing: none — this is a single, atomic, same-sprint change with no
  phased rollout.

**Verdict: APPROVE.** No revisions required; proceed to ticketing.

## Use Cases

No existing use case's user-visible behavior changes. This sprint makes
UC-001, UC-002, UC-005, UC-006, and UC-007 — all already documented in
`docs/design/usecases.md` — work correctly on a second platform, and
gives sprint 002 a single flash implementation to reuse. Two sprint-level
use cases capture what actually changes at the sprint level:

### SUC-001 — Discover and target the fleet from a Linux host

**Actor**: Operator, Agent (unchanged from UC-001/UC-006/UC-007)

**What's new**: `probe`, `deploy <name>`, `deploy /dev/ttyACM0`, and
`connect <name>` all resolve a real port on Linux instead of treating
every board as portless. `probe_all`'s merge algorithm, the relay guard,
and every target-resolution precedence rule are unchanged — only the
live UID→port map underneath them stops being `{}` off macOS.

**Acceptance signal**: on a Nolanet node, `mbdeploy probe` records
`port: /dev/ttyACM0` (not `null`) for a connected board and correctly
types it from its announcement — this is the prerequisite's real proof,
called out explicitly in the issue and in this sprint's success criteria.

### SUC-002 — One flash implementation, callable from more than one place

**Actor**: Operator, Agent today; sprint 002's daemon tomorrow (not built
in this sprint)

**What's new**: the flash → mass-erase-recovery → retry → reset sequence
that UC-005/UC-006 already describe now lives in `flash.flash_hex()`
instead of inline in `cli._cmd_deploy`. Nothing about what the operator
sees changes — same stderr messages, same exit codes, same retry
behavior — but the sequence is now a function with a `log` hook, so
sprint 002 can reuse it instead of forking it.

**Acceptance signal**: the three existing mass-erase-recovery tests in
`tests/test_devices.py` pass unchanged against the extracted
implementation.

## Dependencies

None — the foundation sprint. Sprints 002 and 003 both depend on it. It
ships standalone value even if the arc stopped here: mbdeploy would work
correctly on the Pi with no daemon at all.

## GitHub Issues

(GitHub issues linked to this sprint's tickets. Format: `owner/repo#N`.)

## Definition of Ready

Before tickets can be created, all of the following must be true:

- [ ] Sprint planning document is complete (sprint.md, including its
      Architecture and Use Cases sections)
- [ ] Architecture review passed (or skipped, for changes with no
      architectural impact)
- [ ] Stakeholder has approved the sprint plan

## Tickets

| # | Title | Depends On |
|---|-------|------------|
| 001 | Port `port_serial_map` off `ioreg` onto pyserial, and fix the now-false `ioreg` error path | — |
| 002 | Extract `flash_hex` into `src/mbdeploy/flash.py` | — |
| 003 | Regression tests for both `DEVICE:` announcement dialects | — |
| 004 | Correct README and `agent_manual.md` for Linux/Nolanet support | 001 |

Tickets execute serially in the order listed. 002 and 003 have no
dependency on 001 or on each other — they were ordered to match the
sprint's own goal ordering (port fix, then flash extraction, then test
debt), not because either blocks the other. 004 depends on 001 because it
documents behavior 001 delivers; describing it earlier would document a
caveat this sprint exists to remove before the code actually removes it.
