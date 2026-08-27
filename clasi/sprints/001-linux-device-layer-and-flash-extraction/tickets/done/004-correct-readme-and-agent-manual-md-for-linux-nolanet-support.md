---
id: '004'
title: Correct README and agent_manual.md for Linux/Nolanet support
status: done
use-cases:
- UC-001
- UC-005
- UC-006
- UC-007
- SUC-001
depends-on:
- '001'
github-issue: ''
issue: mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
completes_issue: false
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# Correct README and agent_manual.md for Linux/Nolanet support

## Description

`README.md` and `src/mbdeploy/agent_manual.md` currently document
`port_serial_map` as macOS-only and reachable "via `ioreg`." After
ticket 001 lands, that is false: the port map works identically on
Linux, and `ioreg` is gone from the code entirely. This ticket updates
both documents to match, and adds the Raspberry Pi/Nolanet setup
information operators need — grounded only in facts verified on real
hardware during this sprint's review, not in the issue's original,
partially-incorrect proposal.

Specifically:

- **Drop every `ioreg` phrasing.** `README.md` lines 7, 14, 16 currently
  say the port is read "via `ioreg` on macOS" and describe "the live
  `ioreg` mapping." `agent_manual.md` lines 141, 149, 160, 162 do the
  same, and line 162 explicitly says "Target by enum, name, or UID on
  other platforms" — the caveat this whole sprint exists to make untrue.
  All of these need rewording to describe the live port map
  platform-neutrally (it's a `pyserial` VID:PID scan now, not an
  OS-specific tool).
- **Add a Raspberry Pi / Nolanet setup section.** Correct against facts
  verified on Nolanet during this sprint's review, not the issue's
  original (partially wrong) proposal:
  - The service user (`jtl` on Nolanet) needs `plugdev` and `dialout`
    group membership to open `/dev/ttyACM*` — already true on Nolanet,
    but state it as a requirement for anyone setting up a new host.
  - **No new udev rule is needed on Raspberry Pi OS.** The issue proposed
    writing a `MODE="0666"` rule for VID `0d28`; verification found
    Raspberry Pi OS already ships `/lib/udev/rules.d/70-microbit.rules`
    with `TAG+="uaccess"` for that VID, which already grants the logged-in
    (or, via `plugdev`, group-member) user access. Do not tell operators
    to add a rule that already exists — say plainly that Raspberry Pi OS
    handles this out of the box, and (per this sprint's Architecture
    §7 open question) do not also document the `MODE="0666"` rule as a
    generic Linux fallback unless the stakeholder asks for that broader
    scope.
  - Ports are `/dev/ttyACM0`, not `/dev/cu.usbmodem*` — update any
    example command that hardcodes the macOS spelling.
  - Worth recording as an install-time expectation: all 23 of mbdeploy's
    dependencies resolve to prebuilt aarch64 wheels on Raspberry Pi OS
    (Debian Bookworm) — no compilation step — and a fresh venv measures
    about 83 MB.
- **Remove the false "other platforms" caveat** in `agent_manual.md` §4
  (currently: "If the live map is unavailable (it is read from macOS
  `ioreg`), `deploy` ... Target by enum, name, or UID on other
  platforms.") — replace with the platform-neutral refusal behavior that
  actually exists after ticket 001 (an empty live map is refused with a
  message about no port found / group membership, on any platform, not
  just off macOS).

This ticket depends on ticket 001: it documents the behavior that ticket
delivers, and describing it before it exists would just be a second,
premature version of the same caveat this ticket is meant to remove.

## Acceptance Criteria

- [x] No occurrence of the string `ioreg` remains in `README.md` or
      `src/mbdeploy/agent_manual.md`.
- [x] `agent_manual.md` §4's "Target by enum, name, or UID on other
      platforms" caveat (and the sentence it's attached to) is removed or
      rewritten to reflect that `/dev/…` targeting works on Linux too.
- [x] A new Raspberry Pi / Nolanet setup section exists in at least one
      of the two documents (agent manual preferred, since it's the
      operational reference), covering: `plugdev`/`dialout` group
      membership requirement; the explicit statement that Raspberry Pi OS
      already ships `70-microbit.rules` and **no new udev rule is
      required**; `/dev/ttyACM0` as the Linux port naming convention; and
      the dependency-install facts (23 deps as prebuilt aarch64 wheels,
      no compilation; ~83 MB venv).
- [x] The new section does **not** repeat the issue's proposed
      `MODE="0666"` udev rule as something Nolanet needs — that claim is
      verified incorrect for Raspberry Pi OS and must not be reintroduced.
- [x] No example command in either document uses the macOS
      `/dev/cu.usbmodem*` spelling where a Linux-applicable example would
      be clearer or is now equally valid (existing macOS-specific
      examples may stay as macOS examples; the point is not to state a
      Linux limitation that no longer exists).
- [x] `docs/design/overview.md`'s "Current state" limitation paragraph
      about `port_serial_map` being macOS-only is out of scope for this
      ticket (that file, like `specification.md` and `usecases.md`, is
      explicitly a reverse-engineered artifact this project's own
      convention treats as downstream of source, not hand-maintained —
      see `docs/design/overview.md`'s own header note) — do not edit it
      here.

## Implementation Plan

**Approach**: Grep both files for `ioreg` and `other platform`/`macOS`
port-mapping phrasing (already located above with line numbers) and
rewrite each in place. Add the new Pi/Nolanet section as a new
subsection near the existing platform/setup material in
`agent_manual.md` (its exact placement — which numbered section it
becomes — is left to whoever implements this, since the manual's section
numbers will shift regardless of where this lands).

**Files to modify**:
- `README.md` (lines 7, 14, 16 and any other `ioreg`/macOS-only phrasing
  found by grep).
- `src/mbdeploy/agent_manual.md` (§4, lines ~141-162 and any other
  `ioreg` occurrences found by grep; new Pi/Nolanet setup section).

**Testing plan**: No automated test covers documentation prose. Manual
verification: `grep -rn ioreg README.md src/mbdeploy/agent_manual.md`
returns nothing; a human read-through confirms the Pi section states
only verified facts (no invented udev rule, no invented package names).

**Documentation updates**: This ticket *is* the documentation update.
