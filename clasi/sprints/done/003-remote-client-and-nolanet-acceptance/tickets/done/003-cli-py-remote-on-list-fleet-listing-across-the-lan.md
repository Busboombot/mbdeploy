---
id: '003'
title: 'cli.py: --remote on list (fleet listing across the LAN)'
status: done
use-cases:
- SUC-010
depends-on:
- '002'
github-issue: ''
issue: mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# cli.py: --remote on list (fleet listing across the LAN)

## Description

Add `remote.list_remote(timeout=2.0) -> list[dict]` to `remote.py`: browse
both `_mbserial._tcp.local.` and `_mbflash._tcp.local.`, group the
results by TXT `uid` (the one field both a board's two registrations
share), recover each board's short name from its `browse()` `name`
field (ticket 002's helper logic, factored so both `resolve_board` and
`list_remote` share the short-name-recovery code rather than
duplicating it), and return rows shaped like the existing device table
(`enum`, `common`, `role`, `uid`) plus a `host` field.

Wire `--remote` into `cli.py`'s `list` subparser (`store_true`) and
`_cmd_list`: when set, skip the local `devices_mod.flashable_probes()`/
`load_devices` path entirely and print `list_remote()`'s rows through a
table renderer with an added HOST column, reusing `_ROW_FMT`'s style
rather than inventing a second table format.

## Acceptance Criteria

- [x] `remote.list_remote()` groups a stubbed `mdns.browse()`'s
      multi-service results into one row per board, by `uid`.
- [x] `mbdeploy list --remote` prints a table with a HOST column; the
      existing local `mbdeploy list` output is byte-for-byte unchanged
      when `--remote` is omitted.
- [x] `--fast`/`--target-mcu` (local-only flags) are rejected or
      ignored consistently with `--remote`'s "no local registry
      involved" nature — document whichever choice is made in the
      `--help` text for `--remote`.
- [ ] On real Nolanet hardware (validated in ticket 009, not here):
      `mbdeploy list --remote` shows 4 boards across 4 distinct hosts.

## Testing

- **Existing tests to run**: `tests/test_cli.py` (or wherever `list`'s
  existing tests live) must pass unchanged — no behavior change to
  local `list`.
- **New tests to write**: `tests/test_remote.py` — `list_remote()`
  against a stubbed `mdns.browse()` returning several boards across
  both service types; `_cmd_list`'s `--remote` branch, asserting the
  printed table includes a HOST column matching each row's resolved
  host.
- **Verification command**: `uv run pytest tests/test_remote.py
  tests/test_cli.py`
